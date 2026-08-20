#? ROTAS DE CONSULTA DE LOTE — olhar um lote pronto: página, traço, CSV e relatório
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
from ..processamento import executor_de_arvore as executor_arvore
from ..processamento import executor_de_lote as executor
from ..processamento import fila_de_arvores as fila_arvores
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


@router.get("/api/lotes/{lote_id}/amostras/{chave}/consultar")
def api_consultar(request: Request, lote_id: str, chave: str, banco: str):
    """Consulta o consenso da amostra contra UM banco extra.

    ⚠️ Não é identificação: o veredito continua vindo do banco curado, e esta
    resposta é rotulada como consulta. Trocar uma coisa pela outra mudaria o
    resultado validado sem ninguém decidir (ADR 0037).
    """
    u = _exigir(request)
    _lote_do_usuario(lote_id, u)
    rep = _relatorio(lote_id)
    am = traco.amostra_do_relatorio(rep, chave) if rep else None
    if not am:
        raise HTTPException(status_code=404, detail="amostra não encontrada")
    permitidos = set(bancos.POR_ID) | {b["id"] for b in
                                       bancos.meus_bancos(cfg().data_dir, u.email)}
    if banco not in permitidos or not bancos.existe(cfg().data_dir, banco):
        raise HTTPException(status_code=404, detail="banco não montado")
    # UMA vaga para os dois passos: soltar entre o tracy e o blastn deixaria a
    # janela em que 40 requisições passam pelo primeiro teto e se acumulam no
    # segundo, que é o caro (timeout de 120 s).
    with _vaga_pesada():
        asm = traco.montar(cfg(), executor.pastas_do_lote(cfg(), lote_id)["raiz"], am)
        if asm is None:
            raise HTTPException(status_code=409,
                                detail="não foi possível remontar a amostra")
        try:
            hits = bancos.consultar(asm.consensus_nogap,
                                    bancos.prefixo(cfg().data_dir, banco),
                                    blast_bin=cfg().blast_bin)
        except Exception as e:                  # noqa: BLE001
            # ⚠️ O `str(e)` do blastn/E-S carrega caminho absoluto do servidor e
            # stderr da ferramenta — detalhe interno que não vai no 500. Genérico
            # para quem chama, completo no log do servidor.
            print(f"  ⚠️  falha ao consultar o banco {banco}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
            raise HTTPException(
                status_code=500,
                detail="não foi possível consultar o banco; tente de novo. "
                       "O motivo completo está no log.")
    return JSONResponse({"banco": banco, "hits": hits, "consulta": True})


def _relatorio(lote_id: str) -> dict | None:
    """Os fatos do lote, já lidos do `relatorio.json` gravado pelo trabalhador.

    None quando o arquivo não existe ou não tem forma de relatório — a tela diz
    "relatório indisponível" em vez de mostrar uma lista vazia, que passaria por
    "lote sem amostras" e é o tipo de defeito que se parece com resultado.
    """
    return mod_amostras.carregar(
        executor.pastas_do_lote(cfg(), lote_id)["relatorio_json"])


@router.get("/lotes/{lote_id}", response_class=HTMLResponse)
def pagina_lote(request: Request, lote_id: str, tela_do_lote: int = 0):
    u = _exigir(request)
    lote = _lote_do_usuario(lote_id, u)
    rep = _relatorio(lote_id) if lote["status"] == fila.PRONTO else None

    # Quando o envio é UM PAR — o uso normal, ADR 0051 — o lote tem uma amostra
    # só, e a "tabela de amostras" é uma linha. O resultado que a pessoa veio
    # buscar é a página da amostra; mostrar antes uma tela de lote com um item
    # cobra um clique à toa. `?tela_do_lote=1` é a saída para quem quer mesmo a
    # tela do lote — e é para onde a página da amostra volta, senão daria laço.
    if rep and not tela_do_lote:
        itens = mod_amostras.listar(rep)
        if len(itens) == 1:
            return RedirectResponse(
                f"/lotes/{lote_id}/amostras/{quote(itens[0]['key'])}", status_code=303)

    return TEMPLATES.TemplateResponse(request, "lote.html", {
        **_casca(u), "lote": lote,
        # a página só se recarrega sozinha enquanto há o que esperar
        "recarregar": lote["status"] in (fila.RECEBENDO, fila.NA_FILA, fila.RODANDO),
        # Só se pergunta quando importa: um lote esperando na fila é o único
        # caso em que a tela promete que alguém vai pegar.
        "fila_atendida": (fila.fila_atendida(cfg().sqlite_path)
                          if lote["status"] == fila.NA_FILA else True),
        "amostras": mod_amostras.listar(rep) if rep else None,
        "resumo": mod_amostras.resumo(rep) if rep else None,
        "expira_em": retencao.expira_em(lote, cfg().retencao_dias),
    })


@router.get("/api/lotes/{lote_id}/amostras/{chave}/traco")
def api_traco(request: Request, lote_id: str, chave: str):
    """O cromatograma das duas leituras, no eixo de coluna do consenso.

    Sai por API e não embutido na página porque é o pedaço pesado (~112 KB
    comprimidos): a página abre com os números na hora, e o traço chega depois.
    Remontar custa ~0,3 s de tracy — é feito aqui, sob demanda.
    """
    u = _exigir(request)
    _lote_do_usuario(lote_id, u)
    rep = _relatorio(lote_id)
    if not rep:
        raise HTTPException(status_code=404, detail="relatório indisponível")
    am = traco.amostra_do_relatorio(rep, chave)
    if not am:
        raise HTTPException(status_code=404, detail="amostra não encontrada no lote")
    lote_dir = executor.pastas_do_lote(cfg(), lote_id)["raiz"]
    with _vaga_pesada():
        dados = traco.para_navegador(traco.montar(cfg(), lote_dir, am), am)
    if not dados:
        # Sem traço a página não quebra: ela mostra os números e diz por quê.
        return JSONResponse({"disponivel": False,
                             "motivo": "os arquivos originais não estão mais no "
                                       "servidor, ou esta amostra não é um par F+R"},
                            status_code=200)
    dados["disponivel"] = True
    return JSONResponse(dados)


@router.get("/lotes/{lote_id}/amostras/{chave}", response_class=HTMLResponse)
def pagina_amostra(request: Request, lote_id: str, chave: str):
    u = _exigir(request)
    lote = _lote_do_usuario(lote_id, u)
    rep = _relatorio(lote_id)
    if not rep:
        raise HTTPException(status_code=404, detail="relatório indisponível")
    a = mod_amostras.amostra(rep, chave)
    if not a:
        raise HTTPException(status_code=404, detail="amostra não encontrada no lote")
    return TEMPLATES.TemplateResponse(request, "amostra.html", {
        **_casca(u), "lote": lote, "amostra": a,
        # só os bancos montados: oferecer o que não está pronto é oferecer erro
        "consultaveis": [r for r in _referencias(u)
                         if r["id"] != (lote.get("referencia") or "curado")],
        "referencia_usada": lote.get("referencia") or "curado",
    })


@router.get("/api/lotes/{lote_id}")
def api_lote(request: Request, lote_id: str):
    u = _exigir(request)
    return JSONResponse(_lote_do_usuario(lote_id, u))


# ── árvore filogenética do lote ──────────────────────────────────────────────
# O botão fica na página do lote pronto. A rota só ENFILEIRA — montar é MCMC,
# de segundos a minutos, e é a mesma razão que tirou o lote da requisição.

@router.post("/lotes/{lote_id}/arvore")
def pedir_arvore(request: Request, lote_id: str):
    u = _exigir(request)
    lote = _lote_do_usuario(lote_id, u)
    if lote["status"] != fila.PRONTO:
        raise HTTPException(status_code=409,
                            detail="a árvore só pode ser montada depois que o "
                                   "lote terminar")
    fila_arvores.novo_pedido(cfg().sqlite_path, lote_id=lote_id, dono=u.email)
    return RedirectResponse(f"/lotes/{lote_id}/arvore", status_code=303)


def _pedido_do_usuario(lote_id: str, u: auth.Usuario) -> dict:
    _lote_do_usuario(lote_id, u)     # confere o dono pelo LOTE, como o resto
    pedido = fila_arvores.do_lote(cfg().sqlite_path, lote_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="nenhuma árvore pedida "
                                                    "para este lote")
    return pedido


@router.get("/lotes/{lote_id}/arvore", response_class=HTMLResponse)
def pagina_arvore(request: Request, lote_id: str):
    u = _exigir(request)
    lote = _lote_do_usuario(lote_id, u)
    pedido = fila_arvores.do_lote(cfg().sqlite_path, lote_id)
    return TEMPLATES.TemplateResponse(request, "arvore.html", {
        **_casca(u), "lote": lote, "pedido": pedido,
        "resumo": fila_arvores.resumo_lido(pedido) if pedido else [],
        "recarregar": bool(pedido and pedido["status"] in fila_arvores.ATIVOS),
        # Mesma honestidade da tela do lote: só se promete que alguém vai pegar
        # quando há trabalhador vivo para cumprir.
        "fila_atendida": (fila.fila_atendida(cfg().sqlite_path)
                          if pedido and pedido["status"] == fila_arvores.NA_FILA
                          else True),
    })


@router.get("/api/lotes/{lote_id}/arvore")
def api_arvore(request: Request, lote_id: str):
    u = _exigir(request)
    return JSONResponse(_pedido_do_usuario(lote_id, u))


@router.get("/lotes/{lote_id}/arvore/{pasta}/{arquivo}")
def arquivo_da_arvore(request: Request, lote_id: str, pasta: str, arquivo: str):
    """A figura ou o Newick de um marcador.

    O Newick sai como download porque o destino dele é outro programa — FigTree,
    iTOL, MEGA. Quem publica não vai usar o PNG do EasyContig na figura final, e
    fingir o contrário seria prender o usuário numa saída pior que a dele.
    """
    u = _exigir(request)
    _lote_do_usuario(lote_id, u)
    try:
        caminho = executor_arvore.caminho_de_saida(cfg(), lote_id, pasta, arquivo)
    except ValueError:
        raise HTTPException(status_code=404, detail="arquivo não encontrado")
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="arquivo não gerado")
    if caminho.suffix == ".png":
        return FileResponse(caminho, media_type="image/png")
    return FileResponse(caminho, media_type="text/plain",
                        filename=f"{pasta}_{lote_id}{caminho.suffix}")


