#? ROTAS DE BANCOS — os bancos de referência: montar, remover e enviar o seu
#!
#! 1. Só rotas. O que elas usam em comum mora em `comum.py`.
#! 2. O `app` não é montado aqui: este arquivo expõe um `router`, e quem o
#!    inclui é o `servidor_web`.

#? SERVIDOR WEB — Decisão sobre não processar nada aqui 04/08/2026
#!
#! 1. Sobe assim: `uvicorn easycontig_web.servidor_web:app`
#! 2. ⚠️ REGRA DE OURO: este arquivo NÃO processa nada. Recebe arquivo, grava em
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


# ------------------------------------------------------- bancos de referência
@router.get("/bancos", response_class=HTMLResponse)
def pagina_bancos(request: Request, feito: str = ""):
    u = _exigir(request)
    grupos: dict[str, list] = {}
    for c in bancos.CATALOGO:
        grupos.setdefault(c.grupo, []).append(
            {"c": c, "estado": bancos.estado(cfg().data_dir, c.id)})
    return TEMPLATES.TemplateResponse(request, "bancos.html", {
        **_casca(u), "grupos": grupos,
        "meus": bancos.meus_bancos(cfg().data_dir, u.email),
        "erro": _pegar_recado(request), "feito": feito,
    })


@router.post("/bancos/{banco_id}/montar")
def montar_banco(request: Request, banco_id: str):
    """Baixa do NCBI e monta. Síncrono de propósito: são segundos para os
    pequenos, e quem aperta está olhando. Os grandes avisam o tamanho antes."""
    u = _exigir(request)
    limites.conferir("banco", request, u.email)
    if banco_id not in bancos.POR_ID:
        raise HTTPException(status_code=404, detail="banco desconhecido")
    try:
        bancos.montar(cfg().data_dir, banco_id, blast_bin=cfg().blast_bin)
    except Exception as e:                      # noqa: BLE001
        _por_recado(request, str(e)[:200])
        return RedirectResponse("/bancos", status_code=303)
    return RedirectResponse(f"/bancos?feito={quote(banco_id)}", status_code=303)


def _e_administrador(u: auth.Usuario) -> bool:
    """Quem pode mexer no que é de todo mundo. Vazio = ninguém, de propósito."""
    lista = {e.strip().lower()
             for e in os.environ.get("EASYCONTIG_ADMINS", "").split(",") if e.strip()}
    return u.email.lower() in lista


@router.post("/bancos/{banco_id}/remover")
def remover_banco(request: Request, banco_id: str):
    u = _exigir(request)
    if banco_id.startswith("meu_"):
        # banco de usuário só some pela mão do dono
        if banco_id not in {b["id"] for b in bancos.meus_bancos(cfg().data_dir, u.email)}:
            raise HTTPException(status_code=404, detail="banco não encontrado")
    elif banco_id not in bancos.POR_ID:
        raise HTTPException(status_code=404, detail="banco desconhecido")
    elif not _e_administrador(u):
        # ⚠️ Verificado em 2026-08-06: QUALQUER conta autenticada removia um banco
        # do catálogo COMPARTILHADO — um estagiário recém-cadastrado derrubava a
        # referência que todo o laboratório usa, e a próxima corrida de qualquer
        # pessoa passava a dizer "sem acerto" sem nada explicando por quê. É a
        # pior forma do defeito que a ADR 0039 descreve: falha da instalação
        # aparecendo como resultado da amostra.
        #
        # Remontar leva segundos, então o estrago é reversível — mas só depois de
        # alguém descobrir o que houve, e nada na tela contaria.
        #
        # MONTAR segue liberado: é aditivo, já tem teto de taxa, e é o fluxo que
        # a página inteira existe para oferecer. Quem apaga é que precisa de nome.
        raise HTTPException(
            status_code=403,
            detail="remover um banco compartilhado é ação de quem administra o "
                   "servidor; defina EASYCONTIG_ADMINS para liberar")
    bancos.remover(cfg().data_dir, banco_id)
    return RedirectResponse("/bancos", status_code=303)


