"""
Labs — o diretório dos laboratórios cadastrados.

Pedido do autor (2026-08-08): um botão "Labs" na lateral, abaixo do Readme e ao
lado de Sair, abrindo a lista de perfis cadastrados no site. A funcionalidade
final será informada depois; por ora a página é a vitrine de quem se cadastrou.

O que estes testes travam:
  • a página exige sessão, como todo o resto;
  • lista SÓ o que a pessoa declarou (nome, laboratório, portfólio) — nunca
    corridas nem o que o BLAST achou;
  • quem só entrou e nunca salvou perfil NÃO aparece (não há linha em `perfis`);
  • o botão está na lateral e marca a página quando aberta.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from easycontig_web import perfil


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
    from easycontig_web import main
    importlib.reload(main)
    c = TestClient(main.app)
    c.post("/entrar", data={"email": "gustavo@ufrrj.br"}, follow_redirects=False)
    return c


def _sem_sessao(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "d2"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
    from easycontig_web import main
    importlib.reload(main)
    return TestClient(main.app)


# ───────────────────────────────── porta fechada
def test_labs_exige_sessao(tmp_path, monkeypatch):
    c = _sem_sessao(tmp_path, monkeypatch)
    assert c.get("/labs").status_code == 401


# ───────────────────────────────── o botão está na lateral
def test_o_botao_labs_esta_na_lateral_e_aponta_para_labs(cliente):
    html = cliente.get("/").text
    assert 'href="/labs"' in html, "não há botão Labs na lateral"


def test_a_pagina_labs_fica_marcada_na_lateral_quando_aberta(cliente):
    html = cliente.get("/labs").text
    # o mesmo padrão `viva` que marca o Workspace e o Perfil abertos
    assert 'href="/labs"' in html and "viva" in html.split('href="/labs"')[0][-80:] \
        or 'class="ac viva"' in html, "a página aberta não fica marcada"


# ───────────────────────────────── só quem declarou perfil aparece
def test_quem_nunca_salvou_perfil_nao_aparece(cliente):
    """`pegar` devolve padrões sem criar registro; `listar_perfis` só lê a tabela.
    Entrar no site não cadastra perfil — salvar cadastra."""
    html = cliente.get("/labs").text
    assert "Ainda não há perfis cadastrados" in html


def test_perfil_salvo_aparece_com_o_que_foi_declarado(cliente):
    cliente.post("/perfil", data={
        "nome": "Gustavo Freitas",
        "laboratorio": "Laboratório de Hemoparasitas e Vetores",
        "instituicao": "UFRRJ",
        "especies": "Babesia vogeli\nHepatozoon canis",
        "marcadores": "18S rRNA, 16S rRNA",
    }, follow_redirects=False)

    html = cliente.get("/labs").text
    assert "Gustavo Freitas" in html
    assert "Laboratório de Hemoparasitas e Vetores" in html
    assert "Babesia vogeli" in html
    assert "18S rRNA" in html
    # é a própria conta: ganha o selo "você"
    assert "você" in html


def test_labs_nao_expoe_corridas_nem_email_de_terceiro_por_engano(cliente):
    """A lista é declaração, não atividade: não deve vazar nomes de corrida."""
    from easycontig_web import fila
    from easycontig_web import main as m
    lid = fila.novo_lote(m.cfg.sqlite_path, dono="gustavo@ufrrj.br",
                         nome="F13719 sequencia sigilosa", n_arquivos=2)
    fila.liberar_para_fila(m.cfg.sqlite_path, lid, 2)
    cliente.post("/perfil", data={"nome": "Gustavo"}, follow_redirects=False)

    # A área de trabalho (o diretório em si), sem a lateral — a lateral mostra as
    # corridas DA PRÓPRIA conta de propósito (WorkFolder), e isso não é vazamento.
    corpo = cliente.get("/labs").text.split('class="trabalho"', 1)[1]
    assert "sequencia sigilosa" not in corpo, "o diretório vazou nome de corrida"


# ───────────────────────────────── o helper em si
def test_listar_perfis_ordena_e_so_traz_declaracao(cliente):
    from easycontig_web import main as m
    perfil.salvar(m.cfg.sqlite_path, "zulmira@ufrrj.br", nome="Zulmira")
    perfil.salvar(m.cfg.sqlite_path, "ana@ufrrj.br", nome="Ana",
                  especies="Babesia vogeli")

    lista = perfil.listar_perfis(m.cfg.sqlite_path, "ana@ufrrj.br")
    nomes = [p["nome"] for p in lista]
    assert nomes == sorted(nomes, key=str.lower), "não veio ordenado por nome"
    ana = next(p for p in lista if p["eu"])
    assert ana["email"] == "ana@ufrrj.br"          # o próprio, ela já o conhece
    assert ana["especies"] == ["Babesia vogeli"]   # lista, não JSON cru

    # Achado L1: o e-mail de TERCEIRO não sai do helper. Quem pergunta recebe um
    # booleano para marcar "você" na tela — não a lista de endereços de todos.
    zulmira = next(p for p in lista if p["nome"] == "Zulmira")
    assert "email" not in zulmira, "o diretório devolveu o e-mail de terceiro"


def test_quem_nao_digitou_nome_nao_vira_o_local_part_do_email(cliente):
    """Achado L1 de 2026-08-08 — o vazamento era pela via do NOME.

    `perfil.pegar` sintetiza `nome = email.split("@")[0]` para quem ainda não
    tem registro, e o `salvar` herdava esse padrão com `nome or atual["nome"]`.
    Resultado: quem salvasse o perfil sem digitar nome ficava GRAVADO com o
    local-part do próprio endereço — e o `/labs` mostra o nome de todo mundo.
    Com o domínio anunciado na tela de entrada (`EASYCONTIG_DOMINIO`), qualquer
    conta autenticada reconstruía o e-mail institucional dos demais. O endereço
    completo nunca chegou ao HTML; ele era remontado, que dá no mesmo.
    """
    from easycontig_web import main as m
    perfil.salvar(m.cfg.sqlite_path, "joao.silva@ufrrj.br", laboratorio="LHV")

    guardado = perfil.listar_perfis(m.cfg.sqlite_path)[0]
    assert guardado["nome"] == "", (
        f"gravou {guardado['nome']!r} — o local-part do e-mail virou nome")

    corpo = cliente.get("/labs").text
    assert "joao.silva" not in corpo, "o /labs remontou o e-mail de terceiro"
    assert "perfil sem nome" in corpo
