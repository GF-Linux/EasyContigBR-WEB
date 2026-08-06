"""
migracoes.py — como o esquema do banco muda entre versões, e como voltar atrás.

Por que existe (2026-08-06): até aqui o esquema nascia de `CREATE TABLE IF NOT
EXISTS` mais um `if` avulso que conferia `PRAGMA table_info` para descobrir se a
coluna `referencia` já existia. Aquilo funcionou para UMA coluna. O problema não
é elegância — são três coisas concretas que faltavam:

  1. **Não havia como saber se uma mudança já foi aplicada.** Cada alteração
     futura precisaria do seu próprio `if` inspecionando o banco, e a chance de
     dois deles se contradizerem cresce com o número.
  2. **Não havia como voltar atrás.** Se uma atualização corromper o banco, a
     única saída era o backup que ninguém tinha feito.
  3. **Não havia ordem declarada.** Migração que depende de outra não tinha como
     dizer isso.

O mecanismo é o `PRAGMA user_version` do próprio SQLite: um inteiro guardado no
cabeçalho do arquivo, sem tabela extra e sem dependência nova. O banco diz em
que versão está; aplicamos só o que falta, em ordem, dentro de uma transação.

**Compatível com o que já existe em produção.** Um banco criado antes disto tem
`user_version = 0` e já pode ter a coluna `referencia` (pelo `if` antigo) ou
não. Por isso a migração 1 é escrita para ser idempotente: ela confere antes de
alterar. Rodar num banco novo e num banco antigo dá o mesmo resultado.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .fila import conectar

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


MIGRACOES: list[tuple[int, str, object]] = [
    (1, "referencia gravada no lote", _v1_referencia_no_lote),
    (2, "endereços de rede no perfil", _v2_links_do_perfil),
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
