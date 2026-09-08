"""
teste_pedidos.py — um laboratorio pede uma amostra a outro pelo /labs, e o outro aceita ou recusa: um ciclo so, e acaba.

    POST /labs/{chave}/pedido --> perfis.email_da_chave(chave) --> pedidos.criar(de, para, itens, outro, motivo)
           NaoPode --> recado na sessao + 303 /labs (a pessoa le o motivo na propria pagina)
           ok      --> 303 /pedidos?enviado=1
    PENDENTE --responder(id, quem_recebeu, aceitar, resposta)--> ACEITO / RECUSADO
       '-----cancelar(id, quem_pediu)-------------------------> CANCELADO
    GET /pedidos --> recebidos(eu) + enviados(eu); base.html --> contar_pendentes(eu) na lateral de toda pagina

O QUE SE PROTEGE
  1. A FRONTEIRA DO E-MAIL: pendente e recusado nao mostram endereco nenhum; o ACEITE, e so ele, libera os dois lados —
     e so naquele pedido, nao no site (achado L1 de 08/08/2026, agora com porta declarada e consentimento explicito).
  2. o alvo do formulario e a chave sorteada: nem o e-mail (remontavel pelo dominio anunciado) nem o sha256 dele
     (e-mail institucional e adivinhavel; hash de valor de baixa entropia nao esconde o valor).
  3. o ciclo e final e e de quem e: respondido nao se responde de novo; ninguem responde pedido que nao e seu.
  4. `itens` e filtrado contra o portfolio DECLARADO do alvo, nao gravado como veio; justificativa obrigatoria dos dois lados.
  5. o teto de pendentes por par conta o que esta VIVO na caixa de quem recebe — o teto de taxa conta por hora e esquece.

O local-part NAO e o nome de exibicao de proposito: com `maristela@...` e o cartao dizendo "Maristela Peckle", procurar
"maristela" no HTML acusaria vazamento onde ha so o nome declarado. Com `m.peckle@...`, "m.peckle" SO pode ter vindo do e-mail.
"""

import hashlib

import pytest

from easycontig_web.dados import pedidos_entre_labs
from easycontig_web.dados.pedidos_entre_labs import ACEITO, CANCELADO, MAX_ITENS, MAX_PENDENTES_POR_PAR, PENDENTE, RECUSADO, NaoPode, lista


GUSTAVO = 'g.freitas@ufrrj.br'

MARISTELA = 'm.peckle@ufrrj.br'

LOCAL_GUSTAVO, LOCAL_MARISTELA = 'g.freitas', 'm.peckle'

MOTIVO = 'Preciso de controle positivo para padronizar o 16S.'




class Balcao:

    #* a bancada com o Gustavo dentro e a Maristela com portfolio declarado: o cenario de todo pedido

    def __init__(self, bancada):

        self.bancada = bancada

        self.perfis = bancada.instalacao.perfis

        self.pedidos = bancada.instalacao.pedidos

        self.gustavo = bancada.entrar(GUSTAVO)

        self.perfis.salvar(MARISTELA, nome='Maristela Peckle', laboratorio='LHV', instituicao='UFRRJ', especies='Anaplasma platys, Ehrlichia canis', marcadores='16S rRNA')


    def chave(self, email):

        return self.perfis.pegar(email).chave


    def outra(self, email=MARISTELA):

        return self.bancada.entrar(email)


    def pedir(self, chave=None, cliente=None, **campos):

        #* POST do formulario do /labs; por padrao o Gustavo pede a Maristela com um motivo valido

        dados = {'motivo': MOTIVO, **campos}

        if chave is None:
            chave = self.chave(MARISTELA)

        return (cliente or self.gustavo).post(f'/labs/{chave}/pedido', data=dados)


    def recebido(self, email=MARISTELA):

        return self.pedidos.recebidos(email)[0]


    def responder(self, pedido_id, aceitar, resposta, quem=MARISTELA):

        return self.pedidos.responder(pedido_id, quem, aceitar=aceitar, resposta=resposta)


    def caixa(self, cliente):

        #* a area de trabalho do /pedidos, sem a lateral (que mostra o nome de quem esta logado, de proposito)

        return cliente.get('/pedidos').text.split('class="trabalho"', 1)[1]


    def labs(self, cliente=None):

        return (cliente or self.gustavo).get('/labs').text.split('class="trabalho"', 1)[1]




