"""
main.py — o servidor web. `uvicorn easycontig_web.main:app`

Regra de ouro deste arquivo: ele NÃO processa nada. Recebe arquivo, grava em
disco, enfileira e responde. Todo trabalho pesado é do `trabalhador.py`, em
outro processo — é a diferença entre aguentar 100 pessoas e cair com 20
(ADR 0050).
"""
from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, executor, fila

cfg = config.carregar()
fila.criar_esquema(cfg.sqlite_path)

app = FastAPI(title="EasyContig BR — lotes", docs_url="/api/docs")

# A chave assina o cookie de sessão. Sem SECRET_KEY no ambiente, gera uma por
# processo: seguro, mas derruba as sessões a cada reinício — aceitável em
# desenvolvimento, e o `.env.example` avisa que produção precisa definir.
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("EASYCONTIG_SECRET_KEY") or secrets.token_urlsafe(32),
    https_only=os.environ.get("EASYCONTIG_HTTPS_ONLY", "0") == "1",
    same_site="lax",
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

EXT_ACEITAS = {".ab1", ".abi", ".scf"}


# --------------------------------------------------------------------- sessão
def _u(request: Request) -> auth.Usuario | None:
    return auth.usuario_da_sessao(request)


def _exigir(request: Request) -> auth.Usuario:
    u = _u(request)
    if not u:
        raise HTTPException(status_code=401, detail="entre para continuar")
    return u


def _lote_do_usuario(lote_id: str, u: auth.Usuario) -> dict:
    lote = fila.pegar(cfg.sqlite_path, lote_id)
    if not lote:
        raise HTTPException(status_code=404, detail="lote não encontrado")
    # Dono confere mesmo com o id sendo aleatório: o link é compartilhável por
    # descuido, e um .ab1 não publicado do laboratório não pode vazar por URL
    # adivinhada nem por link colado no grupo errado.
    if lote["dono"] != u.email:
        raise HTTPException(status_code=404, detail="lote não encontrado")
    return lote


# --------------------------------------------------------------------- páginas
@app.get("/", response_class=HTMLResponse)
def inicio(request: Request):
    u = _u(request)
    if not u:
        return RedirectResponse("/entrar", status_code=303)
    return TEMPLATES.TemplateResponse(request, "inicio.html", {
        "usuario": u,
        "lotes": fila.listar(cfg.sqlite_path, dono=u.email),
        "diagnostico": config.diagnostico(cfg),
        "trim": cfg.trim,
        "max_arquivos": cfg.max_arquivos,
        "max_mb": cfg.max_bytes // (1024 * 1024),
    })


@app.get("/entrar", response_class=HTMLResponse)
def pagina_entrar(request: Request, erro: str = ""):
    return TEMPLATES.TemplateResponse(request, "entrar.html", {
        "modo": auth.modo(),
        "google_ok": auth.google_configurado(),
        "dominio": cfg.dominio_permitido,
        "erro": erro,
    })


@app.post("/entrar")
def entrar_dev(request: Request, email: str = Form(...)):
    if auth.modo() != "dev":
        raise HTTPException(status_code=403, detail="login de desenvolvimento desligado")
    email = email.strip().lower()
    if "@" not in email:
        return RedirectResponse("/entrar?erro=e-mail+invalido", status_code=303)
    if not auth.dominio_ok(email, cfg.dominio_permitido):
        return RedirectResponse(
            f"/entrar?erro=fora+do+dominio+{cfg.dominio_permitido}", status_code=303)
    auth.entrar_na_sessao(request, auth.Usuario(email=email, nome=email.split("@")[0]))
    return RedirectResponse("/", status_code=303)


@app.get("/sair")
def sair(request: Request):
    auth.sair_da_sessao(request)
    return RedirectResponse("/entrar", status_code=303)


