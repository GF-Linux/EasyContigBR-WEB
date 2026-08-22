"""F1 — quem não tem sessão não escolhe quanta memória o processo segura.

`/entrar` é o único POST isento do 401 (`POST_SEM_SESSAO`), e até 2026-08-21 ele
pagava o MESMO teto de corpo do envio de lote: 316 MB. O FastAPI chama
`await request.form()` ANTES da função da rota, então nem o 403 do modo nem o
teto de login impediam a alocação — um anônimo, sem cookie e sem `Origin`,
mandava ~330 partes de quase 1 MiB e o parser segurava tudo em RAM.

O formulário de login manda um e-mail e um `proximo`: centenas de bytes. O teto
dele agora é 64 KB, conferido nos dois caminhos (com e sem `Content-Length`).
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
    # Os tetos ficam nos padrões: é a relação entre eles que está sob teste.
    monkeypatch.delenv("EASYCONTIG_MAX_CORPO", raising=False)
    monkeypatch.delenv("EASYCONTIG_MAX_CORPO_SEM_SESSAO", raising=False)
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    from easycontig_web.contas import limite_de_requisicoes as limites
    limites.limpar()
    return main


def test_o_teto_sem_sessao_e_muito_menor_que_o_de_envio(app):
    """A relação é o ponto: se um dia alguém igualar os dois, o buraco volta."""
    assert app.MAX_CORPO_SEM_SESSAO < app.MAX_CORPO / 100, (
        "o teto de quem não tem sessão voltou a ser da ordem do teto de envio")


def test_entrar_com_corpo_grande_leva_413(app):
    """O caso do achado: anônimo, sem cookie, corpo bem acima do formulário."""
    c = TestClient(app.app)
    r = c.post("/entrar", content=b"x" * (128 * 1024),
               headers={"content-type": "application/x-www-form-urlencoded",
                        "accept": "application/json"})
    assert r.status_code == 413, f"veio {r.status_code}"
    assert "teto" in r.json()["detail"]


def test_entrar_com_formulario_de_verdade_passa(app):
    """O teto não pode atrapalhar quem só quer entrar."""
    c = TestClient(app.app)
    r = c.post("/entrar", data={"email": "a@ufrrj.br"}, follow_redirects=False)
    assert r.status_code == 303, f"veio {r.status_code}: o teto pegou o login"


def test_entrar_chunked_acima_do_teto_leva_413(app):
    """A porta sem cabeçalho tem de pagar o mesmo teto — foi por ela que o
    buraco anterior (H1) voltou a abrir uma vez."""

    def corpo():
        for _ in range(64):
            yield b"x" * 4096                      # 256 KB, sem Content-Length

    c = TestClient(app.app)
    r = c.post("/entrar", content=corpo(),
               headers={"content-type": "application/x-www-form-urlencoded",
                        "accept": "application/json"})
    assert r.status_code == 413, f"veio {r.status_code}"


def test_rota_autenticada_mantem_o_teto_grande(app):
    """O teto pequeno é SÓ para quem não tem sessão. Se ele vazasse para o
    envio de lote, um laboratório não conseguiria mais mandar uma corrida."""
    c = TestClient(app.app)
    c.post("/entrar", data={"email": "a@ufrrj.br"}, follow_redirects=False)
    r = c.post("/perfil", content=b"x" * (128 * 1024), follow_redirects=False,
               headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code != 413, "o teto de quem não tem sessão vazou para uma rota autenticada"


def test_o_403_de_modo_ainda_vem_antes_do_teto_de_login(tmp_path, monkeypatch):
    """Regressão do achado de 2026-08-06, que a correção do F1 quase desfez.

    Em produção (`EASYCONTIG_AUTH=google`) `POST /entrar` sempre devolve 403.
    Se o teto de login for conferido ANTES dessa recusa — por exemplo, movendo-o
    para o middleware —, cada batida numa rota desligada queima o orçamento de
    login, que atrás do nginx é COMPARTILHADO por todo visitante anônimo: uma
    rota que não faz nada tranca o login de quem usa. Bater bem acima do teto
    tem de continuar devolvendo 403, nunca 429.
    """
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "google")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.setenv("EASYCONTIG_LIM_LOGIN", "5")
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    from easycontig_web.contas import limite_de_requisicoes as limites
    limites.limpar()

    c = TestClient(main.app)
    vistos = set()
    for _ in range(12):                            # bem acima do teto de 5
        r = c.post("/entrar", data={"email": "a@ufrrj.br"},
                   follow_redirects=False, headers={"accept": "application/json"})
        vistos.add(r.status_code)
    assert vistos == {403}, (
        f"vieram {sorted(vistos)}: a rota desligada voltou a gastar o "
        "orçamento de login compartilhado")
