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
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import amostras as mod_amostras
from . import auth, bancos, config, cotas, executor, fila, perfil, retencao, traco

cfg = config.carregar()
fila.criar_esquema(cfg.sqlite_path)
perfil.criar_esquema(cfg.sqlite_path)

app = FastAPI(title="EasyContig BR — lotes", docs_url="/api/docs")

# A chave assina o cookie de sessão. Sem SECRET_KEY no ambiente, gera uma por
# processo: seguro, mas derruba as sessões a cada reinício — aceitável em
# desenvolvimento, e o `.env.example` avisa que produção precisa definir.
# O traço de um par são ~380 KB de JSON e ~112 KB comprimidos (medido). Sem
# isto, a página da amostra puxaria três vezes mais pela rede sem ganho nenhum.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("EASYCONTIG_SECRET_KEY") or secrets.token_urlsafe(32),
    https_only=os.environ.get("EASYCONTIG_HTTPS_ONLY", "0") == "1",
    same_site="lax",
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# O JS do workspace sai em arquivo, e não embutido no template, porque o
# navegador o guarda em cache entre amostras — a página da amostra é a que mais
# se reabre, uma por par.
app.mount("/estatico",
          StaticFiles(directory=str(Path(__file__).parent / "estatico")),
          name="estatico")


def _br(valor) -> str:
    """Número no formato daqui: 99,4 e não 99.4.

    Só na APRESENTAÇÃO. O CSV e o JSON continuam com ponto, porque são lidos por
    programa (e o CSV vai para planilha e para o R). Misturar os dois na mesma
    tela era o defeito: a página escrevia "25,1 MB" com vírgula e "96.500" com
    ponto, e `96.500` em português lê-se noventa e seis mil e quinhentos.
    """
    texto = "" if valor is None else str(valor)
    return texto.replace(".", ",") if texto else ""


TEMPLATES.env.filters["br"] = _br


@app.exception_handler(HTTPException)
def _erro(request: Request, exc: HTTPException):
    """Quem veio pelo navegador recebe página; quem veio por API recebe JSON.

    Sem isto, abrir um link de lote sem sessão devolvia `{"detail":"entre para
    continuar"}` cru na tela, sem título nem caminho de volta. E não é caso de
    borda: sem `EASYCONTIG_SECRET_KEY` fixa a chave é regerada a cada arranque
    do servidor (ver o comentário do SessionMiddleware), então TODO reinício
    desloga todo mundo e leva todos a esta parede.
    """
    quer_html = "text/html" in request.headers.get("accept", "")
    if not quer_html:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 401:
        # `proximo` preserva o destino: depois de entrar, a pessoa volta para o
        # lote que tentou abrir, em vez de cair na lista e ter de procurá-lo.
        return RedirectResponse(f"/entrar?proximo={quote(request.url.path)}",
                                status_code=303)
    u = _u(request)
    return TEMPLATES.TemplateResponse(request, "erro.html", {
        **(_casca(u) if u else {"usuario": None}),
        "codigo": exc.status_code, "detalhe": exc.detail,
    }, status_code=exc.status_code)

EXT_ACEITAS = {".ab1", ".abi", ".scf"}


# --------------------------------------------------------------------- sessão
def _u(request: Request) -> auth.Usuario | None:
    return auth.usuario_da_sessao(request)


def _exigir(request: Request) -> auth.Usuario:
    u = _u(request)
    if not u:
        raise HTTPException(status_code=401, detail="entre para continuar")
    return u


