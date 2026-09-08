"""
teste_cotas_e_limites.py — o que protege o disco e o processador de UMA VPS quando muita gente (ou um robo)
chega ao mesmo tempo: a cota por conta, a reserva transacional de bytes, o balde por identidade e o IP que
ninguem escolhe.

    Cotas.situacao(email) ---> Cota(lotes_ativos, bytes_usados, bancos, pode_enviar, motivo)   medida do disco + memoria curta
    FilaDeLotes.novo_lote(..., teto_ativos, bytes_previstos, teto_bytes, usados_em_disco)      a conferencia DENTRO da transacao
    Limitador.conferir(regra, request, quem) ---> 429 com Retry-After                          balde por conta; por IP so sem sessao
    ip_de_origem(request, confiaveis) ---> X-Forwarded-For so vale vindo de um proxy declarado, lido da direita

O QUE SE PROTEGE
  1. 06/08/2026: teto 1, oito envios simultaneos da mesma conta, SETE entraram (a cota era conferida antes de o lote existir).
  2. 08/08/2026 (L3): dez envios de 300 MB contra 1 GB, TODOS passavam — o disco so conta byte ja chegado.
  3. 08/08/2026 (M2): o banco proprio nao entrava na cota nem na retencao: ~200 MB/h permanentes e invisiveis.
  4. 06/08/2026: 30 logins seguidos trocando X-Forwarded-For, nenhuma recusa; e 15 abas de um NAT esgotavam o teto do predio.
  5. o disco cheio nao pode sair como 500 anonimo mandando a pessoa "corrigir a selecao".
"""

import errno
import shutil
import threading

from pathlib import Path

import pytest
from fastapi import HTTPException

from conftest import EMAIL, OUTRO_EMAIL
from bancos_de_referencia import id_do_usuario
from easycontig_web.contas import cota_de_espaco, limite_de_requisicoes
from easycontig_web.contas.cota_de_espaco import Cotas, bytes_da_pasta, tamanho
from easycontig_web.contas.limite_de_requisicoes import Limitador, Regra, RequisicaoDeMentira, duracao, ip_de_origem, proxies_confiaveis
from easycontig_web.dados.banco_sqlite import BancoSqlite
from easycontig_web.processamento.fila_de_lotes import FALHOU, NA_FILA, PRONTO, RECEBENDO, RODANDO, CotaDeBytesEstourada, FilaDeLotes




class EstanteVazia:

    #* o minimo de EstanteDeBancos que a cota olha: quem nao tem banco proprio

    def meus_bancos(self, email):

        return []




class Conta:

    #* um fila.sqlite3 + pasta de lotes + a Cotas de uma conta, com tetos escolhidos pelo teste (None = o do ambiente)

    def __init__(self, pasta, estante=None, **tetos):

        self.banco = BancoSqlite(pasta / 'fila.sqlite3')

        self.banco.preparar()

        self.lotes = FilaDeLotes(self.banco)

        self.pasta = pasta / 'lotes'

        self.pasta.mkdir()

        self.cotas = Cotas(self.lotes, estante or EstanteVazia(), self.pasta, **tetos)


    def lote(self, dono='a@ufrrj.br', status=NA_FILA, bytes=0):

        #* uma linha no estado pedido e, se bytes > 0, a pasta em disco

        lote_id = self.lotes.novo_lote(dono, 'c', 1)

        if status != RECEBENDO:
            self.lotes.liberar_para_fila(lote_id, 1)

        if status == RODANDO:  #* reivindicar pega o mais antigo, que pode nao ser este: aqui interessa so o status desta linha
            with self.banco.conectar() as con:
                con.execute('UPDATE lotes SET status=? WHERE id=?', (RODANDO, lote_id))

        elif status == PRONTO:
            self.lotes.reivindicar()
            self.lotes.concluir(lote_id)

        elif status == FALHOU:
            self.lotes.falhar(lote_id, 'erro qualquer')

        if bytes:

            entrada = self.pasta / lote_id / 'entrada'

            entrada.mkdir(parents=True)

            (entrada / 'amostra.ab1').write_bytes(b'\0' * bytes)

        return lote_id


    def situacao(self, email='a@ufrrj.br'):

        return self.cotas.situacao(email)




@pytest.fixture
def conta(tmp_path):

    return Conta(tmp_path)




