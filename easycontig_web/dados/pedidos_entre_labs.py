"""
pedidos_entre_labs.py — um laboratorio pede uma amostra a outro, e o outro aceita ou recusa: um ciclo so, e acaba.

E o par do portfolio do perfil (contas/perfil_do_laboratorio.py): o /labs
mostra QUEM trabalha com o que; isto e o PEDIR. NAO e conversa, e a diferenca
e de desenho, nao de acabamento: um pedido tem exatamente um ciclo — pendente,
depois aceito, recusado ou cancelado — e nenhum texto alem do motivo de quem
pede e da justificativa de quem responde. O app nao manda e-mail: o pedido so
existe dentro do site, e por isso o contador de pendentes vive na lateral de
toda pagina.


COMO FUNCIONA — a vida de um pedido
===================================

    criar(de_email, para_email, itens, outro, motivo)          (POST /labs/{chave}/pedido; o alvo veio pela CHAVE, nunca pelo e-mail)
           |   para si mesmo? motivo vazio? -> NaoPode (texto para a tela)
           |   itens FILTRADOS contra o portfolio declarado do alvo (Perfis.pegar): o formulario e do navegador,
           |   e um POST a mao manda o que quiser — texto livre existe e tem nome, e o `outro`
           |   nem item nem outro? -> NaoPode
           |   transacao(): ja ha MAX_PENDENTES_POR_PAR pendentes deste par? -> NaoPode (quem quer insistir espera resposta)
           v
       PENDENTE --responder(id, quem_recebeu, aceitar, resposta)--> ACEITO / RECUSADO   (so quem recebeu; so o pendente)
           '-----cancelar(id, quem_pediu)--------------------------> CANCELADO           (some da caixa do outro)

    recebidos(eu) / enviados(eu) -> lista de Pedido, pendentes primeiro, depois os mais recentes (ate LIMITE_DA_CAIXA)
    contar_pendentes(eu)         -> o numero da lateral (COUNT com indice proprio, roda em toda pagina)

    Pedido = a linha vista POR UMA DAS PARTES (visao): pedido.recebi diz de que lado estou; pedido.quem e a outra parte
    como ela ja se publica no /labs (nome, laboratorio, instituicao, foto); pedido.contato e o e-mail do outro lado,
    e so existe quando o pedido esta ACEITO — foi o aceite que autorizou. E aqui, e so aqui, que um e-mail atravessa
    a fronteira entre contas.


COMO USAR
=========

    from easycontig_web.dados import banco_sqlite, pedidos_entre_labs

    pedidos = pedidos_entre_labs.Pedidos(banco_sqlite.BancoSqlite(cfg.caminho_do_sqlite))

    try:
        pedido_id = pedidos.criar('ana@ufu.br', 'bia@ufu.br', ['Babesia vogeli'], motivo='padrao positivo para PCR')
    except pedidos_entre_labs.NaoPode as erro:
        ...                                                #* str(erro) e o recado para a tela

    for p in pedidos.recebidos('bia@ufu.br'): p.quem.nome, p.itens, p.motivo, p.estado
    pedidos.responder(pedido_id, 'bia@ufu.br', aceitar=True, resposta='temos DNA extraido')   #* Pedido ou None
    pedidos.cancelar(pedido_id, 'ana@ufu.br')          #* True se ainda estava pendente
    pedidos.contar_pendentes('bia@ufu.br')

    python -m easycontig_web.dados.pedidos_entre_labs  #* quantos pedidos por estado e os ultimos


O QUE MUDOU DO pedidos_entre_labs.py ORIGINAL (272 linhas)
==========================================================

 1. criar_esquema e _ESQUEMA sairam: a tabela `pedidos` nasce no ESQUEMA unico
    do BancoSqlite. Toda funcao que recebia `sqlite_path` virou metodo de
    Pedidos, que recebe o BancoSqlite; _agora e o banco_sqlite.agora().

 2. Pedido e Parte sao objetos (pedido.quem.nome), nao dicts aninhados. A
    visao por uma das partes (`_visao`) continua uma funcao, `visao(linha, eu)`,
    que monta o Pedido.

 3. O BEGIN IMMEDIATE / COMMIT / ROLLBACK escrito a mao em criar e o
    `banco.transacao()`.

 4. O portfolio do alvo vem de Perfis(banco).pegar(para_email).especies e
    .marcadores (ja listas), nao de um dict com .get.

 5. recebidos esconde os cancelados NO SQL (`estado NOT IN`), nao depois de
    ler: no original o filtro vinha depois do LIMIT 200, e cancelados
    ocupavam vaga na caixa de quem recebe.

 6. lista(): JSON que nao e lista -> [] (o original iterava um texto letra a
    letra e quebrava com TypeError num numero).

 7. Nomes: _texto -> texto; _lista -> lista; _listar -> listar(eu, coluna,
    esconder); _CAMPOS/_DE_ONDE -> CAMPOS/DE_ONDE; MAX_TEXTO ->
    TAMANHO_DO_TEXTO; o 200 do LIMIT -> LIMITE_DA_CAIXA.

 8. Nada foi cortado: criar, responder, cancelar, pegar, recebidos, enviados,
    contar_pendentes e NaoPode com os mesmos textos de tela.


POR QUE NASCE CLASSE
====================

    Pedidos e dona da tabela `pedidos` dentro do BancoSqlite: e o recurso.
    Pedido e Parte sao registros como objeto: nao falam com o banco. NaoPode
    e a excecao cuja mensagem a pessoa le na tela. texto, lista e visao sao
    texto/linha -> valor: funcoes.
"""

