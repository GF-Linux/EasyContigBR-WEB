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

from easycontig_web import limites

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
    boca de quem foi declarado.

    ⚠️ ESTE TESTE ESTAVA ERRADO e passava. Escrito na manhã de 2026-08-06, ele
    afirmava que `"1.2.3.4, 10.9.9.9"` vindo do proxy valia **`1.2.3.4`** — que
    é justamente a entrada que o CLIENTE escreve, porque o proxy acrescenta ao
    fim. O teste codificava o defeito e por isso o defeito sobreviveu ao
    conserto do mesmo dia. Achado pela varredura da noite.

    O valor certo é o mais à direita que não seja um proxy nosso: é o último
    salto que uma máquina de confiança viu.
    """
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

    assert limites.ip_de_origem(DoProxy()) == "10.9.9.9", (
        "voltou a acreditar na entrada que o cliente escreveu")
    assert limites.ip_de_origem(DeQualquerUm()) == "198.51.100.7"


def test_cliente_sem_endereco_nao_derruba(montar):
    from easycontig_web import limites
    montar()

    class Req:
        headers: dict = {}
        client = None

    assert limites.ip_de_origem(Req()) == "?"


# ── a cadeia inteira, não só a primeira entrada ──────────────────────────────
# Achado pela varredura de 2026-08-06 à noite, e é o conserto de manhã pela
# metade: confiar só no proxy declarado estava certo; ler a PRIMEIRA entrada da
# lista, não. Proxy ACRESCENTA ao fim — um cliente que manda
# `X-Forwarded-For: 1.2.3.4` faz o proxy entregar `1.2.3.4, <ip real>`, e
# `split(",")[0]` devolve exatamente o texto que o cliente escolheu. O teto por
# IP voltava a ser contornável assim que alguém configurasse um proxy: ou seja,
# em produção e só em produção.
class _Pedido:
    def __init__(self, conexao, xff=None):
        self.client = type("C", (), {"host": conexao})()
        self.headers = {"x-forwarded-for": xff} if xff else {}


def test_o_que_o_cliente_escreveu_na_frente_e_ignorado(monkeypatch):
    monkeypatch.setenv("EASYCONTIG_PROXIES_CONFIAVEIS", "10.0.0.1")
    # o cliente mandou "1.2.3.4"; o proxy acrescentou o IP real dele
    ip = limites.ip_de_origem(_Pedido("10.0.0.1", "1.2.3.4, 203.0.113.7"))
    assert ip == "203.0.113.7", (
        f"pegou {ip!r}: é o que o cliente escreveu, e o teto por IP some")


def test_com_um_proxy_so_o_ip_do_cliente_vale(monkeypatch):
    monkeypatch.setenv("EASYCONTIG_PROXIES_CONFIAVEIS", "10.0.0.1")
    assert limites.ip_de_origem(_Pedido("10.0.0.1", "203.0.113.7")) == "203.0.113.7"


def test_dois_proxies_nossos_em_serie(monkeypatch):
    """Balanceador + servidor web, os dois nossos: o cliente é o que sobra."""
    monkeypatch.setenv("EASYCONTIG_PROXIES_CONFIAVEIS", "10.0.0.1,10.0.0.2")
    ip = limites.ip_de_origem(_Pedido("10.0.0.2", "203.0.113.7, 10.0.0.1"))
    assert ip == "203.0.113.7"


def test_cadeia_toda_forjada_com_nomes_dos_nossos_proxies(monkeypatch):
    """Se o cliente encher a lista com os IPs dos nossos proxies, sobra o
    endereço da conexão — que ele não escolhe."""
    monkeypatch.setenv("EASYCONTIG_PROXIES_CONFIAVEIS", "10.0.0.1")
    assert limites.ip_de_origem(_Pedido("10.0.0.1", "10.0.0.1, 10.0.0.1")) == "10.0.0.1"


def test_sem_proxy_declarado_o_cabecalho_continua_valendo_nada(monkeypatch):
    monkeypatch.delenv("EASYCONTIG_PROXIES_CONFIAVEIS", raising=False)
    assert limites.ip_de_origem(_Pedido("203.0.113.9", "1.2.3.4")) == "203.0.113.9"
