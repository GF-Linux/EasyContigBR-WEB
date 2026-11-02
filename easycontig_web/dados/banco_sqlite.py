"""
banco_sqlite.py — o UNICO arquivo fila.sqlite3 da instalacao: conexao, transacao, esquema e migracoes.

A web guarda em um SQLite tudo o que nao e arquivo de corrida: a fila de lotes,
o pulso dos trabalhadores, os perfis dos laboratorios, os pedidos de amostra
entre eles e a fila das arvores. Este arquivo e o dono desse banco: abre a
conexao do jeito certo (WAL, tempo de espera), leva o esquema ate a versao
atual com backup antes da primeira alteracao, e sabe voltar atras. Quem grava
e le linhas sao as filas (fila_de_lotes.py, fila_de_arvores.py), os perfis
(perfil_do_laboratorio.py) e os pedidos (pedidos_entre_labs.py): todos recebem
um BancoSqlite e nao um caminho.


COMO FUNCIONA — do arranque ao banco pronto
===========================================

    banco = BancoSqlite(cfg.caminho_do_sqlite)
           |
           v
    banco.preparar()                       (servidor_web e trabalhador, a cada arranque; seguro em paralelo e repetido)
           |
           |-- pasta nao existe?  mkdir
           |
           |-- nao ha a tabela `lotes`?   -> banco NOVO (ou arquivo vazio):
           |        BEGIN IMMEDIATE; ESQUEMA inteiro; PRAGMA user_version = VERSAO_ALVO; COMMIT
           |        nasce pronto, na versao alvo, sem rodar migracao e sem backup de banco vazio
           |
           '-- ha                         -> banco EXISTENTE (o da VPS, com corrida de verdade dentro):
                    migrar()      transacao() — UMA trava de escrita para a atualizacao inteira:
                    |                 versao < VERSAO_ALVO?  (quem esperou na trava ve o banco ja migrado e sai com 0)
                    |                 fazer_backup() — UM por atualizacao, e depois, para cada migracao pendente, em ordem:
                    |                 SAVEPOINT; funcao(con); PRAGMA user_version = n; RELEASE
                    |                 (falhou? ROLLBACK TO desfaz so ela; as anteriores ficam; COMMIT; a excecao sobe)
                    '-- ESQUEMA de novo, tudo IF NOT EXISTS: cria a tabela ou o indice que ainda faltar

    banco.conectar()          -> conexao com WAL + busy_timeout: leitura, ou escrita de UM comando
    banco.transacao()         -> BEGIN IMMEDIATE ... COMMIT (ROLLBACK se der excecao): quem le, decide e grava usa isto
    banco.versao              -> PRAGMA user_version
    banco.fazer_backup()      -> <nome>.antes-de-migrar-<carimbo UTC>, em UM arquivo (WAL integrado, journal_mode=DELETE)
    banco.restaurar(backup)   -> volta atras, com o servidor PARADO: apaga -wal/-shm e so entao copia o backup por cima


COMO USAR
=========

    from easycontig_web import configuracao
    from easycontig_web.dados import banco_sqlite

    cfg = configuracao.carregar()
    banco = banco_sqlite.BancoSqlite(cfg.caminho_do_sqlite)

    banco.preparar()                                    #* no arranque; devolve quantas migracoes rodaram

    with banco.conectar() as con:                       #* leitura
        linha = con.execute('SELECT * FROM lotes WHERE id=?', ('v2-UNsPd',)).fetchone()

    with banco.transacao() as con:                      #* ler-decidir-gravar, sem ninguem no meio
        ativos = con.execute('SELECT COUNT(*) FROM lotes WHERE status IN (?,?,?)', ...).fetchone()[0]
        con.execute('INSERT INTO lotes ...')

    banco.restaurar(Path('fila.sqlite3.antes-de-migrar-20260901T120000Z'))   #* com o servidor parado

    python -m easycontig_web.dados.banco_sqlite          #* mostra versao, tabelas e migracoes pendentes; oferece preparar


O QUE MUDOU DO esquema_e_migracoes.py ORIGINAL (e dos criar_esquema espalhados)
===============================================================================

 1. Virou UMA classe, BancoSqlite. No original o mesmo arquivo era tratado por
    quatro modulos: fila_de_lotes.conectar + criar_esquema (lotes e pulso),
    esquema_e_migracoes.aplicar/versao/fazer_backup/restaurar, perfil.criar_esquema
    (perfis) e pedidos.criar_esquema (pedidos) — e a tabela `arvores` so existia
    dentro da migracao 6, sem CREATE em lugar nenhum. Agora o ESQUEMA e um texto
    so, com as cinco tabelas e todos os indices, e as filas, os perfis e os
    pedidos recebem o BancoSqlite.

 2. Banco novo nasce direto na VERSAO_ALVO, numa transacao so. O original criava
    lotes+pulso, via versao 0, fazia um BACKUP DO BANCO VAZIO (o arquivo
    `fila.sqlite3.antes-de-migrar-...` que aparecia no volume em todo primeiro
    arranque) e rodava as seis migracoes como no-op. "Novo" e "nao tem a tabela
    lotes": arquivo vazio conta como novo.

 3. Banco existente: migra PRIMEIRO e so depois passa o ESQUEMA (IF NOT EXISTS).
    O original fazia esquema parcial -> migracoes -> esquema do perfil -> esquema
    dos pedidos, e por isso as migracoes 2 e 5 so mexem em `perfis` se a tabela
    ja existe (um banco criado so pelo trabalhador nao a tem). Essa guarda fica:
    ela continua verdadeira, e e o que faz as duas ordens chegarem ao mesmo lugar.

 4. transacao(): o BEGIN IMMEDIATE / COMMIT / ROLLBACK que o original escrevia a
    mao em tres lugares (aplicar, fila.novo_lote, pedidos.criar) e um with.

 5. agora() mora aqui: o relogio de todas as tabelas (UTC, ISO, segundos). O
    original tinha tres copias de _agora.

 6. Nomes: aplicar -> migrar (preparar devolve o mesmo numero); versao(path) ->
    banco.versao; _v1_referencia_no_lote -> migracao_1_referencia_no_lote (e
    assim por diante); _colunas -> colunas; log -> REGISTRO. As migracoes NAO
    mudaram uma letra de SQL: banco em producao ja as aplicou.

 7. fazer_backup nunca grava por cima de um backup que ja existe: o nome e
    reservado com O_EXCL. O carimbo tem resolucao de segundo, e no original
    restaurar + migrar de novo dentro do mesmo segundo sobrescrevia em silencio
    a copia que acabou de servir; e web + trabalhadores migrando juntos abriam
    o MESMO arquivo de backup e um deles morria com "database is locked"
    (os dois achados na bancada). Agora sai `...antes-de-migrar-<carimbo>-2`.

 8. Arranque em paralelo. Trocar um banco recem-criado para WAL pede trava
    exclusiva e o SQLite nao espera o busy_timeout nessa troca: no original,
    web + 2 trabalhadores subindo juntos num volume vazio morriam 1 em cada 4
    arranques com "database is locked" (medido: 59 mortes em 240 processos).
    ligar_wal insiste ate TEMPO_LIMITE. E migrar() faz a atualizacao inteira
    dentro de UMA trava de escrita, com um SAVEPOINT por migracao: o original
    abria uma transacao por migracao, e dois processos migrando juntos faziam
    dois backups (do mesmo arquivo, ver 7) e intercalavam as migracoes. Agora
    quem chega depois espera, ve o banco pronto e sai: um backup por
    atualizacao, cada migracao uma vez no log. A garantia de sempre fica: a
    migracao que falha e desfeita sozinha e o banco fica na ultima que deu
    certo.

 9. Nada foi cortado: servidor_web (tres criar_esquema) e trabalhador (um) viram
    banco.preparar(); os testes de migracao do original (banco antigo com a
    corrida da Maristela, perfis na v4 sem chave, migracao que explode) passam
    pelo mesmo caminho.


POR QUE NASCE CLASSE
====================

    O banco e um recurso externo — um arquivo com trava, WAL e versao — e o
    caminho dele e o estado que TODA operacao precisa. O original passava o
    Path em cada chamada de cada modulo (fila.pegar(caminho, id),
    perfil.pegar(caminho, email)...); agora o caminho entra uma vez, na
    construcao, e quem precisa do banco recebe o objeto. As migracoes ficam
    funcoes soltas: cada uma recebe a conexao, altera e devolve nada — nao
    guardam estado.
"""

