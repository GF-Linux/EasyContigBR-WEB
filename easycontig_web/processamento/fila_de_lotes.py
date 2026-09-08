"""
fila_de_lotes.py — a fila das corridas enviadas: nasce RECEBENDO, entra na fila com o ultimo byte gravado, e o trabalhador monta.

A requisicao HTTP so ENFILEIRA e devolve um protocolo (processar um lote de 40
leva ~26 s, e vinte pessoas com a requisicao aberta esgotam as conexoes com a
CPU parada). Quem trabalha e outro processo, que le esta fila. SQLite e nao
Redis: uma dependencia a menos para a universidade manter, e o volume real
(algumas corridas por mes) nao justifica outro servico.


COMO FUNCIONA — a vida de um lote
=================================

    novo_lote(dono, nome, n_arquivos, referencia, tetos...)         (rota de envio, ANTES de gravar os arquivos)
           |   transacao(): conta os ativos da conta e a reserva de bytes dos envios em curso;
           |   estourou -> CotaEstourada / CotaDeBytesEstourada (nada e inserido)
           v
       RECEBENDO  -- invisivel para o trabalhador: os arquivos ainda estao subindo
           |         (um lote de 26 MB pego no meio do upload virou relatorio com 33 de 40 amostras, sem aviso nenhum)
           |
           |-- liberar_para_fila(lote_id, n_gravados)              (ultimo byte gravado; n_arquivos = o que REALMENTE caiu no disco)
           |       v
           |    NA_FILA --reivindicar(eu)--> RODANDO --progresso(...)--> concluir(id, eu) --> PRONTO
           |                                    '------------------------falhar(id, erro, eu)---> FALHOU
           |
           '-- falhar_envios_abandonados()                          (arranque do trabalhador)
                   RECEBENDO ha mais de ENVIO_ABANDONADO s -> FALHOU "o envio dos arquivos foi interrompido"
                   (quem envia nao pulsa: a idade e a unica evidencia; o nginx deixa o corpo subir por 300 s, e meia hora e folga)

    reenfileirar_orfaos(eu), pulsar, trabalhadores_vivos, fila_atendida: herdados de fila.Fila
    ATIVOS = (RECEBENDO, NA_FILA, RODANDO): os estados que ocupam vaga na cota da conta


COMO USAR
=========

    from easycontig_web.dados import banco_sqlite
    from easycontig_web.processamento import fila_de_lotes

    lotes = fila_de_lotes.FilaDeLotes(banco_sqlite.BancoSqlite(cfg.caminho_do_sqlite))

    try:
        lote_id = lotes.novo_lote('ana@ufu.br', 'placa 12', 40, referencia='referencias_18S',
                                  teto_ativos=10, bytes_previstos=26_000_000, teto_bytes=2 * 1024 ** 3, usados_em_disco=900_000_000)
    except fila_de_lotes.CotaEstourada as erro:
        ...                                               #* erro.ativos de erro.teto corridas em processamento
    lotes.liberar_para_fila(lote_id, 40)

    lote = lotes.pegar(lote_id)                           #* Lote: lote.status, lote.ativo, lote.n_arquivos...
    for lote in lotes.listar(dono='ana@ufu.br', limite=25): ...

    python -m easycontig_web.processamento.fila_de_lotes  #* quantos lotes por estado, os ultimos, quem esta vivo


O QUE MUDOU DO fila_de_lotes.py ORIGINAL (460 linhas)
=====================================================

 1. conectar, criar_esquema, _ESQUEMA, _agora, INTERVALO_PULSO/PULSO_VELHO e o
    protocolo comum (pegar, reivindicar, _dono_confere, falhar, pulsar,
    trabalhadores_vivos, fila_atendida, reenfileirar_orfaos) sairam daqui: o
    banco e o BancoSqlite (dados/banco_sqlite.py), o protocolo e fila.Fila.
    Este arquivo so tem o que e DOS LOTES: RECEBENDO, as cotas, novo_lote,
    liberar_para_fila, listar, progresso, concluir, falhar_envios_abandonados.

 2. Lote e um objeto com uma propriedade por coluna (lote.status), e nao dict.
    `lote.ativo` responde "ocupa vaga na cota / a pagina deve recarregar".

 3. novo_lote sempre abre transacao (BEGIN IMMEDIATE). O original so abria
    quando havia teto; sem teto inseria em autocommit — dois caminhos para o
    mesmo INSERT. A regra da cota nao mudou: contada DENTRO da transacao do
    INSERT (com teto 1 e oito envios simultaneos da mesma conta, sete entravam
    quando a conta era feita na rota).

 4. falhar_envios_abandonados e um metodo proprio. No original era um UPDATE
    escondido dentro de reenfileirar_orfaos, cuja contagem devolvida nem o
    incluia. O trabalhador chama os dois no arranque.

 5. listar(limite=0) devolve tudo (LIMIT -1); o original nao tinha "sem limite"
    e a cota lia a tabela com SQL proprio.

 6. Os textos que a pagina mostra (o erro do envio interrompido) sao constantes
    com nome, nao literais dentro do SQL.

 7. Nada foi cortado.


POR QUE NASCE CLASSE
====================

    FilaDeLotes herda de Fila (ver fila.py): a tabela `lotes` dentro do banco
    e o recurso, e o banco e o estado. Lote e o registro como objeto: nao
    faz nada com o banco, so carrega os campos com nome e responde `ativo`.
    As duas excecoes carregam os numeros que a rota usa para explicar ao
    usuario o que estourou.
"""