@pytest.fixture
def balcao(bancada):

    return Balcao(bancada)




def teste_pedidos_e_pedir_exigem_sessao(balcao):

    anonimo = balcao.bancada.cliente()

    assert anonimo.get('/pedidos', headers={'accept': 'application/json'}).status_code == 401

    assert anonimo.post(f'/labs/{balcao.chave(MARISTELA)}/pedido', data={'motivo': 'oi'}, headers={'accept': 'application/json'}).status_code == 401




def teste_o_formulario_do_labs_nao_leva_email_nem_hash_do_email(balcao):

    #* o botao precisa dizer PARA QUEM e o pedido, e esse identificador vai para o HTML de uma pagina que todo mundo abre

    corpo = balcao.labs()

    assert LOCAL_MARISTELA not in corpo.lower(), 'o e-mail do alvo foi para o HTML'

    for candidato in (MARISTELA, MARISTELA.upper()):
        assert hashlib.sha256(candidato.encode()).hexdigest()[:16] not in corpo, 'o alvo do pedido e o hash do e-mail'

    assert f'action="/labs/{balcao.chave(MARISTELA)}/pedido"' in corpo, 'nao ha botao de pedir'




def teste_o_cartao_do_proprio_perfil_nao_tem_botao_de_pedir(balcao):

    assert f'action="/labs/{balcao.chave(GUSTAVO)}/pedido"' not in balcao.labs()




def teste_a_chave_nao_muda_a_cada_login(balcao):

    #* girar a chave faria falhar, sem motivo visivel, um formulario ja aberto numa aba

    antes = balcao.chave(GUSTAVO)

    assert antes

    balcao.bancada.entrar(GUSTAVO)

    assert balcao.chave(GUSTAVO) == antes




def teste_chave_desconhecida_nao_vira_oraculo(balcao):

    #* 404 responderia "esta chave nao e de ninguem", e a rota viraria um modo de varrer o espaco de chaves.
    #* o texto do erro viaja na sessao assinada (saiu da URL em 11/08/2026: era falsificavel por link)

    resposta = balcao.pedir('chaveQueNaoExiste', outro='qualquer coisa')

    assert resposta.status_code == 303 and resposta.headers['location'] == '/labs'

    assert 'não foi possível enviar o pedido' in balcao.gustavo.get('/labs').text




def teste_chave_vazia_nao_casa_com_quem_ainda_nao_tem(balcao):

    #* a coluna nasce '' e e preenchida depois: linha com chave='' e estado normal, e um POST com o campo em branco
    #* escolheria a primeira dessas linhas como alvo

    with balcao.bancada.instalacao.banco.conectar() as con:
        con.execute("INSERT INTO perfis (email, nome, chave) VALUES (?,?,'')", ('fantasma@ufrrj.br', 'Fantasma'))

    assert balcao.perfis.email_da_chave('') == ''

    resposta = balcao.pedir('', outro='x')

    assert resposta.headers.get('location') != '/pedidos?enviado=1' and balcao.pedidos.recebidos('fantasma@ufrrj.br') == []




def teste_pendente_nao_mostra_email_de_nenhum_dos_lados(balcao):

    balcao.pedir(itens=['Anaplasma platys'])

    minha = balcao.caixa(balcao.gustavo)

    assert LOCAL_MARISTELA not in minha.lower() and 'Maristela Peckle' in minha, 'devia mostrar o nome declarado, nao o e-mail'

    assert LOCAL_GUSTAVO not in balcao.caixa(balcao.outra()).lower()




def teste_recusado_continua_sem_mostrar_email(balcao):

    balcao.pedir(itens=['Anaplasma platys'])

    maristela = balcao.outra()

    resposta = maristela.post(f'/pedidos/{balcao.recebido().id}/responder', data={'acao': 'recusar', 'resposta': 'Só temos DNA extraído.'})

    assert resposta.status_code == 303 and resposta.headers['location'] == '/pedidos'

    minha = balcao.caixa(balcao.gustavo)

    assert 'Só temos DNA extraído.' in minha, 'a justificativa nao chegou'

    assert LOCAL_MARISTELA not in minha.lower() and LOCAL_GUSTAVO not in balcao.caixa(maristela).lower(), 'recusar liberou o e-mail'




