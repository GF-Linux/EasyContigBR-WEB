#? ESQUEMA E MIGRAÇÕES — Decisão sobre versionar o banco 06/08/2026
#!
#! 1. Como o esquema do banco muda entre versões, e como voltar atrás.
#! 2. Antes o esquema nascia de `CREATE TABLE IF NOT EXISTS` mais um `if` avulso
#!    olhando `PRAGMA table_info`. Funcionou para UMA coluna.
#! 3. O que faltava: saber se uma mudança já foi aplicada, sem cada alteração
#!    futura precisar do próprio `if` inspecionando o banco.
#! 4. Migração que falha NÃO deixa o banco no meio do caminho: ou aplica
#!    inteira, ou o banco continua na versão anterior.
from __future__ import annotations

import logging
import secrets
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..processamento.fila_de_lotes import conectar

log = logging.getLogger("easycontig.migracoes")


def _colunas(con: sqlite3.Connection, tabela: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({tabela})")}


# --------------------------------------------------------------- as migrações
#
# Cada uma recebe a conexão e altera o esquema. O número é a versão a que o
# banco CHEGA depois de aplicá-la. Nunca renumere e nunca edite uma que já foi
# lançada: um banco em produção já a aplicou e não a aplicará de novo.


def _v1_referencia_no_lote(con: sqlite3.Connection) -> None:
    """A coluna que guarda contra qual banco a corrida foi identificada.

    Existia antes como um `if` no `criar_esquema`. Continua idempotente porque
    bancos que passaram por aquele código já a têm.
    """
    if "referencia" not in _colunas(con, "lotes"):
        con.execute("ALTER TABLE lotes ADD COLUMN referencia TEXT NOT NULL DEFAULT ''")


def _v2_links_do_perfil(con: sqlite3.Connection) -> None:
    """Endereços de rede do perfil (GitHub, ORCID, Lattes…).

    ⚠️ Só altera se a tabela JÁ existe. Num banco novo, `perfil.criar_esquema()`
    roda DEPOIS desta migração (e o `trabalhador` nem a chama), e o `_ESQUEMA` de
    lá já traz a coluna — então não há o que migrar. Em banco antigo, a coluna é
    acrescentada aqui. As duas ordens chegam ao mesmo lugar, que é o que uma
    migração precisa garantir.
    """
    existe = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='perfis'").fetchone()
    if existe and "links" not in _colunas(con, "perfis"):
        con.execute("ALTER TABLE perfis ADD COLUMN links TEXT NOT NULL DEFAULT '[]'")


def _v3_quem_reivindicou_o_lote(con: sqlite3.Connection) -> None:
    """Qual trabalhador está com este lote na mão.

    Nasceu de um defeito confirmado em 2026-08-06: `reenfileirar_orfaos` roda no
    arranque do trabalhador e devolvia à fila **todo** lote em `rodando`, sem
    olhar se alguém ainda estava com ele. O `docker-compose.yml` sobe
    `replicas: ${TRABALHADORES:-2}` — então subir o segundo trabalhador, ou
    reiniciar um deles, reenfileirava o lote que o outro estava montando naquele
    instante. Dois processos na mesma pasta, gravando um por cima do outro.

    Sem esta coluna não dá para distinguir "o dono morreu" de "o dono está
    trabalhando": as duas situações são idênticas na tabela.
    """
    if "trabalhador" not in _colunas(con, "lotes"):
        con.execute("ALTER TABLE lotes ADD COLUMN trabalhador TEXT NOT NULL DEFAULT ''")



def _v4_bytes_previstos_do_lote(con: sqlite3.Connection) -> None:
    """Quantos bytes o lote DECLAROU que traria, para a cota poder reservar.

    Nasceu do achado L3 de 2026-08-08. `cotas.situacao` mede o disco, e disco só
    conta byte que já chegou — então dez `POST /lotes` simultâneos da mesma
    conta liam o mesmo `usados` **antes** de qualquer um gravar, e os dez
    passavam. Contra uma cota de 2 GiB, ~3 GB entravam. O teto transacional de
    lotes ativos limitava o estrago a ~1,5×, mas por acidente, não por desenho.

    A reserva mora no BANCO e não no disco porque é isso que a torna
    transacional: ela aparece para a transação seguinte no instante do INSERT,
    que é justamente a janela onde as dez requisições se cruzavam.

    O valor vem do `Content-Length` da requisição — o mesmo cabeçalho que o teto
    de corpo do H1 já usa. Não é exato (traz a moldura do multipart junto), e
    não precisa ser: reserva superestimada erra para o lado seguro.
    """
    if "bytes_previstos" not in _colunas(con, "lotes"):
        con.execute("ALTER TABLE lotes ADD COLUMN "
                    "bytes_previstos INTEGER NOT NULL DEFAULT 0")


def _v5_pedidos_de_amostra(con: sqlite3.Connection) -> None:
    """A tabela de pedidos entre laboratórios, e a chave pública do perfil.

    A tabela nasce aqui **e** em `pedidos._ESQUEMA` (`IF NOT EXISTS` nos dois),
    pelo mesmo arranjo da migração 2: num banco novo a migração roda antes de o
    módulo criar o esquema, num banco antigo é o contrário, e as duas ordens
    precisam chegar ao mesmo lugar.

    ⚠️ **A parte que não é `IF NOT EXISTS` é o preenchimento da `chave`.** Ela é
    o identificador que vai para o HTML no lugar do e-mail (ver o cabeçalho de
    `pedidos.py`), e é sorteada — logo, quem já tem linha em `perfis` não ganha
    uma por padrão de coluna. Sem este laço, todo perfil anterior a esta versão
    apareceria no `/labs` **sem botão de pedir amostra** até a pessoa entrar de
    novo; e como este app não manda e-mail, ninguém seria avisado de que era só
    isso que faltava. É o mesmo gênero da consequência anotada na ADR 0066
    ("quem já entrou antes não aparece até entrar de novo") — que ali foi aceita
    por não haver de onde tirar o dado, e aqui é evitável, porque a chave não
    depende de dado nenhum: é sorteio.

    O `UPDATE` é linha a linha, e não um `randomblob` do SQLite, porque a chave
    precisa sair do mesmo `secrets` que gera as demais — um dia alguém audita o
    formato e não pode encontrar duas origens de aleatoriedade.
    """
    existe = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='perfis'").fetchone()
    if existe and "chave" not in _colunas(con, "perfis"):
        con.execute("ALTER TABLE perfis ADD COLUMN chave TEXT NOT NULL DEFAULT ''")
    if existe:
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_perfis_chave "
                    "ON perfis(chave) WHERE chave <> ''")
        sem = [r[0] for r in con.execute(
            "SELECT email FROM perfis WHERE chave=''")]
        for email in sem:
            con.execute("UPDATE perfis SET chave=? WHERE email=?",
                        (secrets.token_urlsafe(9), email))
        if sem:
            log.info("chave pública sorteada para %d perfil(is)", len(sem))

    # ⚠️ `execute` uma a uma, e NÃO `executescript`. O `executescript` do módulo
    # `sqlite3` faz COMMIT do que estiver pendente antes de rodar o script — e
    # esta função roda dentro do `BEGIN IMMEDIATE` de `aplicar()`. Escrita com
    # `executescript`, a migração fecharia sozinha a transação que existe para
    # que ela seja atômica com o `PRAGMA user_version`, e um erro depois deste
    # ponto deixaria o banco com a tabela criada e a versão antiga — que é
    # exatamente o estado que este módulo foi escrito para nunca produzir.
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
    con.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_para "
                "ON pedidos(para_email, estado)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_de "
                "ON pedidos(de_email, estado)")