def sem_espaco_no_ab1(monkeypatch, erro=None):

    #* Path.open que falha so nos .ab1: o SQLite e o resto do disco continuam funcionando

    real = Path.open

    def falha(self, *argumentos, **nomeados):

        if self.suffix == '.ab1':
            raise erro or OSError(errno.ENOSPC, 'No space left on device')

        return real(self, *argumentos, **nomeados)

    monkeypatch.setattr(Path, 'open', falha)




def teste_conta_sem_lotes_pode_enviar(conta):

    cota = conta.situacao()

    assert (cota.lotes_ativos, cota.bytes_usados, cota.bancos) == (0, 0, 0)

    assert cota.pode_enviar and cota.motivo == ''




def teste_teto_de_lotes_ativos_barra_o_envio(tmp_path):

    conta = Conta(tmp_path, max_lotes_ativos=2)

    conta.lote(status=NA_FILA)

    conta.lote(status=RODANDO)

    cota = conta.situacao()

    assert cota.lotes_ativos == 2 and not cota.pode_enviar

    assert 'espere' in cota.motivo.lower(), 'a frase e o que a pessoa le: diz o que fazer, nao um codigo'




def teste_recebendo_conta_como_ativo(tmp_path):

    #* upload em curso ocupa disco e vaga na fila tanto quanto os outros

    conta = Conta(tmp_path, max_lotes_ativos=1)

    conta.lote(status=RECEBENDO)

    assert not conta.situacao().pode_enviar




def teste_lote_pronto_ou_falhou_nao_ocupa_vaga(tmp_path):

    #* senao a conta travaria para sempre no primeiro dia de uso

    conta = Conta(tmp_path, max_lotes_ativos=2)

    conta.lote(status=PRONTO)

    conta.lote(status=FALHOU)

    cota = conta.situacao()

    assert cota.lotes_ativos == 0 and cota.pode_enviar




def teste_teto_de_bytes_barra_o_envio_e_abaixo_dele_passa(tmp_path):

    conta = Conta(tmp_path, max_bytes_conta=1000)

    conta.lote(status=PRONTO, bytes=600)

    cota = conta.situacao()

    assert cota.bytes_usados == 600 and cota.pode_enviar and cota.motivo == ''

    conta.lote(status=PRONTO, bytes=600)

    cota = conta.situacao()

    assert cota.bytes_usados == 1200 and not cota.pode_enviar and 'ocupam' in cota.motivo




def teste_zero_desliga_os_tetos(tmp_path):

    conta = Conta(tmp_path, max_lotes_ativos=0, max_bytes_conta=0)

    for _ in range(5):
        conta.lote(status=NA_FILA)

    conta.lote(status=PRONTO, bytes=5000)

    cota = conta.situacao()

    assert cota.lotes_ativos == 5 and cota.max_lotes_ativos == 0 and cota.bytes_usados == 5000 and cota.max_bytes_conta == 0

    assert cota.pode_enviar




def teste_pasta_ausente_conta_zero_e_nao_quebra(conta):

    #* lote apagado a mao no servidor e caso comum de manutencao

    conta.lote(status=PRONTO, bytes=0)

    conta.lote(status=PRONTO, bytes=800)

    assert conta.situacao().bytes_usados == 800




def teste_lotes_de_outro_dono_nao_entram_nesta_cota(tmp_path):

    conta = Conta(tmp_path, max_lotes_ativos=1, max_bytes_conta=1000)

    conta.lote(dono='b@ufrrj.br', status=NA_FILA, bytes=5000)

    cota = conta.situacao('a@ufrrj.br')

    assert (cota.lotes_ativos, cota.bytes_usados) == (0, 0) and cota.pode_enviar




def teste_tetos_vem_do_ambiente_e_caem_no_padrao_quando_ilegiveis(ambiente):

    assert cota_de_espaco.teto_lotes_ativos() == 10, 'cada par e um lote: 10, nao 3'

    assert cota_de_espaco.teto_bytes_conta() == 2 * 1024 * 1024 * 1024

    assert cota_de_espaco.teto_bancos_conta() == 12

    ambiente.setenv('EASYCONTIG_MAX_LOTES_ATIVOS', 'três')  #* .env com erro de digitacao nao pode virar "sem limite"

    ambiente.setenv('EASYCONTIG_MAX_BYTES_CONTA', '-1')

    ambiente.setenv('EASYCONTIG_MAX_BANCOS_CONTA', '3')

    assert cota_de_espaco.teto_lotes_ativos() == 10 and cota_de_espaco.teto_bytes_conta() == 2 * 1024 * 1024 * 1024

    assert cota_de_espaco.teto_bancos_conta() == 3

    assert Cotas(None, EstanteVazia(), '/nao/importa').max_bancos == 3, 'a Cotas sem teto explicito le o ambiente'




