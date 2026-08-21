#? ROTAS DE ENTRADA — entrada e sessão: a raiz, o login e a saída
#!
#! 1. Só rotas. O que elas usam em comum mora em `comum.py`.
#! 2. O `app` não é montado aqui: este arquivo expõe um `router`, e quem o
#!    inclui é o `servidor_web`.

#? SERVIDOR WEB — Decisão sobre não processar nada aqui 04/08/2026
#!
#! 1. Sobe assim: `uvicorn easycontig_web.servidor_web:app`
#! 2. REGRA DE OURO: este arquivo NÃO processa nada. Recebe arquivo, grava em
#!    disco, enfileira e responde.
#! 3. Todo trabalho pesado é do trabalhador, em outro processo.
#! 4. É a diferença entre aguentar 100 pessoas e cair com 20.
from __future__ import annotations

import errno
import os
import sys
import traceback
import secrets
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from ..dados import leitura_de_amostras as mod_amostras
from ..contas import autenticacao as auth
from ..dados import bancos_de_referencia as bancos
from .. import configuracao as config
from ..contas import cota_de_espaco as cotas
from ..processamento import executor_de_lote as executor
from ..processamento import fila_de_lotes as fila
from ..contas import limite_de_requisicoes as limites
from ..dados import pedidos_entre_labs as pedidos
from ..contas import perfil_do_laboratorio as perfil
from ..dados import expurgo_por_retencao as retencao
from ..dados import cromatograma as traco


cfg = config.carregar()

# ⚠️ Trava de arranque: `EASYCONTIG_PRODUCAO=1` recusa subir com a porta aberta.
# Fica atrás de uma variável para o desenvolvimento local não precisar de nada,
# e o `docker-compose` de produção a define.
if os.environ.get("EASYCONTIG_PRODUCAO") == "1":
    _problemas = config.conferir_producao()
    if _problemas:
        raise RuntimeError(
            "EASYCONTIG_PRODUCAO=1 e a configuração não está pronta:\n  - "
            + "\n  - ".join(_problemas))

# ⚠️ O `GOOGLE_CLIENT_ID` errado é MUDO deste lado: `google_configurado()` aceita
# qualquer texto, e o erro só aparece na página do Google (`401 invalid_client`,
# "The OAuth client was not found") depois de a pessoa escolher a conta. Aviso,
# não trava: quem sabe se o cliente existe é o Google, e recusar subir por causa
# de um formato seria trocar um palpite por outro.
if os.environ.get("EASYCONTIG_AUTH", "dev").lower() == "google":
    _queixa = auth.queixa_do_client_id()
    if _queixa:
        print("\n  ⚠️  " + _queixa + "\n", file=sys.stderr, flush=True)


# `docs_url=None` em produção: `/api/docs`, `/redoc` e `/openapi.json` respondiam
# 200 SEM sessão (verificado em 2026-08-06) e publicam o mapa inteiro da API —
# toda rota, todo parâmetro, todo formato. Não é vazamento de dado, é o mapa de
# onde procurar, entregue a quem ainda não entrou. Em desenvolvimento continua
# ligado, que é onde ele serve para alguma coisa.
from fastapi import APIRouter

from .comum import (
    EXT_ACEITAS, TEMPLATES, _br, _casca, _exigir, _lote_do_usuario, _marcar_resposta,
    _pegar_recado, _por_recado, _referencias, _u, _vaga_pesada, cfg,
)

router = APIRouter()


# --------------------------------------------------------------------- páginas
@router.get("/", response_class=HTMLResponse)
def inicio(request: Request):
    u = _u(request)
    if not u:
        return RedirectResponse("/entrar", status_code=303)
    return TEMPLATES.TemplateResponse(request, "inicio.html", {
        **_casca(u), "pagina": "inicio",
        "lotes": fila.listar(cfg().sqlite_path, dono=u.email),
        "diagnostico": config.diagnostico(cfg()),
        "trim": cfg().trim,
        "max_arquivos": cfg().max_arquivos,
        "max_mb": cfg().max_bytes // (1024 * 1024),
        "cota": cotas.situacao(cfg().sqlite_path, cfg().lotes_dir, u.email, cfg().data_dir),
        "retencao_dias": cfg().retencao_dias,
        "referencias": _referencias(u),
    })