@router.post("/bancos/meu")
async def enviar_banco(request: Request, apelido: str = Form(...),
                       fasta: UploadFile = File(...)):
    u = _exigir(request)
    # ⚠️ Esta rota era a ÚNICA que escreve em disco sem teto de taxa nenhum, e
    # os bancos do usuário também não entram na cota da conta — então uma conta
    # comum enchia o volume onde moram os `.ab1` de todo mundo, 20 MB por vez,
    # sem nada que recolhesse depois. Achado pelo painel de verificação em
    # 2026-08-06. O teto de "banco" é o mesmo que já protege o NCBI na rota de
    # montar: 10 por hora, que para quem monta banco à mão é folgado.
    limites.conferir("banco", request, u.email)

    # ⚠️ Achado M2 de 2026-08-08: faltavam DUAS travas aqui, e o balde de taxa
    # não substitui nenhuma delas. Um banco de usuário não expira (a faxina só
    # apaga lote) e não entrava na cota — então 10 FASTAs de 20 MB por hora,
    # cada um com apelido diferente, eram ~200 MB/h PERMANENTES e invisíveis.
    # `_NOME_OK` aceita 40 caracteres, ou seja o espaço de apelidos é infinito
    # para efeito prático: o teto tinha de ser de QUANTIDADE e de BYTES.
    quanto = cotas.situacao(cfg().sqlite_path, cfg().lotes_dir, u.email, cfg().data_dir)
    if quanto.max_bancos and quanto.bancos >= quanto.max_bancos:
        _por_recado(request, f"você já mantém {quanto.bancos} bancos próprios "
                             f"(o limite é {quanto.max_bancos}); remova um "
                             "antes de montar outro")
        return RedirectResponse("/bancos", status_code=303)
    if not quanto.pode_enviar and quanto.bytes_usados >= quanto.max_bytes_conta > 0:
        _por_recado(request, quanto.motivo)
        return RedirectResponse("/bancos", status_code=303)

    # ⚠️ ESTA ROTA É `async def`, E POR ISSO O `makeblastdb` NÃO PODE SER CHAMADO
    # DIRETO (achado do painel de 2026-08-11, 3 de 3 verificadores).
    #
    # O corpo de uma rota `async def` roda NO EVENT LOOP, não no threadpool. E
    # `montar_do_usuario` termina em `subprocess.run(makeblastdb, timeout=600)`
    # (`bancos.py:171`), que bloqueia de verdade. Com um uvicorn de um
    # trabalhador só — que é o que o `Dockerfile` sobe —, enquanto esse
    # subprocesso roda o servidor **não atende mais ninguém**: nem o login, nem
    # o `/saude`, nem um envio em curso. O teto de taxa é 10/hora por conta, e
    # 10 × 600 s cobre a hora inteira que ele mede.
    #
    # A rota irmã que monta do NCBI (`montar_banco`) nunca teve o problema por
    # ser um `def` simples — o Starlette manda `def` para o threadpool sozinho.
    # Ou seja, o padrão certo já existia no arquivo e só esta rota escapou dele.
    #
    # Duas correções, e as duas fazem falta:
    #   * `run_in_threadpool` tira o bloqueio do event loop;
    #   * `_vaga_pesada()` é o mesmo semáforo que `api_traco` e `api_consultar`
    #     já usam. Sem ele, sair do event loop só troca o problema de lugar:
    #     40 montagens simultâneas ocupariam o threadpool inteiro, que é o
    #     achado M4 de 2026-08-08 outra vez, por uma rota nova.
    try:
        banco_id = bancos.id_do_usuario(u.email, apelido.strip())
        dados = (await fasta.read(20 * 1024 * 1024 + 1)).decode("utf-8", "replace")
        if len(dados) > 20 * 1024 * 1024:
            raise ValueError("o FASTA passa de 20 MB")
        with _vaga_pesada():
            await run_in_threadpool(bancos.montar_do_usuario, cfg().data_dir,
                                    banco_id, dados, blast_bin=cfg().blast_bin)
    except HTTPException:
        # ⚠️ ANTES do `except Exception`, senão o 503 de "sem vaga" é engolido e
        # volta como um texto de erro na página — a trava existiria e ninguém
        # veria que ela agiu.
        raise
    except Exception as e:                      # noqa: BLE001
        _por_recado(request, str(e)[:200])
        return RedirectResponse("/bancos", status_code=303)
    return RedirectResponse("/bancos?feito=meu", status_code=303)