import secrets

from datetime import datetime, timedelta, timezone

from easycontig_web.dados.banco_sqlite import agora

from easycontig_web.processamento import fila

from easycontig_web.processamento.fila import NA_FILA, RODANDO, PRONTO, FALHOU, TAMANHO_DA_ETAPA



RECEBENDO = 'recebendo'  #* os arquivos ainda estao subindo: o trabalhador nao ve o lote

ATIVOS = (RECEBENDO, NA_FILA, RODANDO)  #* os estados que ocupam vaga na cota da conta: o lote existe e ainda vai dar trabalho

ENVIO_ABANDONADO = 1800.0  #* segundos em RECEBENDO a partir dos quais o envio conta como abandonado (quem envia nao pulsa; a idade e a unica evidencia)

AVISO_DE_ENVIO_INTERROMPIDO = 'o envio dos arquivos foi interrompido; envie a corrida de novo'  #* o erro gravado no lote abandonado; a pagina mostra




class Lote:

    #* uma linha da tabela lotes, com nome em cada campo. nao fala com o banco: quem grava e a FilaDeLotes

    def __init__(self, id, dono, nome, status, n_arquivos, feito, total, etapa, erro,
                 referencia, trabalhador, bytes_previstos, criado_em, iniciado_em, terminado_em):

        self.id = id  #* protocolo (secrets.token_urlsafe(9), 12 caracteres); e o nome da pasta do lote

        self.dono = dono  #* e-mail da conta

        self.nome = nome  #* o nome que o usuario deu a corrida

        self.status = status  #* RECEBENDO / NA_FILA / RODANDO / PRONTO / FALHOU

        self.n_arquivos = n_arquivos  #* quantos .ab1 REALMENTE cairam no disco (liberar_para_fila reescreve)

        self.feito = feito  #* progresso: amostras ja montadas

        self.total = total  #* progresso: amostras no lote

        self.etapa = etapa  #* o que o trabalhador esta fazendo agora (texto da tela)

        self.erro = erro  #* por que FALHOU

        self.referencia = referencia  #* o banco escolhido para identificar: fica GRAVADO, o relatorio continua interpretavel se a instalacao mudar de banco

        self.trabalhador = trabalhador  #* quem reivindicou (maquina + PID); '' antes de reivindicar ou depois de reenfileirar

        self.bytes_previstos = bytes_previstos  #* reserva de cota enquanto os arquivos sobem; 0 depois de liberar_para_fila

        self.criado_em = criado_em  #* ISO UTC

        self.iniciado_em = iniciado_em  #* ISO UTC ou None

        self.terminado_em = terminado_em  #* ISO UTC ou None


    @property
    def ativo(self):

        #* ocupa vaga na cota e a pagina deve continuar recarregando

        return self.status in ATIVOS


    def __repr__(self):

        return f'Lote({self.id!r}, {self.status!r}, dono={self.dono!r}, nome={self.nome!r})'