import contextlib

import logging

import os

import secrets

import shutil

import sqlite3

import time

from datetime import datetime, timezone

from pathlib import Path



REGISTRO = logging.getLogger('easycontig.banco_sqlite')

TEMPO_LIMITE = 30  #* segundos que uma conexao espera pela trava do arquivo antes de desistir (SQLITE_BUSY); web + N trabalhadores gravam no mesmo banco

SUFIXO_DO_BACKUP = '.antes-de-migrar-'  #* fila.sqlite3.antes-de-migrar-20260901T120000Z


ESQUEMA_DAS_AMOSTRAS = (  #* um comando por item: a migracao 7 os executa um a um (ver a regra das migracoes) e o ESQUEMA os junta
    """CREATE TABLE IF NOT EXISTS amostras (
    lote_id     TEXT NOT NULL,
    chave       TEXT NOT NULL,
    dono        TEXT NOT NULL DEFAULT '',
    situacao    TEXT NOT NULL DEFAULT '',
    organismo   TEXT,
    marcador    TEXT,
    identidade  REAL,
    consenso    TEXT NOT NULL DEFAULT '',            -- a sequencia que foi ao BLAST
    fatos       TEXT NOT NULL DEFAULT '{}',          -- o dicionario inteiro da amostra (leituras, ressalvas, evidencia), JSON
    gravado_em  TEXT NOT NULL,
    PRIMARY KEY (lote_id, chave)
)""",
    'CREATE INDEX IF NOT EXISTS ix_amostras_dono      ON amostras(dono)',
    'CREATE INDEX IF NOT EXISTS ix_amostras_organismo ON amostras(organismo)',
)


