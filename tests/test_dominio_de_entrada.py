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

from easycontig_web.contas.autenticacao import dominio_ok


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


# ── o GOOGLE_CLIENT_ID errado é mudo deste lado ──────────────────────────────
# 2026-08-06: o autor cadastrou o cliente no console e o Google respondeu
# `401 invalid_client — The OAuth client was not found`, depois de ele já ter
# escolhido a conta. Nada no nosso lado protestou: `google_configurado()` aceita
# qualquer texto não vazio, inclusive as reticências da receita do `docs/`.
from easycontig_web.contas.autenticacao import queixa_do_client_id


def test_id_bem_formado_nao_gera_queixa():
    assert queixa_do_client_id(
        "123456789012-ab1cd2ef3gh4ij5kl6mn7op8qr9st0uv.apps.googleusercontent.com") == ""


def test_ausente_nao_gera_queixa():
    """Faltar é outro assunto — quem trata é `google_configurado()`."""
    assert queixa_do_client_id("") == ""


def test_as_reticencias_da_receita_sao_pegas():
    """O caso real: a receita do docs/ traz `…apps.googleusercontent.com` como
    exemplo, e rodar aquilo literalmente dá exatamente `invalid_client`."""
    q = queixa_do_client_id("…apps.googleusercontent.com")
    assert q and "exemplo da receita" in q


def test_aspas_vindas_do_env_sao_pegas():
    q = queixa_do_client_id('"123-abc.apps.googleusercontent.com"')
    assert q and "aspas" in q


def test_espaco_de_copiar_e_colar_e_pego():
    q = queixa_do_client_id(" 123-abc.apps.googleusercontent.com\n")
    assert q and "espaço" in q


def test_id_de_outro_provedor_nao_passa_por_google():
    assert queixa_do_client_id("meu-app-id") != ""


# ── a volta do Google: recusa é recusa, não "não respondeu" ──────────────────
# 2026-08-06, depois de o CLIENT_ID já estar certo: a volta do login deu
# `502 — o Google não respondeu; tente de novo`. O Google TINHA respondido:
# `401 invalid_client — The provided client secret is invalid`. A tela afirmava
# um fato falso E dava o pior conselho possível ("tente de novo") para um erro
# de configuração que não se resolve sozinho.
import io
import urllib.error

import pytest as _pytest
from fastapi import HTTPException

from easycontig_web.contas import autenticacao as _auth


class _RequestFalso:
    def __init__(self, estado):
        self.session = {_auth.ESTADO_CHAVE: estado}