class CotaEstourada(Exception):

    #* o teto de lotes ativos da conta ja estava cheio quando o lote ia nascer; a rota diz "espere uma corrida terminar"

    def __init__(self, ativos, teto):

        self.ativos = ativos

        self.teto = teto

        super().__init__(f'{ativos} de {teto} lotes ativos')




class CotaDeBytesEstourada(Exception):

    #* o que a conta ja ocupa MAIS o que este envio reservou passa do teto. separada da CotaEstourada porque a resposta e outra:
    #* aqui nao adianta esperar uma corrida terminar, e preciso liberar espaco

    def __init__(self, usados, reservado, teto):

        self.usados = usados

        self.reservado = reservado

        self.teto = teto

        super().__init__(f'{usados} + {reservado} passa de {teto}')




class FilaDeLotes(fila.Fila):

    #* a tabela lotes: o que so os lotes fazem; o protocolo comum (reivindicar, falhar, pulso, orfaos) vem de Fila

    tabela = 'lotes'


    def registro(self, linha):

        return Lote(**dict(linha))


    def novo_lote(self, dono, nome, n_arquivos, referencia='', teto_ativos=0, bytes_previstos=0, teto_bytes=0, usados_em_disco=0):

        #* abre o lote em RECEBENDO e devolve o id. as cotas sao conferidas AQUI DENTRO, na transacao do INSERT:
        #* conferir na rota, antes de o lote existir, deixava uma janela entre contar e criar (medido em 2026-08-06:
        #* teto 1, oito envios simultaneos da mesma conta, SETE entraram). teto 0 = sem teto.
        #* `usados_em_disco` e medido FORA (percorrer pastas seguraria a trava por dezenas de ms e lote terminado nao muda
        #* de tamanho); o que PRECISA ser lido aqui dentro e a reserva dos lotes ainda em RECEBENDO — a corrida e essa:
        #* dez envios simultaneos liam o mesmo `usados` antes de qualquer byte chegar ao disco

        lote_id = secrets.token_urlsafe(9)

        marcas = ','.join('?' * len(ATIVOS))

        with self.banco.transacao() as con:

            if teto_ativos:

                ativos = con.execute(f'SELECT COUNT(*) FROM lotes WHERE dono=? AND status IN ({marcas})', (dono, *ATIVOS)).fetchone()[0]

                if ativos >= teto_ativos:
                    raise CotaEstourada(ativos, teto_ativos)

            if teto_bytes:

                reservado = con.execute('SELECT COALESCE(SUM(bytes_previstos),0) FROM lotes WHERE dono=? AND status=?', (dono, RECEBENDO)).fetchone()[0]

                if usados_em_disco + reservado + bytes_previstos > teto_bytes:
                    raise CotaDeBytesEstourada(usados_em_disco + reservado, bytes_previstos, teto_bytes)

            con.execute(
                'INSERT INTO lotes (id, dono, nome, status, n_arquivos, criado_em, referencia, bytes_previstos) VALUES (?,?,?,?,?,?,?,?)',
                (lote_id, dono, nome, RECEBENDO, n_arquivos, agora(), referencia, max(0, int(bytes_previstos))))

        return lote_id


    def liberar_para_fila(self, lote_id, n_arquivos):

        #* ultimo byte gravado: agora o trabalhador pode pegar. n_arquivos e reescrito com o que REALMENTE foi gravado (e esse
        #* numero que o executor confere contra o disco); bytes_previstos=0 porque a reserva so vale enquanto o upload esta
        #* em curso — depois os bytes estao no disco e sao medidos de la (manter a previsao cobraria duas vezes o mesmo lote)

        with self.banco.conectar() as con:
            con.execute('UPDATE lotes SET status=?, n_arquivos=?, bytes_previstos=0 WHERE id=? AND status=?', (NA_FILA, n_arquivos, lote_id, RECEBENDO))


    def listar(self, dono=None, limite=50):

        #* os lotes do mais novo para o mais velho; dono=None = de todos; limite=0 = todos

        sql = 'SELECT * FROM lotes'

        argumentos = []

        if dono is not None:

            sql += ' WHERE dono=?'

            argumentos.append(dono)

        sql += ' ORDER BY criado_em DESC LIMIT ?'

        argumentos.append(limite if limite > 0 else -1)

        with self.banco.conectar() as con:
            return [self.registro(linha) for linha in con.execute(sql, argumentos)]


    def progresso(self, lote_id, feito, total, etapa, trabalhador=''):

        #* o executor avisando por onde vai; True se eu ainda era o dono

        return self.gravar_do_dono(lote_id, trabalhador, feito=feito, total=total, etapa=etapa[:TAMANHO_DA_ETAPA])


    def concluir(self, lote_id, trabalhador=''):

        #* PRONTO. True se este trabalhador ainda era o dono; False = perdeu o lote, e o relatorio dele nao deve ser publicado

        return self.gravar_do_dono(lote_id, trabalhador, status=PRONTO, terminado_em=agora(), etapa='')


    def falhar_envios_abandonados(self, momento=None):

        #* RECEBENDO ha mais de ENVIO_ABANDONADO segundos vira FALHOU com o aviso. so os VELHOS: RECEBENDO e o estado de quem
        #* esta com o upload em curso neste segundo, e varrer todos (como ja foi feito) matava o envio que outra pessoa fazia
        #* durante um deploy. um upload interrompido deixou a pasta pela metade, e mandar para a fila reproduziria o
        #* relatorio de 33 de 40 amostras. devolve quantos marcou

        corte = (momento or datetime.now(timezone.utc)) - timedelta(seconds=ENVIO_ABANDONADO)

        with self.banco.conectar() as con:
            return con.execute(
                'UPDATE lotes SET status=?, erro=? WHERE status=? AND criado_em<?',
                (FALHOU, AVISO_DE_ENVIO_INTERROMPIDO, RECEBENDO, corte.isoformat(timespec='seconds'))).rowcount




