"""
A página de entrada, que é PÚBLICA, não pode publicar a lista de autorização
crua.

Achado da varredura de segurança de 2026-08-20 (F4). `EASYCONTIG_DOMINIO` guarda,
além do domínio institucional, endereços pessoais liberados um a um (ver o
docstring de `dominio_ok`). A rota `GET /entrar` é pública — o repositório é
público e a rota não exige sessão — e renderizava a lista literal. Qualquer
visitante anônimo lia o e-mail exato de cada pessoa autorizada: dado pessoal de
um lado, mapa de alvos de phishing do outro, mirando justamente as contas
nominais (que costumam ser as de administrador).

O filtro tem de usar EXATAMENTE a mesma regra que `dominio_ok` usa para decidir
o que é pessoa e o que é domínio, senão a página mostra o que a porta recusa (ou
esconde o que ela aceita). Por isso os casos abaixo são os de fronteira daquela
gramática — arroba na frente é domínio, arroba no meio é pessoa — e não exemplos
redondos.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from easycontig_web.contas import autenticacao as auth


# ------------------------------------------------ o filtro, na unidade

@pytest.mark.parametrize("entrada, esperado", [
    # o caso que motivou o achado: instituição + pessoa nomeada
    ("ufrrj.br, juaredbr@gmail.com", "ufrrj.br"),
    # pessoa no MEIO da lista, com subdomínio de um lado e domínio de outro
    ("ufrrj.br, ana@lhv.ufrrj.br, uff.br", "ufrrj.br, uff.br"),
    # ⚠️ arroba na FRENTE ainda é domínio (dominio_ok tira o @ inicial) — fica
    ("@ufrrj.br", "@ufrrj.br"),
    # ⚠️ '=' pede o domínio EXATO; é domínio, não pessoa — fica, verbatim
    ("=ufrrj.br", "=ufrrj.br"),
    # aberto por escrito: '*' não é pessoa — fica
    ("*", "*"),
    # só pessoa: a lista pública fica VAZIA, não devolve o nome
    ("juaredbr@gmail.com", ""),
    # duas pessoas: vazio, e não "a, b"
    ("a@x.com, b@y.com", ""),
    # domínio configurado vazio (dev): nada a mostrar
    ("", ""),
    # espaços irregulares não viram item fantasma nem quebram a junção
    ("ufrrj.br ,  , juaredbr@gmail.com", "ufrrj.br"),
])
def test_dominios_publicos_tira_so_os_nomes(entrada, esperado):
    assert auth.dominios_publicos(entrada) == esperado


def test_a_regra_de_pessoa_e_a_mesma_do_dominio_ok():
    """Trava contra divergência: para cada item, 'some da vista pública' tem de
    significar 'é pessoa nomeada' na gramática de dominio_ok, e nada mais."""
    lista = "ufrrj.br, @uff.br, =lhv.ufrrj.br, *, juaredbr@gmail.com, ana@x.br"
    publicos = set(auth.dominios_publicos(lista).split(", "))
    for bruto in lista.split(","):
        item = bruto.strip()
        d = item[1:] if item.startswith("@") else item
        eh_pessoa = "@" in d
        assert (item not in publicos) == eh_pessoa, \
            f"{item!r}: visível={item in publicos}, pessoa={eh_pessoa}"


# ------------------------------------------------ o efeito nas rotas públicas

@pytest.fixture()
def app_google(tmp_path, monkeypatch):
    """Modo produção-like: allowlist com uma pessoa nomeada, como no .env real."""
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")   # dev p/ POST /entrar existir
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "ufrrj.br, jared@ufrrj.br, juaredbr@gmail.com")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    return main


def test_a_pagina_de_entrada_nao_mostra_endereco_nomeado(app_google):
    """O e-mail nominal não pode aparecer no HTML servido a quem não entrou."""
    html = TestClient(app_google.app).get("/entrar").text
    assert "ufrrj.br" in html, "o domínio institucional deve continuar orientando"
    assert "juaredbr@gmail.com" not in html
    assert "jared@ufrrj.br" not in html


def test_a_recusa_de_login_nao_devolve_a_lista_de_nomes(app_google):
    """A mensagem 'conta fora do domínio' volta para quem tentou — no modo dev,
    qualquer anônimo. Ela não pode carregar os endereços nominais junto."""
    c = TestClient(app_google.app)
    r = c.post("/entrar", data={"email": "intruso@gmail.com"},
               follow_redirects=True)
    assert "juaredbr@gmail.com" not in r.text
    assert "jared@ufrrj.br" not in r.text


def test_so_pessoas_na_lista_nao_vira_recado_com_dominio_em_branco(tmp_path, monkeypatch):
    """Se a allowlist for só de pessoas, a recusa é uma frase genérica, não
    'conta fora do domínio ' com o campo vazio pendurado."""
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "juaredbr@gmail.com")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    c = TestClient(main.app)
    r = c.post("/entrar", data={"email": "x@y.com"}, follow_redirects=True)
    assert "juaredbr@gmail.com" not in r.text
    assert "conta fora do domínio " not in r.text  # sem o buraco vazio


def test_o_filtro_nao_afeta_quem_de_fato_pode_entrar(app_google):
    """Higiene de exibição, não de acesso: a pessoa nomeada continua entrando,
    mesmo sem aparecer na página."""
    assert auth.dominio_ok("juaredbr@gmail.com", "ufrrj.br, juaredbr@gmail.com")
    assert auth.dominio_ok("alguem@ppgcv.ufrrj.br", "ufrrj.br, juaredbr@gmail.com")
    assert not auth.dominio_ok("intruso@gmail.com", "ufrrj.br, juaredbr@gmail.com")