@router.post("/lotes/{lote_id}/apagar")
def apagar_lote(request: Request, lote_id: str):
    """Apagar agora, sem esperar o prazo.

    Existe porque a retenção automática não substitui isto: quem mandou um lote
    por engano — a pasta errada, o dado de outro projeto — precisa poder tirá-lo
    do servidor no mesmo minuto, e não em 90 dias. `_lote_do_usuario` garante
    que só o dono chega aqui.
    """
    u = _exigir(request)
    _lote_do_usuario(lote_id, u)
    if not retencao.apagar_lote(cfg().sqlite_path, cfg().lotes_dir, lote_id):
        # A remoção recusa lote em `recebendo`/`rodando`: há outro processo
        # lendo ou escrevendo aquela pasta neste instante.
        raise HTTPException(status_code=409,
                            detail="o lote está em uso; tente de novo quando terminar")
    return RedirectResponse("/", status_code=303)


def _arquivo(lote_id: str, u: auth.Usuario, chave: str, midia: str,
             baixar_como: str | None = None) -> FileResponse:
    lote = _lote_do_usuario(lote_id, u)
    if lote["status"] != fila.PRONTO:
        raise HTTPException(status_code=409,
                            detail=f"lote ainda em '{lote['status']}'")
    caminho = executor.pastas_do_lote(cfg(), lote_id)[chave]
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="arquivo não gerado")
    return FileResponse(caminho, media_type=midia, filename=baixar_como)


@router.get("/lotes/{lote_id}/relatorio")
def relatorio(request: Request, lote_id: str):
    # Servido como HTML e não como PDF de propósito: quem converte é o navegador
    # (Ctrl+P → Salvar como PDF). O único trecho do projeto que dependia de Qt
    # para gerar PDF era `app/ui/report_pdf.py`, e na web ele deixa de existir.
    return _arquivo(lote_id, _exigir(request), "relatorio_html", "text/html")


@router.get("/lotes/{lote_id}/resultado.csv")
def resultado_csv(request: Request, lote_id: str):
    return _arquivo(lote_id, _exigir(request), "resultado_csv", "text/csv",
                    baixar_como=f"resultado_{lote_id}.csv")


@router.get("/lotes/{lote_id}/relatorio.json")
def relatorio_json(request: Request, lote_id: str):
    return _arquivo(lote_id, _exigir(request), "relatorio_json", "application/json",
                    baixar_como=f"relatorio_{lote_id}.json")