ESQUEMA = """
CREATE TABLE IF NOT EXISTS lotes (
    id              TEXT PRIMARY KEY,
    dono            TEXT NOT NULL DEFAULT '',
    nome            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL,
    n_arquivos      INTEGER NOT NULL DEFAULT 0,
    feito           INTEGER NOT NULL DEFAULT 0,
    total           INTEGER NOT NULL DEFAULT 0,
    etapa           TEXT NOT NULL DEFAULT '',
    erro            TEXT NOT NULL DEFAULT '',
    referencia      TEXT NOT NULL DEFAULT '',     -- banco escolhido para identificar (migracao 1)
    trabalhador     TEXT NOT NULL DEFAULT '',     -- quem reivindicou o lote (migracao 3)
    bytes_previstos INTEGER NOT NULL DEFAULT 0,   -- reserva de cota enquanto os arquivos sobem (migracao 4)
    criado_em       TEXT NOT NULL,
    iniciado_em     TEXT,
    terminado_em    TEXT
);
CREATE INDEX IF NOT EXISTS ix_lotes_status ON lotes(status, criado_em);
CREATE INDEX IF NOT EXISTS ix_lotes_dono   ON lotes(dono, criado_em);

-- sinal de vida de cada trabalhador: a tela so promete "alguem vai pegar" se alguem pulsou ha pouco
CREATE TABLE IF NOT EXISTS pulso (
    trabalhador TEXT PRIMARY KEY,
    visto_em    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS perfis (
    email        TEXT PRIMARY KEY,
    nome         TEXT NOT NULL DEFAULT '',
    laboratorio  TEXT NOT NULL DEFAULT '',
    instituicao  TEXT NOT NULL DEFAULT '',
    sobre        TEXT NOT NULL DEFAULT '',
    especies     TEXT NOT NULL DEFAULT '[]',     -- portfolio declarado (JSON de lista)
    marcadores   TEXT NOT NULL DEFAULT '[]',     -- idem
    foto         TEXT NOT NULL DEFAULT '',       -- nome do arquivo no volume
    atualizado   TEXT NOT NULL DEFAULT '',
    links        TEXT NOT NULL DEFAULT '[]',     -- enderecos de rede (migracao 2)
    chave        TEXT NOT NULL DEFAULT ''        -- id publico sorteado, vai para o HTML no lugar do e-mail (migracao 5)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_perfis_chave ON perfis(chave) WHERE chave <> '';

CREATE TABLE IF NOT EXISTS pedidos (
    id          TEXT PRIMARY KEY,
    de_email    TEXT NOT NULL,
    para_email  TEXT NOT NULL,
    itens       TEXT NOT NULL DEFAULT '[]',      -- do portfolio declarado do alvo
    outro       TEXT NOT NULL DEFAULT '',        -- o que nao estava na lista
    motivo      TEXT NOT NULL DEFAULT '',
    estado      TEXT NOT NULL DEFAULT 'pendente',
    resposta    TEXT NOT NULL DEFAULT '',
    criado      TEXT NOT NULL DEFAULT '',
    respondido  TEXT NOT NULL DEFAULT ''
);
-- a caixa de quem recebe e a consulta quente: roda em toda pagina, porque o contador de pendentes vive na lateral
CREATE INDEX IF NOT EXISTS idx_pedidos_para ON pedidos(para_email, estado);
CREATE INDEX IF NOT EXISTS idx_pedidos_de   ON pedidos(de_email, estado);

-- arvores pedidas sobre um lote ja pronto: tabela propria porque a mesma corrida rende mais de uma e o lote nao volta a `rodando`
CREATE TABLE IF NOT EXISTS arvores (
    id           TEXT PRIMARY KEY,
    lote_id      TEXT NOT NULL,
    dono         TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL,
    etapa        TEXT NOT NULL DEFAULT '',
    erro         TEXT NOT NULL DEFAULT '',
    resumo       TEXT NOT NULL DEFAULT '[]',     -- JSON com o que saiu por marcador; sobrevive ao expurgo dos .ab1
    trabalhador  TEXT NOT NULL DEFAULT '',
    criado_em    TEXT NOT NULL,
    iniciado_em  TEXT,
    terminado_em TEXT
);
CREATE INDEX IF NOT EXISTS ix_arvores_lote   ON arvores(lote_id, criado_em);
CREATE INDEX IF NOT EXISTS ix_arvores_status ON arvores(status, criado_em);

-- uma linha por amostra de lote PRONTO: o resultado consultavel por SQL sem abrir relatorio.json (migracao 7)
""" + ''.join(f'{comando};\n' for comando in ESQUEMA_DAS_AMOSTRAS)