def teste_o_aceite_libera_o_email_dos_dois_lados(balcao):

    #* a unica travessia de e-mail entre contas neste site, e ela e deliberada: aceitar E o consentimento.
    #* sem isto o recurso nao termina — a amostra viaja de correio, e alguem tem de saber para quem escrever

    balcao.pedir(itens=['Anaplasma platys'])

    maristela = balcao.outra()

    resposta = maristela.post(f'/pedidos/{balcao.recebido().id}/responder', data={'acao': 'aceitar', 'resposta': 'Temos alíquota de 2021.'})

    assert resposta.status_code == 303

    assert MARISTELA in balcao.caixa(balcao.gustavo), 'quem pediu nao ve o contato'

    assert GUSTAVO in balcao.caixa(maristela), 'quem aceitou nao ve o contato'




def teste_o_email_liberado_nao_vaza_para_o_labs_nem_para_terceiro(balcao):

    #* aceitar abre o endereco NAQUELE pedido, e nao no site

    balcao.pedir(itens=['Anaplasma platys'])

    pedido_id = balcao.recebido().id

    balcao.responder(pedido_id, True, 'Pode vir buscar.')

    assert LOCAL_MARISTELA not in balcao.labs().lower(), 'o aceite vazou o e-mail para o diretorio'

    terceiro = balcao.outra('zulmira@ufrrj.br')

    caixa = terceiro.get('/pedidos').text

    assert GUSTAVO not in caixa and MARISTELA not in caixa, 'uma conta de fora do pedido viu os enderecos'

    assert balcao.pedidos.pegar(pedido_id, 'zulmira@ufrrj.br') is None

    assert terceiro.post(f'/pedidos/{pedido_id}/responder', data={'acao': 'recusar', 'resposta': 'nao e meu'}, headers={'accept': 'application/json'}).status_code == 404




def teste_quem_nao_recebeu_nao_responde(balcao):

    balcao.pedir(itens=['Anaplasma platys'])

    pedido_id = balcao.recebido().id

    resposta = balcao.gustavo.post(f'/pedidos/{pedido_id}/responder', data={'acao': 'aceitar', 'resposta': 'eu aceito o meu'}, headers={'accept': 'application/json'})

    assert resposta.status_code == 404, 'o proprio autor do pedido aceitou o pedido dele'

    assert balcao.pedidos.pegar(pedido_id, GUSTAVO).estado == PENDENTE




def teste_respondido_nao_se_responde_de_novo(balcao):

    #* a condicao estado=pendente esta no UPDATE, nao num SELECT antes: dois cliques (ou duas abas) nao respondem duas vezes

    balcao.pedir(itens=['Anaplasma platys'])

    pedido_id = balcao.recebido().id

    primeiro = balcao.responder(pedido_id, False, 'não temos')

    assert primeiro is not None and primeiro.estado == RECUSADO and primeiro.recebi and primeiro.contato == ''

    assert balcao.responder(pedido_id, True, 'mudei de ideia') is None

    assert balcao.pedidos.pegar(pedido_id, MARISTELA).estado == RECUSADO




def teste_acao_desconhecida_na_rota_nao_muda_nada(balcao):

    balcao.pedir(itens=['Anaplasma platys'])

    pedido_id = balcao.recebido().id

    maristela = balcao.outra()

    assert maristela.post(f'/pedidos/{pedido_id}/responder', data={'acao': 'talvez', 'resposta': 'hm'}, headers={'accept': 'application/json'}).status_code in (400, 404, 422)

    assert balcao.pedidos.pegar(pedido_id, MARISTELA).estado == PENDENTE




def teste_cancelar_e_so_de_quem_pediu_e_so_enquanto_pendente(balcao):

    balcao.pedir(itens=['Anaplasma platys'])

    pedido_id = balcao.recebido().id

    assert balcao.pedidos.cancelar(pedido_id, MARISTELA) is False, 'quem recebeu cancelou'

    assert balcao.gustavo.post(f'/pedidos/{pedido_id}/cancelar').headers['location'] == '/pedidos'

    assert balcao.pedidos.cancelar(pedido_id, GUSTAVO) is False, 'cancelou duas vezes'

    assert balcao.pedidos.recebidos(MARISTELA) == [], 'quem pediu desistiu antes de ela olhar: some da caixa'

    assert balcao.pedidos.enviados(GUSTAVO)[0].estado == CANCELADO and not balcao.pedidos.enviados(GUSTAVO)[0].recebi