def _v6_arvores_do_lote(con: sqlite3.Connection) -> None:
    """A fila das árvores filogenéticas pedidas sobre um lote já pronto.

    Tabela PRÓPRIA, e não colunas em `lotes`, por três motivos que só ficaram
    claros com o fluxo na mão:

    * a árvore é **pedida depois**, sobre um lote que já terminou — o lote volta
      a ter trabalho pendente sem voltar a `rodando`, e mexer no `status` dele
      confundiria a página, a cota e o `reenfileirar_orfaos`;
    * a mesma corrida rende **mais de uma árvore** (a F13719 deu três: 16S,
      groEL e dsb), e um lote não tem lugar para três resultados;
    * pedir de novo com outro parâmetro é um pedido NOVO, e o anterior continua
      valendo — histórico que uma coluna sobrescrita perderia.

    `resumo` guarda o JSON com o que saiu por marcador (taxa, colunas aparadas,
    identidade, ASDSF, avisos). Fica no banco porque é o que a tela mostra sem
    abrir arquivo nenhum, e porque sobrevive ao expurgo dos `.ab1`.
    """
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
    con.execute("CREATE INDEX IF NOT EXISTS ix_arvores_lote "
                "ON arvores(lote_id, criado_em)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_arvores_status "
                "ON arvores(status, criado_em)")