def agora():

    #* o relogio de todas as tabelas: UTC, ISO 8601, sem fracao de segundo (2026-09-01T12:00:00+00:00)

    return datetime.now(timezone.utc).isoformat(timespec='seconds')




def colunas(con, tabela):

    #* o conjunto de nomes de coluna da tabela; vazio se ela nao existe

    return {linha[1] for linha in con.execute(f'PRAGMA table_info({tabela})')}




def tabela_existe(con, nome):

    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nome,)).fetchone() is not None




def ligar_wal(con):

    #* PRAGMA journal_mode=WAL, insistindo ate TEMPO_LIMITE: trocar o modo de um banco recem-criado pede trava exclusiva, e nessa troca
    #* o SQLite NAO consulta o busy_timeout. web + N trabalhadores subindo juntos num volume vazio morriam aqui com "database is locked"
    #! (medido na bancada: 1 em cada 4 arranques, no original e no novo antes disto). ja em WAL, o comando nao pede trava nenhuma

    prazo = time.monotonic() + TEMPO_LIMITE

    while True:

        try:
            con.execute('PRAGMA journal_mode=WAL')
            return

        except sqlite3.OperationalError as erro:

            if time.monotonic() >= prazo or not ('locked' in str(erro) or 'busy' in str(erro)):
                raise

            time.sleep(0.05)




