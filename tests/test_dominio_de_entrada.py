"""
Quem entra: `EASYCONTIG_DOMINIO`.

É a função que separa "o Google prova QUEM é" de "esta pessoa pode entrar", e
até 2026-08-06 não tinha teste nenhum — sendo que é ela que decide quem passa a
poder mandar `.ab1` para o volume `dados/`.

O defeito que motivou o arquivo: a comparação era do domínio inteiro, e
universidade brasileira reparte o e-mail por unidade. Com `ufrrj.br` na lista,
`@ppgcv.ufrrj.br` e `@lhv.ufrrj.br` eram recusados — o pessoal do laboratório
para quem o app é feito, com a mensagem "conta fora do domínio".
"""
from __future__ import annotations

import pytest

from easycontig_web.auth import dominio_ok


# ── sem configuração: porta aberta, e é de propósito ─────────────────────────
def test_sem_dominio_configurado_qualquer_conta_entra():
    """O desenvolvimento local não pode exigir configuração. Em servidor isso
    vira decisão explícita — está avisado no `.env.example`."""
    assert dominio_ok("qualquer@gmail.com", "")


def test_sem_arroba_nao_entra_nem_com_dominio_vazio_na_lista():
    assert not dominio_ok("gustavo", "ufrrj.br")


# ── o caso que estava quebrado ───────────────────────────────────────────────
@pytest.mark.parametrize("email", [
    "gustavo@ufrrj.br",
    "gustavo@ppgcv.ufrrj.br",
    "gustavo@lhv.ufrrj.br",
    "gustavo@a.b.ufrrj.br",
])
def test_subdominio_da_instituicao_entra(email):
    assert dominio_ok(email, "ufrrj.br"), (
        f"{email} recusado: é a unidade dentro da própria universidade")


# ── e o que o sufixo NÃO pode deixar passar ──────────────────────────────────
@pytest.mark.parametrize("email", [
    "alguem@falso-ufrrj.br",     # sósia: sem o ponto, é outro dono
    "alguem@ufrrj.br.com",       # o domínio de verdade é `br.com`
    "alguem@naoufrrj.br",
    "alguem@gmail.com",
])
def test_dominio_parecido_nao_entra(email):
    assert not dominio_ok(email, "ufrrj.br")


def test_o_ponto_e_o_que_separa_filho_de_sosia():
    """Explicita a regra: o que entra é `X.ufrrj.br`, não `Xufrrj.br`."""
    assert dominio_ok("a@x.ufrrj.br", "ufrrj.br")
    assert not dominio_ok("a@xufrrj.br", "ufrrj.br")


# ── lista, e as formas que alguém digita na prática ──────────────────────────
def test_lista_de_instituicoes():
    lista = "ufrrj.br, usp.br ,unicamp.br"
    for bom in ("a@ufrrj.br", "a@vet.usp.br", "a@unicamp.br"):
        assert dominio_ok(bom, lista), bom
    assert not dominio_ok("a@ufmg.br", lista)


@pytest.mark.parametrize("escrito", ["@ufrrj.br", "ufrrj.br.", " UFRRJ.BR "])
def test_forma_de_escrever_nao_muda_o_resultado(escrito):
    """Arroba na frente, ponto no fim e maiúscula são erros de digitação
    prováveis num `.env`, e nenhum deles pode trancar o laboratório fora."""
    assert dominio_ok("Gustavo@UFRRJ.br", escrito)


def test_maiuscula_no_email_nao_recusa():
    assert dominio_ok("Gustavo.Freittas@UFRRJ.BR", "ufrrj.br")


# ── quando o subdomínio NÃO é desejado ───────────────────────────────────────
def test_igual_na_frente_prende_no_dominio_exato():
    """Escape para quem quiser o domínio e nada abaixo dele."""
    assert dominio_ok("a@ufrrj.br", "=ufrrj.br")
    assert not dominio_ok("a@lhv.ufrrj.br", "=ufrrj.br")


# ── o alerta que vale a pena estar em teste ──────────────────────────────────
def test_um_tld_na_lista_abre_o_pais_inteiro():
    """Não é defeito, é consequência — mas quem escrever `br` no `.env` precisa
    saber que acabou de liberar qualquer endereço brasileiro."""
    assert dominio_ok("alguem@empresa-privada.br", "br")
