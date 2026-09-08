"""
teste_bancos.py — os bancos de referencia: o catalogo do NCBI, o banco proprio (FASTA enviado) e a estante que os guarda.

    CATALOGO (Ficha: id, nome, grupo, marcador, termo, aprox) ---> POR_ID[id]
                                                                        |
    POST /bancos/{id}/montar --> estante.montar(id, ncbi) --> ncbi.procurar(termo) --> baixar_fasta --> montar_de_fasta
    POST /bancos/meu ---------> id_do_usuario(email, apelido) --> estante.montar_do_usuario(id, fasta) --> montar_de_fasta
                                                                                                                |
                                          pasta/<id>.novo: FASTA -> makeblastdb -> meta.json ---trocar---> pasta/<id>/<id>.nsq
    POST /bancos/{id}/remover --> meu_... so do dono; do catalogo so administrador (EASYCONTIG_ADMINS)

O QUE SE PROTEGE
  1. o catalogo e consultavel por inteiro e o termo que vai ao NCBI e o da ficha, sem rede nenhuma no teste
     (o cliente do NCBI e de mentira: registra o termo e devolve o FASTA que o teste quiser).
  2. a montagem nao deixa banco pela metade: NCBI sem resultado, acima do teto, download incompleto, makeblastdb
     falhando ou pendurado (teto de tempo) deixam o banco ANTERIOR inteiro e nao sobra pasta .novo, .velho nem trava.
  3. UMA montagem por banco entre processos (trava O_EXCL): o segundo pedido sai com BancoOcupado/409, a trava
     solta no fim e depois de excecao, e a trava orfa (processo morto) e limpa pela idade.
  4. o banco proprio e da conta: o apelido e filtrado antes de virar pasta (travessia de caminho), o id forjado nao
     pisa em banco do catalogo, outro usuario nao ve nem remove, e o teto de taxa vale ANTES de qualquer trabalho.
  5. banco compartilhado ja montado so administrador remonta ou remove; montar o que ainda nao existe e de todos.
  6. o makeblastdb roda FORA do laco do servidor (threadpool) e dentro de uma vaga pesada: sem vaga, 503 na hora.
  7. o que da errado chega a TELA com o motivo (recado de uma vez so) e nunca com caminho do servidor; a pagina nao
     imprime texto que veio na URL; o oficina.js escapa tudo que vem de fora (titulo do BLAST, nome de arquivo).

O makeblastdb e pedido so pelo teste que monta de verdade (exigir_ferramenta): sem ele, o teste e pulado, nao falha.
"""

import asyncio
import os
import threading
import time
from pathlib import Path

import pytest

import bancos_de_referencia as bancos
from bancos_de_referencia import CATALOGO, POR_ID, TRAVA_VELHA, BancoOcupado, ErroDoBanco, EstanteDeBancos, contar_sequencias, espaco_da_conta, id_do_usuario
from cliente_ncbi import ErroDoNcbi, Procura
from conftest import EMAIL, OUTRO_EMAIL, RAIZ, exigir_ferramenta
from easycontig_web.web import rotas_de_bancos


ADMIN = 'chefe@ufrrj.br'

PEQUENO = 'adeleorina_18s'  #* a menor ficha do catalogo: e a que se monta nos testes

MALICIOSOS = ['../etc/passwd', '..%2f..%2fetc', '....//etc', '/etc/passwd', '..\\..\\windows', '%2e%2e%2f', 'a/../../b', '', 'a' * 41, 'com espaço', 'açúcar']

MARCADORES_DA_PLACA = {'16S rRNA', 'groEL', 'dsb', 'sodB'}  #* os quatro genes de Anaplasmataceae que a placa do laboratorio amplifica




def fasta_de(quantas, prefixo='ref'):

    #* um FASTA com `quantas` sequencias de ids distintos (o makeblastdb -parse_seqids recusa id repetido)

    return ''.join(f'>{prefixo}{i:03} sequência {i}\n' + 'ACGTTGCAAC' * 6 + '\n' for i in range(1, quantas + 1))




class NcbiDeMentira:

    #* o cliente do NCBI sem rede: responde ao procurar() com o total pedido e ao baixar_fasta() com o FASTA pedido

    def __init__(self, total=3, baixa=None, erro=None, ao_procurar=None):

        self.total = total
        self.baixa = total if baixa is None else baixa  #* quantas sequencias vem de fato: menos que o total e download pela metade
        self.erro = erro                                 #* levantado no procurar(): o NCBI fora do ar
        self.ao_procurar = ao_procurar                   #* chamado no procurar(): o teste espia o que a estante ja fez ate ali
        self.termos = []


    def procurar(self, termo, banco='nuccore'):

        self.termos.append(termo)

        if self.ao_procurar:
            self.ao_procurar()

        if self.erro:
            raise self.erro

        return Procura(termo, self.total, 'webenv-de-mentira', 'chave')


    def baixar_fasta(self, procura, banco='nuccore', por_pagina=500):

        return fasta_de(self.baixa)




