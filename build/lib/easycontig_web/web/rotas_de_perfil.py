#? ROTAS DE PERFIL — leia-me, perfil do laboratório e as fotos
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


@router.get("/leiame", response_class=HTMLResponse)
def pagina_leiame(request: Request):
    """Como nomear os arquivos e como o motor decide F/R.

    Nasceu de um caso real (2026-08-07): uma corrida com 81 arquivos nomeados
    só pelo poço da placa produziu 81 leituras avulsas, e quem enviou leu isso
    como falha do programa. O conteúdo está aqui e não só na aba de envio
    porque a pergunta reaparece depois — na hora de entender o resultado.

    ⚠️ A página afirma que a orientação sai do DNA e NÃO de metadado do `.ab1`
    nem do nome. Isso é o que `app/core/orientation.py` faz (k-mers da
    sequência contra k-mers do reverso-complemento) e é a tese do produto: se
    alguém mudar o motor, esta página passa a mentir para o laboratório.
    """
    return TEMPLATES.TemplateResponse(request, "leiame.html", {
        **_casca(_exigir(request)), "pagina": "leiame"})


@router.get("/perfil", response_class=HTMLResponse)
def pagina_perfil(request: Request, editando: int = 0):
    u = _exigir(request)
    lotes = fila.listar(cfg().sqlite_path, dono=u.email, limite=500)
    # Só a CONTAGEM de amostras é lida de cada relatório — é tudo o que a tabela
    # e o resumo usam. Antes esta página decodificava o `relatorio.json` inteiro
    # de cada corrida da conta (25,89 ms para 40; o teto aqui é 500) para no fim
    # tirar um inteiro de cada um. Ver `amostras.contar_amostras`.
    reps = []
    for l in lotes:
        if l["status"] == fila.PRONTO:
            n = mod_amostras.contar_amostras(
                executor.pastas_do_lote(cfg(), l["id"])["relatorio_json"])
            if n is not None:
                reps.append({"lote": l, "n": n})
    return TEMPLATES.TemplateResponse(request, "perfil.html", {
        **_casca(u), "pagina": "perfil",
        "perfil": perfil.pegar(cfg().sqlite_path, u.email),
        "relatorios": reps,
        "resumo": perfil.resumo_das_corridas(lotes, [x["n"] for x in reps]),
        "cota": cotas.situacao(cfg().sqlite_path, cfg().lotes_dir, u.email, cfg().data_dir),
        "editando": bool(editando),
    })


@router.post("/perfil")
async def salvar_perfil(request: Request,
                        nome: str = Form(""), laboratorio: str = Form(""),
                        instituicao: str = Form(""), sobre: str = Form(""),
                        especies: str = Form(""), marcadores: str = Form(""),
                        links: str = Form(""),
                        foto: UploadFile | None = File(None)):
    u = _exigir(request)
    # Salvar o perfil é escrita: sem teto, um laço no formulário grava foto atrás
    # de foto. Reaproveita o teto de envio, que é o balde de "esta conta está
    # escrevendo no servidor".
    limites.conferir("envio", request, u.email)
    nome_foto = None
    if foto is not None and foto.filename:
        ext = Path(foto.filename).suffix.lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise HTTPException(status_code=400,
                                detail="a foto precisa ser .png, .jpg ou .webp")
        dados = await foto.read(2 * 1024 * 1024 + 1)
        if len(dados) > 2 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="a foto passa de 2 MB")
        pasta = cfg().data_dir / "fotos"
        pasta.mkdir(parents=True, exist_ok=True)
        # Nome sorteado, não derivado do arquivo enviado: nome de arquivo é
        # entrada do usuário e não decide caminho em disco.
        nome_foto = secrets.token_urlsafe(8) + ext
        (pasta / nome_foto).write_bytes(dados)
        # A ANTERIOR SAI. Sem isto cada troca de foto deixava até 2 MB órfãos no
        # volume para sempre: nada apontava mais para o arquivo, nenhuma cota o
        # contava (a cota mede as pastas de LOTE) e a retenção não o alcança.
        antigo = (perfil.pegar(cfg().sqlite_path, u.email) or {}).get("foto")
        if antigo and antigo != nome_foto:
            (pasta / Path(antigo).name).unlink(missing_ok=True)
    perfil.salvar(cfg().sqlite_path, u.email, nome=nome, laboratorio=laboratorio,
                  instituicao=instituicao, sobre=sobre, especies=especies,
                  marcadores=marcadores, foto=nome_foto, links=links)
    return RedirectResponse("/perfil", status_code=303)


@router.get("/perfil/foto")
def foto_do_perfil(request: Request):
    """A foto sai por rota, e não de uma pasta estática servida direto: assim
    ela continua sendo de quem entrou, e não um arquivo público adivinhável."""
    u = _exigir(request)
    p = perfil.pegar(cfg().sqlite_path, u.email)
    if not p["foto"]:
        raise HTTPException(status_code=404, detail="sem foto")
    caminho = cfg().data_dir / "fotos" / Path(p["foto"]).name
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="sem foto")
    return FileResponse(caminho)


@router.get("/labs/foto/{arquivo}")
def foto_do_diretorio(request: Request, arquivo: str):
    """A foto de QUALQUER perfil, para o diretório desenhar os cartões.

    A `/perfil/foto` continua existindo e continua sendo só do dono; esta é a
    outra pergunta: mostrar a foto dos outros no `/labs`. O receio anotado no
    template era servir foto **por e-mail**, o que daria uma rota adivinhável —
    quem soubesse o endereço de alguém pediria a foto dele. Aqui o endereço é o
    **nome do arquivo**, que é `secrets.token_urlsafe(8)` sorteado no envio: não
    se adivinha, não diz de quem é, e não se deriva do e-mail.

    Três travas, e nenhuma delas confia na anterior:
      * `_exigir` — o diretório inteiro é atrás do login;
      * `Path(...).name` — o `{arquivo}` não vira caminho para fora da pasta;
      * `foto_registrada` — só sai o que está na coluna `foto` de algum perfil,
        e não qualquer arquivo que exista no volume.
    """
    _exigir(request)
    nome = Path(arquivo).name
    if not perfil.foto_registrada(cfg().sqlite_path, nome):
        raise HTTPException(status_code=404, detail="sem foto")
    caminho = cfg().data_dir / "fotos" / nome
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="sem foto")
    return FileResponse(caminho)


