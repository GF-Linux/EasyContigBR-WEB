"""
auth.py — quem é o usuário, e se ele pode entrar.

Dois provedores atrás da mesma costura:

  * `dev`     — digita o e-mail e entra. Só para desenvolvimento local; recusa
                subir se não estiver explicitamente ligado.
  * `google`  — OAuth do Google, como decidido na ADR 0050 (não gov.br).

`EASYCONTIG_DOMINIO=ufrrj.br` restringe a entrada ao domínio institucional. Isso
é o que permite usar uma conta Google comum e ainda assim ser um serviço da
universidade: o Google prova QUEM é, o domínio decide SE entra.

⚠️ O caminho `google` **nunca foi exercitado com credencial real** — depende de
um CLIENT_ID/SECRET que só o autor pode criar no console do Google Cloud. O
fluxo está inteiro (ida, volta, troca do código por token, leitura do e-mail),
mas até alguém rodar com credencial de verdade, considere não testado.

O que ele faz, e por quê:
  1. manda para o Google com `state` aleatório guardado na sessão — sem isso,
     um link forjado por terceiro completaria um login na conta dele dentro do
     navegador da vítima (CSRF de login);
  2. na volta, confere o `state`, troca o `code` por token no servidor (o
     `client_secret` nunca chega ao navegador) e lê o e-mail verificado;
  3. exige `email_verified`, porque conta com e-mail não confirmado não prova
     domínio — e é o domínio que decide quem entra.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from fastapi import HTTPException, Request

SESSAO_CHAVE = "usuario"


@dataclass(frozen=True)
class Usuario:
    email: str
    nome: str = ""

    @property
    def dominio(self) -> str:
        return self.email.rsplit("@", 1)[-1].lower() if "@" in self.email else ""


def modo() -> str:
    return os.environ.get("EASYCONTIG_AUTH", "dev").lower()


def google_configurado() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID")
                and os.environ.get("GOOGLE_CLIENT_SECRET"))


# Um ID de cliente do Google é `<números>-<letras>.apps.googleusercontent.com`.
FORMA_CLIENT_ID = re.compile(r"^\d+-[a-z0-9_]+\.apps\.googleusercontent\.com$")


def queixa_do_client_id(valor: str | None = None) -> str:
    """Diz o que há de errado com o `GOOGLE_CLIENT_ID`, ou `""` se está bem.

    Existe porque o erro do outro lado é mudo: um valor que não corresponde a
    cliente nenhum só se revela na página do Google, como
    `401 invalid_client — The OAuth client was not found`, depois de a pessoa já
    ter escolhido a conta. Nada no nosso lado protesta: `google_configurado()`
    aceita qualquer texto não vazio.

    Aconteceu em 2026-08-06 com o autor. Os três casos abaixo são os que dá para
    ver **sem falar com o Google**, e cobrem o que de fato acontece: reticências
    de exemplo copiadas da receita, aspas vindas do `.env`, e espaço de
    copiar-e-colar. Não é validação de credencial — o Google segue sendo o único
    que sabe se o cliente existe."""
    bruto = os.environ.get("GOOGLE_CLIENT_ID", "") if valor is None else valor
    if not bruto:
        return ""                                  # ausente é outro assunto
    if bruto != bruto.strip():
        return ("GOOGLE_CLIENT_ID tem espaço ou quebra de linha nas pontas — "
                "o Google compara a string inteira")
    if bruto.strip('"\'') != bruto:
        return ("GOOGLE_CLIENT_ID veio com aspas em volta; no `.env` o valor "
                "vai cru, sem aspas")
    if not FORMA_CLIENT_ID.match(bruto):
        return (f"GOOGLE_CLIENT_ID não tem a forma de um ID do Google "
                f"(`<números>-<letras>.apps.googleusercontent.com`): {bruto!r}. "
                "Se ainda tem `…` ou `<>`, é o exemplo da receita, não a "
                "credencial")
    return ""


def dominio_ok(email: str, dominio_permitido: str) -> bool:
    """Sem domínio configurado, qualquer conta entra — de propósito, para o
    desenvolvimento local não exigir configuração. Em produção o `.env` define
    `EASYCONTIG_DOMINIO` e a checagem passa a valer.

    **O SUBDOMÍNIO ENTRA JUNTO** (2026-08-06). A comparação era do domínio
    inteiro, e universidade brasileira reparte o e-mail por unidade: com
    `ufrrj.br` na lista, `@ppgcv.ufrrj.br` e `@lhv.ufrrj.br` eram RECUSADOS —
    justamente o pessoal do laboratório para quem o app é feito, e a recusa
    chegaria como "conta fora do domínio", sem pista do motivo.

    O ponto exigido antes do sufixo é o que separa filho de sósia: `ufrrj.br`
    cobre `lhv.ufrrj.br`, e **não** cobre `falso-ufrrj.br`, que é um domínio de
    outro dono. Quem quiser o domínio exato e nada abaixo dele escreve `=`
    na frente: `=ufrrj.br`."""
    if not dominio_permitido:
        return True
    if "@" not in email:
        return False
    de = email.rsplit("@", 1)[-1].lower().strip().rstrip(".")
    for bruto in dominio_permitido.split(","):
        d = bruto.strip().lower().lstrip("@").rstrip(".")
        if not d:
            continue
        if d.startswith("="):
            if de == d[1:]:
                return True
        elif de == d or de.endswith("." + d):
            return True
    return False


AUTORIZA = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
PERFIL = "https://openidconnect.googleapis.com/v1/userinfo"
ESTADO_CHAVE = "oauth_estado"


def url_de_ida(request: Request, redirect_uri: str) -> str:
    """Para onde mandar quem clicou em "Entrar com Google"."""
    import secrets
    import urllib.parse
    estado = secrets.token_urlsafe(24)
    request.session[ESTADO_CHAVE] = estado
    return AUTORIZA + "?" + urllib.parse.urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": estado,
        # `select_account` evita o login silencioso em máquina compartilhada,
        # que é o caso de um computador de laboratório.
        "prompt": "select_account",
    })


def usuario_da_volta(request: Request, code: str, estado: str,
                     redirect_uri: str) -> Usuario:
    """Confere o `state`, troca o código por token e devolve quem entrou."""
    import json
    import urllib.parse
    import urllib.request

    esperado = request.session.pop(ESTADO_CHAVE, None)
    if not esperado or estado != esperado:
        # Sem isto, um link forjado completaria o login da conta do atacante
        # dentro do navegador da vítima.
        raise HTTPException(status_code=400, detail="estado inválido; tente entrar de novo")

    corpo = urllib.parse.urlencode({
        "code": code,
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(TOKEN, data=corpo), timeout=20) as r:
            token = json.loads(r.read())["access_token"]
        req = urllib.request.Request(PERFIL, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            perfil = json.loads(r.read())
    except HTTPException:
        raise
    except Exception:                            # noqa: BLE001
        raise HTTPException(status_code=502, detail="o Google não respondeu; tente de novo")

    email = (perfil.get("email") or "").strip().lower()
    if not email or not perfil.get("email_verified"):
        # Conta sem e-mail confirmado não prova domínio, e é o domínio que
        # decide quem entra.
        raise HTTPException(status_code=403, detail="o Google não confirmou este e-mail")
    return Usuario(email=email, nome=perfil.get("name") or email.split("@")[0])


def usuario_da_sessao(request: Request) -> Usuario | None:
    d = request.session.get(SESSAO_CHAVE)
    return Usuario(**d) if d else None


def entrar_na_sessao(request: Request, u: Usuario) -> None:
    request.session[SESSAO_CHAVE] = {"email": u.email, "nome": u.nome}


def sair_da_sessao(request: Request) -> None:
    request.session.pop(SESSAO_CHAVE, None)


def exigir_usuario(request: Request) -> Usuario:
    u = usuario_da_sessao(request)
    if not u:
        raise HTTPException(status_code=401, detail="não autenticado")
    return u