def teste_lote_apagado_some_da_conta(conta):

    #* o modo de falha que um campo no banco teria: alguem apaga a pasta no servidor e a cota continua cobrando por ela

    lote_id = conta.lote(status=PRONTO, bytes=5000)

    assert conta.situacao().bytes_usados == 5000 and lote_id in conta.cotas.medidos, 'devia ter entrado na memoria curta'

    shutil.rmtree(conta.pasta / lote_id)  #* apagado por fora, sem avisar

    assert conta.situacao().bytes_usados == 0, 'a cota cobrou por um lote que nao existe mais no disco'

    assert lote_id not in conta.cotas.medidos




def teste_lote_em_andamento_e_remedido_toda_vez(conta):

    #* recebendo/rodando estao sendo escritos agora: congelar o tamanho deixaria passar um upload que estoura

    lote_id = conta.lote(status=RECEBENDO, bytes=1000)

    primeiro = conta.situacao().bytes_usados

    assert lote_id not in conta.cotas.medidos, 'lote ativo nao pode entrar na memoria'

    (conta.pasta / lote_id / 'entrada' / 'outra.ab1').write_bytes(b'y' * 9000)  #* o upload continua e a pasta cresce

    assert conta.situacao().bytes_usados > primeiro, 'a cota nao viu o lote crescer durante o upload'




def teste_o_numero_com_memoria_e_o_mesmo_sem_memoria(conta):

    lote_id = conta.lote(status=PRONTO, bytes=7777)

    com = conta.situacao().bytes_usados

    conta.cotas.medidos.clear()

    sem = conta.situacao().bytes_usados

    assert com == sem == bytes_da_pasta(conta.pasta / lote_id) == 7777




def teste_lote_que_termina_passa_a_ser_lembrado(conta):

    #* a transicao e o momento delicado: rodando nao podia ser lembrado, e pronto tem que passar a ser

    lote_id = conta.lote(status=RODANDO, bytes=2000)

    conta.situacao()

    assert lote_id not in conta.cotas.medidos

    conta.lotes.concluir(lote_id)

    conta.situacao()

    assert lote_id in conta.cotas.medidos




def teste_o_banco_do_usuario_entra_na_cota(tmp_path, bancada):

    #* M2: sem o banco proprio a conta enxergava so metade do que ocupa

    banco_id = id_do_usuario(EMAIL, 'refs')

    pasta = bancada.cfg.pasta_de_bancos / banco_id

    pasta.mkdir(parents=True)

    (pasta / f'{banco_id}.nsq').write_bytes(b'x' * 5_000_000)  #* .nsq e o que a estante procura para dizer que esta montado

    cota = bancada.instalacao.cotas.situacao(EMAIL)

    assert cota.bytes_usados >= 5_000_000 and cota.bancos == 1

    assert bancada.instalacao.cotas.situacao(OUTRO_EMAIL).bancos == 0




def teste_a_fila_que_nao_responde_deixa_enviar(conta, monkeypatch):

    #* a cota e limite de convivencia, nao trava de seguranca: sem resposta do banco, a pagina inicial nao pode cair

    monkeypatch.setattr(conta.lotes, 'listar', lambda **_: (_ for _ in ()).throw(RuntimeError('banco travado')))

    cota = conta.situacao()

    assert cota.pode_enviar and cota.lotes_ativos == 0




@pytest.mark.parametrize('n, texto', [(0, '0 MB'), (1536 * 1024, '1,5 MB'), (300 * 1024 * 1024, '300 MB'), (2 * 1024 ** 3, '2,0 GB')])
def teste_tamanho_em_texto_de_gente(n, texto):

    assert tamanho(n) == texto




