"""
fila_de_arvores.py — a fila das arvores filogeneticas pedidas sobre uma corrida ja pronta: um pedido ativo por lote.

Montar a arvore e MCMC: 6 s numa corrida pequena, minutos numa grande. E o
mesmo motivo que tirou o lote da requisicao HTTP, entao ela entra pela mesma
porta — uma tabela propria (`arvores`), porque o lote ja terminou quando o
pedido nasce e uma corrida rende uma arvore POR MARCADOR. O mesmo trabalhador
atende as duas filas, lote primeiro: quem espera o resultado da corrida espera
mais do que quem pediu uma analise extra.


COMO FUNCIONA — a vida de um pedido
===================================

    novo_pedido(lote_id, dono)                       (botao "montar arvore" na pagina do lote PRONTO)
           |   transacao(): ja ha pedido ATIVO deste lote?  -> devolve o id dele, nao cria outro
           |   (dois cliques — ou o duplo-clique de quem ve a pagina demorar — poriam dois trabalhadores
           |    gravando o NEXUS na MESMA pasta, um por cima do outro)
           v
       NA_FILA --reivindicar(eu)--> RODANDO --etapa(id, 'marcador 18S...')--> concluir(id, resumo, eu) --> PRONTO
                                        '---------------------------------------falhar(id, erro, eu)----> FALHOU

    do_lote(lote_id)  -> o pedido mais recente do lote: e o que a pagina do lote mostra
    pedido.resumo     -> a lista (JSON gravado em concluir) ja decodificada: o que saiu por marcador; sobrevive ao expurgo dos .ab1

    reenfileirar_orfaos(eu), pulsar, trabalhadores_vivos, fila_atendida: herdados de fila.Fila
    ATIVOS = (NA_FILA, RODANDO)


COMO USAR
=========

    from easycontig_web.dados import banco_sqlite
    from easycontig_web.processamento import fila_de_arvores

    arvores = fila_de_arvores.FilaDeArvores(banco_sqlite.BancoSqlite(cfg.caminho_do_sqlite))

    pedido_id = arvores.novo_pedido('v2-UNsPdKxQ', 'ana@ufu.br')
    pedido = arvores.do_lote('v2-UNsPdKxQ')              #* PedidoDeArvore ou None
    pedido.ativo, pedido.resumo                          #* recarregar a pagina?; [{'marcador': ..., 'recusado': ...}, ...]

    python -m easycontig_web.processamento.fila_de_arvores   #* quantos pedidos por estado e os ultimos


O QUE MUDOU DO fila_de_arvores.py ORIGINAL (176 linhas)
=======================================================

 1. pegar, reivindicar, _dono_confere, falhar e reenfileirar_orfaos eram copias
    letra por letra da fila de lotes: vieram de fila.Fila. Ficou aqui so o que e
    das arvores: novo_pedido, do_lote, etapa, concluir e o resumo.

 2. PedidoDeArvore e um objeto (pedido.status), e `pedido.resumo` ja e a lista:
    o JSON e decodificado ao montar o registro (ler_resumo), e JSON quebrado vira
    lista vazia, nao excecao — a tela do pedido nao pode morrer por causa do
    campo de exibicao. O original guardava o texto e tinha resumo_lido(pedido).

 3. reenfileirar_orfaos(eu) le os vivos da tabela pulso sozinho; o original
    recebia `vivos` da fila de lotes.

 4. Nada foi cortado.


POR QUE NASCE CLASSE
====================

    FilaDeArvores herda de Fila (ver fila.py): a tabela `arvores` no banco e
    o recurso. PedidoDeArvore e o registro como objeto, com o resumo ja
    decodificado. ler_resumo e texto -> lista: funcao.
"""

import json

import secrets

from easycontig_web.dados.banco_sqlite import agora

from easycontig_web.processamento import fila

from easycontig_web.processamento.fila import NA_FILA, RODANDO, PRONTO, FALHOU, TAMANHO_DA_ETAPA



ATIVOS = (NA_FILA, RODANDO)  #* pedido que ainda vai dar trabalho: so um por lote




def ler_resumo(texto):

    #* o JSON gravado em concluir como lista; texto vazio, quebrado ou que nao e lista -> []

    try:
        dados = json.loads(texto or '[]')

    except (ValueError, TypeError):
        return []

    return dados if isinstance(dados, list) else []