def _casca(u: auth.Usuario) -> dict:
    """O que a barra lateral precisa, em qualquer página.

    A lista de corridas mora na lateral porque é o que a pessoa volta para
    consultar — o uso real é par a par (ADR 0051), e o que se acumula é o
    histórico. Fica curta de propósito: a lateral é atalho, não arquivo.
    """
    return {"usuario": u, "corridas": fila.listar(cfg.sqlite_path, dono=u.email, limite=25)}


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
        **_casca(u),
        "lotes": fila.listar(cfg.sqlite_path, dono=u.email),
        "diagnostico": config.diagnostico(cfg),
        "trim": cfg.trim,
        "max_arquivos": cfg.max_arquivos,
        "max_mb": cfg.max_bytes // (1024 * 1024),
        "cota": cotas.situacao(cfg.sqlite_path, cfg.lotes_dir, u.email),
        "retencao_dias": cfg.retencao_dias,
    })


def _destino(proximo: str) -> str:
    """Para onde mandar depois do login. Só caminho local — nunca outro site.

    `proximo` chega pela URL, então é entrada do usuário: sem esta checagem, um
    link `/entrar?proximo=https://outro.site` faria a nossa página de login
    despejar a pessoa em outro domínio logo depois de ela digitar o e-mail.
    Exigir que comece com uma barra só (e não `//`, que o navegador lê como
    outro host) resolve.
    """
    if proximo.startswith("/") and not proximo.startswith("//"):
        return proximo
    return "/"


@app.get("/entrar", response_class=HTMLResponse)
def pagina_entrar(request: Request, erro: str = "", proximo: str = ""):
    return TEMPLATES.TemplateResponse(request, "entrar.html", {
        "modo": auth.modo(),
        "google_ok": auth.google_configurado(),
        "dominio": cfg.dominio_permitido,
        "erro": erro,
        "proximo": _destino(proximo),
    })


@app.post("/entrar")
def entrar_dev(request: Request, email: str = Form(...), proximo: str = Form("")):
    if auth.modo() != "dev":
        raise HTTPException(status_code=403, detail="login de desenvolvimento desligado")
    email = email.strip().lower()
    destino = _destino(proximo)
    volta = f"&proximo={quote(destino)}" if destino != "/" else ""
    if "@" not in email:
        return RedirectResponse(f"/entrar?erro=e-mail+invalido{volta}", status_code=303)
    if not auth.dominio_ok(email, cfg.dominio_permitido):
        return RedirectResponse(
            f"/entrar?erro=fora+do+dominio+{cfg.dominio_permitido}{volta}",
            status_code=303)
    auth.entrar_na_sessao(request, auth.Usuario(email=email, nome=email.split("@")[0]))
    return RedirectResponse(destino, status_code=303)


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

    # A cota é conferida ANTES de `novo_lote`: recusar depois deixaria uma linha
    # no banco e uma pasta em disco de um lote que nunca vai rodar — e é
    # justamente o disco que a cota existe para proteger.
    # ⚠️ Alcance honesto: quando este handler roda, o Starlette já recebeu o
    # corpo inteiro. A cota impede que os bytes sejam GUARDADOS, não que
    # trafeguem; barrar o tráfego exigiria um middleware olhando Content-Length.
    situacao = cotas.situacao(cfg.sqlite_path, cfg.lotes_dir, u.email)
    if not situacao.pode_enviar:
        raise HTTPException(status_code=413, detail=situacao.motivo)

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


@app.get("/perfil", response_class=HTMLResponse)
def pagina_perfil(request: Request, editando: int = 0):
    u = _exigir(request)
    lotes = fila.listar(cfg.sqlite_path, dono=u.email, limite=500)
    reps = []
    for l in lotes:
        if l["status"] == fila.PRONTO:
            r = _relatorio(l["id"])
            if r:
                reps.append({"lote": l, "rep": r,
                             "n": len(r.get("samples") or [])})
    return TEMPLATES.TemplateResponse(request, "perfil.html", {
        **_casca(u),
        "perfil": perfil.pegar(cfg.sqlite_path, u.email),
        "relatorios": reps,
        "resumo": perfil.resumo_das_corridas(lotes, [x["rep"] for x in reps]),
        "cota": cotas.situacao(cfg.sqlite_path, cfg.lotes_dir, u.email),
        "editando": bool(editando),
    })