def teste_o_envio_que_nao_cabe_e_recusado_e_o_que_cabe_passa(conta):

    #* L3: um envio de 900 MB contra 100 MB de sobra passava, porque no instante da pergunta a conta ainda nao tinha estourado

    with pytest.raises(CotaDeBytesEstourada) as erro:
        conta.lotes.novo_lote('a@ufrrj.br', 'grande', 1, bytes_previstos=900_000_000, teto_bytes=1_000_000_000, usados_em_disco=900_000_000)

    assert (erro.value.usados, erro.value.reservado, erro.value.teto) == (900_000_000, 900_000_000, 1_000_000_000)

    lote_id = conta.lotes.novo_lote('a@ufrrj.br', 'ok', 1, bytes_previstos=50_000_000, teto_bytes=1_000_000_000, usados_em_disco=100_000_000)

    assert conta.lotes.pegar(lote_id).bytes_previstos == 50_000_000




def teste_a_rajada_simultanea_nao_fura_a_cota_de_bytes(conta):

    #* 10 envios ao mesmo tempo, cada um reservando 300 MB, contra 1 GB: sem a reserva transacional TODOS entravam.
    #* teto_ativos=0 de proposito: o teto de lotes ativos limitava o estrago por acidente, e aqui se mede a trava de BYTES sozinha

    entraram, recusados = [], []

    largada = threading.Barrier(10)

    def enviar():

        largada.wait(timeout=5)

        try:
            entraram.append(conta.lotes.novo_lote('a@ufrrj.br', 'r', 1, bytes_previstos=300_000_000, teto_bytes=1_000_000_000, usados_em_disco=0))

        except CotaDeBytesEstourada:
            recusados.append(1)

    fios = [threading.Thread(target=enviar) for _ in range(10)]

    for fio in fios:
        fio.start()

    for fio in fios:
        fio.join(timeout=10)

    assert len(entraram) == 3, f'{len(entraram)} envios de 300 MB entraram numa cota de 1 GB'

    assert len(recusados) == 7

    with conta.banco.conectar() as con:
        assert con.execute('SELECT COALESCE(SUM(bytes_previstos),0) FROM lotes').fetchone()[0] <= 1_000_000_000




def teste_a_reserva_morre_quando_o_lote_entra_na_fila(conta):

    #* mantida depois, a conta pagaria duas vezes pelo mesmo lote: uma pela previsao e outra pelo disco

    lote_id = conta.lotes.novo_lote('a@ufrrj.br', 'x', 2, bytes_previstos=10_000_000, teto_bytes=1_000_000_000)

    assert conta.lotes.pegar(lote_id).bytes_previstos == 10_000_000

    conta.lotes.liberar_para_fila(lote_id, 2)

    assert conta.lotes.pegar(lote_id).bytes_previstos == 0




def teste_sem_teto_de_bytes_nada_muda(conta):

    for _ in range(5):
        conta.lotes.novo_lote('a@ufrrj.br', 'x', 1, bytes_previstos=10 ** 12, teto_bytes=0)

    assert len(conta.lotes.listar(dono='a@ufrrj.br')) == 5




def teste_oito_envios_ao_mesmo_tempo_nao_furam_o_teto_de_ativos(montar, ab1):

    #* 06/08/2026: teto 1, oito envios simultaneos da mesma conta, sete entraram. a conferencia mora DENTRO da transacao que cria o lote

    bancada = montar(MAX_LOTES_ATIVOS='1', LIM_ENVIO='0')

    cliente = bancada.entrar(tolerante=True)

    arquivos = [('arquivos', (nome, ab1[nome], 'application/octet-stream')) for nome in ('amostra12_F_BTF2.ab1', 'amostra12_R_BTR2.ab1')]

    codigos = [None] * 8

    largada = threading.Barrier(8)

    def enviar(indice):

        largada.wait()

        codigos[indice] = cliente.post('/lotes', files=arquivos, data={'nome': 'par', 'referencia': 'curado'}).status_code

    fios = [threading.Thread(target=enviar, args=(indice,)) for indice in range(8)]

    for fio in fios:
        fio.start()

    for fio in fios:
        fio.join()

    criados = len(bancada.instalacao.lotes.listar(limite=0))

    assert criados == 1, f'teto de 1 e {criados} lotes criados: a cota foi furada'

    assert codigos.count(413) == 7 and codigos.count(303) == 1, f'recusas erradas: {codigos}'




