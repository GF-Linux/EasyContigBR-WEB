"""
Não dá para escolher o próprio IP e escapar do teto.

Achado em 2026-08-06 medindo: `limites.chave()` lia `X-Forwarded-For` direto do
que chegasse. Com o teto de login em 5 por 600 s, **30 tentativas seguidas
passaram sem uma única recusa**, trocando o cabeçalho a cada uma. No modo `dev`
cada tentativa que acerta o formato ENTRA, então o teto de login era a única
coisa entre um robô e uma sessão.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def montar(tmp_path, monkeypatch):
    def _montar(**env):
        monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
        monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
        monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
        monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
        monkeypatch.setenv("EASYCONTIG_LIM_LOGIN", "5")
        monkeypatch.delenv("EASYCONTIG_PROXIES_CONFIAVEIS", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        from easycontig_web import main
        importlib.reload(main)
        return TestClient(main.app)
    return _montar


def test_forjar_x_forwarded_for_nao_escapa_do_teto(montar):
    c = montar()
    barrado_em = None
    for i in range(1, 31):
        r = c.post("/entrar", data={"email": f"chute{i}@ufrrj.br"},
                   headers={"x-forwarded-for": f"10.0.0.{i}"},
                   follow_redirects=False)
        if r.status_code == 429:
            barrado_em = i
            break
    assert barrado_em is not None, (
        "30 tentativas de login passaram trocando X-Forwarded-For: o teto por "
        "IP voltou a ser contornável por cabeçalho")
    assert barrado_em <= 6, f"barrou tarde demais (tentativa {barrado_em})"


def test_sem_proxy_declarado_o_cabecalho_e_ignorado(montar):
    from easycontig_web import limites
    montar()

    class Req:
        headers = {"x-forwarded-for": "1.2.3.4"}
        class client:                      # noqa: D106
            host = "203.0.113.9"

    assert limites.ip_de_origem(Req()) == "203.0.113.9"


def test_proxy_declarado_pode_dizer_o_ip_real(montar):
    """Numa VPS atrás de nginx, o IP verdadeiro precisa chegar — mas só pela
    boca de quem foi declarado."""
    from easycontig_web import limites
    montar(EASYCONTIG_PROXIES_CONFIAVEIS="203.0.113.9, 10.1.1.1")

    class DoProxy:
        headers = {"x-forwarded-for": "1.2.3.4, 10.9.9.9"}
        class client:                      # noqa: D106
            host = "203.0.113.9"

    class DeQualquerUm:
        headers = {"x-forwarded-for": "1.2.3.4"}
        class client:                      # noqa: D106
            host = "198.51.100.7"

    assert limites.ip_de_origem(DoProxy()) == "1.2.3.4"
    assert limites.ip_de_origem(DeQualquerUm()) == "198.51.100.7"


def test_cliente_sem_endereco_nao_derruba(montar):
    from easycontig_web import limites
    montar()

    class Req:
        headers: dict = {}
        client = None

    assert limites.ip_de_origem(Req()) == "?"
