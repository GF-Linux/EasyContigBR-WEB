#? ROTAS DE LABS E PEDIDOS — o diretório de laboratórios e os pedidos entre eles
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


# ------------------------------------------------------------ labs (diretório)
@router.get("/labs", response_class=HTMLResponse)
def pagina_labs(request: Request):
    """O diretório dos laboratórios cadastrados no site.

    Fica atrás do login como todo o resto (`_exigir`): é uma lista de quem usa o
    site, não uma página pública — nada aqui vai para o Google (o `noindex` do
    middleware vale para toda resposta).

    Mostra só o que a pessoa DECLAROU no perfil (ver `perfil.listar_perfis`) —
    nunca corridas, `.ab1` ou o que o BLAST achou, que é dado não publicado do
    laboratório e não sai daqui por diretório. A foto NÃO é servida por e-mail
    de propósito: a lista usa a inicial, para não abrir uma rota de foto
    adivinhável por endereço.

    Desde 2026-08-10 o diretório deixou de ser só vitrine: cada cartão tem o
    formulário de **pedido de amostra** (ADR 0052 — o portfólio existe para
    outro laboratório achar quem trabalha com o quê e pedir uma sequência).
    """
    u = _exigir(request)
    return TEMPLATES.TemplateResponse(request, "labs.html", {
        **_casca(u), "pagina": "labs",
        "perfis": perfil.listar_perfis(cfg().sqlite_path, u.email),
        "erro": _pegar_recado(request),
        "enviado": request.query_params.get("enviado", "") == "1",
    })


# ------------------------------------------------- pedidos de amostra (0052)
@router.post("/labs/{chave}/pedido")
def pedir_amostra(request: Request, chave: str,
                  itens: list[str] = Form(default=[]),
                  outro: str = Form(""), motivo: str = Form("")):
    """Um laboratório pede uma amostra a outro.

    ⚠️ O alvo vem por `chave` sorteada, nunca por e-mail — se fosse por e-mail,
    o endereço do outro teria de estar no HTML do `/labs`, que é exatamente o
    vazamento fechado pelo achado L1. E nem por hash do e-mail, que confirma
    palpite (ver o cabeçalho de `pedidos.py`).

    **Chave desconhecida responde como pedido inválido, e não 404.** Um 404 aqui
    responderia "esta chave não é de ninguém", transformando a rota num oráculo
    para varrer o espaço de chaves. O custo é uma mensagem menos precisa num
    caso que, para quem usa a tela, não acontece.
    """
    u = _exigir(request)
    limites.conferir("pedido", request, u.email)
    destino = perfil.email_da_chave(cfg().sqlite_path, chave)
    if not destino:
        _por_recado(request, "não foi possível enviar o pedido a este "
                             "laboratório; recarregue a página")
        return RedirectResponse("/labs", status_code=303)
    try:
        pedidos.criar(cfg().sqlite_path, de_email=u.email, para_email=destino,
                      itens=itens, outro=outro, motivo=motivo)
    except pedidos.NaoPode as e:
        _por_recado(request, str(e))
        return RedirectResponse("/labs", status_code=303)
    return RedirectResponse("/pedidos?enviado=1", status_code=303)


@router.get("/pedidos", response_class=HTMLResponse)
def pagina_pedidos(request: Request, enviado: int = 0):
    """A caixa de pedidos: o que pediram a mim, e o que eu pedi.

    Não é caixa de mensagens (ver o cabeçalho de `pedidos.py`): cada linha tem
    no máximo dois textos — o motivo de quem pediu e a justificativa de quem
    respondeu — e um estado final.

    Esta página existe porque o app **não manda e-mail**. Sem SMTP, o único
    lugar onde um pedido pode aparecer é dentro do site, e é por isso que o
    contador vai também na lateral de toda página (`_casca`).
    """
    u = _exigir(request)
    return TEMPLATES.TemplateResponse(request, "pedidos.html", {
        **_casca(u), "pagina": "pedidos",
        "recebidos": pedidos.recebidos(cfg().sqlite_path, u.email),
        "enviados": pedidos.enviados(cfg().sqlite_path, u.email),
        "enviado": bool(enviado),
    })


@router.post("/pedidos/{pedido_id}/responder")
def responder_pedido(request: Request, pedido_id: str,
                     acao: str = Form(""), resposta: str = Form("")):
    """Aceitar ou recusar, sempre com justificativa (decisão do autor).

    ⚠️ **Aceitar libera o e-mail dos dois lados naquele pedido** — é o único
    ponto do app em que um endereço atravessa a fronteira entre contas, e o
    clique aqui é o consentimento que autoriza. A tela avisa disso ANTES do
    botão; se este comportamento mudar, o aviso do template muda junto.
    """
    u = _exigir(request)
    limites.conferir("pedido", request, u.email)
    if acao not in ("aceitar", "recusar"):
        raise HTTPException(status_code=400, detail="ação desconhecida")
    try:
        atualizado = pedidos.responder(cfg().sqlite_path, pedido_id, u.email,
                                       aceitar=(acao == "aceitar"),
                                       resposta=resposta)
    except pedidos.NaoPode as e:
        _por_recado(request, str(e))
        return RedirectResponse("/pedidos", status_code=303)
    if atualizado is None:
        # Não existe, não é seu, ou já foi respondido — a mesma resposta para os
        # três, pelo motivo anotado em `pedidos.responder`.
        raise HTTPException(status_code=404, detail="pedido não encontrado")
    return RedirectResponse("/pedidos", status_code=303)


@router.post("/pedidos/{pedido_id}/cancelar")
def cancelar_pedido(request: Request, pedido_id: str):
    u = _exigir(request)
    if not pedidos.cancelar(cfg().sqlite_path, pedido_id, u.email):
        raise HTTPException(status_code=404, detail="pedido não encontrado")
    return RedirectResponse("/pedidos", status_code=303)