#* ---- as migracoes -------------------------------------------------------------------------------------------
#! cada uma recebe a conexao (ja dentro de BEGIN IMMEDIATE) e altera o esquema. o numero e a versao a que o banco CHEGA.
#! nunca renumere e nunca edite uma que ja foi lancada: o banco em producao ja a aplicou e nao vai aplicar de novo.
#! `execute` um comando por vez, e NAO `executescript`: o executescript fecha a transacao aberta antes de rodar,
#! e a migracao deixaria de ser atomica com o PRAGMA user_version.


def migracao_1_referencia_no_lote(con):

    #* contra qual banco a corrida foi identificada. idempotente: bancos anteriores a este arquivo ja a tinham por um `if` avulso

    if 'referencia' not in colunas(con, 'lotes'):
        con.execute("ALTER TABLE lotes ADD COLUMN referencia TEXT NOT NULL DEFAULT ''")




def migracao_2_links_do_perfil(con):

    #* enderecos de rede do perfil (GitHub, ORCID, Lattes...)
    #! so se `perfis` JA existe: banco criado so pelo trabalhador nao a tem, e o ESQUEMA a cria depois ja com a coluna

    if tabela_existe(con, 'perfis') and 'links' not in colunas(con, 'perfis'):
        con.execute("ALTER TABLE perfis ADD COLUMN links TEXT NOT NULL DEFAULT '[]'")




def migracao_3_quem_reivindicou_o_lote(con):

    #* qual trabalhador esta com o lote na mao. sem isto, reiniciar um trabalhador reenfileirava o lote que o OUTRO estava montando

    if 'trabalhador' not in colunas(con, 'lotes'):
        con.execute("ALTER TABLE lotes ADD COLUMN trabalhador TEXT NOT NULL DEFAULT ''")




def migracao_4_bytes_previstos_do_lote(con):

    #* quantos bytes o lote DECLAROU (Content-Length) que traria: a reserva de cota mora no banco para ser transacional

    if 'bytes_previstos' not in colunas(con, 'lotes'):
        con.execute('ALTER TABLE lotes ADD COLUMN bytes_previstos INTEGER NOT NULL DEFAULT 0')




def migracao_5_pedidos_de_amostra(con):

    #* a tabela de pedidos entre laboratorios e a chave publica do perfil
    #! a parte que NAO e IF NOT EXISTS e o sorteio da chave de quem ja tinha perfil: sem ele, todo perfil antigo apareceria
    #! no /labs sem o botao de pedir amostra ate a pessoa entrar de novo — e o app nao manda e-mail avisando

    if tabela_existe(con, 'perfis'):

        if 'chave' not in colunas(con, 'perfis'):
            con.execute("ALTER TABLE perfis ADD COLUMN chave TEXT NOT NULL DEFAULT ''")

        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_perfis_chave ON perfis(chave) WHERE chave <> ''")

        sem_chave = [linha[0] for linha in con.execute("SELECT email FROM perfis WHERE chave=''")]

        for email in sem_chave:  #* linha a linha com o `secrets`, e nao randomblob do SQLite: uma origem so de aleatoriedade para auditar
            con.execute('UPDATE perfis SET chave=? WHERE email=?', (secrets.token_urlsafe(9), email))

        if sem_chave:
            REGISTRO.info('chave publica sorteada para %d perfil(is)', len(sem_chave))


    con.execute("""
        CREATE TABLE IF NOT EXISTS pedidos (
            id          TEXT PRIMARY KEY,
            de_email    TEXT NOT NULL,
            para_email  TEXT NOT NULL,
            itens       TEXT NOT NULL DEFAULT '[]',
            outro       TEXT NOT NULL DEFAULT '',
            motivo      TEXT NOT NULL DEFAULT '',
            estado      TEXT NOT NULL DEFAULT 'pendente',
            resposta    TEXT NOT NULL DEFAULT '',
            criado      TEXT NOT NULL DEFAULT '',
            respondido  TEXT NOT NULL DEFAULT ''
        )""")

    con.execute('CREATE INDEX IF NOT EXISTS idx_pedidos_para ON pedidos(para_email, estado)')

    con.execute('CREATE INDEX IF NOT EXISTS idx_pedidos_de ON pedidos(de_email, estado)')




