"""
fila.py — a fila de lotes, em SQLite.

Por que existe (ADR 0050): o processamento em si é barato — 0,65 s de CPU por
amostra, medido. O que derruba um servidor não é o BLAST, é manter a requisição
HTTP ABERTA os ~26 s que um lote de 40 leva: vinte pessoas e acabaram as
conexões, com a CPU quase parada. Então a requisição só ENFILEIRA e devolve um
protocolo; quem trabalha é um processo à parte.

SQLite e não Redis de propósito: uma dependência a menos para a universidade
manter, e o volume real (algumas corridas por mês) não chega perto de justificar
outro serviço. A troca por Redis/RQ é local — só este arquivo muda.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# RECEBENDO existe por causa de uma corrida real: o lote era inserido como
# `na_fila` ANTES de os arquivos terminarem de subir, o trabalhador (que varre a
# fila a cada 0,25 s) pegava no meio do upload de 26 MB e montava só o que já
# tinha caído no disco. Resultado observado: relatório com 33 das 40 amostras e
# NADA avisando da falta — o pior tipo de defeito, porque parece um resultado.
# Agora o lote só entra na fila depois do último byte gravado.
RECEBENDO, NA_FILA, RODANDO, PRONTO, FALHOU = (
    "recebendo", "na_fila", "rodando", "pronto", "falhou")

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS lotes (
    id            TEXT PRIMARY KEY,
    dono          TEXT NOT NULL DEFAULT '',
    nome          TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    n_arquivos    INTEGER NOT NULL DEFAULT 0,
    feito         INTEGER NOT NULL DEFAULT 0,
    total         INTEGER NOT NULL DEFAULT 0,
    etapa         TEXT NOT NULL DEFAULT '',
    erro          TEXT NOT NULL DEFAULT '',
    referencia    TEXT NOT NULL DEFAULT '',   -- banco escolhido para identificar
    criado_em     TEXT NOT NULL,
    iniciado_em   TEXT,
    terminado_em  TEXT
);
CREATE INDEX IF NOT EXISTS ix_lotes_status ON lotes(status, criado_em);
CREATE INDEX IF NOT EXISTS ix_lotes_dono   ON lotes(dono, criado_em);
"""


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conectar(caminho: Path):
    con = sqlite3.connect(caminho, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    # WAL: o worker escreve progresso enquanto a página web lê o status. Sem
    # isso, o SQLite serializa leitor e escritor e a página trava a cada polling.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
    finally:
        con.close()


def criar_esquema(caminho: Path) -> None:
    """Cria as tabelas se faltarem e leva o banco à versão de esquema atual.

    As mudanças de esquema saíram daqui e foram para `migracoes.py` (2026-08-06):
    o `if` que conferia `PRAGMA table_info` para descobrir se a coluna
    `referencia` já existia funcionou para UMA coluna, mas não dizia em que
    versão o banco estava, não tinha ordem declarada e não deixava por onde
    voltar atrás. Agora o `PRAGMA user_version` responde as três coisas, e há
    backup automático antes da primeira alteração.

    Continua seguro chamar a cada arranque, de qualquer processo: `IF NOT
    EXISTS` para as tabelas e, nas migrações, nada roda duas vezes.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with conectar(caminho) as con:
        con.executescript(_ESQUEMA)
    # Importado aqui, e não no topo, porque `migracoes` importa `conectar` deste
    # módulo — no topo seria import circular.
    from . import migracoes
    migracoes.aplicar(caminho)


def novo_lote(caminho: Path, *, dono: str, nome: str, n_arquivos: int,
              referencia: str = "") -> str:
    """Abre o lote em RECEBENDO — invisível para o trabalhador até liberar.

    `referencia` é o banco escolhido para identificar. Fica GRAVADO no lote, e
    não lido da configuração na hora de rodar: assim o relatório continua
    interpretável mesmo que a instalação mude de banco depois — a pergunta "o
    que este resultado comparou contra o quê" tem resposta no próprio registro.
    """
    lote_id = secrets.token_urlsafe(9)
    with conectar(caminho) as con:
        con.execute(
            "INSERT INTO lotes (id, dono, nome, status, n_arquivos, criado_em,"
            " referencia) VALUES (?,?,?,?,?,?,?)",
            (lote_id, dono, nome, RECEBENDO, n_arquivos, _agora(), referencia),
        )
    return lote_id


def liberar_para_fila(caminho: Path, lote_id: str, n_arquivos: int) -> None:
    """Último byte gravado: agora sim o trabalhador pode pegar.

    `n_arquivos` é reescrito aqui com o que REALMENTE foi gravado, e não com o
    que o navegador prometeu no início — é esse número que o executor confere
    contra os arquivos que acha em disco.
    """
    with conectar(caminho) as con:
        con.execute("UPDATE lotes SET status=?, n_arquivos=? WHERE id=? AND status=?",
                    (NA_FILA, n_arquivos, lote_id, RECEBENDO))


def pegar(caminho: Path, lote_id: str) -> dict | None:
    with conectar(caminho) as con:
        r = con.execute("SELECT * FROM lotes WHERE id=?", (lote_id,)).fetchone()
    return dict(r) if r else None


def listar(caminho: Path, *, dono: str | None = None, limite: int = 50) -> list[dict]:
    sql = "SELECT * FROM lotes"
    args: list = []
    if dono is not None:
        sql += " WHERE dono=?"
        args.append(dono)
    sql += " ORDER BY criado_em DESC LIMIT ?"
    args.append(limite)
    with conectar(caminho) as con:
        return [dict(r) for r in con.execute(sql, args)]


def reivindicar(caminho: Path) -> dict | None:
    """Marca o lote mais antigo da fila como RODANDO e devolve — ou None.

    O UPDATE condicional é o que permite mais de um trabalhador na mesma fila:
    quem perder a corrida atualiza 0 linhas e tenta o próximo. Sem isso, dois
    workers montariam o mesmo lote e gravariam por cima um do outro.
    """
    with conectar(caminho) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            r = con.execute(
                "SELECT * FROM lotes WHERE status=? ORDER BY criado_em LIMIT 1",
                (NA_FILA,),
            ).fetchone()
            if not r:
                con.execute("COMMIT")
                return None
            n = con.execute(
                "UPDATE lotes SET status=?, iniciado_em=? WHERE id=? AND status=?",
                (RODANDO, _agora(), r["id"], NA_FILA),
            ).rowcount
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return dict(r) if n == 1 else None


def progresso(caminho: Path, lote_id: str, feito: int, total: int, etapa: str) -> None:
    with conectar(caminho) as con:
        con.execute("UPDATE lotes SET feito=?, total=?, etapa=? WHERE id=?",
                    (feito, total, etapa[:120], lote_id))


def concluir(caminho: Path, lote_id: str) -> None:
    with conectar(caminho) as con:
        con.execute("UPDATE lotes SET status=?, terminado_em=?, etapa='' WHERE id=?",
                    (PRONTO, _agora(), lote_id))


def falhar(caminho: Path, lote_id: str, erro: str) -> None:
    with conectar(caminho) as con:
        con.execute(
            "UPDATE lotes SET status=?, terminado_em=?, erro=?, etapa='' WHERE id=?",
            (FALHOU, _agora(), erro[:2000], lote_id))


def reenfileirar_orfaos(caminho: Path) -> int:
    """Devolve à fila os lotes deixados em RODANDO por um worker que morreu.

    Chamado quando o worker sobe. Sem isso, uma queda de energia (o cenário que
    motivou pôr isto numa VPS com no-break) deixaria o lote preso em "rodando"
    para sempre, e a página do usuário giraria eternamente.

    RECEBENDO NÃO é reenfileirado: um upload interrompido deixou a pasta pela
    metade, e mandá-la para a fila reproduziria exatamente o defeito das 33 de
    40 amostras. Lote sem upload completo é lote perdido, e tem que dizer isso.
    """
    with conectar(caminho) as con:
        n = con.execute(
            "UPDATE lotes SET status=?, etapa='' WHERE status=?", (NA_FILA, RODANDO)
        ).rowcount
        con.execute(
            "UPDATE lotes SET status=?, erro=? WHERE status=?",
            (FALHOU, "o envio dos arquivos foi interrompido; envie a corrida de novo",
             RECEBENDO))
        return n