MIGRACOES: list[tuple[int, str, object]] = [
    (1, "referencia gravada no lote", _v1_referencia_no_lote),
    (2, "endereços de rede no perfil", _v2_links_do_perfil),
    (3, "qual trabalhador reivindicou o lote", _v3_quem_reivindicou_o_lote),
    (4, "bytes previstos do lote, para a cota reservar", _v4_bytes_previstos_do_lote),
    (5, "pedidos de amostra entre laboratórios", _v5_pedidos_de_amostra),
    (6, "fila das árvores filogenéticas do lote", _v6_arvores_do_lote),
]

VERSAO_ALVO = max(n for n, _d, _f in MIGRACOES) if MIGRACOES else 0


# ------------------------------------------------------------------- execução
def versao(sqlite_path: Path) -> int:
    with conectar(sqlite_path) as con:
        return int(con.execute("PRAGMA user_version").fetchone()[0])


def fazer_backup(sqlite_path: Path) -> Path | None:
    """Copia o banco antes de mexer nele. Devolve o caminho, ou None se não há
    banco ainda.

    Usa a API de backup do próprio SQLite, e não `cp`: com WAL ligado — que é o
    nosso caso — copiar o arquivo com o servidor rodando pode capturar um estado
    sem as transações que ainda estão no `-wal`, e o backup pareceria íntegro
    sendo incompleto. É o tipo de defeito que só aparece no dia em que se precisa
    do backup.
    """
    origem = Path(sqlite_path)
    if not origem.exists():
        return None
    carimbo = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destino = origem.with_name(f"{origem.name}.antes-de-migrar-{carimbo}")

    # ⚠️ `with sqlite3.connect(...)` NÃO fecha a conexão — ele gerencia a
    # transação. Escrito assim (que foi como isto nasceu), as duas conexões
    # ficavam abertas e o backup ficava em modo WAL com `-wal`/`-shm` próprios:
    # o arquivo entregue como "o backup" era só uma parte dele, e restaurar
    # copiando um arquivo só dava `disk I/O error`. Daí o try/finally.
    src = sqlite3.connect(origem)
    dst = sqlite3.connect(destino)
    try:
        src.backup(dst)
        # O backup sai como UM arquivo: o WAL é integrado e o modo volta para
        # `delete`. Backup que depende de três arquivos é backup que alguém vai
        # copiar pela metade no dia em que precisar dele.
        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst.execute("PRAGMA journal_mode=DELETE")
        dst.commit()
    finally:
        dst.close()
        src.close()
    log.info("backup do banco em %s", destino)
    return destino


def aplicar(sqlite_path: Path) -> int:
    """Leva o banco até `VERSAO_ALVO`. Devolve quantas migrações rodaram.

    Faz backup antes da primeira alteração e só dele — banco já na versão alvo
    não gera cópia, senão todo arranque do servidor deixaria um arquivo novo no
    volume.
    """
    atual = versao(sqlite_path)
    pendentes = [m for m in MIGRACOES if m[0] > atual]
    if not pendentes:
        return 0

    log.info("banco na versão %d; aplicando %d migração(ões) até a %d",
             atual, len(pendentes), VERSAO_ALVO)
    fazer_backup(sqlite_path)

    rodadas = 0
    for numero, descricao, funcao in sorted(pendentes):
        with conectar(sqlite_path) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                funcao(con)
                # `user_version` não aceita parâmetro ligado; `numero` é inteiro
                # vindo da constante deste módulo, nunca de entrada externa.
                con.execute(f"PRAGMA user_version = {int(numero)}")
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                log.exception("migração %d (%s) falhou; banco continua na %d",
                              numero, descricao, versao(sqlite_path))
                raise
        log.info("migração %d aplicada: %s", numero, descricao)
        rodadas += 1
    return rodadas


def restaurar(backup: Path, sqlite_path: Path) -> None:
    """Volta atrás. Existe para que 'poder reverter' seja um comando, e não uma
    reconstrução de memória no meio de um problema.

    ⚠️ A ORDEM É O CONSERTO. Os arquivos `-wal`/`-shm` saem ANTES da cópia, não
    depois. Escrito na ordem inversa — que foi como isto nasceu — a próxima
    conexão morria com `disk I/O error`: o `-wal` que sobrava pertencia ao banco
    substituído e não casava com o cabeçalho do arquivo restaurado. O teste
    `test_restaurar_devolve_o_banco_anterior` existe por causa disso.

    O servidor precisa estar parado para chamar isto. Restaurar por baixo de um
    processo com o banco aberto é trocar o chão de quem está andando.
    """
    for sufixo in ("-wal", "-shm"):
        sobra = Path(str(sqlite_path) + sufixo)
        if sobra.exists():
            sobra.unlink()
    shutil.copy2(backup, sqlite_path)
    log.warning("banco restaurado a partir de %s", backup)