def teste_a_recusa_por_cota_na_rota_diz_o_que_fazer(montar, ab1):

    bancada = montar(MAX_LOTES_ATIVOS='1')

    cliente = bancada.entrar()

    bancada.enviar_lote(cliente, [('amostra12_F_BTF2.ab1', ab1['amostra12_F_BTF2.ab1'])])

    resposta = cliente.post('/lotes', files=[('arquivos', ('amostra28_F_BTF2.ab1', ab1['amostra28_F_BTF2.ab1'], 'application/octet-stream'))],
                            data={'nome': 'outra', 'referencia': 'curado'}, headers={'accept': 'application/json'})

    assert resposta.status_code == 413 and 'espere' in resposta.json()['detail'].lower()




def teste_disco_cheio_diz_que_e_do_servidor_e_nao_da_selecao(bancada, ab1, monkeypatch):

    #* 507 e uma frase que nao manda a pessoa mexer no que esta certo

    cliente = bancada.entrar(tolerante=True)

    sem_espaco_no_ab1(monkeypatch)

    resposta = cliente.post('/lotes', files=[('arquivos', ('amostra12_F_BTF2.ab1', ab1['amostra12_F_BTF2.ab1'], 'application/octet-stream'))],
                            data={'nome': 'par', 'referencia': 'curado'}, headers={'accept': 'text/html'})

    assert resposta.status_code == 507, 'disco cheio nao pode sair como 500 anonimo'

    texto = resposta.text.lower()

    assert 'espaço em disco' in texto and 'não é a sua seleção' in texto, 'manda a pessoa corrigir o que esta certo'

    lotes = bancada.instalacao.lotes.listar(limite=0)

    assert lotes and all(lote.status == FALHOU and 'disco' in lote.erro for lote in lotes), 'o erro gravado nao diz o que houve'

    assert bancada.instalacao.lotes.reivindicar() is None, 'lote quebrado foi para o trabalhador'

    assert not any(bancada.cfg.pasta_de_lotes.iterdir()), 'sobrou pasta ocupando o volume'




def teste_erro_nao_previsto_ao_gravar_sai_com_casca_e_sem_vazar_o_caminho(bancada, ab1, monkeypatch):

    cliente = bancada.entrar(tolerante=True)

    sem_espaco_no_ab1(monkeypatch, RuntimeError('/caminho/secreto/do/servidor explodiu'))

    resposta = cliente.post('/lotes', files=[('arquivos', ('amostra12_F_BTF2.ab1', ab1['amostra12_F_BTF2.ab1'], 'application/octet-stream'))],
                            data={'nome': 'par', 'referencia': 'curado'}, headers={'accept': 'text/html'})

    assert resposta.status_code == 500

    assert 'não foi por causa do que você mandou' in resposta.text and '/caminho/secreto' not in resposta.text

    assert all(lote.status == FALHOU for lote in bancada.instalacao.lotes.listar(limite=0))




def teste_o_balde_e_por_conta_e_o_429_diz_quando_voltar():

    limitador = Limitador(regras={'leitura': Regra('leitura', 3, 60)}, proxies=set())

    pedido = RequisicaoDeMentira('203.0.113.9')

    for instante in (0, 1, 2):
        limitador.conferir('leitura', pedido, 'ana@ufrrj.br', agora=instante)

    with pytest.raises(HTTPException) as erro:
        limitador.conferir('leitura', pedido, 'ana@ufrrj.br', agora=3)

    assert erro.value.status_code == 429 and erro.value.headers['Retry-After'] == '58' and 'tente de novo' in erro.value.detail

    limitador.conferir('leitura', pedido, 'bruno@ufrrj.br', agora=3)  #* mesmo IP, outra conta: outro balde

    limitador.conferir('leitura', pedido, 'ana@ufrrj.br', agora=61)  #* o instante mais velho saiu da janela




def teste_atras_do_proxy_declarado_o_balde_e_do_ip_encaminhado():

    #* com o nginx na frente, toda conexao chega do mesmo IP: sem ler o X-Forwarded-For todo mundo dividiria um balde so

    limitador = Limitador(regras={'leitura': Regra('leitura', 2, 60)}, proxies={'10.0.0.1'})

    for instante in (0, 1):
        limitador.conferir('leitura', RequisicaoDeMentira('10.0.0.1', '203.0.113.7'), agora=instante)

    with pytest.raises(HTTPException):
        limitador.conferir('leitura', RequisicaoDeMentira('10.0.0.1', '203.0.113.7'), agora=2)

    limitador.conferir('leitura', RequisicaoDeMentira('10.0.0.1', '203.0.113.8'), agora=2)  #* outro cliente atras do mesmo proxy: outro balde

    assert sorted(limitador.registro) == [('leitura', 'ip:203.0.113.7'), ('leitura', 'ip:203.0.113.8')]