def fingir_montado(pasta_de_bancos, id, sequencias=4902):

    #* um banco "montado" sem makeblastdb: o .nsq (o que a estante confere) e o meta.json

    pasta = Path(pasta_de_bancos) / id

    pasta.mkdir(parents=True, exist_ok=True)

    (pasta / f'{id}.nsq').write_bytes(b'\0' * 1024)

    (pasta / f'{id}.meta.json').write_text(f'{{"sequencias": {sequencias}, "termo": "t", "baixado_em": "2026-08-30T10:00:00+00:00"}}', encoding='utf-8')

    return pasta




def enviar_banco(cliente, apelido='meubanco', fasta=b'>a\nACGT\n', nome='x.fasta'):

    return cliente.post('/bancos/meu', data={'apelido': apelido}, files={'fasta': (nome, fasta, 'text/plain')})




def sobras(pasta):

    #* o que uma montagem NAO pode deixar para tras: pasta de trabalho, pasta velha, trava

    return sorted(p.name for p in Path(pasta).glob('*') if p.suffix in ('.novo', '.velho', '.lock'))




@pytest.fixture
def estante(tmp_path):

    #* uma estante sem makeblastdb: lista, consulta, trava e remove; a montagem de verdade pede a fixture `oficina`

    return EstanteDeBancos(tmp_path / 'bancos')




@pytest.fixture
def oficina(tmp_path):

    #* uma estante COM makeblastdb, ou o teste e pulado

    return EstanteDeBancos(tmp_path / 'bancos', exigir_ferramenta('makeblastdb'))




def teste_todo_id_do_catalogo_e_consultavel():

    assert len(POR_ID) == len(CATALOGO), 'dois bancos com o mesmo id no catálogo'

    for ficha in CATALOGO:

        assert POR_ID[ficha.id] is ficha

        assert bancos.APELIDO_VALIDO.match(ficha.id), f'{ficha.id!r} não serve de nome de pasta'

        assert ficha.gene == ficha.marcador.split()[0]

        assert ficha.aprox > 0 and ficha.termo and ficha.grupo




def teste_anaplasmataceae_cobre_os_quatro_marcadores_da_placa():

    marcadores = {ficha.marcador for ficha in CATALOGO if ficha.nome == 'Anaplasmataceae'}

    assert MARCADORES_DA_PLACA <= marcadores, f'faltam no catálogo: {MARCADORES_DA_PLACA - marcadores}'




def teste_sodb_aponta_para_o_locus_certo():

    ficha = POR_ID['anaplasmataceae_sodb']

    assert ficha.marcador == 'sodB' and ficha.grupo == 'Bactérias'

    assert 'txid942' in ficha.termo and 'sodB' in ficha.termo  #* Anaplasmataceae, e o gene no titulo




@pytest.mark.parametrize('id, gene', [('apicomplexa_18s', '18S'), ('anaplasmataceae_groel', 'groEL'), ('cestoda_coi', 'COI'), ('trematoda_28s', '28S')])
def teste_o_gene_da_ficha_e_como_o_laboratorio_escreve(id, gene):

    assert POR_ID[id].gene == gene




def teste_id_desconhecido_nao_chega_ao_ncbi(estante):

    ncbi = NcbiDeMentira()

    with pytest.raises(ErroDoBanco, match='não está no catálogo'):
        estante.montar('nao_existe', ncbi)

    assert ncbi.termos == []




def teste_montar_consulta_o_termo_da_ficha(estante):

    #* sem rede: o NCBI de mentira cai na hora, mas ja registrou o termo que a estante mandou

    ncbi = NcbiDeMentira(erro=ErroDoNcbi('sem rede'))

    with pytest.raises(ErroDoNcbi):
        estante.montar('apicomplexa_18s', ncbi)

    assert ncbi.termos == [POR_ID['apicomplexa_18s'].termo]

    assert sobras(estante.pasta) == []