def _destino(proximo: str) -> str:
    """Para onde mandar depois do login. Só caminho local — nunca outro site.

    `proximo` chega pela URL, então é entrada do usuário: sem esta checagem, um
    link `/entrar?proximo=https://outro.site` faria a nossa página de login
    despejar a pessoa em outro domínio logo depois de ela digitar o e-mail.
    Exigir que comece com uma barra só (e não `//`, que o navegador lê como
    outro host) resolve.
    """
    # ⚠️ A barra invertida também escapa. Verificado em 2026-08-06:
    # `/\evil.com` passava por esta checagem, e o navegador normaliza `\` para
    # `/` antes de resolver o endereço — ou seja, vira `//evil.com`, que é outro
    # host. A regra passa a ser positiva: começa com UMA barra e o segundo
    # caractere não pode ser separador nenhum.
    if not proximo.startswith("/") or proximo.startswith(("//", "/\\")):
        return "/"
    # Caractere de controle no meio (`\t`, `\n`, `\r`) é removido por alguns
    # navegadores ANTES de resolver, então `/%09/evil.com` decodificado poderia
    # virar outra coisa. Nenhum destino legítimo do site tem controle no caminho.
    if any(c in proximo for c in "\t\n\r\\"):
        return "/"
    return proximo


@router.get("/entrar", response_class=HTMLResponse)
def pagina_entrar(request: Request, proximo: str = ""):
    return TEMPLATES.TemplateResponse(request, "entrar.html", {
        "modo": auth.modo(),
        "google_ok": auth.google_configurado(),
        # Só os domínios, nunca os endereços nomeados: esta página é pública
        # (ver `auth.dominios_publicos`).
        "dominio": auth.dominios_publicos(cfg().dominio_permitido),
        "erro": _pegar_recado(request),
        "proximo": _destino(proximo),
    })


def _fora_do_dominio() -> str:
    """A mensagem de recusa, sem vazar os endereços nomeados.

    Ela volta para QUEM TENTOU entrar — no modo dev, qualquer anônimo — então
    reflete só os domínios públicos, pela mesma razão da página `/entrar`. Lista
    só de pessoas vira uma frase genérica em vez de um recado com o domínio em
    branco.
    """
    pub = auth.dominios_publicos(cfg().dominio_permitido)
    return f"conta fora do domínio {pub}" if pub else "conta não autorizada"


@router.post("/entrar")
def entrar_dev(request: Request, email: str = Form(...), proximo: str = Form("")):
    # ⚠️ A ORDEM IMPORTA. O teto vinha ANTES da checagem de modo, então em
    # produção — onde esta rota sempre devolve 403 — cada batida ainda queimava
    # o orçamento de login, que atrás de um proxy é COMPARTILHADO por todo
    # visitante anônimo. Ou seja: uma rota desligada servia para trancar o
    # login de quem usa. Achado pelo painel de verificação em 2026-08-06.
    if auth.modo() != "dev":
        raise HTTPException(status_code=403, detail="login de desenvolvimento desligado")
    # Teto no login: sem ele, tentar e-mail atrás de e-mail é adivinhação de
    # graça — e no modo `dev` cada tentativa que acerta o formato ENTRA.
    limites.conferir("login", request)
    email = email.strip().lower()
    destino = _destino(proximo)
    # Sem `?erro=` na frente, `proximo` passou a ser o único parâmetro que
    # sobra — então ele abre a query com `?` em vez de emendar com `&`.
    volta = f"?proximo={quote(destino)}" if destino != "/" else ""
    if "@" not in email:
        _por_recado(request, "e-mail inválido")
        return RedirectResponse(f"/entrar{volta}", status_code=303)
    if not auth.dominio_ok(email, cfg().dominio_permitido):
        # A mensagem nomeia o domínio a partir da CONFIGURAÇÃO do servidor, e
        # nunca de nada que tenha vindo na requisição.
        _por_recado(request, _fora_do_dominio())
        return RedirectResponse(f"/entrar{volta}", status_code=303)
    auth.entrar_na_sessao(request, auth.Usuario(email=email, nome=email.split("@")[0]))
    # Cadastra no diretório como o Google faz — mas SEM nome: aqui não há
    # provedor que declare um, e o `email.split("@")[0]` acima é só para a
    # lateral (o próprio endereço de quem lê). Passá-lo adiante gravaria o
    # local-part no diretório, que é o achado L1.
    perfil.garantir_registro(cfg().sqlite_path, email)
    return RedirectResponse(destino, status_code=303)