import json

import secrets

from easycontig_web.dados.banco_sqlite import agora

from easycontig_web.contas.perfil_do_laboratorio import Perfis



PENDENTE, ACEITO, RECUSADO, CANCELADO = 'pendente', 'aceito', 'recusado', 'cancelado'  #* os quatro estados; PENDENTE e o unico que ainda pede acao

TAMANHO_DO_TEXTO = 600  #* motivo, resposta e outro: as justificativas sao obrigatorias ("recusar sem dizer por que" mataria o recurso de desuso)

MAX_ITENS = 12  #* itens do portfolio do alvo num pedido

MAX_PENDENTES_POR_PAR = 3  #* pendentes da mesma pessoa com o mesmo laboratorio: o teto de taxa conta por hora e esquece; este conta o que esta VIVO na caixa

LIMITE_DA_CAIXA = 200  #* quantos pedidos a pagina lista por lado

CAMPOS = """
    p.id, p.de_email, p.para_email, p.itens, p.outro, p.motivo, p.estado, p.resposta, p.criado, p.respondido,
    de.nome AS de_nome, de.laboratorio AS de_lab, de.instituicao AS de_inst, de.foto AS de_foto,
    pa.nome AS para_nome, pa.laboratorio AS para_lab, pa.instituicao AS para_inst, pa.foto AS para_foto
"""  #* a linha do pedido com as duas partes ja juntas: o que a caixa mostra sem consultar os perfis um a um

DE_ONDE = """
    FROM pedidos p
    LEFT JOIN perfis de ON de.email = p.de_email
    LEFT JOIN perfis pa ON pa.email = p.para_email
"""  #* LEFT: perfil que sumiu nao esconde o pedido




class NaoPode(Exception):

    #* o pedido nao pode existir (ou ser respondido) assim, e a mensagem e para a pessoa ler na tela

    pass




def texto(bruto):

    #* motivo, resposta ou outro: sem espacos nas pontas e cortado em TAMANHO_DO_TEXTO

    return (bruto or '').strip()[:TAMANHO_DO_TEXTO]




def lista(bruto):

    #* a coluna itens (JSON de lista de textos) como lista; JSON quebrado ou que nao e lista -> []

    try:
        dados = json.loads(bruto or '[]')

    except (ValueError, TypeError):
        return []

    if not isinstance(dados, list):
        return []

    return [item for item in dados if isinstance(item, str)][:MAX_ITENS]




def visao(linha, eu):

    #* a linha do JOIN vista por uma das duas partes: `recebi` diz de que lado estou; `quem` e a outra parte como ela ja se
    #* publica no /labs; `contato` (o e-mail do outro lado) so quando ACEITO — foi o aceite que autorizou

    dados = dict(linha)

    recebi = dados['para_email'] == eu

    outro_lado = 'de' if recebi else 'para'

    quem = Parte(dados[f'{outro_lado}_nome'] or '', dados[f'{outro_lado}_lab'] or '', dados[f'{outro_lado}_inst'] or '', dados[f'{outro_lado}_foto'] or '')

    contato = (dados['de_email'] if recebi else dados['para_email']) if dados['estado'] == ACEITO else ''

    return Pedido(dados['id'], dados['estado'], recebi, lista(dados['itens']), dados['outro'], dados['motivo'],
                  dados['resposta'], dados['criado'], dados['respondido'], quem, contato)