def teste_espaco_da_conta_normaliza_o_email():

    assert espaco_da_conta(' A@UFRRJ.BR ') == espaco_da_conta('a@ufrrj.br')

    assert espaco_da_conta('a@ufrrj.br') != espaco_da_conta('b@ufrrj.br')

    #* o ponto do nome e parte do e-mail: joao.silva e joaosilva sao contas diferentes, e o hash tem de separar

    assert espaco_da_conta('joao.silva@ufrrj.br') != espaco_da_conta('joaosilva@ufrrj.br')

    #* dois e-mails compridos que so diferem no fim: o corte em 16 hex nao pode aproxima-los

    assert espaco_da_conta('maristela.pesquisadora.laboratorio@ufrrj.br') != espaco_da_conta('maristela.pesquisadora.laboratorio@ufrrj.bx')

    assert len(espaco_da_conta('a@ufrrj.br')) == 16

    #* e o e-mail nunca aparece no nome da pasta

    assert 'maristela' not in espaco_da_conta('maristela@ufrrj.br') and 'ufrrj' not in id_do_usuario('maristela@ufrrj.br', 'refs')

    assert id_do_usuario('A@UFRRJ.BR', 'refs') == id_do_usuario('a@ufrrj.br', 'refs')

    assert bancos.ID_DE_USUARIO_VALIDO.match(id_do_usuario(EMAIL, 'refs_2024-b'))




@pytest.mark.parametrize('mau', MALICIOSOS)
def teste_apelido_malicioso_e_recusado(mau):

    #* id_do_usuario vira caminho em disco: o filtro e a fronteira

    with pytest.raises(ErroDoBanco, match='apelido'):
        id_do_usuario(EMAIL, mau)




def teste_contar_sequencias():

    assert contar_sequencias(fasta_de(7)) == 7

    assert contar_sequencias('ACGT\n>') == 1

    assert contar_sequencias('') == 0




def teste_estado_de_banco_nao_montado(estante):

    estado = estante.estado('apicomplexa_18s')

    assert not estado.montado and estado.sequencias == 0 and estado.bytes == 0

    assert not estante.existe('apicomplexa_18s')

    assert estante.meta('apicomplexa_18s') == {}




def teste_estado_le_o_meta_e_mede_a_pasta(estante):

    pasta = fingir_montado(estante.pasta, 'apicomplexa_18s', sequencias=42)

    estado = estante.estado('apicomplexa_18s')

    assert estado.montado and estado.sequencias == 42 and estado.baixado_em.startswith('2026-08-30')

    assert estado.bytes == sum(arquivo.stat().st_size for arquivo in pasta.iterdir())

    assert estado.apelido == ''




def teste_meta_sumido_ou_ilegivel_nao_derruba(estante):

    pasta = fingir_montado(estante.pasta, 'apicomplexa_18s')

    (pasta / 'apicomplexa_18s.meta.json').write_text('{isso não é json', encoding='utf-8')

    assert estante.meta('apicomplexa_18s') == {}

    estado = estante.estado('apicomplexa_18s')

    assert estado.montado and estado.sequencias is None  #* montado, sem saber quantas: e o que a tela mostra




def teste_apelido_do_banco_do_usuario_pode_ter_sublinhado():

    assert bancos.EstadoDoBanco(id_do_usuario(EMAIL, 'refs_2024_b')).apelido == 'refs_2024_b'

    assert bancos.EstadoDoBanco('apicomplexa_18s').apelido == ''




def teste_meus_bancos_so_os_montados_desta_conta(estante):

    conta = f'meu_{espaco_da_conta(EMAIL)}_'

    fingir_montado(estante.pasta, conta + 'b')
    fingir_montado(estante.pasta, conta + 'a')
    fingir_montado(estante.pasta, conta + 'c.novo')                  #* pasta de trabalho de uma montagem em curso: nunca banco
    fingir_montado(estante.pasta, id_do_usuario(OUTRO_EMAIL, 'a'))  #* de outra conta

    (estante.pasta / (conta + 'd')).mkdir()                          #* pasta sem .nsq: nao esta montado

    assert [estado.apelido for estado in estante.meus_bancos(EMAIL)] == ['a', 'b']

    assert estante.meus_bancos('ninguem@ufrrj.br') == []




def teste_meus_bancos_sem_pasta(tmp_path):

    assert EstanteDeBancos(tmp_path / 'nao' / 'existe').meus_bancos(EMAIL) == []




def teste_o_banco_para_a_busca_leva_o_gene_ou_o_apelido(estante):

    do_catalogo = estante.banco('anaplasmataceae_groel')

    assert do_catalogo.marcador == 'groEL' and do_catalogo.prefixo == estante.pasta / 'anaplasmataceae_groel' / 'anaplasmataceae_groel'

    assert estante.banco(id_do_usuario(EMAIL, 'refs')).marcador == 'refs'  #* e o que o relatorio chama o banco




def teste_remover(estante):

    assert estante.remover('apicomplexa_18s') is False  #* nada montado: nada a apagar

    fingir_montado(estante.pasta, 'apicomplexa_18s')

    assert estante.remover('apicomplexa_18s') is True and not (estante.pasta / 'apicomplexa_18s').exists()

    for id in ('nao_existe', '../apicomplexa_18s', 'meu_x', ''):

        with pytest.raises(ErroDoBanco):
            estante.remover(id)




