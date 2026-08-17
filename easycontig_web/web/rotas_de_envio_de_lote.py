#? ROTAS DE ENVIO DE LOTE — o envio de uma corrida: recebe, grava e enfileira
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


# ----------------------------------------------------------------------- lotes
@router.post("/lotes")
async def criar_lote(request: Request,
                     arquivos: list[UploadFile] = File(...),
                     nome: str = Form(""),
                     referencia: str = Form("")):
    u = _exigir(request)
    limites.conferir("envio", request, u.email)

    # TRAVA: sem referência escolhida não se processa. Montar o consenso sem ter
    # contra o que comparar produz um relatório que só diz "não achei" — e "não
    # achei" sem banco declarado é uma frase sem significado. Escolher passa a
    # ser parte do envio, e a escolha fica gravada no lote.
    referencia = (referencia or "").strip()
    if not referencia:
        raise HTTPException(status_code=400,
                            detail="escolha a referência antes de processar")
    permitidos = {"curado"} | set(bancos.POR_ID) | {
        b["id"] for b in bancos.meus_bancos(cfg().data_dir, u.email)}
    if referencia not in permitidos:
        raise HTTPException(status_code=400, detail="referência desconhecida")
    if referencia != "curado" and not bancos.existe(cfg().data_dir, referencia):
        raise HTTPException(
            status_code=400,
            detail="essa referência ainda não foi montada nesta instalação")

    aceitos = [a for a in arquivos
               if Path(a.filename or "").suffix.lower() in EXT_ACEITAS]
    if not aceitos:
        raise HTTPException(status_code=400,
                            detail="nenhum arquivo .ab1/.abi/.scf no envio")
    if len(aceitos) > cfg().max_arquivos:
        raise HTTPException(
            status_code=413,
            detail=f"{len(aceitos)} arquivos; o teto é {cfg().max_arquivos}")

    # A cota é conferida ANTES de `novo_lote`: recusar depois deixaria uma linha
    # no banco e uma pasta em disco de um lote que nunca vai rodar — e é
    # justamente o disco que a cota existe para proteger.
    # ⚠️ Alcance honesto: quando este handler roda, o Starlette já recebeu o
    # corpo inteiro. A cota impede que os bytes sejam GUARDADOS, não que
    # trafeguem; barrar o tráfego exigiria um middleware olhando Content-Length.
    situacao = cotas.situacao(cfg().sqlite_path, cfg().lotes_dir, u.email, cfg().data_dir)
    if not situacao.pode_enviar:
        raise HTTPException(status_code=413, detail=situacao.motivo)

    # O teto vai JUNTO para dentro da transação que cria o lote. A conferência
    # acima sozinha tem uma janela: medido em 06/08, com teto de 1 e oito envios
    # simultâneos da mesma conta, sete entraram — todos passaram pela contagem
    # antes de qualquer um aparecer nela.
    # ⚠️ E o teto de BYTES vai junto (achado L3 de 2026-08-08). A conferência do
    # `situacao` acima é medida do DISCO, e disco só conta byte que já chegou:
    # dez envios simultâneos liam o mesmo `usados` antes de qualquer um gravar,
    # e os dez passavam — ~3 GB contra uma cota de 2 GiB. A reserva
    # (`bytes_previstos`) aparece no INSERT e é lida dentro da MESMA transação,
    # então o segundo a entrar já enxerga o primeiro.
    #
    # O tamanho declarado vem do `Content-Length`, o mesmo cabeçalho do teto de
    # corpo. Superestima (traz a moldura do multipart), e é o lado certo de
    # errar: reserva a mais barra envio legítimo no limite, reserva a menos
    # deixa a cota furar.
    try:
        declarado = int(request.headers.get("content-length") or 0)
    except ValueError:
        declarado = 0
    try:
        lote_id = fila.novo_lote(cfg().sqlite_path, dono=u.email,
                                 nome=nome.strip() or "lote", n_arquivos=len(aceitos),
                                 referencia=referencia,
                                 teto_ativos=cotas.teto_lotes_ativos(),
                                 bytes_previstos=declarado,
                                 teto_bytes=situacao.max_bytes_conta,
                                 usados_em_disco=situacao.bytes_usados)
    except fila.CotaEstourada as e:
        raise HTTPException(
            status_code=413,
            detail=f"você já tem {e.ativos} corrida(s) em processamento, e o "
                   f"teto é {e.teto}. Espere uma terminar e envie de novo.")
    except fila.CotaDeBytesEstourada:
        raise HTTPException(
            status_code=413,
            detail="este envio não cabe no espaço que sobra para a sua conta. "
                   "Apague uma corrida antiga ou um banco próprio e tente de novo.")
    p = executor.pastas_do_lote(cfg(), lote_id)
    p["entrada"].mkdir(parents=True, exist_ok=True)

    total = 0
    gravados = 0
    try:
        for a in aceitos:
            # basename: o navegador manda "pasta/sub/arquivo.ab1" quando o
            # usuário escolhe uma PASTA, e um nome com ".." escreveria fora do
            # lote. O caminho vira só o nome do arquivo, sempre.
            destino = p["entrada"] / Path(a.filename or "sem_nome").name
            with destino.open("wb") as fh:
                while pedaco := await a.read(1024 * 1024):
                    total += len(pedaco)
                    if total > cfg().max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"lote acima de {cfg().max_bytes // (1024*1024)} MB")
                    fh.write(pedaco)
            gravados += 1
    except OSError as e:
        # ⚠️ DISCO CHEIO, exercitado em 2026-08-06 contra um sistema de arquivos
        # de 1 MB de verdade: a limpeza funcionava (lote marcado `falhou`, pasta
        # removida, nada na fila), mas quem enviou recebia `500 Internal Server
        # Error` cru — sem casca, sem motivo — e o aviso do navegador mandava
        # "corrija a seleção e envie de novo", que é o conselho errado: não há
        # nada errado com a seleção, e reenviar não vai funcionar.
        shutil.rmtree(p["raiz"], ignore_errors=True)
        fila.falhar(cfg().sqlite_path, lote_id, f"falha ao gravar no disco: {e.strerror}")
        sem_espaco = e.errno == errno.ENOSPC
        print(f"  ⚠️  falha de disco ao receber o lote {lote_id}: {e}",
              file=sys.stderr, flush=True)
        raise HTTPException(
            status_code=507 if sem_espaco else 500,
            detail=("o servidor está sem espaço em disco — nada foi gravado. "
                    "Não é a sua seleção, e reenviar não resolve: avise quem "
                    "cuida do servidor."
                    if sem_espaco else
                    f"o servidor não conseguiu gravar os arquivos ({e.strerror}). "
                    "Nada foi processado."))
    except Exception:
        shutil.rmtree(p["raiz"], ignore_errors=True)
        fila.falhar(cfg().sqlite_path, lote_id, "falha ao receber os arquivos")
        raise

    # SÓ AGORA o lote fica visível para o trabalhador. Enfileirar antes desta
    # linha é o defeito que produziu um relatório de 33 das 40 amostras sem
    # avisar da falta — ver o comentário de RECEBENDO em fila.py.
    fila.liberar_para_fila(cfg().sqlite_path, lote_id, gravados)
    return RedirectResponse(f"/lotes/{lote_id}", status_code=303)


