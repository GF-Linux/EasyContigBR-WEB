"""H1 — recusar pelo Content-Length, antes de o corpo tocar o disco.

O `MultiPartParser` do Starlette spoola cada parte de arquivo em `/tmp` ANTES de
a rota rodar: `max_bytes`, cota e retenção agem todos depois. Medido em
2026-08-08: 12.000.296 bytes subiram inteiros num `POST /perfil` de teto 2 MB, e
só então voltou o 413.

A barreira de produção é o `client_max_body_size` do nginx. Isto é defesa em
profundidade — e é a ÚNICA para quem roda sem nginx na frente.
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
    monkeypatch.setenv("EASYCONTIG_MAX_CORPO", str(4096))
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    return main


def _entrar(c):
    c.post("/entrar", data={"email": "a@ufrrj.br"}, follow_redirects=False)
    return c


def test_corpo_acima_do_teto_leva_413(app):
    c = _entrar(TestClient(app.app))
    r = c.post("/perfil", content=b"x" * 8192,
               headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413
    assert "teto" in r.json()["detail"]


def test_corpo_dentro_do_teto_passa(app):
    c = _entrar(TestClient(app.app))
    r = c.post("/perfil", data={"nome": "Gustavo"}, follow_redirects=False)
    assert r.status_code in (200, 303)


def test_a_recusa_vem_do_middleware_antes_da_sessao(app):
    """Sem sessão o middleware já devolvia 401; o teto de corpo vem ANTES, senão
    um anônimo ainda decidiria quantos bytes o servidor recebe — que foi
    exatamente o buraco fechado em 2026-08-06 para o caso do 401."""
    c = TestClient(app.app)                      # sem entrar
    r = c.post("/lotes", content=b"x" * 8192,
               headers={"content-type": "application/x-www-form-urlencoded",
                        "accept": "application/json"})
    assert r.status_code == 413, f"veio {r.status_code}: o teto não é o primeiro"


def test_corpo_chunked_sem_content_length_leva_413(app):
    """A porta sem cabeçalho: um POST `chunked` não declara `Content-Length`, e
    o fast-path não tinha o que conferir — o parser spoolava as partes em /tmp
    antes da rota. O teto de corpo tem de valer também aqui.

    (O TestClient entrega o corpo do gerador num único `http.request`, então
    não dá para observar por aqui a PARADA no teto — isso depende do uvicorn
    fatiar o `chunked` em vários `receive()`. O que se afirma é o 413.)"""

    def corpo():
        for _ in range(64):
            yield b"x" * 4096                      # 262144 bytes, sem length

    c = _entrar(TestClient(app.app))
    r = c.post("/perfil", content=corpo(),
               headers={"content-type": "application/x-www-form-urlencoded",
                        "accept": "application/json"})
    assert r.status_code == 413
    assert "teto" in r.json()["detail"]


def test_corpo_chunked_sob_o_teto_chega_a_rota(app):
    """Ler o corpo no middleware para conferir não pode PERDÊ-LO: um `chunked`
    sob o teto ainda tem de chegar inteiro ao parser da rota."""

    def corpo():
        yield b"nome=Gustavo"

    c = _entrar(TestClient(app.app))
    r = c.post("/perfil", content=corpo(), follow_redirects=False,
               headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code in (200, 303)


def test_chunked_sem_sessao_recusa_sem_ler_o_corpo(app):
    """O teto de corpo em `chunked` mora DEPOIS do 401: um POST sem sessão a
    rota protegida ainda é recusado com zero byte lido, sem passar pela leitura
    do corpo (o buraco de memória pré-auth continua fechado)."""
    lido = {"bytes": 0}

    def corpo():
        for _ in range(64):
            pedaco = b"x" * 4096
            lido["bytes"] += len(pedaco)
            yield pedaco

    c = TestClient(app.app)                         # sem entrar
    r = c.post("/lotes", content=corpo(), follow_redirects=False,
               headers={"content-type": "multipart/form-data; boundary=z",
                        "accept": "application/json"})
    assert r.status_code == 401, f"veio {r.status_code}"
    assert lido["bytes"] == 0, f"leu {lido['bytes']} bytes de quem não tem sessão"


def test_content_length_invalido_e_recusado(app):
    c = _entrar(TestClient(app.app))
    r = c.post("/perfil", content=b"abc",
               headers={"content-length": "nao-e-numero",
                        "content-type": "application/x-www-form-urlencoded"})
    assert r.status_code in (400, 413)


def test_o_teto_padrao_acompanha_o_max_bytes(tmp_path, monkeypatch):
    """O número não é fixo: sobe junto com o teto por lote. Um teto de corpo
    menor que o lote permitido recusaria envio legítimo — e um fixo envelheceria
    calado no dia em que MAX_BYTES mudasse."""
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.delenv("EASYCONTIG_MAX_CORPO", raising=False)
    monkeypatch.setenv("EASYCONTIG_MAX_BYTES", str(50 * 1024 * 1024))
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    assert main.MAX_CORPO > main.cfg.max_bytes, (
        "o teto de corpo ficou abaixo do teto por lote — recusaria envio válido")