def teste_pedir_de_novo_e_permitido_depois_da_recusa(balcao):

    #* estado final e final, mas um pedido novo sobre o mesmo assunto e outro pedido: recusa nao e banimento

    balcao.pedir(itens=['Anaplasma platys'])

    balcao.responder(balcao.recebido().id, False, 'agora não')

    resposta = balcao.pedir(itens=['Anaplasma platys'], motivo='Voltei porque conseguimos a autorização do SisGen.')

    assert resposta.headers['location'] == '/pedidos?enviado=1'

    assert len(balcao.pedidos.enviados(GUSTAVO)) == 2




def teste_itens_e_filtrado_contra_o_portfolio_declarado(balcao):

    #* as caixas saem do portfolio que o servidor desenhou, mas o formulario e do navegador e um POST a mao manda o que quiser

    balcao.pedir(itens=['Anaplasma platys', 'Trypanosoma cruzi <script>', '16S rRNA', 'Anaplasma platys'])

    assert balcao.recebido().itens == ['Anaplasma platys', '16S rRNA']




def teste_ha_teto_de_itens_por_pedido(balcao):

    muitas = [f'Especie {indice}' for indice in range(MAX_ITENS + 5)]

    balcao.perfis.salvar(MARISTELA, nome='Maristela Peckle', especies='\n'.join(muitas))

    balcao.pedir(itens=muitas)

    assert len(balcao.recebido().itens) == MAX_ITENS




def teste_pedido_sem_motivo_nao_entra(balcao):

    resposta = balcao.pedir(itens=['Anaplasma platys'], motivo='   ')

    assert resposta.status_code == 303 and resposta.headers['location'] == '/labs'

    assert 'diga para que serve a amostra' in balcao.gustavo.get('/labs').text

    assert balcao.pedidos.recebidos(MARISTELA) == []




def teste_pedido_sem_item_nem_texto_livre_nao_entra(balcao):

    balcao.pedir()

    assert 'marque ao menos um item' in balcao.gustavo.get('/labs').text

    assert balcao.pedidos.recebidos(MARISTELA) == []




def teste_resposta_sem_justificativa_nao_passa(balcao):

    #* "recusar sem dizer por que" e o comportamento que faria o recurso morrer de desuso. e o recado tem de aparecer
    #* NA pagina de pedidos: no original ele era gravado e nunca lido ali — a recusa sumia calada e o texto brotava no /labs

    balcao.pedir(itens=['Anaplasma platys'])

    pedido_id = balcao.recebido().id

    with pytest.raises(NaoPode):
        balcao.responder(pedido_id, False, '  ')

    assert balcao.pedidos.pegar(pedido_id, MARISTELA).estado == PENDENTE

    maristela = balcao.outra()

    resposta = maristela.post(f'/pedidos/{pedido_id}/responder', data={'acao': 'recusar', 'resposta': ''})

    assert resposta.headers['location'] == '/pedidos' and 'quem pediu precisa saber' in maristela.get('/pedidos').text

    assert 'quem pediu precisa saber' not in maristela.get('/labs').text, 'o recado sobrou para a pagina errada'




def teste_nao_da_para_pedir_a_si_mesmo(balcao):

    balcao.pedir(balcao.chave(GUSTAVO), outro='minha própria amostra')

    assert 'um pedido é para outro laboratório' in balcao.gustavo.get('/labs').text

    assert balcao.pedidos.recebidos(GUSTAVO) == []




def teste_teto_de_pendentes_com_o_mesmo_lab(balcao):

    #* sem ele, 30 pedidos por hora, hora apos hora, entopem a caixa de um lab so, e quem recebe nao tem como fazer parar

    for indice in range(MAX_PENDENTES_POR_PAR):
        assert balcao.pedir(outro=f'amostra {indice}').headers['location'] == '/pedidos?enviado=1'

    assert balcao.pedir(outro='mais uma').headers['location'] == '/labs'

    assert 'aguarde a resposta antes de mandar outro' in balcao.gustavo.get('/labs').text

    assert len(balcao.pedidos.recebidos(MARISTELA)) == MAX_PENDENTES_POR_PAR

    balcao.responder(balcao.recebido().id, False, 'não')  #* responder libera a vaga

    assert balcao.pedir(outro='agora vai').headers['location'] == '/pedidos?enviado=1'




