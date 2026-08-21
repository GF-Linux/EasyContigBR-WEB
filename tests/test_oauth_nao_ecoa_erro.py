"""
O retorno do OAuth não pode escrever na nossa página de login.

Achado F3 da varredura de 2026-08-20. `GET /auth/google/volta` recebia `error`
pela query — rota pública, parâmetro escolhido por quem monta o link — e o
guardava literalmente no recado da sessão, que `/entrar` renderiza na caixa de
erro do site. Não é XSS (o Jinja escapa o texto): é **falsificação de conteúdo**
carregada pelo domínio verdadeiro e pelo certificado verdadeiro, logo acima do
campo de e-mail. A isca óbvia é "sua sessão expirou, reenvie suas credenciais
em <domínio-do-atacante>", lida como se o próprio EasyContig a tivesse escrito.

A vítima não precisa estar logada: este ramo roda ANTES da checagem de `state`.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from easycontig_web.contas import autenticacao as auth

FRASE_DO_ATACANTE = "Sua sessao expirou. Reenvie suas credenciais em easycontig-ufrrj.com"


# ------------------------------------------------------- o tradutor, na unidade

def test_codigo_conhecido_vira_frase_do_servidor():
    assert auth.mensagem_de_erro_oauth("access_denied") == "entrada cancelada"
    assert "organização" in auth.mensagem_de_erro_oauth("admin_policy_enforced")


def test_codigo_desconhecido_nunca_devolve_o_texto_recebido():
    """⚠️ O ponto do fix: o que não está no catálogo vira genérica, e o texto
    do atacante não aparece em lugar nenhum da saída."""
    saida = auth.mensagem_de_erro_oauth(FRASE_DO_ATACANTE)
    assert saida == "não foi possível entrar pelo Google"
    assert "credenciais" not in saida
    assert "easycontig-ufrrj.com" not in saida


@pytest.mark.parametrize("entrada", ["", "   ", "ACCESS_DENIED", "access_denied  "])
def test_entrada_torta_nao_quebra_nem_vaza(entrada):
    saida = auth.mensagem_de_erro_oauth(entrada)
    assert saida in ("entrada cancelada", "não foi possível entrar pelo Google")


# ------------------------------------------------------------- o efeito na rota

@pytest.fixture()
def app_google(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id-de-teste")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo-de-teste")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "ufrrj.br")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    return main


def test_a_frase_do_atacante_nao_chega_a_pagina_de_login(app_google):
    """O caminho inteiro: link forjado -> recado na sessão -> /entrar renderiza.
    A frase escolhida pelo atacante não pode sair do outro lado."""
    c = TestClient(app_google.app)
    r = c.get(f"/auth/google/volta?error={FRASE_DO_ATACANTE}",
              follow_redirects=True)
    assert r.status_code == 200
    assert "Reenvie suas credenciais" not in r.text
    assert "easycontig-ufrrj.com" not in r.text


def test_cancelar_no_google_ainda_explica_o_que_houve(app_google):
    """A defesa não pode emudecer o caso legítimo: quem clica em 'cancelar' no
    Google merece saber por que voltou para o login."""
    c = TestClient(app_google.app)
    r = c.get("/auth/google/volta?error=access_denied", follow_redirects=True)
    assert "entrada cancelada" in r.text