def main():

    from easycontig_web import configuracao
    from easycontig_web.dados import banco_sqlite

    digitado = input('Caminho do fila.sqlite3 (enter = o da configuracao, EASYCONTIG_DATA_DIR): ').strip()

    banco = banco_sqlite.BancoSqlite(digitado) if digitado else banco_sqlite.BancoSqlite(configuracao.carregar().caminho_do_sqlite)

    if not banco.caminho.exists():
        print(f'o arquivo nao existe: {banco.caminho}')
        return

    lotes = FilaDeLotes(banco)

    with banco.conectar() as con:
        contagem = {linha['status']: linha['n'] for linha in con.execute('SELECT status, COUNT(*) AS n FROM lotes GROUP BY status')}

    print(f'\n{lotes!r}')

    for estado in (RECEBENDO, NA_FILA, RODANDO, PRONTO, FALHOU):
        print(f'  {estado:10} {contagem.get(estado, 0)}')

    print('\nultimos 10 lotes:')

    for lote in lotes.listar(limite=10):
        print(f'  {lote.id}  {lote.status:10} {lote.criado_em}  {lote.dono}  {lote.nome!r}  {lote.n_arquivos} arquivo(s)' + (f'  erro: {lote.erro[:60]}' if lote.erro else ''))

    print(f'\ntrabalhadores vivos: {sorted(lotes.trabalhadores_vivos()) or "nenhum"}; fila atendida: {lotes.fila_atendida()}')




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
