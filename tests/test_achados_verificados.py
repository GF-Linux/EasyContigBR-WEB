"""
Os achados que sobreviveram ao painel de verificação (2026-08-06, noite).

A varredura do plugin `claude-security` foi a única das cinco tentativas do dia
a chegar até a verificação: 21 candidatos, painel de três lentes independentes,
63 votos, **5 sobreviveram e 16 caíram com motivo**.

Estes testes cobrem os três que ainda não tinham conserto. Os outros dois (o
reenfileiramento entre réplicas e o teto do banco do usuário) estão em
`test_fila.py` e `test_seguranca.py`.
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
    monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
    from easycontig_web import main
    importlib.reload(main)
    return main


def _cli(app):
    return TestClient(app.app)


def _entrar(c, email="a@ufrrj.br"):
    c.post("/entrar", data={"email": email}, follow_redirects=False)
    return c


# ═════════════════════════════════════════ F5 — o corpo antes do 401
# Medido nos pacotes instalados: o FastAPI executa `await request.form()` dentro
# do manipulador da requisição, ANTES de chamar a função do endpoint — e a
# autenticação deste app é a primeira instrução DE DENTRO dela. Um chamador sem
# cookie decidia quantos bytes o servidor recebia e por quanto tempo.
def test_post_sem_sessao_e_recusado_antes_de_ler_o_corpo(app):
    """A prova é o corpo NÃO ter sido consumido: um multipart declarado e nunca
    lido. Se o 401 viesse da rota, o parser já teria rodado."""
    lido = {"bytes": 0}

    class Corpo:
        """Conta o que o servidor puxar. Um iterável, não um bytes pronto."""

        def __iter__(self):
            for _ in range(64):
                pedaco = b"x" * 65536
                lido["bytes"] += len(pedaco)
                yield pedaco

    c = _cli(app)
    r = c.post("/lotes", content=Corpo(), follow_redirects=False,
               headers={"content-type": "multipart/form-data; boundary=z",
                        "accept": "application/json"})

    assert r.status_code == 401, f"esperava 401, veio {r.status_code}"
    assert lido["bytes"] == 0, (
        f"o servidor puxou {lido['bytes']} bytes de quem não tem sessão")


@pytest.mark.parametrize("rota", [
    "/lotes", "/bancos/meu", "/bancos/curado/montar", "/bancos/curado/remover",
    "/perfil", "/lotes/qualquer/apagar",
])
def test_todo_post_protegido_recusa_sem_sessao(app, rota):
    """Allowlist e não blocklist: rota nova nasce fechada."""
    r = _cli(app).post(rota, follow_redirects=False,
                       headers={"accept": "application/json"})
    assert r.status_code == 401, f"{rota} devolveu {r.status_code}"


def test_o_login_continua_aberto(app):
    """A allowlist não pode trancar a porta de entrada."""
    r = _cli(app).post("/entrar", data={"email": "a@ufrrj.br"},
                       follow_redirects=False)
    assert r.status_code == 303


def test_com_sessao_o_post_passa(app):
    c = _entrar(_cli(app))
    r = c.post("/lotes", data={"nome": "x", "referencia": "curado"},
               follow_redirects=False)
    assert r.status_code != 401


# ═════════════════════════════════════════ F4 — /saude sem sessão
def test_saude_sem_sessao_nao_entrega_o_mapa_da_maquina(app):
    """Caminhos absolutos do contêiner, quais ferramentas existem e onde, e a
    profundidade da fila SEM filtro de dono — repetindo a chamada, um estranho
    acompanha quando o laboratório trabalha e quanto."""
    d = _cli(app).get("/saude").json()

    assert d["ok"] in (True, False), "o healthcheck do compose precisa disto"
    assert "dependencias" not in d, "os caminhos do contêiner saíram sem sessão"
    assert "na_fila" not in d, "a atividade do laboratório saiu sem sessão"
    assert set(d) == {"ok"}


def test_saude_com_sessao_continua_util_para_quem_opera(app):
    d = _entrar(_cli(app)).get("/saude").json()
    assert "dependencias" in d and "na_fila" in d


def test_saude_responde_200_sem_cookie(app):
    """O `healthcheck` do compose chama sem sessão e só olha o código."""
    assert _cli(app).get("/saude").status_code == 200


# ═════════════════════════════════════════ F2 — rota desligada queimando o teto
# Atrás de um proxy, todo visitante anônimo divide um balde só. Uma rota que
# está DESLIGADA não pode gastar esse orçamento: era uma forma de trancar o
# login de quem usa batendo numa porta que já responde 403.
def test_login_dev_desligado_nao_consome_o_teto_compartilhado(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "google")     # dev DESLIGADO
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.setenv("EASYCONTIG_LIM_LOGIN", "5")
    monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
    from easycontig_web import limites, main
    importlib.reload(main)
    limites.limpar()
    c = TestClient(main.app)

    for _ in range(20):
        r = c.post("/entrar", data={"email": "x@y.com"}, follow_redirects=False)
        assert r.status_code == 403, "a rota deveria estar desligada"

    # O orçamento tem de estar INTACTO para quem vai entrar de verdade: atrás de
    # um proxy este balde é de TODO visitante anônimo.
    gastos = [k for k in limites._registro if k.startswith("login|")]
    assert not gastos, (
        f"20 batidas numa rota desligada gastaram o teto compartilhado: {gastos}")