def migracao_6_arvores_do_lote(con):

    #* a fila das arvores filogeneticas pedidas sobre um lote ja pronto

    con.execute("""
        CREATE TABLE IF NOT EXISTS arvores (
            id           TEXT PRIMARY KEY,
            lote_id      TEXT NOT NULL,
            dono         TEXT NOT NULL DEFAULT '',
            status       TEXT NOT NULL,
            etapa        TEXT NOT NULL DEFAULT '',
            erro         TEXT NOT NULL DEFAULT '',
            resumo       TEXT NOT NULL DEFAULT '[]',
            trabalhador  TEXT NOT NULL DEFAULT '',
            criado_em    TEXT NOT NULL,
            iniciado_em  TEXT,
            terminado_em TEXT
        )""")

    con.execute('CREATE INDEX IF NOT EXISTS ix_arvores_lote ON arvores(lote_id, criado_em)')

    con.execute('CREATE INDEX IF NOT EXISTS ix_arvores_status ON arvores(status, criado_em)')




def migracao_7_amostras_do_lote(con):

    #* as amostras de cada lote pronto no banco. decisao de 02/11/2026: os resultados ficam, sem prazo, com o consentimento dos usuarios

    for comando in ESQUEMA_DAS_AMOSTRAS:
        con.execute(comando)




MIGRACOES = (  #* (versao a que o banco chega, descricao para o log, funcao); em ordem, e a ordem e a numeracao
    (1, 'referencia gravada no lote', migracao_1_referencia_no_lote),
    (2, 'enderecos de rede no perfil', migracao_2_links_do_perfil),
    (3, 'qual trabalhador reivindicou o lote', migracao_3_quem_reivindicou_o_lote),
    (4, 'bytes previstos do lote, para a cota reservar', migracao_4_bytes_previstos_do_lote),
    (5, 'pedidos de amostra entre laboratorios', migracao_5_pedidos_de_amostra),
    (6, 'fila das arvores filogeneticas do lote', migracao_6_arvores_do_lote),
    (7, 'amostras e resultados de cada lote pronto', migracao_7_amostras_do_lote),
)

VERSAO_ALVO = MIGRACOES[-1][0]  #* a versao que um banco preparado tem; o ESQUEMA acima e o retrato do banco nesta versao