def teste_regra_desconhecida_ou_desligada_passa_direto():

    limitador = Limitador(regras={'leitura': Regra('leitura', 0, 60)}, proxies=set())

    for _ in range(50):
        limitador.conferir('leitura', RequisicaoDeMentira('1.1.1.1'), agora=0)
        limitador.conferir('inventada', RequisicaoDeMentira('1.1.1.1'), agora=0)

    assert limitador.registro == {}




def teste_a_varredura_solta_os_baldes_vencidos():

    #* sem ela o registro guardaria para sempre um balde por identidade que bateu UMA vez, e a memoria cresceria sem teto

    limitador = Limitador(regras={'leitura': Regra('leitura', 5, 60)}, proxies=set())

    for indice in range(100):
        limitador.conferir('leitura', RequisicaoDeMentira(f'10.0.0.{indice}'), agora=0)

    assert len(limitador.registro) == 100

    limitador.conferir('leitura', RequisicaoDeMentira('10.0.0.1'), agora=1000)

    assert list(limitador.registro) == [('leitura', 'ip:10.0.0.1')]




def teste_regras_e_proxies_vem_do_ambiente(ambiente):

    ambiente.setenv('EASYCONTIG_LIM_ENVIO', '7')

    ambiente.setenv('EASYCONTIG_PROXIES_CONFIAVEIS', '10.0.0.1, 10.0.0.2')

    limitador = Limitador()

    assert limitador.regras['envio'].quantas == 7 and str(limitador.regras['envio']) == '7 por 1 h'

    assert limitador.regras['leitura'].quantas == 300 and limitador.proxies == {'10.0.0.1', '10.0.0.2'}

    ambiente.setenv('EASYCONTIG_PROXIES_CONFIAVEIS', 'nenhum')

    assert proxies_confiaveis() == set(), '"nenhum" e o mesmo conjunto vazio de sempre, escrito por extenso'

    ambiente.setenv('EASYCONTIG_PROXIES_CONFIAVEIS', '  NENHUM  ')

    assert proxies_confiaveis() == set(), 'caixa e espacos nao viram um proxy chamado "NENHUM"'

    assert (duracao(3600), duracao(600), duracao(3599), duracao(5)) == ('1 h', '10 min', '60 min', '5 s')




def teste_o_que_o_cliente_escreveu_na_frente_e_ignorado():

    #* proxy ACRESCENTA ao fim: o cliente manda "1.2.3.4" e o proxy entrega "1.2.3.4, <ip real>". split(",")[0] era o texto do cliente

    assert ip_de_origem(RequisicaoDeMentira('10.0.0.1', '1.2.3.4, 203.0.113.7'), {'10.0.0.1'}) == '203.0.113.7'

    assert ip_de_origem(RequisicaoDeMentira('10.0.0.1', '203.0.113.7'), {'10.0.0.1'}) == '203.0.113.7'




def teste_dois_proxies_nossos_em_serie():

    #* balanceador + servidor web, os dois nossos: o cliente e o que sobra

    assert ip_de_origem(RequisicaoDeMentira('10.0.0.2', '203.0.113.7, 10.0.0.1'), {'10.0.0.1', '10.0.0.2'}) == '203.0.113.7'




def teste_cadeia_toda_forjada_com_nomes_dos_nossos_proxies():

    #* se o cliente encher a lista com os IPs dos nossos proxies, sobra o endereco da conexao — que ele nao escolhe

    assert ip_de_origem(RequisicaoDeMentira('10.0.0.1', '10.0.0.1, 10.0.0.1'), {'10.0.0.1'}) == '10.0.0.1'




def teste_sem_proxy_declarado_o_cabecalho_vale_nada():

    assert ip_de_origem(RequisicaoDeMentira('203.0.113.9', '1.2.3.4'), set()) == '203.0.113.9'

    assert ip_de_origem(RequisicaoDeMentira('198.51.100.7', '1.2.3.4'), {'203.0.113.9'}) == '198.51.100.7', 'so pela boca de quem foi declarado'




