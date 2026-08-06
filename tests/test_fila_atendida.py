"""
"Na fila" é uma promessa: alguém vai pegar. Só pode ser feita se houver alguém.

2026-08-06: o autor mandou um par e o cromatograma nunca apareceu. O lote ficou
em `na_fila` **para sempre** — nenhum trabalhador estava atendendo aquela fila
(os que rodavam eram sobras de sessões minhas, apontando para `data_dir` de
teste em `/tmp`). A página seguia se atualizando sozinha e dizendo "Na fila.
Esta página se atualiza sozinha", que promete o que ninguém ia cumprir.

Mesmo gênero da mensagem de envio interrompido de 05/08 e do "o Google não
respondeu" de hoje: a tela afirmando um fato que não verificou.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from easycontig_web import fila


@pytest.fixture()
def banco(tmp_path):
    p = tmp_path / "fila.sqlite3"
    fila.criar_esquema(p)
    return p


# ── a pergunta ───────────────────────────────────────────────────────────────
def test_fila_sem_trabalhador_nenhum_nao_esta_atendida(banco):
    """O caso que aconteceu: ninguém NUNCA bateu pulso neste banco."""
    assert fila.fila_atendida(banco) is False


def test_pulso_recente_significa_atendida(banco):
    fila.pulsar(banco, "deck:123")
    assert fila.fila_atendida(banco) is True


def test_pulso_velho_nao_conta(banco):
    fila.pulsar(banco, "deck:123")
    tarde = datetime.now(timezone.utc) + timedelta(seconds=fila.PULSO_VELHO + 30)
    assert fila.fila_atendida(banco, agora=tarde) is False


def test_um_trabalhador_vivo_basta(banco):
    """Vale o pulso MAIS RECENTE: um worker morto ao lado de um vivo não pode
    fazer a fila parecer abandonada."""
    fila.pulsar(banco, "morto:1")
    fila.pulsar(banco, "vivo:2")
    quase = datetime.now(timezone.utc) + timedelta(seconds=fila.PULSO_VELHO - 5)
    assert fila.fila_atendida(banco, agora=quase) is True


def test_pulsar_duas_vezes_nao_duplica_o_trabalhador(banco):
    fila.pulsar(banco, "deck:123")
    fila.pulsar(banco, "deck:123")
    with fila.conectar(banco) as con:
        assert con.execute("SELECT COUNT(*) FROM pulso").fetchone()[0] == 1


def test_registro_ilegivel_erra_para_o_lado_de_atendida(banco):
    """Anunciar abandono por engano assusta à toa, e o lote sendo processado
    desmente o aviso em segundos. O caso que importa — pulso NENHUM — não
    depende de interpretar data alguma."""
    with fila.conectar(banco) as con:
        con.execute("INSERT INTO pulso VALUES ('x','isto não é uma data')")
    assert fila.fila_atendida(banco) is True


# ── a tela ───────────────────────────────────────────────────────────────────
@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
    from easycontig_web import main
    importlib.reload(main)
    c = TestClient(main.app)
    c.post("/entrar", data={"email": "gustavo@ufrrj.br"}, follow_redirects=False)
    c._main = main
    return c


def _lote_na_fila(cliente) -> str:
    caminho = cliente._main.cfg.sqlite_path
    lote_id = fila.novo_lote(caminho, dono="gustavo@ufrrj.br", nome="par",
                             n_arquivos=2)
    fila.liberar_para_fila(caminho, lote_id, 2)
    return lote_id


def test_a_pagina_avisa_quando_ninguem_esta_atendendo(cliente):
    lote_id = _lote_na_fila(cliente)
    html = cliente.get(f"/lotes/{lote_id}?tela_do_lote=1").text
    assert "Nada está atendendo a fila" in html, (
        "a página continua prometendo que alguém vai pegar o lote")
    assert "seus arquivos chegaram" in html.lower(), (
        "não diz que o envio está a salvo — a pessoa vai reenviar à toa")
    assert "trabalhador" in html, "não diz o que fazer"


def test_com_trabalhador_vivo_a_promessa_volta(cliente):
    lote_id = _lote_na_fila(cliente)
    fila.pulsar(cliente._main.cfg.sqlite_path, "deck:1")
    html = cliente.get(f"/lotes/{lote_id}?tela_do_lote=1").text
    assert "Nada está atendendo a fila" not in html
    assert "Esta página se atualiza sozinha" in html


def test_o_aviso_e_so_de_quem_espera_na_fila(cliente):
    """Um lote RECEBENDO ainda não é promessa de processamento, e um PRONTO já
    foi processado — nos dois o aviso seria ruído."""
    caminho = cliente._main.cfg.sqlite_path
    lote_id = fila.novo_lote(caminho, dono="gustavo@ufrrj.br", nome="par",
                             n_arquivos=2)
    html = cliente.get(f"/lotes/{lote_id}?tela_do_lote=1").text
    assert "Nada está atendendo a fila" not in html


def _banco_de_antes_do_pulso(tmp_path):
    """Um banco real, com tudo, menos a tabela nova — que é o estado de quem
    tem o código novo em disco e o servidor antigo ainda de pé."""
    p = tmp_path / "velho.sqlite3"
    fila.criar_esquema(p)
    with fila.conectar(p) as con:
        con.execute("DROP TABLE pulso")
    return p


def test_banco_anterior_ao_recurso_nao_dispara_alarme_falso(tmp_path):
    """Estado que só existe entre subir o código novo e reiniciar o servidor.
    Nessa janela, dizer "nada está atendendo" para todo mundo ao mesmo tempo
    seria mentira — e mentira idêntica à que este arquivo veio consertar."""
    assert fila.fila_atendida(_banco_de_antes_do_pulso(tmp_path)) is True


def test_o_arranque_fecha_essa_janela_sozinho(tmp_path):
    """`criar_esquema` roda a cada arranque e cria a tabela sem migração — é
    `CREATE TABLE IF NOT EXISTS`, não alteração de coluna."""
    p = _banco_de_antes_do_pulso(tmp_path)
    fila.criar_esquema(p)
    assert fila.fila_atendida(p) is False, (
        "depois de reiniciar, a fila sem trabalhador tem que aparecer como tal")