class BancoSqlite:

    #* o arquivo fila.sqlite3: abre, migra, faz backup e restaura. quem le e grava linhas sao as filas, os perfis e os pedidos.

    def __init__(self, caminho):

        self.caminho = Path(caminho)


    @contextlib.contextmanager
    def conectar(self):

        #* conexao pronta para a web: WAL (o trabalhador grava progresso enquanto a pagina le sem travar), Row por nome,
        #* autocommit (isolation_level=None) — quem quer transacao pede transacao()

        con = sqlite3.connect(self.caminho, timeout=TEMPO_LIMITE, isolation_level=None)

        con.row_factory = sqlite3.Row

        con.execute(f'PRAGMA busy_timeout={TEMPO_LIMITE * 1000}')

        ligar_wal(con)

        try:
            yield con

        finally:
            con.close()


    @contextlib.contextmanager
    def transacao(self):

        #* BEGIN IMMEDIATE ... COMMIT numa conexao propria: ler, decidir e gravar sem outro processo no meio.
        #! qualquer excecao dentro do with vira ROLLBACK e continua subindo; o banco fica como estava antes do with

        with self.conectar() as con:

            con.execute('BEGIN IMMEDIATE')

            try:
                yield con

            except BaseException:
                con.execute('ROLLBACK')
                raise

            con.execute('COMMIT')


    @property
    def versao(self):

        with self.conectar() as con:
            return int(con.execute('PRAGMA user_version').fetchone()[0])


    def preparar(self):

        #* cria o que falta e leva o banco a VERSAO_ALVO. seguro a cada arranque, de qualquer processo, quantas vezes for.
        #* devolve quantas migracoes rodaram (0 no banco novo e no banco ja em dia)

        self.caminho.parent.mkdir(parents=True, exist_ok=True)

        with self.conectar() as con:
            novo = not tabela_existe(con, 'lotes')

        if novo:  #* nasce inteiro e ja na versao alvo, numa transacao so: o segundo processo que chegar espera e nao encontra nada por fazer

            with self.conectar() as con:
                con.executescript(f'BEGIN IMMEDIATE;\n{ESQUEMA}\nPRAGMA user_version = {VERSAO_ALVO};\nCOMMIT;')

            REGISTRO.info('banco novo em %s, criado na versao %d', self.caminho, VERSAO_ALVO)

            return 0


        rodadas = self.migrar()

        with self.conectar() as con:
            con.executescript(ESQUEMA)  #* tudo IF NOT EXISTS: so faz a tabela ou o indice que este banco ainda nao tinha

        return rodadas


    def migrar(self):

        #* leva o banco ate VERSAO_ALVO dentro de UMA trava de escrita: backup antes da primeira alteracao e um SAVEPOINT por migracao.
        #! a migracao que falha e desfeita sozinha (ROLLBACK TO): as anteriores ficam gravadas, a versao fica na ultima que deu certo
        #! e a excecao sobe. quem esperava na trava (web + N trabalhadores sobem juntos) encontra o banco ja migrado e nao faz nada —
        #! um backup por atualizacao e cada migracao uma vez no log, seja quantos processos for. devolve quantas migracoes rodaram.

        falha = None

        rodadas = 0

        with self.transacao() as con:

            atual = int(con.execute('PRAGMA user_version').fetchone()[0])

            pendentes = [migracao for migracao in MIGRACOES if migracao[0] > atual]

            if not pendentes:
                return 0


            REGISTRO.info('banco %s na versao %d; aplicando %d migracao(oes) ate a %d', self.caminho, atual, len(pendentes), VERSAO_ALVO)

            self.fazer_backup()  #* dentro da trava: a copia e o banco exatamente como esta, e ninguem grava enquanto ela sai

            for numero, descricao, funcao in pendentes:

                con.execute(f'SAVEPOINT migracao_{int(numero)}')

                try:
                    funcao(con)
                    con.execute(f'PRAGMA user_version = {int(numero)}')  #* nao aceita parametro ligado; `numero` vem da constante deste arquivo

                except Exception as erro:
                    con.execute(f'ROLLBACK TO migracao_{int(numero)}')
                    con.execute(f'RELEASE migracao_{int(numero)}')
                    REGISTRO.exception('migracao %d (%s) falhou; o banco fica na versao %d', numero, descricao, con.execute('PRAGMA user_version').fetchone()[0])
                    falha = erro
                    break

                con.execute(f'RELEASE migracao_{int(numero)}')

                REGISTRO.info('migracao %d aplicada: %s', numero, descricao)

                rodadas += 1


        if falha is not None:  #* fora do with: o que deu certo ja esta gravado (COMMIT), e so agora a falha sobe
            raise falha

        return rodadas


    def fazer_backup(self):

        #* copia o banco em UM arquivo ao lado dele, pela API de backup do SQLite (nao `cp`: com WAL, copiar o arquivo com o servidor
        #* rodando perde as transacoes que ainda estao no -wal). devolve o caminho, ou None se ainda nao ha banco.

        if not self.caminho.exists():
            return None


        carimbo = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')

        destino = self.reservar_backup(carimbo)

        origem = sqlite3.connect(self.caminho)

        copia = sqlite3.connect(destino)

        try:
            origem.backup(copia)
            copia.execute('PRAGMA wal_checkpoint(TRUNCATE)')  #* o WAL entra no arquivo e o modo volta a DELETE: backup de um arquivo so
            copia.execute('PRAGMA journal_mode=DELETE')
            copia.commit()

        finally:  #! `with sqlite3.connect()` NAO fecha a conexao (so gerencia transacao); sem o close a copia ficava em WAL com -wal proprio
            copia.close()
            origem.close()


        REGISTRO.info('backup do banco em %s', destino)

        return destino


    def reservar_backup(self, carimbo):

        #* o nome do backup, ja criado vazio com O_EXCL: dois processos (ou duas migracoes no mesmo segundo) nunca escrevem no mesmo arquivo
        #! o carimbo tem resolucao de segundo: restaurar e migrar de novo no mesmo segundo NAO pode gravar por cima do backup que acabou de servir

        repetido = 1

        while True:

            sufixo = '' if repetido == 1 else f'-{repetido}'

            destino = self.caminho.with_name(f'{self.caminho.name}{SUFIXO_DO_BACKUP}{carimbo}{sufixo}')

            try:
                os.close(os.open(destino, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
                return destino

            except FileExistsError:
                repetido += 1


    def restaurar(self, backup):

        #* volta atras: o banco vira a copia. com o servidor PARADO — restaurar por baixo de um processo com o banco aberto e trocar o chao de quem anda.
        #! a ORDEM e o conserto: -wal e -shm saem ANTES da copia. na ordem inversa a proxima conexao morria com `disk I/O error`,
        #! porque o -wal que sobrava pertencia ao banco substituido e nao casava com o cabecalho do arquivo restaurado

        for sufixo in ('-wal', '-shm'):

            sobra = Path(f'{self.caminho}{sufixo}')

            if sobra.exists():
                sobra.unlink()


        shutil.copy2(backup, self.caminho)

        REGISTRO.warning('banco %s restaurado a partir de %s', self.caminho, backup)


    def __repr__(self):

        return f'BancoSqlite({str(self.caminho)!r})'




def main():

    from easycontig_web import configuracao

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')

    digitado = input('Caminho do fila.sqlite3 (enter = o da configuracao, EASYCONTIG_DATA_DIR): ').strip()

    banco = BancoSqlite(digitado) if digitado else BancoSqlite(configuracao.carregar().caminho_do_sqlite)

    print(f'\n{banco!r}')

    if not banco.caminho.exists():
        print('  o arquivo nao existe: preparar() vai cria-lo do zero, na versao', VERSAO_ALVO)

    else:

        with banco.conectar() as con:

            tabelas = [linha[0] for linha in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

            print(f'  versao {banco.versao} (alvo {VERSAO_ALVO}); {len(tabelas)} tabela(s)')

            for tabela in tabelas:
                print(f'    {tabela}: {con.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]} linha(s)')


        pendentes = [f'{numero} ({descricao})' for numero, descricao, funcao in MIGRACOES if numero > banco.versao]

        print('  migracoes pendentes: ' + (', '.join(pendentes) if pendentes else 'nenhuma'))

        backups = sorted(banco.caminho.parent.glob(f'{banco.caminho.name}{SUFIXO_DO_BACKUP}*'))

        if backups:
            print(f'  {len(backups)} backup(s) ao lado; o mais novo: {backups[-1].name}')


    if input('\nPreparar agora (criar o que falta e migrar)? [s/N] ').strip().lower() == 's':
        print(f'{banco.preparar()} migracao(oes) rodaram; versao {banco.versao}')




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