# ----------------------------------------------------------------------- lotes
@app.post("/lotes")
async def criar_lote(request: Request,
                     arquivos: list[UploadFile] = File(...),
                     nome: str = Form("")):
    u = _exigir(request)

    aceitos = [a for a in arquivos
               if Path(a.filename or "").suffix.lower() in EXT_ACEITAS]
    if not aceitos:
        raise HTTPException(status_code=400,
                            detail="nenhum arquivo .ab1/.abi/.scf no envio")
    if len(aceitos) > cfg.max_arquivos:
        raise HTTPException(
            status_code=413,
            detail=f"{len(aceitos)} arquivos; o teto é {cfg.max_arquivos}")

    lote_id = fila.novo_lote(cfg.sqlite_path, dono=u.email,
                             nome=nome.strip() or "lote", n_arquivos=len(aceitos))
    p = executor.pastas_do_lote(cfg, lote_id)
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
                    if total > cfg.max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"lote acima de {cfg.max_bytes // (1024*1024)} MB")
                    fh.write(pedaco)
            gravados += 1
    except Exception:
        shutil.rmtree(p["raiz"], ignore_errors=True)
        fila.falhar(cfg.sqlite_path, lote_id, "falha ao receber os arquivos")
        raise

    # SÓ AGORA o lote fica visível para o trabalhador. Enfileirar antes desta
    # linha é o defeito que produziu um relatório de 33 das 40 amostras sem
    # avisar da falta — ver o comentário de RECEBENDO em fila.py.
    fila.liberar_para_fila(cfg.sqlite_path, lote_id, gravados)
    return RedirectResponse(f"/lotes/{lote_id}", status_code=303)


@app.get("/lotes/{lote_id}", response_class=HTMLResponse)
def pagina_lote(request: Request, lote_id: str):
    u = _exigir(request)
    lote = _lote_do_usuario(lote_id, u)
    return TEMPLATES.TemplateResponse(request, "lote.html", {
        "usuario": u, "lote": lote,
        # a página só se recarrega sozinha enquanto há o que esperar
        "recarregar": lote["status"] in (fila.RECEBENDO, fila.NA_FILA, fila.RODANDO),
    })


@app.get("/api/lotes/{lote_id}")
def api_lote(request: Request, lote_id: str):
    u = _exigir(request)
    return JSONResponse(_lote_do_usuario(lote_id, u))


def _arquivo(lote_id: str, u: auth.Usuario, chave: str, midia: str,
             baixar_como: str | None = None) -> FileResponse:
    lote = _lote_do_usuario(lote_id, u)
    if lote["status"] != fila.PRONTO:
        raise HTTPException(status_code=409,
                            detail=f"lote ainda em '{lote['status']}'")
    caminho = executor.pastas_do_lote(cfg, lote_id)[chave]
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="arquivo não gerado")
    return FileResponse(caminho, media_type=midia, filename=baixar_como)


@app.get("/lotes/{lote_id}/relatorio")
def relatorio(request: Request, lote_id: str):
    # Servido como HTML e não como PDF de propósito: quem converte é o navegador
    # (Ctrl+P → Salvar como PDF). O único trecho do projeto que dependia de Qt
    # para gerar PDF era `app/ui/report_pdf.py`, e na web ele deixa de existir.
    return _arquivo(lote_id, _exigir(request), "relatorio_html", "text/html")


@app.get("/lotes/{lote_id}/resultado.csv")
def resultado_csv(request: Request, lote_id: str):
    return _arquivo(lote_id, _exigir(request), "resultado_csv", "text/csv",
                    baixar_como=f"resultado_{lote_id}.csv")


@app.get("/lotes/{lote_id}/relatorio.json")
def relatorio_json(request: Request, lote_id: str):
    return _arquivo(lote_id, _exigir(request), "relatorio_json", "application/json",
                    baixar_como=f"relatorio_{lote_id}.json")


# ----------------------------------------------------------------------- saúde
@app.get("/saude")
def saude():
    itens = config.diagnostico(cfg)
    return {
        "ok": all(ok for _, ok, _ in itens),
        "dependencias": [{"item": i, "ok": ok, "detalhe": d} for i, ok, d in itens],
        "na_fila": sum(1 for x in fila.listar(cfg.sqlite_path, limite=500)
                       if x["status"] == fila.NA_FILA),
    }
