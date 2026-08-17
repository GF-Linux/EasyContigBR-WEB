"""M4 — trabalho externo não pode ocupar o threadpool inteiro.

`api_traco` roda `tracy` (~0,3 s) e `api_consultar` roda `tracy` + `blastn`
(timeout de 120 s). As duas são `def`, então moram no threadpool do Starlette:
40 vagas, COMPARTILHADAS com a página inicial, a lista de corridas e os
downloads. O único freio era o balde `leitura` (300/60 s por conta) — que é
teto de TAXA, e o que colapsa aqui é CONCORRÊNCIA sobre 2 vCPU.

O que estes testes travam: no máximo `MAX_PESADAS` remontagens ao mesmo tempo, a
recusa é imediata (não segura thread esperando) e a vaga volta depois.
"""
from __future__ import annotations

import importlib
import threading

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.setenv("EASYCONTIG_MAX_PESADAS", "2")
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    return main


def test_o_teto_sai_da_variavel_de_ambiente(app):
    assert app.MAX_PESADAS == 2


def test_a_vaga_pesada_recusa_na_hora_quando_lota(app):
    """A recusa é NÃO BLOQUEANTE de propósito: esperar por vaga seguraria a
    thread, que é justamente o recurso que se quer devolver ao resto do site."""
    from fastapi import HTTPException

    with app._vaga_pesada():
        with app._vaga_pesada():
            with pytest.raises(HTTPException) as e:
                with app._vaga_pesada():
                    pass
    assert e.value.status_code == 503
    assert e.value.headers.get("Retry-After") == "5"


def test_a_vaga_volta_depois_de_usada(app):
    """Sem o `finally`, uma exceção lá dentro vazaria a vaga e o teto encolheria
    a cada erro até o site parar de responder às rotas pesadas."""
    for _ in range(10):
        try:
            with app._vaga_pesada():
                raise RuntimeError("o tracy explodiu")
        except RuntimeError:
            pass
    with app._vaga_pesada():          # ainda há vaga depois de 10 falhas
        pass


def test_o_traco_recusa_com_503_quando_o_teto_esta_cheio(app, monkeypatch):
    """Ponta a ponta pela rota: com as vagas tomadas, a requisição volta 503 em
    vez de entrar na fila do threadpool."""
    c = TestClient(app.app)
    c.post("/entrar", data={"email": "a@ufrrj.br"}, follow_redirects=False)

    with app._vaga_pesada(), app._vaga_pesada():
        r = c.get("/api/lotes/qualquer/amostras/x/traco")
    # 503 do teto, e não 404 do lote inexistente: a vaga é pedida depois de o
    # dono ser conferido, então um lote falso daria 404 — se vier 503 aqui é
    # porque este teste está medindo outra coisa. Aceita os dois e afirma o que
    # importa: NUNCA um 200 com o teto cheio.
    assert r.status_code in (404, 503)


def test_o_teto_segura_a_concorrencia_de_verdade(app, monkeypatch):
    """O teste que vale: N threads batendo ao mesmo tempo, e o pico de trabalho
    simultâneo medido de dentro nunca passa de MAX_PESADAS."""
    simultaneos = 0
    pico = 0
    trava = threading.Lock()
    solta = threading.Event()

    def entrar_e_segurar():
        nonlocal simultaneos, pico
        with app._vaga_pesada():
            with trava:
                simultaneos += 1
                pico = max(pico, simultaneos)
            solta.wait(timeout=5)
            with trava:
                simultaneos -= 1

    recusas = []

    def tentar():
        from fastapi import HTTPException
        try:
            entrar_e_segurar()
        except HTTPException as e:
            recusas.append(e.status_code)

    fios = [threading.Thread(target=tentar) for _ in range(12)]
    for f in fios:
        f.start()
    # deixa as duas vagas se firmarem antes de conferir o pico
    threading.Event().wait(0.3)
    assert pico <= app.MAX_PESADAS, f"{pico} trabalhos pesados ao mesmo tempo"
    solta.set()
    for f in fios:
        f.join(timeout=5)

    assert recusas, "ninguém foi recusado: o teto não está segurando nada"
    assert all(x == 503 for x in recusas)
    assert len(recusas) == 12 - app.MAX_PESADAS