class Parte:

    #* a outra parte de um pedido, com o que ela ja publica no /labs — nunca o e-mail (esse e o `contato` do Pedido)

    def __init__(self, nome, laboratorio, instituicao, foto):

        self.nome = nome  #* '' se o perfil nunca declarou (a tela mostra "perfil sem nome")

        self.laboratorio = laboratorio

        self.instituicao = instituicao

        self.foto = foto  #* nome do arquivo para /labs/foto/{arquivo}; '' sem foto


    def __repr__(self):

        return f'Parte({self.nome!r}, {self.laboratorio!r})'




class Pedido:

    #* um pedido visto por uma das partes (ver visao). nao fala com o banco: quem grava e Pedidos

    def __init__(self, id, estado, recebi, itens, outro, motivo, resposta, criado, respondido, quem, contato):

        self.id = id  #* secrets.token_urlsafe(9)

        self.estado = estado  #* PENDENTE / ACEITO / RECUSADO / CANCELADO

        self.recebi = recebi  #* True: pediram a mim (eu respondo); False: eu pedi (eu cancelo). a tela nao decide isso sozinha

        self.itens = itens  #* o que foi marcado do portfolio do alvo (lista de textos)

        self.outro = outro  #* o que nao estava na lista (texto livre de quem pediu)

        self.motivo = motivo  #* para que serve a amostra (quem pediu)

        self.resposta = resposta  #* a justificativa de quem respondeu; '' enquanto pendente

        self.criado = criado  #* ISO UTC

        self.respondido = respondido  #* ISO UTC da resposta ou do cancelamento; '' enquanto pendente

        self.quem = quem  #* Parte: a outra parte

        self.contato = contato  #* o e-mail da outra parte, SO quando ACEITO; '' nos outros estados


    def __repr__(self):

        return f'Pedido({self.id!r}, {self.estado!r}, recebi={self.recebi!r})'