def teste_cliente_sem_endereco_nao_derruba():

    pedido = RequisicaoDeMentira('x')

    pedido.client = None

    assert ip_de_origem(pedido, set()) == '?'




def teste_forjar_x_forwarded_for_nao_escapa_do_teto(montar):

    #* 06/08/2026: com o teto de login em 5, 30 tentativas seguidas passaram trocando o cabecalho a cada uma

    cliente = montar(LIM_LOGIN='5').cliente()

    barrado_em = None

    for tentativa in range(1, 31):

        resposta = cliente.post('/entrar', data={'email': f'chute{tentativa}@ufrrj.br'}, headers={'x-forwarded-for': f'10.0.0.{tentativa}'})

        if resposta.status_code == 429:

            barrado_em = tentativa

            break

    assert barrado_em is not None, '30 tentativas de login passaram trocando X-Forwarded-For'

    assert barrado_em <= 6, f'barrou tarde demais (tentativa {barrado_em})'




def teste_duas_contas_no_mesmo_ip_nao_dividem_o_teto(montar):

    #* dois pesquisadores atras do mesmo NAT: o limitador roda por DENTRO do SessionMiddleware, senao conta pelo IP

    bancada = montar(LIM_LEITURA='10')

    ana = bancada.entrar('ana@ufrrj.br')

    for _ in range(10):
        assert ana.get('/saude').status_code != 429

    assert ana.get('/saude').status_code == 429, 'a Ana devia ter estourado o dela'

    bruno = bancada.entrar('bruno@ufrrj.br')

    assert bruno.get('/saude').status_code != 429, 'o Bruno foi barrado pelo consumo da Ana: o teto voltou a ser por IP'




def teste_quem_nao_entrou_continua_contado_por_ip(montar):

    #* o caminho anonimo nao tem outra identidade: o teto por IP e o comportamento certo, nao uma limitacao

    bancada = montar(LIM_LEITURA='5')

    anonimo = bancada.cliente()

    for _ in range(5):
        assert anonimo.get('/saude').status_code != 429

    assert anonimo.get('/saude').status_code == 429

    assert bancada.cliente().get('/saude').status_code == 429, 'dois anonimos do mesmo IP dividem o teto — e o que sobra sem conta'




def teste_head_gasta_o_teto_igual_ao_get(montar):

    #* o middleware dizia method == "GET": bastava trocar o verbo para bater a vontade. o servidor monta a resposta inteira num HEAD

    cliente = montar(LIM_LEITURA='5').cliente()

    for _ in range(5):
        assert cliente.head('/saude').status_code != 429

    assert cliente.head('/saude').status_code == 429, 'HEAD nao gastou o teto: existe um verbo que bate de graca'

    assert cliente.get('/saude').status_code == 429, 'GET passou depois de HEAD estourar: cada verbo ganhou balde proprio'




def teste_sessao_ilegivel_nao_derruba_a_requisicao(montar):

    #* cookie assinado com outra chave (todo reinicio sem SECRET_KEY fixa gera uma nova) cai para o IP e segue

    cliente = montar(LIM_LEITURA='10').cliente()

    cliente.cookies.set('session', 'lixo.que.nao.decodifica')

    assert cliente.get('/saude').status_code in (200, 503), 'o /saude responde 503 sem tracy, mas nunca 500 nem 429 por um cookie ruim'

    resposta = cliente.get('/')

    assert resposta.status_code == 303 and resposta.headers['location'].startswith('/entrar'), 'cookie ilegivel e o mesmo que nenhum: manda entrar'




def teste_o_limitador_roda_por_dentro_do_session(bancada):

    #* add_middleware empilha de dentro para fora: o limitador tem de aparecer DEPOIS do SessionMiddleware na lista (mais para dentro)

    nomes = [getattr((camada.kwargs or {}).get('dispatch'), '__name__', None) or camada.cls.__name__ for camada in bancada.app.user_middleware]

    assert 'SessionMiddleware' in nomes and 'limite_de_leitura' in nomes, nomes

    assert nomes.index('limite_de_leitura') > nomes.index('SessionMiddleware'), f'o limitador ficou por FORA do SessionMiddleware: {nomes}'




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
