"""O /saude precisa falar HTTP, não só JSON — senão o monitor externo não serve.

Motivo (2026-08-08): a rota devolvia 200 mesmo com `ok: false`. Quem lê o corpo
via a verdade; quem olha o código de status — todo monitor de prateleira, e o
`healthcheck` do próprio compose — via "no ar" com o tracy sumido.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    from easycontig_web import main
    importlib.reload(main)
    return main


def test_saude_ruim_responde_503(app, monkeypatch):
    monkeypatch.setattr(app.config, "diagnostico",
                        lambda cfg: [("tracy", False, "não encontrado"),
                                     ("blastn", True, "/usr/bin/blastn")])
    r = TestClient(app.app).get("/saude")
    assert r.status_code == 503, "doente respondeu 200 — o monitor não veria nada"
    assert r.json()["ok"] is False


def test_saude_boa_responde_200(app, monkeypatch):
    monkeypatch.setattr(app.config, "diagnostico",
                        lambda cfg: [("tracy", True, "/usr/local/bin/tracy")])
    r = TestClient(app.app).get("/saude")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_saude_ruim_com_sessao_leva_o_detalhe_e_o_503(app, monkeypatch):
    """Quem opera precisa do detalhe E do código certo — os dois juntos."""
    monkeypatch.setattr(app.config, "diagnostico",
                        lambda cfg: [("banco 16S", False, "sem índice .nsq")])
    c = TestClient(app.app)
    c.post("/entrar", data={"email": "a@ufrrj.br"}, follow_redirects=False)
    r = c.get("/saude")
    assert r.status_code == 503
    assert r.json()["dependencias"][0]["item"] == "banco 16S"


def test_saude_nao_vaza_detalhe_para_quem_nao_entrou(app):
    r = TestClient(app.app).get("/saude")
    assert set(r.json()) == {"ok"}, "a rota aberta voltou a contar a instalação"