def teste_duas_montagens_do_mesmo_banco_nao_correm_juntas(estante):

    with estante.trava('apicomplexa_18s'):

        with pytest.raises(BancoOcupado, match='já está sendo montado'):

            with estante.trava('apicomplexa_18s'):
                pass

        with estante.trava('piroplasmida_18s'):  #* outro banco nao espera
            pass




def teste_a_trava_e_liberada_no_fim(estante):

    with estante.trava('apicomplexa_18s'):
        assert (estante.pasta / 'apicomplexa_18s.lock').read_text() == str(os.getpid())

    with estante.trava('apicomplexa_18s'):
        pass

    assert sobras(estante.pasta) == []




def teste_a_trava_solta_mesmo_se_a_montagem_estourar(estante):

    with pytest.raises(RuntimeError):

        with estante.trava('apicomplexa_18s'):
            raise RuntimeError('NCBI caiu')

    with estante.trava('apicomplexa_18s'):
        pass




def teste_trava_orfa_e_limpa_pela_idade(estante):

    #* processo morto no meio deixa o .lock: passado TRAVA_VELHA, o proximo pedido entra; antes disso, espera

    estante.pasta.mkdir(parents=True)

    trava = estante.pasta / 'apicomplexa_18s.lock'

    trava.write_text('999999')

    with pytest.raises(BancoOcupado):

        with estante.trava('apicomplexa_18s'):
            pass

    velha = time.time() - TRAVA_VELHA - 10

    os.utime(trava, (velha, velha))

    with estante.trava('apicomplexa_18s'):
        assert trava.read_text() == str(os.getpid())




def teste_a_trava_cobre_o_download(estante):

    #* dois pedidos do mesmo banco nao baixam o NCBI duas vezes: a trava ja esta posta quando o procurar() roda

    visto = {}

    def espiar():

        visto['trava_posta'] = (estante.pasta / f'{PEQUENO}.lock').exists()

    with pytest.raises(ErroDoNcbi):
        estante.montar(PEQUENO, NcbiDeMentira(erro=ErroDoNcbi('sem rede'), ao_procurar=espiar))

    assert visto['trava_posta'] is True

    assert sobras(estante.pasta) == []




def teste_montar_do_catalogo_grava_o_banco_e_o_meta(oficina):

    estado = oficina.montar(PEQUENO, NcbiDeMentira(total=3))

    assert estado.montado and estado.sequencias == 3 and estado.termo == POR_ID[PEQUENO].termo

    assert estado.baixado_em[:4] == '2026' and estado.bytes > 0

    assert oficina.existe(PEQUENO) and oficina.estado(PEQUENO).sequencias == 3

    assert not list((oficina.pasta / PEQUENO).glob('*.fasta')), 'o FASTA solto so ocuparia espaco: o banco ja tem as sequencias'

    assert sobras(oficina.pasta) == []




def teste_ncbi_sem_resultado_orienta(oficina):

    with pytest.raises(ErroDoBanco, match='nenhuma sequência') as erro:
        oficina.montar(PEQUENO, NcbiDeMentira(total=0))

    assert POR_ID[PEQUENO].termo in str(erro.value)  #* o termo do catalogo, para conferir

    assert not oficina.existe(PEQUENO) and sobras(oficina.pasta) == []




def teste_acima_do_teto_orienta(oficina):

    with pytest.raises(ErroDoBanco) as erro:
        oficina.montar(PEQUENO, NcbiDeMentira(total=100), teto=10)

    texto = str(erro.value)

    assert '100' in texto and 'teto de 10' in texto and str(POR_ID[PEQUENO].aprox) in texto

    assert not oficina.existe(PEQUENO)




def teste_download_pela_metade_nao_monta(oficina):

    with pytest.raises(ErroDoBanco, match='menos de 90 %'):
        oficina.montar(PEQUENO, NcbiDeMentira(total=10, baixa=5))

    assert not oficina.existe(PEQUENO) and sobras(oficina.pasta) == []




def teste_remontar_troca_o_banco_sem_deixar_o_velho(oficina):

    oficina.montar(PEQUENO, NcbiDeMentira(total=2))

    assert oficina.montar(PEQUENO, NcbiDeMentira(total=5)).sequencias == 5

    assert oficina.estado(PEQUENO).sequencias == 5 and sobras(oficina.pasta) == []




