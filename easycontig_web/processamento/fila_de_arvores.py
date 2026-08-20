#? FILA DE ÁRVORES — Decisão sobre reaproveitar a fila que já existe 19/08/2026
#!
#! 1. Montar a árvore é MCMC: 6 s numa corrida pequena, minutos numa grande. É
#!    o mesmo motivo que tirou o lote da requisição HTTP (ADR 0050) — então ela
#!    entra pela mesma porta, e não por uma exceção.
#! 2. Tabela própria, `arvores`: o lote já terminou quando o pedido nasce, e uma
#!    corrida rende uma árvore POR MARCADOR (a F13719 deu três).
#! 3. O mesmo trabalhador atende as duas filas. Um processo a mais para a
#!    universidade manter seria pagar com operação o que não custa nada em código.
#! 4. Lote primeiro, árvore depois: quem está esperando o resultado da corrida
#!    espera mais do que quem pediu uma análise extra sobre corrida já pronta.
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .fila_de_lotes import _agora, conectar

NA_FILA, RODANDO, PRONTO, FALHOU = "na_fila", "rodando", "pronto", "falhou"
ATIVOS = (NA_FILA, RODANDO)


def novo_pedido(caminho: Path, *, lote_id: str, dono: str) -> str:
    """Enfileira uma árvore para este lote. Devolve o id do pedido.

    ⚠️ Recusa se já houver pedido ATIVO para o mesmo lote, na mesma transação
    que insere. Sem isso, dois cliques no botão — ou um duplo-clique, que é o
    que acontece de verdade quando a página demora a responder — põem dois
    trabalhadores montando na MESMA pasta de saída, gravando o NEXUS um por
    cima do outro. É a corrida do `novo_lote`, no mesmo lugar e pelo mesmo
    motivo; a diferença é que aqui a chave natural existe (o lote), então dá
    para conferir direto.
    """
    pedido_id = secrets.token_urlsafe(9)
    with conectar(caminho) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            marcas = ",".join("?" * len(ATIVOS))
            ja = con.execute(
                f"SELECT id FROM arvores WHERE lote_id=? AND status IN ({marcas})",
                (lote_id, *ATIVOS)).fetchone()
            if ja:
                con.execute("COMMIT")
                return ja["id"]
            con.execute(
                "INSERT INTO arvores (id, lote_id, dono, status, criado_em) "
                "VALUES (?,?,?,?,?)",
                (pedido_id, lote_id, dono, NA_FILA, _agora()))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return pedido_id


def pegar(caminho: Path, pedido_id: str) -> dict | None:
    with conectar(caminho) as con:
        r = con.execute("SELECT * FROM arvores WHERE id=?", (pedido_id,)).fetchone()
    return dict(r) if r else None


def do_lote(caminho: Path, lote_id: str) -> dict | None:
    """O pedido mais recente deste lote — é o que a página do lote mostra."""
    with conectar(caminho) as con:
        r = con.execute(
            "SELECT * FROM arvores WHERE lote_id=? ORDER BY criado_em DESC LIMIT 1",
            (lote_id,)).fetchone()
    return dict(r) if r else None


def reivindicar(caminho: Path, trabalhador: str = "") -> dict | None:
    """Marca o pedido mais antigo como RODANDO e devolve — ou None.

    Mesmo UPDATE condicional da fila de lotes, e pelo mesmo motivo: é ele que
    deixa dois trabalhadores dividirem a fila sem montarem o mesmo pedido.
    """
    with conectar(caminho) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            r = con.execute(
                "SELECT * FROM arvores WHERE status=? ORDER BY criado_em LIMIT 1",
                (NA_FILA,)).fetchone()
            if not r:
                con.execute("COMMIT")
                return None
            n = con.execute(
                "UPDATE arvores SET status=?, iniciado_em=?, trabalhador=? "
                "WHERE id=? AND status=?",
                (RODANDO, _agora(), trabalhador, r["id"], NA_FILA)).rowcount
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return dict(r) if n == 1 else None


def _dono_confere(trabalhador: str) -> tuple[str, tuple]:
    return (" AND trabalhador=?", (trabalhador,)) if trabalhador else ("", ())


def etapa(caminho: Path, pedido_id: str, texto: str, trabalhador: str = "") -> bool:
    """Em que marcador o MCMC está agora — é o que a tela mostra enquanto roda."""
    cond, extra = _dono_confere(trabalhador)
    with conectar(caminho) as con:
        return con.execute(
            f"UPDATE arvores SET etapa=? WHERE id=?{cond}",
            (texto[:120], pedido_id, *extra)).rowcount == 1


def concluir(caminho: Path, pedido_id: str, resumo: list[dict],
             trabalhador: str = "") -> bool:
    cond, extra = _dono_confere(trabalhador)
    with conectar(caminho) as con:
        return con.execute(
            f"UPDATE arvores SET status=?, terminado_em=?, etapa='', resumo=? "
            f"WHERE id=?{cond}",
            (PRONTO, _agora(), json.dumps(resumo, ensure_ascii=False),
             pedido_id, *extra)).rowcount == 1


def falhar(caminho: Path, pedido_id: str, erro: str,
           trabalhador: str = "") -> bool:
    cond, extra = _dono_confere(trabalhador)
    with conectar(caminho) as con:
        return con.execute(
            f"UPDATE arvores SET status=?, terminado_em=?, erro=?, etapa='' "
            f"WHERE id=?{cond}",
            (FALHOU, _agora(), erro[:2000], pedido_id, *extra)).rowcount == 1


def resumo_lido(pedido: dict) -> list[dict]:
    """O `resumo` já como lista. JSON quebrado vira lista vazia, não exceção:
    a tela do pedido não pode morrer por causa do campo de exibição."""
    try:
        dados = json.loads(pedido.get("resumo") or "[]")
    except (ValueError, TypeError):
        return []
    return dados if isinstance(dados, list) else []


def reenfileirar_orfaos(caminho: Path, vivos: set[str], eu: str = "") -> int:
    """Devolve à fila os pedidos deixados em RODANDO por trabalhador morto.

    `vivos` vem de `fila_de_lotes.trabalhadores_vivos` — a mesma tabela de
    pulso, porque é o mesmo processo que atende as duas filas. Um pedido sem
    dono registrado só volta se NINGUÉM estiver vivo, pela mesma regra da fila
    de lotes: na dúvida não se toca no trabalho alheio.
    """
    vivos = set(vivos) - {eu}

    def orfao(dono: str) -> bool:
        if dono:
            return dono not in vivos      # o dono existe: só é órfão se morreu
        return not vivos                  # sem dono: só se ninguém está vivo

    with conectar(caminho) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            rodando = con.execute(
                "SELECT id, trabalhador FROM arvores WHERE status=?",
                (RODANDO,)).fetchall()
            orfaos = [r["id"] for r in rodando if orfao(r["trabalhador"])]
            n = 0
            if orfaos:
                marcas = ",".join("?" * len(orfaos))
                n = con.execute(
                    f"UPDATE arvores SET status=?, etapa='', trabalhador='' "
                    f"WHERE id IN ({marcas}) AND status=?",
                    (NA_FILA, *orfaos, RODANDO)).rowcount
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return n