def _redirect_uri(request: Request) -> str:
    """O endereço de volta do Google. Tem que casar EXATAMENTE com o cadastrado
    no console — daí sair da configuração, e não da URL da requisição, que atrás
    de um proxy reverso vem com o host errado."""
    base = os.environ.get("EASYCONTIG_URL_BASE", "").rstrip("/")
    return (base or str(request.base_url).rstrip("/")) + "/auth/google/volta"


@router.get("/auth/google")
def entrar_google(request: Request):
    # Mesma ordem do `entrar_dev`: rota desligada não pode consumir o orçamento
    # compartilhado de quem está tentando entrar de verdade.
    if auth.modo() != "google" or not auth.google_configurado():
        raise HTTPException(status_code=404, detail="login pelo Google não configurado")
    limites.conferir("login", request)
    return RedirectResponse(auth.url_de_ida(request, _redirect_uri(request)),
                            status_code=303)


@router.get("/auth/google/volta")
def volta_google(request: Request, code: str = "", state: str = "", error: str = ""):
    if auth.modo() != "google" or not auth.google_configurado():
        raise HTTPException(status_code=404, detail="login pelo Google não configurado")
    if error or not code:
        # ⚠️ O `error` NUNCA vai para a tela. Ele é parâmetro de query de uma
        # rota pública, ou seja, texto escolhido por quem monta o link — e a
        # página de login é NOSSA, com o nosso domínio e o nosso cadeado. Ecoar
        # deixava um atacante redigir a frase que a vítima lê acima do campo de
        # e-mail ("sua sessão expirou, reenvie suas credenciais em ..."). O
        # Jinja escapa o texto, então não era XSS — era falsificação de conteúdo
        # carregada pelo site verdadeiro, que é o achado F3. Traduz-se para uma
        # frase do servidor; o valor cru vai para o log.
        if error:
            print(f"  ⚠️  retorno do OAuth com error={error!r}",
                  file=sys.stderr, flush=True)
        _por_recado(request, auth.mensagem_de_erro_oauth(error) if error
                    else "entrada cancelada")
        return RedirectResponse("/entrar",
                                status_code=303)
    u = auth.usuario_da_volta(request, code, state, _redirect_uri(request))
    if not auth.dominio_ok(u.email, cfg().dominio_permitido):
        # O Google prova QUEM é; o domínio decide SE entra (ADR 0050).
        _por_recado(request, _fora_do_dominio())
        return RedirectResponse("/entrar", status_code=303)
    auth.entrar_na_sessao(request, u)
    # Entrar cadastra no diretório, com o nome que o Google declarou (ver
    # `perfil.garantir_registro`). Depois da sessão de propósito: se isto
    # falhar, a pessoa entra do mesmo jeito — o diretório é conveniência, não
    # condição para usar o site.
    perfil.garantir_registro(cfg().sqlite_path, u.email, u.nome_google)
    return RedirectResponse("/", status_code=303)


@router.post("/sair")
def sair(request: Request):
    """Sair é POST, não GET (achado L2 de 2026-08-08).

    Como GET, bastava um `<img src="https://.../sair">` numa página qualquer
    para derrubar a sessão de quem abrisse: o cookie `Lax` acompanha navegação
    de topo por GET, e foi reproduzido — `303` + `session=null` com `Referer`
    externo. Não perde dado, mas é ação de estado num verbo que o navegador
    trata como seguro (e que qualquer pré-carregador pode disparar sozinho).

    Como POST, passa pela checagem de `Origin` do `_origem_confere` junto com
    todos os outros — não precisa de guarda própria.
    """
    auth.sair_da_sessao(request)
    # ⚠️ `?saiu=1` não é enfeite: é o que manda o navegador APAGAR os rascunhos
    # de cromatograma do `localStorage` (achado de 2026-08-11, 3 de 3
    # verificadores). Derrubar a sessão é do servidor; o rascunho é do navegador,
    # e nada aqui o alcança — ver o bloco correspondente em `entrar.html`.
    return RedirectResponse("/entrar?saiu=1", status_code=303)