@app.post("/perfil")
async def salvar_perfil(request: Request,
                        nome: str = Form(""), laboratorio: str = Form(""),
                        instituicao: str = Form(""), sobre: str = Form(""),
                        especies: str = Form(""), marcadores: str = Form(""),
                        foto: UploadFile | None = File(None)):
    u = _exigir(request)
    nome_foto = None
    if foto is not None and foto.filename:
        ext = Path(foto.filename).suffix.lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise HTTPException(status_code=400,
                                detail="a foto precisa ser .png, .jpg ou .webp")
        dados = await foto.read(2 * 1024 * 1024 + 1)
        if len(dados) > 2 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="a foto passa de 2 MB")
        pasta = cfg.data_dir / "fotos"
        pasta.mkdir(parents=True, exist_ok=True)
        # Nome derivado do e-mail, não do arquivo enviado: nome de arquivo é
        # entrada do usuário e não decide caminho em disco.
        nome_foto = secrets.token_urlsafe(8) + ext
        (pasta / nome_foto).write_bytes(dados)
    perfil.salvar(cfg.sqlite_path, u.email, nome=nome, laboratorio=laboratorio,
                  instituicao=instituicao, sobre=sobre, especies=especies,
                  marcadores=marcadores, foto=nome_foto)
    return RedirectResponse("/perfil", status_code=303)


@app.get("/perfil/foto")
def foto_do_perfil(request: Request):
    """A foto sai por rota, e não de uma pasta estática servida direto: assim
    ela continua sendo de quem entrou, e não um arquivo público adivinhável."""
    u = _exigir(request)
    p = perfil.pegar(cfg.sqlite_path, u.email)
    if not p["foto"]:
        raise HTTPException(status_code=404, detail="sem foto")
    caminho = cfg.data_dir / "fotos" / Path(p["foto"]).name
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="sem foto")
    return FileResponse(caminho)


# ------------------------------------------------------- bancos de referência
@app.get("/bancos", response_class=HTMLResponse)
def pagina_bancos(request: Request, erro: str = "", feito: str = ""):
    u = _exigir(request)
    grupos: dict[str, list] = {}
    for c in bancos.CATALOGO:
        grupos.setdefault(c.grupo, []).append(
            {"c": c, "estado": bancos.estado(cfg.data_dir, c.id)})
    return TEMPLATES.TemplateResponse(request, "bancos.html", {
        **_casca(u), "grupos": grupos,
        "meus": bancos.meus_bancos(cfg.data_dir, u.email),
        "erro": erro, "feito": feito,
    })


@app.post("/bancos/{banco_id}/montar")
def montar_banco(request: Request, banco_id: str):
    """Baixa do NCBI e monta. Síncrono de propósito: são segundos para os
    pequenos, e quem aperta está olhando. Os grandes avisam o tamanho antes."""
    _exigir(request)
    if banco_id not in bancos.POR_ID:
        raise HTTPException(status_code=404, detail="banco desconhecido")
    try:
        bancos.montar(cfg.data_dir, banco_id, blast_bin=cfg.blast_bin)
    except Exception as e:                      # noqa: BLE001
        return RedirectResponse(f"/bancos?erro={quote(str(e)[:200])}", status_code=303)
    return RedirectResponse(f"/bancos?feito={quote(banco_id)}", status_code=303)


@app.post("/bancos/{banco_id}/remover")
def remover_banco(request: Request, banco_id: str):
    u = _exigir(request)
    if banco_id.startswith("meu_"):
        # banco de usuário só some pela mão do dono
        if banco_id not in {b["id"] for b in bancos.meus_bancos(cfg.data_dir, u.email)}:
            raise HTTPException(status_code=404, detail="banco não encontrado")
    elif banco_id not in bancos.POR_ID:
        raise HTTPException(status_code=404, detail="banco desconhecido")
    bancos.remover(cfg.data_dir, banco_id)
    return RedirectResponse("/bancos", status_code=303)


