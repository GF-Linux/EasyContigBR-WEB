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
    from easycontig_web import servidor_web as main
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


def test_saude_responde_sem_cookie_e_o_codigo_segue_a_saude(app, monkeypatch):
    """O `healthcheck` do compose chama sem sessão e só olha o código.

    ⚠️ Este teste afirmava `== 200` fixo, e o 200 era incidental: até 2026-08-08
    a rota devolvia 200 **mesmo doente**, então qualquer ambiente passava. Hoje
    o que se trava são as duas coisas de verdade — a rota não exige sessão (não
    é 401/403), e o código ACOMPANHA a saúde, que é o que faz o monitor externo
    servir para alguma coisa.
    """
    from easycontig_web import configuracao as config
    c = _cli(app)
    assert c.get("/saude").status_code in (200, 503), "a rota passou a exigir sessão"

    monkeypatch.setattr(config, "diagnostico", lambda cfg: [("tracy", True, "ok")])
    assert c.get("/saude").status_code == 200
    monkeypatch.setattr(config, "diagnostico", lambda cfg: [("tracy", False, "sumiu")])
    assert c.get("/saude").status_code == 503


def test_saude_responde_a_head_porque_e_o_que_o_monitor_fala(app):
    """O 503 da função acima não vale nada se o monitor não chega a ler o código.

    ⚠️ Achado de 2026-08-08, com o site DE PÉ e o UptimeRobot em "down" havia
    horas: o FastAPI, ao contrário do Starlette puro, **não** acrescenta HEAD a
    uma rota declarada com `@app.get` — então `HEAD /saude` levava `405`, e o
    monitor lia 405 como fora do ar. Escolher o método é recurso PAGO no
    UptimeRobot; o conserto que não depende do plano de ninguém é este.

    Trava as duas metades: HEAD é atendido, e o código dele ACOMPANHA a saúde
    igual ao do GET — um HEAD que respondesse 200 fixo devolveria o defeito
    original (monitor cego) por outro caminho.
    """
    from easycontig_web import configuracao as config
    c = _cli(app)
    r = c.head("/saude")
    assert r.status_code != 405, (
        "HEAD voltou a levar 405: o monitor externo vê o site de pé como caído")
    assert r.status_code in (200, 503)

    monkeypatch_livre = pytest.MonkeyPatch()
    try:
        monkeypatch_livre.setattr(config, "diagnostico",
                                  lambda cfg: [("tracy", False, "sumiu")])
        assert c.head("/saude").status_code == 503, (
            "HEAD respondeu de pé com a máquina doente")
        monkeypatch_livre.setattr(config, "diagnostico",
                                  lambda cfg: [("tracy", True, "ok")])
        assert c.head("/saude").status_code == 200
    finally:
        monkeypatch_livre.undo()


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
    from easycontig_web.contas import limite_de_requisicoes as limites
    from easycontig_web import servidor_web as main
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


# ═════════════════════ cabeçalhos de segurança (checklist de produção)
# Nenhum destes existia até 2026-08-06. O mais específico deste projeto é o
# `noindex`: com domínio próprio o app fica descobrível de fora, e o que está
# atrás do login é sequenciamento NÃO PUBLICADO.
def test_toda_resposta_pede_para_nao_ser_indexada(app):
    c = _cli(app)
    for rota in ("/entrar", "/saude"):
        h = c.get(rota).headers
        assert "noindex" in h.get("x-robots-tag", ""), rota


@pytest.mark.parametrize("cabecalho,valor", [
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "same-origin"),
])
def test_cabecalhos_basicos(app, cabecalho, valor):
    assert _cli(app).get("/entrar").headers.get(cabecalho) == valor


def test_a_csp_recusa_script_inline_sem_nonce(app):
    """É o que separa CSP de enfeite. Provado no navegador em 06/08: um
    `<script>` injetado sem nonce NÃO executou, e a página seguiu funcionando —
    cromatograma desenhado, alinhamento montado, zero erro no console.

    `style-src` fica com `'unsafe-inline'` de propósito (68 atributos `style=`
    nos templates); `script-src` não pode, porque é onde XSS mora — e este
    projeto já teve um XSS armazenado vindo do `stitle` do BLAST."""
    csp = _cli(app).get("/entrar").headers["content-security-policy"]
    script = [d for d in csp.split(";") if d.strip().startswith("script-src")][0]
    assert "'unsafe-inline'" not in script, "a CSP virou enfeite"
    assert "'nonce-" in script
    assert "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp


def test_o_nonce_muda_a_cada_resposta(app):
    """Nonce fixo é o mesmo que não ter nonce: bastaria lê-lo uma vez."""
    c = _cli(app)
    a, b = (c.get("/entrar").headers["content-security-policy"] for _ in range(2))
    assert a != b


def test_o_nonce_do_cabecalho_e_o_mesmo_da_pagina(app):
    """Se divergirem, o próprio JS do app para de rodar."""
    import re
    r = _cli(app).get("/entrar")
    do_cabecalho = re.search(r"'nonce-([^']+)'", r.headers["content-security-policy"])[1]
    assert f'nonce="{do_cabecalho}"' in r.text


def test_hsts_so_quando_ha_tls(app, monkeypatch):
    """Mandar HSTS em http://localhost ensina o navegador a recusar o próprio
    desenvolvimento pelos meses do `max-age`."""
    assert "strict-transport-security" not in _cli(app).get("/entrar").headers

    monkeypatch.setenv("EASYCONTIG_HTTPS_ONLY", "1")
    importlib.reload(app)
    r = TestClient(app.app).get("/entrar")
    assert "max-age=" in r.headers.get("strict-transport-security", "")


def test_o_favicon_existe(app):
    """`GET /favicon.ico` respondia 404 em toda visita — aparecia no log do
    autor e sujava o console do navegador."""
    r = _cli(app).get("/estatico/favicon.svg")
    assert r.status_code == 200 and r.content.startswith(b"<?xml")
    assert 'rel="icon"' in _cli(app).get("/entrar").text