class Pedidos:

    #* a tabela pedidos: cria, responde, cancela e lista os pedidos de cada conta. recebe o BancoSqlite ja preparado

    def __init__(self, banco):

        self.banco = banco  #* dados.banco_sqlite.BancoSqlite


    def criar(self, de_email, para_email, itens, outro='', motivo=''):

        #* registra o pedido e devolve o id; NaoPode com o texto de tela quando nao pode existir
        #! itens e FILTRADO contra o portfolio declarado do alvo, nao gravado como veio: os valores chegam de caixas que o
        #! servidor desenhou daquele portfolio, mas um POST a mao manda o que quiser — sem o filtro, itens viraria um campo
        #! de texto livre extra, sem rotulo e sem teto, atravessando de uma conta para outra. o texto livre e o `outro`

        if de_email == para_email:
            raise NaoPode('um pedido é para outro laboratório')

        motivo = texto(motivo)

        if not motivo:
            raise NaoPode('diga para que serve a amostra — quem recebe decide com isso')

        alvo = Perfis(self.banco).pegar(para_email)

        declarado = {*alvo.especies, *alvo.marcadores}

        escolhidos, vistos = [], set()

        for item in itens or []:

            nome = ' '.join(str(item).split())

            if nome in declarado and nome not in vistos:
                vistos.add(nome)
                escolhidos.append(nome)

            if len(escolhidos) >= MAX_ITENS:
                break

        outro = texto(outro)

        if not escolhidos and not outro:
            raise NaoPode('marque ao menos um item do portfólio, ou escreva o que precisa')

        pedido_id = secrets.token_urlsafe(9)

        with self.banco.transacao() as con:  #* contar-e-inserir e uma decisao so: sem a trava, dois envios simultaneos leem o mesmo total e os dois passam

            pendentes = con.execute('SELECT COUNT(*) FROM pedidos WHERE de_email=? AND para_email=? AND estado=?', (de_email, para_email, PENDENTE)).fetchone()[0]

            if pendentes >= MAX_PENDENTES_POR_PAR:
                raise NaoPode(f'você já tem {pendentes} pedidos esperando resposta deste laboratório; aguarde a resposta antes de mandar outro')

            con.execute('INSERT INTO pedidos (id, de_email, para_email, itens, outro, motivo, estado, criado) VALUES (?,?,?,?,?,?,?,?)',
                        (pedido_id, de_email, para_email, json.dumps(escolhidos, ensure_ascii=False), outro, motivo, PENDENTE, agora()))

        return pedido_id


    def responder(self, pedido_id, quem, aceitar, resposta):

        #* aceita ou recusa: so quem RECEBEU responde, e so o que esta pendente. devolve o Pedido atualizado, ou None se nao havia
        #* o que responder — o id nao existe, e de outra pessoa, ou ja foi respondido. os tres casos dao a mesma resposta de
        #* proposito: distinguir "nao e seu" de "nao existe" conta a quem tentou que aquele id e de alguem
        #! a condicao estado=PENDENTE esta no UPDATE, nao num SELECT antes: e o que impede dois cliques de responderem duas vezes

        resposta = texto(resposta)

        if not resposta:
            raise NaoPode('diga o motivo — quem pediu precisa saber com o que contar')

        estado = ACEITO if aceitar else RECUSADO

        with self.banco.conectar() as con:
            gravou = con.execute('UPDATE pedidos SET estado=?, resposta=?, respondido=? WHERE id=? AND para_email=? AND estado=?',
                                 (estado, resposta, agora(), pedido_id, quem, PENDENTE)).rowcount

        if gravou == 0:
            return None

        return self.pegar(pedido_id, quem)


    def cancelar(self, pedido_id, quem):

        #* quem pediu desiste, enquanto ninguem respondeu; some da caixa do outro. True se havia o que cancelar

        with self.banco.conectar() as con:
            return con.execute('UPDATE pedidos SET estado=?, respondido=? WHERE id=? AND de_email=? AND estado=?',
                               (CANCELADO, agora(), pedido_id, quem, PENDENTE)).rowcount > 0


    def pegar(self, pedido_id, eu):

        #* um pedido, se a conta for uma das duas partes dele; None se nao e

        with self.banco.conectar() as con:
            linha = con.execute(f'SELECT {CAMPOS} {DE_ONDE} WHERE p.id=? AND (p.de_email=? OR p.para_email=?)', (pedido_id, eu, eu)).fetchone()

        return visao(linha, eu) if linha else None


    def listar(self, eu, coluna, esconder=()):

        #* os pedidos em que `eu` esta na `coluna` (para_email: recebi; de_email: enviei), menos os estados em `esconder`.
        #* pendentes primeiro (e o que pede acao), depois os mais recentes, ate LIMITE_DA_CAIXA

        filtro = f' AND p.estado NOT IN ({",".join("?" * len(esconder))})' if esconder else ''

        with self.banco.conectar() as con:
            linhas = con.execute(f'SELECT {CAMPOS} {DE_ONDE} WHERE p.{coluna}=?{filtro} ORDER BY (p.estado=?) DESC, p.criado DESC LIMIT ?',
                                 (eu, *esconder, PENDENTE, LIMITE_DA_CAIXA)).fetchall()

        return [visao(linha, eu) for linha in linhas]


    def recebidos(self, eu):

        #* o que pediram a esta conta. cancelado nao aparece: quem pediu desistiu antes de a pessoa olhar, e mostrar
        #* "fulano desistiu" e ruido sobre um pedido que nunca chegou a exigir nada

        return self.listar(eu, 'para_email', esconder=(CANCELADO,))


    def enviados(self, eu):

        #* o que esta conta pediu, em qualquer estado

        return self.listar(eu, 'de_email')


    def contar_pendentes(self, eu):

        #* quantos pedidos esperam resposta DESTA conta: o numero da lateral, em toda pagina (por isso o idx_pedidos_para)

        with self.banco.conectar() as con:
            return int(con.execute('SELECT COUNT(*) FROM pedidos WHERE para_email=? AND estado=?', (eu, PENDENTE)).fetchone()[0])


    def __repr__(self):

        return f'Pedidos({self.banco!r})'




def main():

    from easycontig_web import configuracao
    from easycontig_web.dados import banco_sqlite

    digitado = input('Caminho do fila.sqlite3 (enter = o da configuracao, EASYCONTIG_DATA_DIR): ').strip()

    banco = banco_sqlite.BancoSqlite(digitado) if digitado else banco_sqlite.BancoSqlite(configuracao.carregar().caminho_do_sqlite)

    if not banco.caminho.exists():
        print(f'o arquivo nao existe: {banco.caminho}')
        return

    pedidos = Pedidos(banco)

    with banco.conectar() as con:

        contagem = {linha['estado']: linha['n'] for linha in con.execute('SELECT estado, COUNT(*) AS n FROM pedidos GROUP BY estado')}

        ultimos = con.execute('SELECT id, estado, de_email, para_email, criado, respondido FROM pedidos ORDER BY criado DESC LIMIT 10').fetchall()

    print(f'\n{pedidos!r}')

    for estado in (PENDENTE, ACEITO, RECUSADO, CANCELADO):
        print(f'  {estado:10} {contagem.get(estado, 0)}')

    print('\nultimos 10 pedidos:')

    for linha in ultimos:
        print(f'  {linha["id"]}  {linha["estado"]:10} {linha["criado"]}  {linha["de_email"]} -> {linha["para_email"]}' + (f'  respondido {linha["respondido"]}' if linha['respondido'] else ''))




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
