"""
auth.py — quem é o usuário, e se ele pode entrar.

Dois provedores atrás da mesma costura:

  * `dev`     — digita o e-mail e entra. Só para desenvolvimento local; recusa
                subir se não estiver explicitamente ligado.
  * `google`  — OAuth do Google, como decidido na ADR 0050 (não gov.br).

`EASYCONTIG_DOMINIO=ufrrj.br` restringe a entrada ao domínio institucional. Isso
é o que permite usar uma conta Google comum e ainda assim ser um serviço da
universidade: o Google prova QUEM é, o domínio decide SE entra.

⚠️ O caminho `google` está escrito mas NÃO foi exercitado — depende de um
CLIENT_ID/SECRET que só o autor pode criar no console do Google Cloud. Até
alguém rodar isso com credencial de verdade, considere não testado.
"""
from __future__ import annotations

import os
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


def dominio_ok(email: str, dominio_permitido: str) -> bool:
    """Sem domínio configurado, qualquer conta entra — de propósito, para o
    desenvolvimento local não exigir configuração. Em produção o `.env` define
    `EASYCONTIG_DOMINIO` e a checagem passa a valer."""
    if not dominio_permitido:
        return True
    permitidos = {d.strip().lower() for d in dominio_permitido.split(",") if d.strip()}
    return "@" in email and email.rsplit("@", 1)[-1].lower() in permitidos


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