def _volta(monkeypatch, erro):
    """Roda `usuario_da_volta` com o `urlopen` trocado por `erro`."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "1-a.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "GOCSPX-x")

    def urlopen(*a, **k):
        raise erro

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with _pytest.raises(HTTPException) as e:
        _auth.usuario_da_volta(_RequestFalso("s"), "cod", "s",
                               "http://localhost:8099/auth/google/volta")
    return e.value


def test_recusa_do_google_nao_e_anunciada_como_silencio(monkeypatch):
    corpo = b'{"error":"invalid_client","error_description":"The provided client secret is invalid."}'
    erro = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(corpo))
    exc = _volta(monkeypatch, erro)

    assert exc.status_code == 502
    assert "não respondeu" not in exc.detail, (
        "continua afirmando que o Google ficou calado quando ele respondeu")
    assert "invalid_client" in exc.detail, "o motivo do Google não chegou à tela"
    assert "não adianta tentar de novo" in exc.detail, (
        "erro de configuração não se resolve repetindo")


def test_o_codigo_do_google_chega_qualquer_que_seja(monkeypatch):
    corpo = b'{"error":"redirect_uri_mismatch"}'
    erro = urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(corpo))
    assert "redirect_uri_mismatch" in _volta(monkeypatch, erro).detail


def test_google_realmente_mudo_continua_dizendo_que_esta_mudo(monkeypatch):
    """A frase antiga sobrevive onde ela é VERDADE: rede, DNS, tempo esgotado."""
    exc = _volta(monkeypatch, TimeoutError("tempo esgotado"))
    assert exc.status_code == 502
    assert "não respondeu" in exc.detail
    assert "tente de novo" in exc.detail


def test_resposta_ilegivel_nao_quebra_a_volta(monkeypatch):
    """Corpo que não é JSON não pode virar exceção dentro do tratador de erro."""
    erro = urllib.error.HTTPError("u", 500, "erro", {}, io.BytesIO(b"<html>ops"))
    exc = _volta(monkeypatch, erro)
    assert exc.status_code == 502 and "recusou" in exc.detail


def test_estado_invalido_continua_sendo_400_e_nao_502(monkeypatch):
    """A trava de CSRF vem antes e não pode ser confundida com falha do Google."""
    with _pytest.raises(HTTPException) as e:
        _auth.usuario_da_volta(_RequestFalso("certo"), "cod", "forjado", "u")
    assert e.value.status_code == 400


# ── aberto tem que ser declarado, não herdado ────────────────────────────────
# 2026-08-06, medido pelo autor: tela de consentimento em "Testando", UM e-mail
# na lista de usuários de teste, e TRÊS contas entraram — duas fora da lista.
# A trava de usuário de teste do Google vale para escopos SENSÍVEIS, e
# `openid email profile` não são. O Google prova a identidade e não restringe
# nada: quem restringe é o EASYCONTIG_DOMINIO, e só ele.
from easycontig_web import configuracao as _config


def test_asterisco_abre_por_escrito():
    for email in ("a@gmail.com", "b@empresa.com", "c@ufrrj.br"):
        assert dominio_ok(email, "*"), email


def test_asterisco_numa_lista_tambem_abre():
    """Se `*` está lá, o resto da lista não estreita nada — e é bom que seja
    óbvio, para ninguém achar que `ufrrj.br,*` restringe alguma coisa."""
    assert dominio_ok("a@gmail.com", "ufrrj.br,*")


def test_producao_recusa_subir_com_a_porta_indefinida(monkeypatch):
    monkeypatch.setenv("EASYCONTIG_AUTH", "google")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "x")
    monkeypatch.setenv("EASYCONTIG_HTTPS_ONLY", "1")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    problemas = _config.conferir_producao()
    assert any("EASYCONTIG_DOMINIO" in p for p in problemas), (
        "produção aceita subir sem ninguém ter decidido quem entra")


@_pytest.mark.parametrize("valor", ["ufrrj.br", "ufrrj.br,usp.br", "*"])
def test_producao_aceita_a_porta_declarada(monkeypatch, valor):
    """Tanto faz se é restrita ou aberta — o que se exige é que esteja escrita."""
    monkeypatch.setenv("EASYCONTIG_AUTH", "google")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "x")
    monkeypatch.setenv("EASYCONTIG_HTTPS_ONLY", "1")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", valor)
    assert not any("EASYCONTIG_DOMINIO" in p for p in _config.conferir_producao())


def test_em_desenvolvimento_o_branco_continua_valendo():
    """Sem EASYCONTIG_PRODUCAO nada disso roda: o local não pede configuração."""
    assert dominio_ok("qualquer@gmail.com", "")


# ── endereço nomeado: a instituição, mais as exceções com nome ───────────────
# Pedido do autor em 2026-08-06: "libera para mim o juaredbr@gmail.com". A
# alternativa era pôr `gmail.com` na lista — abrir o Gmail inteiro para tirar
# UMA pessoa do frio.
LISTA = "ufrrj.br, juaredbr@gmail.com"


def test_a_pessoa_nomeada_entra():
    assert dominio_ok("juaredbr@gmail.com", LISTA)


def test_nomear_uma_pessoa_nao_abre_o_provedor_dela():
    """O ponto inteiro da mudança: `juaredbr@gmail.com` na lista não pode fazer
    o Gmail do mundo entrar junto."""
    for outro in ("juaredpokemon@gmail.com", "estranho@gmail.com",
                  "juaredbr@outro.com"):
        assert not dominio_ok(outro, LISTA), outro


def test_a_instituicao_continua_valendo_ao_lado_do_nome():
    assert dominio_ok("jared@ufrrj.br", LISTA)
    assert dominio_ok("aluno@ppgcv.ufrrj.br", LISTA)


def test_maiuscula_e_espaco_no_endereco_nomeado():
    assert dominio_ok("  JuaredBR@Gmail.com ", " ufrrj.br ,  JUAREDBR@GMAIL.COM ")


def test_arroba_na_frente_continua_significando_dominio():
    """`@ufrrj.br` é erro de digitação comum e sempre significou o domínio —
    não pode virar "endereço nomeado sem nome" e parar de casar."""
    assert dominio_ok("jared@ufrrj.br", "@ufrrj.br")
    assert dominio_ok("aluno@lhv.ufrrj.br", "@ufrrj.br")


def test_endereco_nomeado_nao_casa_por_subdominio():
    """Pessoa é exata: `a@gmail.com` na lista não libera `a@mail.gmail.com`."""
    assert not dominio_ok("juaredbr@mail.gmail.com", LISTA)