def teste_makeblastdb_falhando_deixa_o_banco_anterior_inteiro(oficina):

    #* -parse_seqids recusa id repetido: e a falha real mais facil de provocar. o motivo vai na frase, o caminho do servidor nao

    oficina.montar(PEQUENO, NcbiDeMentira(total=2))

    class NcbiRepetido(NcbiDeMentira):

        def baixar_fasta(self, procura, banco='nuccore', por_pagina=500):

            return '>a\nACGT\n>a\nACGT\n'

    with pytest.raises(ErroDoBanco, match='makeblastdb falhou') as erro:
        oficina.montar(PEQUENO, NcbiRepetido(total=2))

    assert 'Duplicate' in str(erro.value) and str(oficina.pasta) not in str(erro.value)

    assert oficina.estado(PEQUENO).sequencias == 2 and sobras(oficina.pasta) == []




def teste_makeblastdb_pendurado_e_derrubado_pelo_teto(tmp_path):

    #* um makeblastdb que nunca termina seguraria a vaga pesada para sempre: o teto derruba, a frase orienta, nada sobra.
    #* o programa e de mentira (um sh que dorme), o teto e curto e o tempo medido prova que foi o teto, nao a sorte

    pendurado = tmp_path / 'makeblastdb'

    pendurado.write_text('#!/bin/sh\nexec sleep 30\n')

    pendurado.chmod(0o755)

    estante = EstanteDeBancos(tmp_path / 'bancos', pendurado, tempo_limite=0.3)

    comeco = time.monotonic()

    with pytest.raises(ErroDoBanco, match='passou de 0.3 s e foi interrompida'):
        estante.montar_do_usuario(id_do_usuario(EMAIL, 'refs'), fasta_de(1))

    assert time.monotonic() - comeco < 5

    assert sobras(estante.pasta) == [] and estante.meus_bancos(EMAIL) == []




def teste_sem_makeblastdb_a_estante_orienta_e_nao_deixa_rastro(estante):

    with pytest.raises(ErroDoBanco, match='EASYCONTIG_BLAST_BIN'):
        estante.montar_do_usuario(id_do_usuario(EMAIL, 'refs'), fasta_de(1))

    assert sobras(estante.pasta) == [] and estante.meus_bancos(EMAIL) == []




def teste_fasta_sem_cabecalho_e_recusado_antes_de_qualquer_trabalho(estante):

    with pytest.raises(ErroDoBanco, match='não parece um FASTA'):
        estante.montar_do_usuario(id_do_usuario(EMAIL, 'refs'), 'ACGTACGT\n')

    assert sobras(estante.pasta) == []




@pytest.mark.parametrize('id', ['apicomplexa_18s', 'meu_', 'meu_x_../y', '', 'meu_' + 'a' * 30 + '_refs'])
def teste_id_forjado_nao_vira_banco_de_usuario(estante, id):

    #* um FASTA enviado nunca pisa num banco do catalogo nem sai da forma meu_<conta>_<apelido>

    with pytest.raises(ErroDoBanco, match='inválido'):
        estante.montar_do_usuario(id, fasta_de(1))




def teste_montar_do_usuario_guarda_o_apelido_e_a_conta(oficina):

    id = id_do_usuario(EMAIL, 'refs_2024')

    estado = oficina.montar_do_usuario(id, fasta_de(4))

    assert estado.montado and estado.sequencias == 4 and estado.apelido == 'refs_2024' and estado.termo == 'enviado pelo usuário'

    assert [e.id for e in oficina.meus_bancos(EMAIL)] == [id]

    assert oficina.meus_bancos(OUTRO_EMAIL) == []

    assert oficina.banco(id).marcador == 'refs_2024' and oficina.banco(id).instalado




def teste_cabecalho_de_fasta_hostil_nao_quebra_a_montagem(oficina):

    #* o payload pode entrar no banco; o que nao pode e virar HTML depois (isso e do oficina.js, abaixo)

    estado = oficina.montar_do_usuario(id_do_usuario(EMAIL, 'hostil'), '>REF001 <img src=x onerror=alert(1)>\n' + 'ACGT' * 30 + '\n')

    assert estado.montado and estado.sequencias == 1




def teste_o_titulo_do_banco_e_so_o_nome_do_arquivo(oficina):

    #* cwd na pasta + nomes relativos: o titulo gravado no banco nao leva o caminho do servidor nem o '.novo'

    oficina.montar_do_usuario(id_do_usuario(EMAIL, 'refs'), fasta_de(1))

    pasta = oficina.pasta / id_do_usuario(EMAIL, 'refs')

    conteudo = b''.join(arquivo.read_bytes() for arquivo in pasta.glob('*.n*'))

    assert str(oficina.pasta).encode() not in conteudo and b'.novo' not in conteudo




def teste_bancos_exige_sessao(bancada):

    assert bancada.cliente().get('/bancos', headers={'accept': 'application/json'}).status_code == 401

    assert bancada.cliente().post('/bancos/apicomplexa_18s/montar').status_code in (401, 403)