@app.post("/bancos/meu")
async def enviar_banco(request: Request, apelido: str = Form(...),
                       fasta: UploadFile = File(...)):
    u = _exigir(request)
    try:
        banco_id = bancos.id_do_usuario(u.email, apelido.strip())
        dados = (await fasta.read(20 * 1024 * 1024 + 1)).decode("utf-8", "replace")
        if len(dados) > 20 * 1024 * 1024:
            raise ValueError("o FASTA passa de 20 MB")
        bancos.montar_do_usuario(cfg.data_dir, banco_id, dados, blast_bin=cfg.blast_bin)
    except Exception as e:                      # noqa: BLE001
        return RedirectResponse(f"/bancos?erro={quote(str(e)[:200])}", status_code=303)
    return RedirectResponse("/bancos?feito=meu", status_code=303)


@app.get("/api/lotes/{lote_id}/amostras/{chave}/consultar")
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
                                       bancos.meus_bancos(cfg.data_dir, u.email)}
    if banco not in permitidos or not bancos.existe(cfg.data_dir, banco):
        raise HTTPException(status_code=404, detail="banco não montado")
    asm = traco.montar(cfg, executor.pastas_do_lote(cfg, lote_id)["raiz"], am)
    if asm is None:
        raise HTTPException(status_code=409, detail="não foi possível remontar a amostra")
    try:
        hits = bancos.consultar(asm.consensus_nogap,
                                bancos.prefixo(cfg.data_dir, banco),
                                blast_bin=cfg.blast_bin)
    except Exception as e:                      # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)[:200])
    return JSONResponse({"banco": banco, "hits": hits, "consulta": True})


def _relatorio(lote_id: str) -> dict | None:
    """Os fatos do lote, já lidos do `relatorio.json` gravado pelo trabalhador.

    None quando o arquivo não existe ou não tem forma de relatório — a tela diz
    "relatório indisponível" em vez de mostrar uma lista vazia, que passaria por
    "lote sem amostras" e é o tipo de defeito que se parece com resultado.
    """
    return mod_amostras.carregar(
        executor.pastas_do_lote(cfg, lote_id)["relatorio_json"])


@app.get("/lotes/{lote_id}", response_class=HTMLResponse)
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
        "amostras": mod_amostras.listar(rep) if rep else None,
        "resumo": mod_amostras.resumo(rep) if rep else None,
        "expira_em": retencao.expira_em(lote, cfg.retencao_dias),
    })


@app.get("/api/lotes/{lote_id}/amostras/{chave}/traco")
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
    lote_dir = executor.pastas_do_lote(cfg, lote_id)["raiz"]
    dados = traco.para_navegador(traco.montar(cfg, lote_dir, am), am)
    if not dados:
        # Sem traço a página não quebra: ela mostra os números e diz por quê.
        return JSONResponse({"disponivel": False,
                             "motivo": "os arquivos originais não estão mais no "
                                       "servidor, ou esta amostra não é um par F+R"},
                            status_code=200)
    dados["disponivel"] = True
    return JSONResponse(dados)


@app.get("/lotes/{lote_id}/amostras/{chave}", response_class=HTMLResponse)
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
    })


@app.get("/api/lotes/{lote_id}")
def api_lote(request: Request, lote_id: str):
    u = _exigir(request)
    return JSONResponse(_lote_do_usuario(lote_id, u))


@app.post("/lotes/{lote_id}/apagar")
def apagar_lote(request: Request, lote_id: str):
    """Apagar agora, sem esperar o prazo.

    Existe porque a retenção automática não substitui isto: quem mandou um lote
    por engano — a pasta errada, o dado de outro projeto — precisa poder tirá-lo
    do servidor no mesmo minuto, e não em 90 dias. `_lote_do_usuario` garante
    que só o dono chega aqui.
    """
    u = _exigir(request)
    _lote_do_usuario(lote_id, u)
    if not retencao.apagar_lote(cfg.sqlite_path, cfg.lotes_dir, lote_id):
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