class PedidoDeArvore:

    #* uma linha da tabela arvores, com nome em cada campo e o resumo ja decodificado. nao fala com o banco

    def __init__(self, id, lote_id, dono, status, etapa, erro, resumo, trabalhador, criado_em, iniciado_em, terminado_em):

        self.id = id  #* secrets.token_urlsafe(9)

        self.lote_id = lote_id  #* a corrida sobre a qual a arvore e montada

        self.dono = dono  #* e-mail de quem pediu

        self.status = status  #* NA_FILA / RODANDO / PRONTO / FALHOU

        self.etapa = etapa  #* em que marcador o MCMC esta agora (texto da tela)

        self.erro = erro  #* por que FALHOU

        self.resumo = ler_resumo(resumo)  #* lista com o que saiu por marcador; [] enquanto nao terminou

        self.trabalhador = trabalhador  #* quem reivindicou

        self.criado_em = criado_em  #* ISO UTC

        self.iniciado_em = iniciado_em  #* ISO UTC ou None

        self.terminado_em = terminado_em  #* ISO UTC ou None


    @property
    def ativo(self):

        #* a pagina deve continuar recarregando

        return self.status in ATIVOS


    def __repr__(self):

        return f'PedidoDeArvore({self.id!r}, {self.status!r}, lote_id={self.lote_id!r})'




class FilaDeArvores(fila.Fila):

    #* a tabela arvores: o que so os pedidos de arvore fazem; o protocolo comum vem de Fila

    tabela = 'arvores'


    def registro(self, linha):

        return PedidoDeArvore(**dict(linha))


    def novo_pedido(self, lote_id, dono):

        #* enfileira uma arvore para o lote e devolve o id do pedido. se ja ha pedido ATIVO do mesmo lote, devolve o id DELE:
        #* conferido na mesma transacao que insere, que e a corrida do novo_lote no mesmo lugar e pelo mesmo motivo

        pedido_id = secrets.token_urlsafe(9)

        marcas = ','.join('?' * len(ATIVOS))

        with self.banco.transacao() as con:

            existente = con.execute(f'SELECT id FROM arvores WHERE lote_id=? AND status IN ({marcas})', (lote_id, *ATIVOS)).fetchone()

            if existente:
                return existente['id']

            con.execute('INSERT INTO arvores (id, lote_id, dono, status, criado_em) VALUES (?,?,?,?,?)', (pedido_id, lote_id, dono, NA_FILA, agora()))

        return pedido_id


    def do_lote(self, lote_id):

        #* o pedido mais recente deste lote — e o que a pagina do lote mostra; None se nunca pediram

        with self.banco.conectar() as con:
            linha = con.execute('SELECT * FROM arvores WHERE lote_id=? ORDER BY criado_em DESC LIMIT 1', (lote_id,)).fetchone()

        return self.registro(linha) if linha else None


    def etapa(self, pedido_id, texto, trabalhador=''):

        #* em que marcador o MCMC esta agora; True se eu ainda era o dono

        return self.gravar_do_dono(pedido_id, trabalhador, etapa=texto[:TAMANHO_DA_ETAPA])


    def concluir(self, pedido_id, resumo, trabalhador=''):

        #* PRONTO com o resumo (lista de dicts, um por marcador) gravado como JSON; True se eu ainda era o dono

        return self.gravar_do_dono(pedido_id, trabalhador, status=PRONTO, terminado_em=agora(), etapa='', resumo=json.dumps(resumo, ensure_ascii=False))




def main():

    from easycontig_web import configuracao
    from easycontig_web.dados import banco_sqlite

    digitado = input('Caminho do fila.sqlite3 (enter = o da configuracao, EASYCONTIG_DATA_DIR): ').strip()

    banco = banco_sqlite.BancoSqlite(digitado) if digitado else banco_sqlite.BancoSqlite(configuracao.carregar().caminho_do_sqlite)

    if not banco.caminho.exists():
        print(f'o arquivo nao existe: {banco.caminho}')
        return

    arvores = FilaDeArvores(banco)

    with banco.conectar() as con:

        contagem = {linha['status']: linha['n'] for linha in con.execute('SELECT status, COUNT(*) AS n FROM arvores GROUP BY status')}

        ultimos = [arvores.registro(linha) for linha in con.execute('SELECT * FROM arvores ORDER BY criado_em DESC LIMIT 10')]

    print(f'\n{arvores!r}')

    for estado in (NA_FILA, RODANDO, PRONTO, FALHOU):
        print(f'  {estado:10} {contagem.get(estado, 0)}')

    print('\nultimos 10 pedidos:')

    for pedido in ultimos:
        print(f'  {pedido.id}  {pedido.status:10} {pedido.criado_em}  lote {pedido.lote_id}  {pedido.dono}  {len(pedido.resumo)} marcador(es)' + (f'  erro: {pedido.erro[:60]}' if pedido.erro else ''))

    print(f'\ntrabalhadores vivos: {sorted(arvores.trabalhadores_vivos()) or "nenhum"}; fila atendida: {arvores.fila_atendida()}')




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