def teste_a_pagina_lista_o_catalogo_por_grupo(bancada):

    html = bancada.entrar().get('/bancos').text

    for ficha in CATALOGO:
        assert f'action="/bancos/{ficha.id}/montar"' in html

    for grupo in ('Protozoários', 'Bactérias', 'Helmintos', 'Hospedeiro'):
        assert grupo in html

    assert 'Banco montado.' not in html and 'remover' not in html.split('Meus bancos')[0]




def teste_a_pagina_mostra_o_estado_do_banco_montado(bancada):

    fingir_montado(bancada.cfg.pasta_de_bancos, 'apicomplexa_18s', sequencias=4902)

    html = bancada.entrar().get('/bancos').text

    assert '4902 seqs' in html and 'action="/bancos/apicomplexa_18s/remover"' in html

    assert 'action="/bancos/apicomplexa_18s/montar"' not in html  #* montado: o botao e remover, nao montar




def teste_montar_do_catalogo_pela_rota(bancada):

    exigir_ferramenta('makeblastdb')

    bancada.instalacao.ncbi = NcbiDeMentira(total=2)

    cliente = bancada.entrar()

    resposta = cliente.post(f'/bancos/{PEQUENO}/montar')

    assert resposta.status_code == 303 and resposta.headers['location'] == f'/bancos?feito={PEQUENO}'

    assert 'Banco montado.' in cliente.get(resposta.headers['location']).text

    assert bancada.instalacao.estante.estado(PEQUENO).sequencias == 2




def teste_banco_do_catalogo_inventado_e_recusado(bancada):

    cliente = bancada.entrar()

    assert cliente.post('/bancos/../../etc/montar').status_code in (404, 405)

    assert cliente.post('/bancos/nao_existe/montar').status_code == 404

    assert cliente.post('/bancos/nao_existe/remover').status_code == 404




def teste_conta_comum_nao_remonta_banco_compartilhado_ja_montado(bancada):

    bancada.instalacao.ncbi = NcbiDeMentira()  #* se a autorizacao falhar, o teste nao pode ir ao NCBI de verdade

    fingir_montado(bancada.cfg.pasta_de_bancos, 'apicomplexa_18s')

    resposta = bancada.entrar().post('/bancos/apicomplexa_18s/montar')

    assert resposta.status_code == 403 and 'administra' in resposta.json()['detail']




def teste_administrador_pode_remontar_e_o_erro_do_ncbi_chega_a_tela(montar):

    #* passa da autorizacao; o que vem depois e a montagem de verdade, que aqui cai no NCBI de mentira — e o motivo vai para a tela

    bancada = montar(ADMINS=f'Outro@ufrrj.br, {ADMIN.upper()}')

    fingir_montado(bancada.cfg.pasta_de_bancos, 'apicomplexa_18s')

    bancada.instalacao.ncbi = NcbiDeMentira(erro=ErroDoNcbi('o NCBI não respondeu em 60 s; tente de novo mais tarde'))

    cliente = bancada.entrar(ADMIN)

    resposta = cliente.post('/bancos/apicomplexa_18s/montar')

    assert resposta.status_code == 303 and resposta.headers['location'] == '/bancos'

    assert 'o NCBI não respondeu em 60 s' in cliente.get('/bancos').text




def teste_montar_o_que_ainda_nao_existe_continua_liberado(bancada):

    #* criar e aditivo e e o que a pagina existe para oferecer; so sobrescrever virou acao de administrador

    bancada.instalacao.ncbi = NcbiDeMentira(erro=ErroDoNcbi('sem rede'))

    assert bancada.entrar('estagiario@ufrrj.br').post('/bancos/apicomplexa_18s/montar').status_code == 303




def teste_montagem_em_curso_recusa_com_409(bancada):

    bancada.instalacao.ncbi = NcbiDeMentira()

    cliente = bancada.entrar()

    with bancada.instalacao.estante.trava('apicomplexa_18s'):

        resposta = cliente.post('/bancos/apicomplexa_18s/montar')

        assert resposta.status_code == 409 and 'já está sendo montado' in resposta.json()['detail']

    assert bancada.instalacao.ncbi.termos == [], 'com a trava posta o NCBI nem foi consultado'




def teste_o_motivo_da_falha_aparece_uma_vez_e_some(bancada):

    bancada.instalacao.ncbi = NcbiDeMentira(total=0)

    cliente = bancada.entrar()

    assert cliente.post(f'/bancos/{PEQUENO}/montar').status_code == 303

    primeira = cliente.get('/bancos').text

    assert 'nenhuma sequência' in primeira and POR_ID[PEQUENO].termo in primeira

    assert 'nenhuma sequência' not in cliente.get('/bancos').text, 'o recado grudou na tela'