def teste_o_teto_de_pendentes_segura_a_rajada(balcao):

    #* contar-e-inserir e uma decisao so, dentro da transacao: dois envios simultaneos nao leem o mesmo total

    import threading

    entraram, recusados = [], []

    largada = threading.Barrier(8)

    def pedir():

        largada.wait(timeout=5)

        try:
            entraram.append(balcao.pedidos.criar(GUSTAVO, MARISTELA, [], outro='x', motivo='y'))

        except NaoPode:
            recusados.append(1)

    fios = [threading.Thread(target=pedir) for _ in range(8)]

    for fio in fios:
        fio.start()

    for fio in fios:
        fio.join(timeout=10)

    assert len(entraram) == MAX_PENDENTES_POR_PAR and len(recusados) == 8 - MAX_PENDENTES_POR_PAR




def teste_cancelado_nao_ocupa_vaga_na_caixa_de_quem_recebe(balcao):

    #* o filtro dos cancelados e no SQL: no original vinha depois do LIMIT, e cancelados ocupavam vaga

    for indice in range(pedidos_entre_labs.LIMITE_DA_CAIXA + 5):

        pedido_id = balcao.pedidos.criar(GUSTAVO, MARISTELA, [], outro=f'{indice}', motivo='m')

        if indice < pedidos_entre_labs.LIMITE_DA_CAIXA + 4:
            balcao.pedidos.cancelar(pedido_id, GUSTAVO)

    recebidos = balcao.pedidos.recebidos(MARISTELA)

    assert len(recebidos) == 1 and recebidos[0].outro == str(pedidos_entre_labs.LIMITE_DA_CAIXA + 4)




def teste_pendentes_vem_primeiro_na_caixa(balcao):

    velho = balcao.pedidos.criar(GUSTAVO, MARISTELA, [], outro='velho', motivo='m')

    balcao.pedidos.criar(GUSTAVO, MARISTELA, [], outro='novo', motivo='m')

    balcao.responder(velho, True, 'ok')

    caixa = balcao.pedidos.recebidos(MARISTELA)

    assert [p.outro for p in caixa] == ['novo', 'velho'] and caixa[0].estado == PENDENTE and caixa[1].contato == GUSTAVO




def teste_a_visao_traz_a_outra_parte_como_ela_se_publica(balcao):

    balcao.perfis.salvar(GUSTAVO, nome='Gustavo Freitas', laboratorio='LHV', foto='sorteada.png')

    balcao.pedir(itens=['Anaplasma platys'])

    recebido = balcao.recebido()

    assert (recebido.quem.nome, recebido.quem.laboratorio, recebido.quem.foto) == ('Gustavo Freitas', 'LHV', 'sorteada.png')

    enviado = balcao.pedidos.enviados(GUSTAVO)[0]

    assert enviado.quem.nome == 'Maristela Peckle' and not enviado.recebi and enviado.criado and enviado.respondido == ''




def teste_a_lista_de_itens_gravada_e_tolerante():

    assert lista('nao e json') == [] and lista('{"a": 1}') == [] and lista('[1, "x", null, "y"]') == ['x', 'y']

    assert len(lista(str(['a'] * 40).replace("'", '"'))) == MAX_ITENS




def teste_a_tela_avisa_do_email_antes_de_o_botao_ser_apertado(balcao):

    assert 'se ele aceitar' in balcao.labs()

    balcao.pedir(itens=['Anaplasma platys'])

    assert 'seu e-mail passa a aparecer' in balcao.outra().get('/pedidos').text




def teste_o_contador_da_lateral_conta_so_o_que_espera_esta_conta(balcao):

    #* o app nao manda e-mail: sem o numero na lateral de toda pagina, um pedido pode ficar meses sem ninguem saber que chegou

    balcao.pedir(itens=['Anaplasma platys'])

    assert balcao.pedidos.contar_pendentes(GUSTAVO) == 0 and '<span class="cont">' not in balcao.gustavo.get('/').text

    maristela = balcao.outra()

    assert balcao.pedidos.contar_pendentes(MARISTELA) == 1 and '<span class="cont">1</span>' in maristela.get('/').text

    assert 'href="/pedidos"' in maristela.get('/').text




def teste_a_pagina_de_pedidos_confirma_o_envio(balcao):

    resposta = balcao.pedir(itens=['Anaplasma platys'])

    pagina = balcao.gustavo.get(resposta.headers['location'])

    assert pagina.status_code == 200 and 'Anaplasma platys' in pagina.text




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
