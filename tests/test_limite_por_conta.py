"""
O teto de leitura conta por CONTA, e não pelo prédio inteiro.

Por que estes testes existem (2026-08-06): o limitador chamava
`limites.conferir("leitura", request, None)` — sempre `None` — porque estava
registrado por FORA do `SessionMiddleware` e não tinha como saber quem era.
Medido na época: 300 leituras/60 s dividido por 0,33 req/s de cada aba de lote
aberta dá **15 abas no mesmo IP** para esgotar o teto. Como a universidade sai
por um NAT, quem seria barrado é o laboratório em dia de corrida — o oposto de
quem o teto existe para conter.

A correção é POSICIONAL: o middleware passou a ser registrado antes do
`SessionMiddleware`, o que o coloca por dentro dele. Isso é fácil de desfazer
sem perceber ao reorganizar o arquivo — daí o primeiro teste travar a ordem
diretamente, e não só o efeito.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def montar(tmp_path, monkeypatch):
    """Devolve uma fábrica de clientes que compartilham o mesmo processo."""
    def _montar(teto: str = "10"):
        monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
        monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
        monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
        monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
        monkeypatch.setenv("EASYCONTIG_LIM_LEITURA", teto)
        from easycontig_web import main
        importlib.reload(main)
        return main
    return _montar


def test_o_limitador_roda_por_dentro_do_session(montar):
    """A ordem é o mecanismo. `add_middleware` empilha de dentro para fora:
    quem é registrado depois fica por fora. O limitador tem que aparecer DEPOIS
    do SessionMiddleware nesta lista — o que significa mais para dentro."""
    main = montar()
    nomes = []
    for m in main.app.user_middleware:
        alvo = (m.kwargs or {}).get("dispatch")
        nomes.append(getattr(alvo, "__name__", None) or m.cls.__name__)

    assert "SessionMiddleware" in nomes, nomes
    assert "_limite_de_leitura" in nomes, nomes
    assert nomes.index("_limite_de_leitura") > nomes.index("SessionMiddleware"), (
        "o limitador voltou a ficar por FORA do SessionMiddleware; "
        f"ele deixa de enxergar a conta e conta pelo IP. Ordem atual: {nomes}")


def test_duas_contas_no_mesmo_ip_nao_dividem_o_teto(montar):
    """O caso que motivou a mudança: dois pesquisadores atrás do mesmo NAT."""
    main = montar(teto="10")

    ana = TestClient(main.app)
    ana.post("/entrar", data={"email": "ana@ufrrj.br"}, follow_redirects=False)
    for _ in range(10):
        assert ana.get("/saude").status_code == 200
    assert ana.get("/saude").status_code == 429, "a Ana devia ter estourado o dela"

    # Mesmo processo, mesmo IP de teste — antes da correção, o Bruno já nascia
    # barrado porque a Ana consumiu a cota do IP.
    bruno = TestClient(main.app)
    bruno.post("/entrar", data={"email": "bruno@ufrrj.br"}, follow_redirects=False)
    assert bruno.get("/saude").status_code == 200, (
        "o Bruno foi barrado pelo consumo da Ana: o teto voltou a ser por IP")


def test_quem_nao_entrou_continua_contado_por_ip(montar):
    """O caminho anônimo não tem outra identidade — e é onde o teto por IP é o
    comportamento certo, não uma limitação."""
    main = montar(teto="5")
    anonimo = TestClient(main.app)
    for _ in range(5):
        assert anonimo.get("/saude").status_code == 200
    assert anonimo.get("/saude").status_code == 429

    outro_anonimo = TestClient(main.app)
    assert outro_anonimo.get("/saude").status_code == 429, (
        "dois anônimos do mesmo IP deviam dividir o teto — é o que sobra "
        "quando não há conta")


def test_sessao_ilegivel_nao_derruba_a_requisicao(montar):
    """Cookie assinado com outra chave (todo reinício sem SECRET_KEY fixa gera
    uma nova) não pode virar erro 500: cai para o IP e segue."""
    main = montar(teto="10")
    c = TestClient(main.app)
    c.cookies.set("session", "lixo.que.nao.decodifica")
    assert c.get("/saude").status_code == 200