def teste_erro_nao_previsto_vira_o_generico_e_vai_ao_log(bancada, caplog):

    bancada.instalacao.ncbi = NcbiDeMentira(erro=ZeroDivisionError('bug interno'))

    cliente = bancada.entrar()

    assert cliente.post(f'/bancos/{PEQUENO}/montar').status_code == 303

    html = cliente.get('/bancos').text

    assert 'não foi possível montar o banco' in html and 'bug interno' not in html

    assert 'bug interno' in caplog.text




def teste_remover_banco_compartilhado_so_administrador(montar):

    bancada = montar(ADMINS=ADMIN)

    pasta = fingir_montado(bancada.cfg.pasta_de_bancos, 'apicomplexa_18s')

    resposta = bancada.entrar().post('/bancos/apicomplexa_18s/remover')

    assert resposta.status_code == 403 and 'administra' in resposta.json()['detail'] and pasta.exists()

    assert bancada.entrar(ADMIN).post('/bancos/apicomplexa_18s/remover').status_code == 303 and not pasta.exists()




def teste_sem_administrador_declarado_ninguem_remove_o_compartilhado(bancada):

    fingir_montado(bancada.cfg.pasta_de_bancos, 'apicomplexa_18s')

    assert bancada.entrar().post('/bancos/apicomplexa_18s/remover').status_code == 403




def teste_banco_do_usuario_nao_e_visivel_nem_removivel_por_outro(bancada):

    id = id_do_usuario(EMAIL, 'refs-do-a')

    pasta = fingir_montado(bancada.cfg.pasta_de_bancos, id)

    dono, outro = bancada.entrar(EMAIL), bancada.entrar(OUTRO_EMAIL)

    assert 'refs-do-a' in dono.get('/bancos').text and 'refs-do-a' not in outro.get('/bancos').text

    assert outro.post(f'/bancos/{id}/remover').status_code == 404 and pasta.exists()

    assert dono.post(f'/bancos/{id}/remover').status_code == 303 and not pasta.exists()




def teste_enviar_banco_proprio_de_ponta_a_ponta(bancada):

    exigir_ferramenta('makeblastdb')

    cliente = bancada.entrar()

    resposta = enviar_banco(cliente, 'refs-do-a', fasta_de(3).encode())

    assert resposta.status_code == 303 and resposta.headers['location'] == '/bancos?feito=meu'

    html = cliente.get('/bancos?feito=meu').text

    assert 'Banco montado.' in html and 'refs-do-a' in html and '3 seqs' in html

    assert [e.apelido for e in bancada.instalacao.estante.meus_bancos(EMAIL)] == ['refs-do-a']




@pytest.mark.parametrize('apelido, fasta, motivo', [('../x', b'>a\nACGT\n', 'apelido inválido'),
                                                    ('refs', b'ACGT\n', 'não parece um FASTA')])
def teste_envio_ruim_orienta_na_tela(bancada, apelido, fasta, motivo):

    #* nenhum dos dois chega ao makeblastdb: o apelido e filtrado antes, e o FASTA sem '>' tambem

    cliente = bancada.entrar()

    resposta = enviar_banco(cliente, apelido, fasta)

    assert resposta.status_code == 303 and resposta.headers['location'] == '/bancos'

    assert motivo in cliente.get('/bancos').text




def teste_sem_makeblastdb_a_rota_diz_onde_apontar(bancada):

    bancada.instalacao.estante.makeblastdb = None

    cliente = bancada.entrar()

    assert enviar_banco(cliente).status_code == 303

    assert 'EASYCONTIG_BLAST_BIN' in cliente.get('/bancos').text




def teste_o_fasta_e_medido_em_bytes(bancada, monkeypatch):

    #* 1 MB + 1 byte de cabecalho com acento: em CARACTERES cabe no teto (cada c-cedilha sao 2 bytes), em bytes nao — e bytes e o que ocupa disco

    monkeypatch.setattr(rotas_de_bancos, 'TETO_DO_FASTA', 1024 * 1024)

    corpo = ('>' + 'ç' * (512 * 1024) + '\nACGT\n').encode('utf-8')

    assert len(corpo) > 1024 * 1024 >= len(corpo.decode('utf-8'))

    cliente = bancada.entrar()

    assert enviar_banco(cliente, 'grande', corpo).status_code == 303

    assert 'passa de 1 MB' in cliente.get('/bancos').text

    assert bancada.instalacao.estante.meus_bancos(EMAIL) == []




def teste_montagem_do_usuario_em_curso_recusa_com_409(bancada):

    cliente = bancada.entrar()

    with bancada.instalacao.estante.trava(id_do_usuario(EMAIL, 'refs')):

        resposta = enviar_banco(cliente, 'refs')

    assert resposta.status_code == 409 and 'já está sendo montado' in resposta.json()['detail']




