"""
Os achados do painel de verificação de 2026-08-11, e as travas que os fecham.

Contexto: a varredura de 10/08 morreu por limite de sessão duas vezes seguidas
(a 5ª e a 6ª deste projeto) sem que o painel votasse em nada. Os candidatos que
ela produziu foram recuperados do journal e levados a um painel de três lentes
por fora. Estes testes cobrem o que sobreviveu, mais os achados antigos que
nunca tinham sido corrigidos.

⚠️ Cada teste aqui falha contra o código anterior. Um teste de regressão que
passa nos dois lados não prova que a correção fez alguma coisa.
"""
from __future__ import annotations

import asyncio
import importlib
import threading

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_teste(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
    from easycontig_web import main
    importlib.reload(main)
    return main


@pytest.fixture()
def cliente(app_teste):
    c = TestClient(app_teste.app)
    c.post("/entrar", data={"email": "g.freitas@ufrrj.br"}, follow_redirects=False)
    return c


# ─────────────────────────── C6 · o makeblastdb saiu do event loop (3/3)
def _enviar_banco(cliente, apelido="meubanco"):
    return cliente.post("/bancos/meu",
                        data={"apelido": apelido},
                        files={"fasta": ("x.fasta", b">a\nACGT\n", "text/plain")},
                        follow_redirects=False)


def test_montar_banco_do_usuario_nao_roda_no_event_loop(app_teste, cliente, monkeypatch):
    """A trava do achado C6 — MEDIUM, 3 de 3 verificadores.

    `enviar_banco` é `async def`, então o corpo dela roda NO EVENT LOOP. Chamar
    `montar_do_usuario` direto dali punha um `subprocess.run(makeblastdb,
    timeout=600)` em cima do único loop do uvicorn: enquanto ele rodasse, o
    servidor não atenderia mais NINGUÉM — nem o login, nem o `/saude`. E o teto
    de taxa é 10/hora, com 600 s cada: cobre a hora inteira que ele mede.

    ⚠️ A prova é `asyncio.get_running_loop()` DE DENTRO da função chamada. Numa
    thread do threadpool não há loop rodando e ele levanta `RuntimeError`; no
    event loop ele devolve o loop. É a diferença exata que o achado descreve, e
    não dá para satisfazê-la por acidente.
    """
    visto = {}

    def falso_montar(*a, **k):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            visto["no_loop"] = False          # threadpool: é o que se quer
        else:
            visto["no_loop"] = True           # event loop: é o defeito
        visto["thread"] = threading.current_thread().name

    monkeypatch.setattr(app_teste.bancos, "montar_do_usuario", falso_montar)
    r = _enviar_banco(cliente)

    assert r.status_code == 303, r.text
    assert visto, "montar_do_usuario nem foi chamado"
    assert visto["no_loop"] is False, (
        f"o makeblastdb rodou no event loop (thread {visto['thread']}) — "
        "um envio segura o site inteiro")


def test_sem_vaga_pesada_o_envio_de_banco_recusa_em_vez_de_ocupar_o_threadpool(
        app_teste, cliente, monkeypatch):
    """Tirar o bloqueio do event loop sozinho só muda o problema de lugar.

    O threadpool do Starlette tem 40 vagas e é compartilhado por TODA rota
    `def` do app. Sem o semáforo, 40 montagens simultâneas o ocupam inteiro — o
    achado M4 de 2026-08-08 de novo, por uma rota nova. `_vaga_pesada()` é o
    mesmo mecanismo que `api_traco` e `api_consultar` já usavam; esta rota era
    a única pesada que tinha escapado dele.
    """
    monkeypatch.setattr(app_teste.bancos, "montar_do_usuario",
                        lambda *a, **k: None)
    # ocupa todas as vagas, como se outras montagens estivessem em curso
    for _ in range(app_teste.MAX_PESADAS):
        assert app_teste._VAGAS_PESADAS.acquire(blocking=False)
    try:
        r = _enviar_banco(cliente)
        assert r.status_code == 503, (
            f"sem vaga, o envio devia recusar na hora; veio {r.status_code}")
        assert r.headers.get("Retry-After") == "5"
    finally:
        for _ in range(app_teste.MAX_PESADAS):
            app_teste._VAGAS_PESADAS.release()


def test_o_503_de_sem_vaga_nao_vira_texto_de_erro_na_pagina(app_teste, cliente,
                                                            monkeypatch):
    """A armadilha do conserto: a rota tem `except Exception` que transforma
    qualquer falha num redirecionamento com a mensagem na barra. Sem o
    `except HTTPException: raise` antes dele, o 503 da vaga seria engolido e
    voltaria como texto — a trava existiria e ninguém veria que ela agiu."""
    monkeypatch.setattr(app_teste.bancos, "montar_do_usuario",
                        lambda *a, **k: None)
    for _ in range(app_teste.MAX_PESADAS):
        app_teste._VAGAS_PESADAS.acquire(blocking=False)
    try:
        r = _enviar_banco(cliente)
        assert r.status_code != 303, "o 503 virou redirecionamento"
    finally:
        for _ in range(app_teste.MAX_PESADAS):
            app_teste._VAGAS_PESADAS.release()


# ───────────────── o texto de erro saiu da URL (achado no codigo de 10/08)
@pytest.mark.parametrize("pagina", ["/labs", "/bancos", "/entrar"])
def test_a_pagina_nao_imprime_texto_que_veio_na_url(app_teste, cliente, pagina):
    """A caixa de erro só mostra o que o APP escreveu.

    O padrão antigo era `/labs?erro=<texto>` e `{{ erro }}` direto do parâmetro:
    o texto exibido não era o que o app escreveu, era o que estivesse na URL.
    Qualquer um monta um link e a frase dele sai dentro da caixa de erro do
    próprio site, com a autoridade visual dele. Não é XSS — o Jinja escapa, e o
    verificador foi ler o `select_autoescape` do Starlette instalado — é
    falsificação de conteúdo, que é o que uma isca precisa.

    Encontrado em 2026-08-10 no `/labs`, que era código escrito naquele mesmo
    dia; o mesmo padrão já existia antes em `/bancos` e `/entrar`, e os três
    foram fechados juntos.
    """
    isca = "sua sessao expirou, reenvie suas credenciais em exemplo-malicioso"
    html = cliente.get(f"{pagina}?erro={isca.replace(' ', '+')}").text
    assert isca not in html, f"{pagina} imprimiu texto vindo da URL"


def test_o_recado_do_app_aparece_uma_vez_e_some(app_teste, cliente):
    """O outro lado: a mensagem legítima continua chegando — e só uma vez.

    Sem o "de uma vez só", o recado reapareceria em todo recarregamento e a
    pessoa ficaria olhando um erro que já resolveu.
    """
    from easycontig_web import perfil
    perfil.salvar(app_teste.cfg.sqlite_path, "m.peckle@ufrrj.br", nome="Maristela",
                  especies="Anaplasma platys")
    chave = perfil.chave_de(app_teste.cfg.sqlite_path, "m.peckle@ufrrj.br")
    # motivo em branco: o pedido é recusado e o app tem algo a dizer
    cliente.post(f"/labs/{chave}/pedido", data={"itens": ["Anaplasma platys"],
                 "motivo": "  "}, follow_redirects=False)

    primeira = cliente.get("/labs").text
    assert "diga para que serve a amostra" in primeira, "o recado do app sumiu"
    segunda = cliente.get("/labs").text
    assert "diga para que serve a amostra" not in segunda, "o recado grudou na tela"


def test_sair_manda_o_navegador_limpar_os_rascunhos(app_teste, cliente):
    """A trava do achado C8 — 3 de 3 verificadores.

    O `localStorage` guarda `est.seq`, as sequências alinhadas que vieram de uma
    rota atrás de `_exigir` + dono do lote: sequenciamento NÃO PUBLICADO. Sair
    da conta derruba a sessão no servidor, mas nada alcançava o navegador — numa
    máquina de laboratório compartilhada o próximo usuário lia o rascunho.
    """
    r = cliente.post("/sair", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/entrar?saiu=1", (
        "o /sair não sinaliza a limpeza para o navegador")
    html = cliente.get("/entrar?saiu=1").text
    assert "easycontig:rascunho:" in html and 'saiu' in html, (
        "a página de entrada não traz a limpeza do rascunho")


def test_a_limpeza_nao_dispara_numa_visita_normal_a_entrada(cliente):
    """Apagar em toda visita destruiria o rascunho vivo de quem só abriu a tela
    de entrada noutra aba. A limpeza é condicionada a `?saiu=1`."""
    html = cliente.get("/entrar").text
    assert 'get("saiu") === "1"' in html, "a limpeza deixou de ser condicional"


# ───────────────────────────── proxies: a escolha tem de estar escrita
def test_producao_recusa_proxies_confiaveis_em_branco(monkeypatch):
    """Em branco atrás de um proxy, TODO anônimo cai num balde só: o teto de
    login deixa de ser 20 por pessoa e vira 20 no mundo — uma pessoa errando o
    login vinte vezes tranca a entrada do site. Não há conserto em código (atrás
    de um proxy que não se confia o servidor não distingue dois clientes), então
    o que se exige é a DECLARAÇÃO, como o `EASYCONTIG_DOMINIO` já faz com `*`."""
    from easycontig_web import config
    for k, v in {"EASYCONTIG_AUTH": "google", "EASYCONTIG_SECRET_KEY": "x",
                 "EASYCONTIG_HTTPS_ONLY": "1", "EASYCONTIG_DOMINIO": "ufrrj.br",
                 "EASYCONTIG_URL_BASE": "https://e.ufrrj.br"}.items():
        monkeypatch.setenv(k, v)

    monkeypatch.setenv("EASYCONTIG_PROXIES_CONFIAVEIS", "")
    assert any("PROXIES_CONFIAVEIS" in p for p in config.conferir_producao())

    monkeypatch.setenv("EASYCONTIG_PROXIES_CONFIAVEIS", "nenhum")
    assert not any("PROXIES_CONFIAVEIS" in p for p in config.conferir_producao())

    monkeypatch.setenv("EASYCONTIG_PROXIES_CONFIAVEIS", "172.18.0.1")
    assert not any("PROXIES_CONFIAVEIS" in p for p in config.conferir_producao())


def test_nenhum_e_o_mesmo_conjunto_vazio_de_sempre(monkeypatch):
    """`nenhum` muda o que a produção EXIGE, nunca o que o limitador FAZ."""
    from easycontig_web import limites
    monkeypatch.setenv("EASYCONTIG_PROXIES_CONFIAVEIS", "nenhum")
    assert limites.proxies_confiaveis() == set()
    monkeypatch.setenv("EASYCONTIG_PROXIES_CONFIAVEIS", "  NENHUM  ")
    assert limites.proxies_confiaveis() == set()