def teste_enviar_banco_proprio_respeita_o_teto_de_taxa(bancada):

    #* a unica rota que escrevia em disco sem teto (achado de 06/08/2026). o teto vale ANTES do trabalho: nao depende do makeblastdb

    bancada.instalacao.estante.makeblastdb = None

    cliente = bancada.entrar()

    codigos = [enviar_banco(cliente, f'b{i}').status_code for i in range(14)]

    assert 429 in codigos, f'14 envios seguidos e nenhuma recusa: {sorted(set(codigos))}'

    assert codigos.count(303) <= 10  #* EASYCONTIG_LIM_BANCO padrao: 10 por hora




def teste_o_teto_de_bancos_da_conta_orienta(montar):

    bancada = montar(MAX_BANCOS_CONTA='1')

    fingir_montado(bancada.cfg.pasta_de_bancos, id_do_usuario(EMAIL, 'primeiro'))

    cliente = bancada.entrar()

    resposta = enviar_banco(cliente, 'segundo')

    assert resposta.status_code == 303 and resposta.headers['location'] == '/bancos'

    assert 'remova um antes de montar outro' in cliente.get('/bancos').text

    assert [e.apelido for e in bancada.instalacao.estante.meus_bancos(EMAIL)] == ['primeiro']




def teste_montar_banco_do_usuario_nao_roda_no_laco_do_servidor(bancada, monkeypatch):

    #* enviar_banco e async: o makeblastdb rodando ali seguraria o site inteiro por ate 600 s. a prova e o laco visto de DENTRO:
    #* numa thread do threadpool nao ha laco rodando (RuntimeError); no laco, get_running_loop devolve o laco

    visto = {}

    def montar_de_mentira(id, fasta_texto):

        try:
            asyncio.get_running_loop()

        except RuntimeError:
            visto['no_laco'] = False

        else:
            visto['no_laco'] = True

        visto['thread'] = threading.current_thread().name

    monkeypatch.setattr(bancada.instalacao.estante, 'montar_do_usuario', montar_de_mentira)

    assert enviar_banco(bancada.entrar()).status_code == 303

    assert visto and visto['no_laco'] is False, f'o makeblastdb rodou no laço do servidor (thread {visto.get("thread")})'




def teste_sem_vaga_pesada_o_envio_recusa_em_vez_de_esperar(bancada, monkeypatch):

    #* o threadpool e de toda rota `def` do app: sem o semaforo, montagens simultaneas o ocupam inteiro.
    #* e o 503 tem de ser 503: o `except Exception` da rota nao pode engoli-lo e devolver um 303 com texto

    monkeypatch.setattr(bancada.instalacao.estante, 'montar_do_usuario', lambda id, fasta_texto: None)

    vagas = bancada.instalacao.vagas

    for _ in range(bancada.instalacao.max_pesadas):
        assert vagas.acquire(blocking=False)

    try:
        resposta = enviar_banco(bancada.entrar())

    finally:

        for _ in range(bancada.instalacao.max_pesadas):
            vagas.release()

    assert resposta.status_code == 503 and resposta.headers.get('Retry-After') == '5'

    assert enviar_banco(bancada.entrar()).status_code == 303, 'vaga solta: o envio volta a passar'




def teste_a_pagina_nao_imprime_texto_que_veio_na_url(bancada):

    #* o padrao antigo era /bancos?erro=<texto> e {{ erro }} direto do parametro: a frase de qualquer link saia na caixa de erro do site

    isca = 'sua sessao expirou, reenvie suas credenciais em exemplo-malicioso'

    cliente = bancada.entrar()

    assert isca not in cliente.get('/bancos?erro=' + isca.replace(' ', '+')).text

    html = cliente.get('/bancos?feito=%3Cb%3Eisca%3C/b%3E').text

    assert 'Banco montado.' in html and 'isca' not in html  #* feito so liga a frase fixa; o valor nao e impresso




def teste_o_oficina_escapa_o_que_vem_de_fora():

    #* XSS armazenado provado em 06/08/2026: cabecalho de FASTA -> stitle do BLAST -> titulo do acerto -> innerHTML.
    #* a regra fica guardada no fonte: todo dado externo passa por escapar() antes de virar HTML

    js = (RAIZ / 'easycontig_web' / 'estatico' / 'oficina.js').read_text(encoding='utf-8')

    assert 'function escapar(' in js, 'a funcao de escape sumiu'

    for cru in ('${acerto.titulo}', '${acerto.acesso}', '${l.nome}', '${l.primer}', '${l.q_rotulo}', '+ motivo +'):
        assert cru not in js, f'{cru} entra em innerHTML sem escapar'

    for esperado in ('${escapar(acerto.titulo)}', '${escapar(acerto.acesso)}', '${escapar(l.nome)}', 'escapar(motivo)'):
        assert esperado in js, f'{esperado} não está escapado'




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
