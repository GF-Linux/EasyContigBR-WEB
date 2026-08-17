"""
main.py — o servidor web. `uvicorn easycontig_web.main:app`

Regra de ouro deste arquivo: ele NÃO processa nada. Recebe arquivo, grava em
disco, enfileira e responde. Todo trabalho pesado é do `trabalhador.py`, em
outro processo — é a diferença entre aguentar 100 pessoas e cair com 20
(ADR 0050).
"""
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
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .dados import leitura_de_amostras as mod_amostras
from .contas import autenticacao as auth
from .dados import bancos_de_referencia as bancos
from . import configuracao as config
from .contas import cota_de_espaco as cotas
from .processamento import executor_de_lote as executor
from .processamento import fila_de_lotes as fila
from .contas import limite_de_requisicoes as limites
from .dados import pedidos_entre_labs as pedidos
from .contas import perfil_do_laboratorio as perfil
from .dados import expurgo_por_retencao as retencao
from .dados import cromatograma as traco

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

fila.criar_esquema(cfg.sqlite_path)
perfil.criar_esquema(cfg.sqlite_path)
pedidos.criar_esquema(cfg.sqlite_path)

# `docs_url=None` em produção: `/api/docs`, `/redoc` e `/openapi.json` respondiam
# 200 SEM sessão (verificado em 2026-08-06) e publicam o mapa inteiro da API —
# toda rota, todo parâmetro, todo formato. Não é vazamento de dado, é o mapa de
# onde procurar, entregue a quem ainda não entrou. Em desenvolvimento continua
# ligado, que é onde ele serve para alguma coisa.
_DOCS = None if os.environ.get("EASYCONTIG_PRODUCAO") == "1" else "/api/docs"
app = FastAPI(title="EasyContig BR — lotes", docs_url=_DOCS,
              redoc_url=None, openapi_url=None if _DOCS is None else "/openapi.json")

# A chave assina o cookie de sessão. Sem SECRET_KEY no ambiente, gera uma por
# processo: seguro, mas derruba as sessões a cada reinício — aceitável em
# desenvolvimento, e o `.env.example` avisa que produção precisa definir.
# O traço de um par são ~380 KB de JSON e ~112 KB comprimidos (medido). Sem
# isto, a página da amostra puxaria três vezes mais pela rede sem ganho nenhum.
app.add_middleware(GZipMiddleware, minimum_size=1024)


# Os únicos POST que uma pessoa sem sessão pode fazer. Allowlist e não
# blocklist: rota nova nasce fechada, que é o mesmo princípio do teto de leitura
# logo abaixo.
POST_SEM_SESSAO = {"/entrar"}


def _origem_confere(request: Request) -> bool:
    """O POST veio de uma página DESTE site? (achado M3 de 2026-08-08)

    O cookie de sessão é `SameSite=Lax`, e Lax é *same-**site***, não
    *same-**origin***. A diferença não é acadêmica quando o endereço final for
    `easycontig.ufrrj.br`: aí **qualquer host sob `ufrrj.br`** — o site
    departamental com um XSS, o subdomínio de um evento abandonado — é
    same-site, e um `<form method=post>` escondido lá dentro sai com o cookie
    junto. Os alvos são de estado e alguns são irreversíveis: `/perfil`
    (sobrescreve), `/lotes/{id}/apagar`, `/bancos/meu`, `/bancos/{id}/remover`.

    Em vez de token CSRF em seis formulários — que a sétima rota esqueceria —,
    a checagem mora aqui, no middleware que já vê TODO POST: rota nova nasce
    coberta, do mesmo jeito que já nasce com teto de leitura e exigindo sessão.

    **Ausência de `Origin` passa de propósito.** Navegador manda `Origin` em
    todo POST desde 2020 (inclusive same-origin); quem não manda é cliente de
    linha de comando — `curl`, o `TestClient` da suíte, um script do laboratório
    —, e esses não carregam cookie de terceiro, que é o mecanismo do ataque.
    Recusar sem `Origin` quebraria os três sem fechar nada.
    """
    origem = request.headers.get("origin")
    if not origem:
        return True
    base = os.environ.get("EASYCONTIG_URL_BASE", "").rstrip("/")
    # Sem URL_BASE (desenvolvimento) o espelho é a própria requisição. Atrás do
    # nginx isso viria com o host errado — daí a configuração vencer quando há.
    esperado = base or str(request.base_url).rstrip("/")
    return origem.rstrip("/").lower() == esperado.lower()


# Teto de CORPO, em bytes. Sobra generosa sobre `EASYCONTIG_MAX_BYTES` (o teto
# por lote, 300 MB por padrão) porque o multipart carrega cabeçalho e fronteira
# por arquivo — 400 partes somam alguns MB só de moldura.
_FOLGA_MULTIPART = 16 * 1024 * 1024
MAX_CORPO = int(os.environ.get("EASYCONTIG_MAX_CORPO",
                               str(cfg.max_bytes + _FOLGA_MULTIPART)))


def _corpo_grande_demais(request: Request) -> str:
    """Recusa pelo `Content-Length`, antes de o corpo existir (achado H1).

    O `MultiPartParser` do Starlette spoola **cada parte de arquivo** num
    `SpooledTemporaryFile`, que vira arquivo real em `/tmp` assim que passa do
    limiar — e isso acontece **antes** de a função da rota rodar. Ou seja,
    `max_bytes`, cota e retenção agem todos DEPOIS de os bytes já terem tocado o
    disco. Medido em 2026-08-08: 12.000.296 bytes subiram inteiros num
    `POST /perfil` cujo teto é 2 MB, e só então voltou o 413.

    A barreira de verdade é o `client_max_body_size` do nginx (512 MB, em
    `implantacao/nginx-easycontig.conf`). Isto aqui é defesa em profundidade,
    e vale por dois motivos concretos: quem roda o app sem nginx na frente
    (desenvolvimento, ou uma instalação nova que ainda não copiou a receita)
    não tem barreira NENHUMA; e o teto do app acompanha `MAX_BYTES`, enquanto o
    do nginx é um número fixo que alguém teria de lembrar de mexer junto.

    ⚠️ **Sem `Content-Length` não há o que conferir** — corpo em `chunked` não
    declara tamanho. Aí a barreira volta a ser só o nginx. Recusar requisição
    sem o cabeçalho quebraria cliente legítimo para fechar um caminho que o
    proxy já fecha.
    """
    bruto = request.headers.get("content-length")
    if not bruto:
        return ""
    try:
        n = int(bruto)
    except ValueError:
        return "content-length inválido"
    if n > MAX_CORPO:
        return (f"o envio passa do teto de {MAX_CORPO // (1024 * 1024)} MB por "
                "requisição; mande em lotes menores")
    return ""


# ⚠️ A POSIÇÃO DESTE BLOCO É O QUE O FAZ FUNCIONAR — não mova para junto dos
# outros middlewares lá embaixo. `add_middleware` empilha de dentro para fora:
# quem é registrado DEPOIS fica por FORA. Registrando o limitador aqui, entre o
# GZip e o Session, ele passa a rodar POR DENTRO do `SessionMiddleware` e
# `request.session` existe quando ele roda. Registrado depois do Session — que
# era como estava — ele roda por fora, a sessão ainda não foi decodificada, e
# só sobra o IP.
#
# Isso deixou de ser detalhe quando se mediu o efeito: contando por IP, o teto
# de 300 leituras/60 s é do PRÉDIO, não da pessoa. A página do lote pergunta o
# estado de 3 em 3 s, ou seja 0,33 req/s por aba aberta — **15 abas no mesmo IP
# esgotam o teto**, e a universidade inteira sai por um NAT. Quem seria barrado
# é justamente o laboratório em dia de corrida.
@app.middleware("http")
async def _limite_de_leitura(request: Request, call_next):
    """Teto de leitura no middleware, e não rota a rota: assim uma rota nova já
    nasce protegida em vez de depender de alguém lembrar.

    Conta por CONTA quando há sessão e cai para o IP quando não há — que é o
    comportamento que `limites.chave()` já implementava e este middleware não
    conseguia usar. O caminho anônimo continua contado por IP, e é o que se quer:
    ali o IP é a única identidade que existe.

    ⚠️ **E o 401 dos POST tem que sair DAQUI, não da rota.** Medido nos pacotes
    instalados em 2026-08-06: o FastAPI executa `await request.form()` dentro do
    manipulador da requisição, **antes** de chamar a função do endpoint — e a
    autenticação deste app é a primeira instrução DE DENTRO dela. Ou seja, um
    chamador sem cookie nenhum decidia quantos bytes o servidor recebia e por
    quanto tempo, e só depois levava 401: mil partes de ~1 MB seguravam ~1 GB
    residente no processo do uvicorn. Middleware roda antes do parser, então
    recusar aqui é recusar antes de o corpo existir.
    """
    caminho = request.url.path
    if caminho.startswith("/estatico/"):
        return await call_next(request)

    if request.method == "POST" and not _origem_confere(request):
        # Recusado ANTES do 401 e antes do parser: um POST forjado de fora não
        # merece nem que o corpo dele seja lido.
        return JSONResponse({"detail": "origem não confere"}, status_code=403)

    if request.method == "POST":
        grande = _corpo_grande_demais(request)
        if grande:
            return JSONResponse({"detail": grande}, status_code=413)

    if request.method == "POST" and caminho not in POST_SEM_SESSAO:
        try:
            tem_sessao = auth.usuario_da_sessao(request) is not None
        except Exception:                       # noqa: BLE001
            tem_sessao = False
        if not tem_sessao:
            if "text/html" in request.headers.get("accept", ""):
                return RedirectResponse(f"/entrar?proximo={quote(caminho)}",
                                        status_code=303)
            return JSONResponse({"detail": "entre para continuar"}, status_code=401)

    # ⚠️ HEAD conta junto com GET. HEAD é leitura — o servidor resolve a rota,
    # monta a resposta inteira e só descarta o corpo — e enquanto isto dizia
    # apenas `== "GET"` havia um método que não gastava orçamento NENHUM: bastava
    # trocar o verbo para bater à vontade. Apareceu ao abrir o `/saude` para
    # HEAD (o UptimeRobot só fala HEAD no plano grátis), mas o buraco não era do
    # `/saude`: era de toda rota deste app.
    if request.method in ("GET", "HEAD"):
        quem = None
        try:
            u = auth.usuario_da_sessao(request)
            quem = u.email if u else None
        except Exception:                       # noqa: BLE001
            # Sessão ilegível (cookie de uma chave antiga, por exemplo) não pode
            # derrubar a requisição: cai para o IP, que é o teto de quem não
            # está identificado. O limite é convivência, não autenticação.
            quem = None
        try:
            limites.conferir("leitura", request, quem)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=429,
                                headers=e.headers or {})
    return await call_next(request)


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


@app.middleware("http")
async def _sem_cache(request: Request, call_next):
    """Página com sessão não vai para o cache do navegador.

    Sem isto, apagar uma corrida e apertar VOLTAR trazia de volta a página dela
    do cache — com o botão "Apagar agora" e tudo —, e qualquer clique ali dava
    404. A corrida "sumia" sem explicação, que foi a queixa. Com `no-store` o
    voltar refaz a requisição e vê o 404 honesto, que a tela de erro explica.

    Vale também para quem sai da conta num computador compartilhado: sem isto o
    histórico do navegador ainda mostraria o relatório de quem entrou antes.
    Arquivos estáticos ficam de fora — o JS do workspace se beneficia do cache.
    """
    resposta = await call_next(request)
    if not request.url.path.startswith("/estatico/"):
        resposta.headers["Cache-Control"] = "no-store, must-revalidate"
        resposta.headers["Pragma"] = "no-cache"
    return resposta


@app.middleware("http")
async def _cabecalhos_de_seguranca(request: Request, call_next):
    """O que o navegador precisa ouvir do servidor. Nenhum destes existia.

    **`X-Robots-Tag: noindex`** é o mais específico deste projeto, e vai em TODA
    resposta: com domínio próprio o app fica descobrível de fora, e o que está
    atrás do login é sequenciamento **não publicado**. Não pode ser indexado nem
    por engano — nem pelo buscador que segue um link colado no grupo errado.

    **CSP com nonce, e sem `'unsafe-inline'` no script.** A página não carrega
    nada de fora — nem fonte, nem folha, nem imagem — então dá para fechar quase
    tudo. `script-src` sem `'unsafe-inline'` é o que transforma a CSP em defesa
    real contra o gênero de XSS armazenado que já apareceu aqui (o `stitle` do
    BLAST, em 06/08): mesmo que um texto vindo de fora volte a escapar do
    escape, ele não executa sem o nonce, que muda a cada resposta.

    `style-src` fica com `'unsafe-inline'` de propósito: são 68 atributos
    `style=` nos templates, e bloqueá-los quebraria o layout para proteger
    contra um risco muito menor. Trocar funcionamento por teatro não paga.

    **HSTS só quando há TLS.** Mandá-lo em `http://localhost` ensinaria o
    navegador a recusar o próprio desenvolvimento pelos meses do `max-age`.
    """
    nonce = secrets.token_urlsafe(16)
    request.state.nonce = nonce
    resposta = await call_next(request)
    _marcar_resposta(resposta, nonce)
    return resposta


def _marcar_resposta(resposta, nonce: str) -> None:
    """Carimba a política de segurança numa resposta. Fica em função à parte
    porque a página de 500 precisa dos MESMOS cabeçalhos e **não passa por
    middleware nenhum** — ver `_erro_nao_previsto`."""
    h = resposta.headers
    h["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "same-origin"
    h["Content-Security-Policy"] = (
        "default-src 'none'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'")
    if os.environ.get("EASYCONTIG_HTTPS_ONLY", "0") == "1":
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


@app.exception_handler(HTTPException)
def _erro(request: Request, exc: HTTPException):
    """Quem veio pelo navegador recebe página; quem veio por API recebe JSON.

    Sem isto, abrir um link de lote sem sessão devolvia `{"detail":"entre para
    continuar"}` cru na tela, sem título nem caminho de volta. E não é caso de
    borda: sem `EASYCONTIG_SECRET_KEY` fixa a chave é regerada a cada arranque
    do servidor (ver o comentário do SessionMiddleware), então TODO reinício
    desloga todo mundo e leva todos a esta parede.
    """
    # ⚠️ Os cabeçalhos da exceção viajam junto (achado de 2026-08-11, encontrado
    # ao escrever o teste do 503 de `_vaga_pesada`). Este manipulador montava a
    # resposta do zero e descartava `exc.headers` — então o `Retry-After` que
    # `_vaga_pesada()` e `limites.conferir()` põem no 503 e no 429 nunca chegava
    # a ninguém. O servidor sabia em quantos segundos valia tentar de novo e não
    # contava, e um cliente educado não tinha como ser educado.
    cabecalhos = dict(exc.headers or {})
    quer_html = "text/html" in request.headers.get("accept", "")
    if not quer_html:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                            headers=cabecalhos)
    if exc.status_code == 401:
        # `proximo` preserva o destino: depois de entrar, a pessoa volta para o
        # lote que tentou abrir, em vez de cair na lista e ter de procurá-lo.
        return RedirectResponse(f"/entrar?proximo={quote(request.url.path)}",
                                status_code=303)
    u = _u(request)
    return TEMPLATES.TemplateResponse(request, "erro.html", {
        **(_casca(u) if u else {"usuario": None}),
        "codigo": exc.status_code, "detalhe": exc.detail,
    }, status_code=exc.status_code, headers=cabecalhos)

@app.exception_handler(Exception)
def _erro_nao_previsto(request: Request, exc: Exception):
    """A parede de qualquer exceção que ninguém previu.

    Sem isto o Starlette devolve `Internal Server Error` em texto puro — sem
    casca, sem caminho de volta e sem dizer se o trabalho foi perdido. Visto de
    verdade em 2026-08-06, simulando disco cheio: a limpeza estava correta e a
    tela não contava nada disso.

    O texto NÃO leva a exceção: quem está do outro lado não pode ler caminho de
    arquivo nem consulta. O detalhe vai para o log, onde quem opera lê.

    ⚠️ **ESTA RESPOSTA NÃO PASSA POR MIDDLEWARE NENHUM**, e por isso ela carimba
    os cabeçalhos à mão. Achado M1 de 2026-08-08: um `@app.exception_handler(
    Exception)` é servido pelo `ServerErrorMiddleware`, que o Starlette põe no
    ponto MAIS DE FORA da pilha — fora, portanto, do `_cabecalhos_de_seguranca`
    e do `_sem_cache`, que são `@app.middleware("http")`. O resultado é que a
    única página que sai numa hora ruim saía **sem** CSP, sem `nosniff`, sem
    `X-Frame-Options`, sem `noindex` e **cacheável** — e ela herda o `base.html`,
    ou seja, leva a lateral com o nome, o e-mail de quem entrou e os nomes das
    corridas. A 500 do HTTPException (`_erro`) não tem o problema: aquela vem do
    `ExceptionMiddleware`, que fica por DENTRO da pilha.
    """
    print(f"  ⚠️  erro não previsto em {request.method} {request.url.path}: "
          f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)
    # O nonce é posto por `_cabecalhos_de_seguranca` antes de a rota rodar; o
    # sorteio aqui é só para o caso de a exceção nascer antes disso — uma CSP
    # com nonce que não casa é mais segura que CSP nenhuma.
    nonce = getattr(request.state, "nonce", None) or secrets.token_urlsafe(16)
    if "text/html" not in request.headers.get("accept", ""):
        resposta = JSONResponse({"detail": "erro interno do servidor"},
                                status_code=500)
    else:
        u = _u(request)
        resposta = TEMPLATES.TemplateResponse(request, "erro.html", {
            **(_casca(u) if u else {"usuario": None}),
            "codigo": 500,
            "detalhe": ("Algo quebrou do lado do servidor, e não foi por causa "
                        "do que você mandou. O registro do erro ficou no log do "
                        "servidor — avise quem cuida dele."),
        }, status_code=500)
    _marcar_resposta(resposta, nonce)
    resposta.headers["Cache-Control"] = "no-store, must-revalidate"
    resposta.headers["Pragma"] = "no-cache"
    return resposta


EXT_ACEITAS = {".ab1", ".abi", ".scf"}


# ───────────────────────────────────────────── teto de trabalho pesado (M4)
# Duas rotas GET rodam ferramenta externa por requisição: `api_traco` chama
# `tracy` (~0,3 s) e `api_consultar` chama `tracy` **mais** `blastn` (timeout de
# 120 s). As duas são `def` síncronas, então rodam no threadpool do Starlette —
# que tem 40 vagas e é COMPARTILHADO por todas as rotas `def` do app.
#
# O buraco (achado M4 de 2026-08-08): o único freio era o balde `leitura`, de
# 300 por 60 s POR CONTA. O dono de um lote pronto dispara 300 GETs num minuto,
# o threadpool enche de `blastn`, e aí param junto a página inicial, a lista de
# corridas e os downloads — de todo mundo, não só dele. Não precisa de má
# intenção: um laço de script do laboratório faz igual.
#
# O teto é sobre CONCORRÊNCIA, e não sobre taxa, porque o recurso que colapsa é
# CPU (a VPS tem 2 vCPU, e o trabalhador disputa a mesma). E a tomada é
# **não bloqueante** de propósito: esperar por vaga seguraria a thread, que é
# exatamente o que se quer liberar. Sem vaga, 503 na hora — a rota pesada
# degrada e o resto do site continua de pé, que é a troca certa.
MAX_PESADAS = max(1, int(os.environ.get("EASYCONTIG_MAX_PESADAS", "4")))
_VAGAS_PESADAS = threading.BoundedSemaphore(MAX_PESADAS)


@contextmanager
def _vaga_pesada():
    """Uma das `MAX_PESADAS` vagas de trabalho externo, ou 503 na hora."""
    if not _VAGAS_PESADAS.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail="o servidor está ocupado montando outras amostras; tente de novo",
            headers={"Retry-After": "5"})
    try:
        yield
    finally:
        _VAGAS_PESADAS.release()


# --------------------------------------------------------------------- sessão
def _u(request: Request) -> auth.Usuario | None:
    return auth.usuario_da_sessao(request)


def _exigir(request: Request) -> auth.Usuario:
    u = _u(request)
    if not u:
        raise HTTPException(status_code=401, detail="entre para continuar")
    return u


# ------------------------------------------------------- recado de uma vez só
# ⚠️ A MENSAGEM DE ERRO NÃO VIAJA MAIS NA URL (achado de 2026-08-11).
#
# O padrão antigo era `RedirectResponse("/bancos?erro=" + quote(texto))`, e a
# página imprimia `{{ erro }}` direto do parâmetro. Como o leitor não conferia
# nada, o texto exibido não era o que o app escreveu — era o que estivesse na
# URL. Qualquer um monta `/labs?erro=sua+sessão+expirou,+reenvie+suas+
# credenciais+em...` e a frase sai dentro da caixa de erro do próprio site, com
# a autoridade visual dele. Não é XSS (o Jinja escapa, e foi conferido), é
# falsificação de conteúdo — que é o que uma isca precisa.
#
# Podia ter virado um catálogo de códigos (`?erro=cota_bancos`), mas isso
# obrigaria a jogar fora o detalhe das falhas de montagem, que é justamente o
# que ajuda quem está tentando entender por que o banco não subiu. A sessão
# resolve melhor: ela é um cookie ASSINADO pelo servidor, então o cliente não
# consegue escrever nela, e o texto continua sendo exatamente o que o app pôs.
#
# "De uma vez só" é a outra metade: o recado é REMOVIDO ao ser lido, senão ele
# reaparece em todo recarregamento da página e a pessoa fica olhando um erro que
# já resolveu.
_RECADO = "recado"


def _por_recado(request: Request, texto: str) -> None:
    request.session[_RECADO] = (texto or "")[:300]


def _pegar_recado(request: Request) -> str:
    return request.session.pop(_RECADO, "")


def _referencias(u: auth.Usuario) -> list[dict]:
    """As referências que esta conta pode escolher para identificar.

    O curado vem primeiro e é o padrão: é o que produziu todos os resultados
    validados até aqui (ADR 0037). Os demais só aparecem depois de montados —
    oferecer o que não está pronto seria oferecer um erro.
    """
    itens = [{"id": "curado", "nome": "Banco curado do laboratório",
              "detalhe": "18S + 16S · RAIC 2026 · o que validou os resultados até aqui",
              "grupo": "Padrão"}]
    for c in bancos.POR_ID.values():
        if bancos.existe(cfg.data_dir, c.id):
            e = bancos.estado(cfg.data_dir, c.id)
            itens.append({"id": c.id, "nome": f"{c.nome} · {c.marcador}",
                          "detalhe": f"{e.get('sequencias')} sequências do GenBank",
                          "grupo": c.grupo})
    for b in bancos.meus_bancos(cfg.data_dir, u.email):
        itens.append({"id": b["id"], "nome": b["apelido"],
                      "detalhe": f"{b.get('sequencias')} sequências suas",
                      "grupo": "Meus bancos"})
    return itens


def _casca(u: auth.Usuario) -> dict:
    """O que a barra lateral precisa, em qualquer página.

    A lista de corridas mora na lateral porque é o que a pessoa volta para
    consultar — o uso real é par a par (ADR 0051), e o que se acumula é o
    histórico. Fica curta de propósito: a lateral é atalho, não arquivo.

    `perfil_lateral` traz nome e foto porque a lateral mostrava a INICIAL do
    e-mail mesmo depois de a pessoa ter enviado a foto — que aparecia só na
    página de perfil. Quem troca a foto e continua vendo "GU" conclui, com
    razão, que o envio não funcionou.
    """
    p = perfil.pegar(cfg.sqlite_path, u.email) or {}
    return {
        "usuario": u,
        "corridas": fila.listar(cfg.sqlite_path, dono=u.email, limite=25),
        "perfil_lateral": {"nome": p.get("nome") or "", "foto": p.get("foto") or ""},
        # O contador de pedidos esperando ESTA conta. Vai na casca, e não só na
        # página de pedidos, porque este app **não manda e-mail**: se o número
        # não estiver na lateral de toda página, um pedido pode ficar meses sem
        # ninguém saber que chegou. É um COUNT com índice próprio
        # (`idx_pedidos_para`), medido em microssegundos.
        "pedidos_pendentes": pedidos.contar_pendentes(cfg.sqlite_path, u.email),
    }


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
        **_casca(u), "pagina": "inicio",
        "lotes": fila.listar(cfg.sqlite_path, dono=u.email),
        "diagnostico": config.diagnostico(cfg),
        "trim": cfg.trim,
        "max_arquivos": cfg.max_arquivos,
        "max_mb": cfg.max_bytes // (1024 * 1024),
        "cota": cotas.situacao(cfg.sqlite_path, cfg.lotes_dir, u.email, cfg.data_dir),
        "retencao_dias": cfg.retencao_dias,
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


@app.get("/entrar", response_class=HTMLResponse)
def pagina_entrar(request: Request, proximo: str = ""):
    return TEMPLATES.TemplateResponse(request, "entrar.html", {
        "modo": auth.modo(),
        "google_ok": auth.google_configurado(),
        "dominio": cfg.dominio_permitido,
        "erro": _pegar_recado(request),
        "proximo": _destino(proximo),
    })


@app.post("/entrar")
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
    if not auth.dominio_ok(email, cfg.dominio_permitido):
        # A mensagem nomeia o domínio a partir da CONFIGURAÇÃO do servidor, e
        # nunca de nada que tenha vindo na requisição.
        _por_recado(request, f"conta fora do domínio {cfg.dominio_permitido}")
        return RedirectResponse(f"/entrar{volta}", status_code=303)
    auth.entrar_na_sessao(request, auth.Usuario(email=email, nome=email.split("@")[0]))
    # Cadastra no diretório como o Google faz — mas SEM nome: aqui não há
    # provedor que declare um, e o `email.split("@")[0]` acima é só para a
    # lateral (o próprio endereço de quem lê). Passá-lo adiante gravaria o
    # local-part no diretório, que é o achado L1.
    perfil.garantir_registro(cfg.sqlite_path, email)
    return RedirectResponse(destino, status_code=303)


def _redirect_uri(request: Request) -> str:
    """O endereço de volta do Google. Tem que casar EXATAMENTE com o cadastrado
    no console — daí sair da configuração, e não da URL da requisição, que atrás
    de um proxy reverso vem com o host errado."""
    base = os.environ.get("EASYCONTIG_URL_BASE", "").rstrip("/")
    return (base or str(request.base_url).rstrip("/")) + "/auth/google/volta"


@app.get("/auth/google")
def entrar_google(request: Request):
    # Mesma ordem do `entrar_dev`: rota desligada não pode consumir o orçamento
    # compartilhado de quem está tentando entrar de verdade.
    if auth.modo() != "google" or not auth.google_configurado():
        raise HTTPException(status_code=404, detail="login pelo Google não configurado")
    limites.conferir("login", request)
    return RedirectResponse(auth.url_de_ida(request, _redirect_uri(request)),
                            status_code=303)


@app.get("/auth/google/volta")
def volta_google(request: Request, code: str = "", state: str = "", error: str = ""):
    if auth.modo() != "google" or not auth.google_configurado():
        raise HTTPException(status_code=404, detail="login pelo Google não configurado")
    if error or not code:
        _por_recado(request, error or "entrada cancelada")
        return RedirectResponse("/entrar",
                                status_code=303)
    u = auth.usuario_da_volta(request, code, state, _redirect_uri(request))
    if not auth.dominio_ok(u.email, cfg.dominio_permitido):
        # O Google prova QUEM é; o domínio decide SE entra (ADR 0050).
        _por_recado(request, f"conta fora do domínio {cfg.dominio_permitido}")
        return RedirectResponse("/entrar", status_code=303)
    auth.entrar_na_sessao(request, u)
    # Entrar cadastra no diretório, com o nome que o Google declarou (ver
    # `perfil.garantir_registro`). Depois da sessão de propósito: se isto
    # falhar, a pessoa entra do mesmo jeito — o diretório é conveniência, não
    # condição para usar o site.
    perfil.garantir_registro(cfg.sqlite_path, u.email, u.nome_google)
    return RedirectResponse("/", status_code=303)


@app.post("/sair")
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


# ----------------------------------------------------------------------- lotes
@app.post("/lotes")
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
        b["id"] for b in bancos.meus_bancos(cfg.data_dir, u.email)}
    if referencia not in permitidos:
        raise HTTPException(status_code=400, detail="referência desconhecida")
    if referencia != "curado" and not bancos.existe(cfg.data_dir, referencia):
        raise HTTPException(
            status_code=400,
            detail="essa referência ainda não foi montada nesta instalação")

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
    situacao = cotas.situacao(cfg.sqlite_path, cfg.lotes_dir, u.email, cfg.data_dir)
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
        lote_id = fila.novo_lote(cfg.sqlite_path, dono=u.email,
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
    except OSError as e:
        # ⚠️ DISCO CHEIO, exercitado em 2026-08-06 contra um sistema de arquivos
        # de 1 MB de verdade: a limpeza funcionava (lote marcado `falhou`, pasta
        # removida, nada na fila), mas quem enviou recebia `500 Internal Server
        # Error` cru — sem casca, sem motivo — e o aviso do navegador mandava
        # "corrija a seleção e envie de novo", que é o conselho errado: não há
        # nada errado com a seleção, e reenviar não vai funcionar.
        shutil.rmtree(p["raiz"], ignore_errors=True)
        fila.falhar(cfg.sqlite_path, lote_id, f"falha ao gravar no disco: {e.strerror}")
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
        fila.falhar(cfg.sqlite_path, lote_id, "falha ao receber os arquivos")
        raise

    # SÓ AGORA o lote fica visível para o trabalhador. Enfileirar antes desta
    # linha é o defeito que produziu um relatório de 33 das 40 amostras sem
    # avisar da falta — ver o comentário de RECEBENDO em fila.py.
    fila.liberar_para_fila(cfg.sqlite_path, lote_id, gravados)
    return RedirectResponse(f"/lotes/{lote_id}", status_code=303)


@app.get("/leiame", response_class=HTMLResponse)
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


@app.get("/perfil", response_class=HTMLResponse)
def pagina_perfil(request: Request, editando: int = 0):
    u = _exigir(request)
    lotes = fila.listar(cfg.sqlite_path, dono=u.email, limite=500)
    # Só a CONTAGEM de amostras é lida de cada relatório — é tudo o que a tabela
    # e o resumo usam. Antes esta página decodificava o `relatorio.json` inteiro
    # de cada corrida da conta (25,89 ms para 40; o teto aqui é 500) para no fim
    # tirar um inteiro de cada um. Ver `amostras.contar_amostras`.
    reps = []
    for l in lotes:
        if l["status"] == fila.PRONTO:
            n = mod_amostras.contar_amostras(
                executor.pastas_do_lote(cfg, l["id"])["relatorio_json"])
            if n is not None:
                reps.append({"lote": l, "n": n})
    return TEMPLATES.TemplateResponse(request, "perfil.html", {
        **_casca(u), "pagina": "perfil",
        "perfil": perfil.pegar(cfg.sqlite_path, u.email),
        "relatorios": reps,
        "resumo": perfil.resumo_das_corridas(lotes, [x["n"] for x in reps]),
        "cota": cotas.situacao(cfg.sqlite_path, cfg.lotes_dir, u.email, cfg.data_dir),
        "editando": bool(editando),
    })


@app.post("/perfil")
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
        pasta = cfg.data_dir / "fotos"
        pasta.mkdir(parents=True, exist_ok=True)
        # Nome sorteado, não derivado do arquivo enviado: nome de arquivo é
        # entrada do usuário e não decide caminho em disco.
        nome_foto = secrets.token_urlsafe(8) + ext
        (pasta / nome_foto).write_bytes(dados)
        # A ANTERIOR SAI. Sem isto cada troca de foto deixava até 2 MB órfãos no
        # volume para sempre: nada apontava mais para o arquivo, nenhuma cota o
        # contava (a cota mede as pastas de LOTE) e a retenção não o alcança.
        antigo = (perfil.pegar(cfg.sqlite_path, u.email) or {}).get("foto")
        if antigo and antigo != nome_foto:
            (pasta / Path(antigo).name).unlink(missing_ok=True)
    perfil.salvar(cfg.sqlite_path, u.email, nome=nome, laboratorio=laboratorio,
                  instituicao=instituicao, sobre=sobre, especies=especies,
                  marcadores=marcadores, foto=nome_foto, links=links)
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


@app.get("/labs/foto/{arquivo}")
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
    if not perfil.foto_registrada(cfg.sqlite_path, nome):
        raise HTTPException(status_code=404, detail="sem foto")
    caminho = cfg.data_dir / "fotos" / nome
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="sem foto")
    return FileResponse(caminho)


# ------------------------------------------------------------ labs (diretório)
@app.get("/labs", response_class=HTMLResponse)
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
        "perfis": perfil.listar_perfis(cfg.sqlite_path, u.email),
        "erro": _pegar_recado(request),
        "enviado": request.query_params.get("enviado", "") == "1",
    })


# ------------------------------------------------- pedidos de amostra (0052)
@app.post("/labs/{chave}/pedido")
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
    destino = perfil.email_da_chave(cfg.sqlite_path, chave)
    if not destino:
        _por_recado(request, "não foi possível enviar o pedido a este "
                             "laboratório; recarregue a página")
        return RedirectResponse("/labs", status_code=303)
    try:
        pedidos.criar(cfg.sqlite_path, de_email=u.email, para_email=destino,
                      itens=itens, outro=outro, motivo=motivo)
    except pedidos.NaoPode as e:
        _por_recado(request, str(e))
        return RedirectResponse("/labs", status_code=303)
    return RedirectResponse("/pedidos?enviado=1", status_code=303)


@app.get("/pedidos", response_class=HTMLResponse)
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
        "recebidos": pedidos.recebidos(cfg.sqlite_path, u.email),
        "enviados": pedidos.enviados(cfg.sqlite_path, u.email),
        "enviado": bool(enviado),
    })


@app.post("/pedidos/{pedido_id}/responder")
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
        atualizado = pedidos.responder(cfg.sqlite_path, pedido_id, u.email,
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


@app.post("/pedidos/{pedido_id}/cancelar")
def cancelar_pedido(request: Request, pedido_id: str):
    u = _exigir(request)
    if not pedidos.cancelar(cfg.sqlite_path, pedido_id, u.email):
        raise HTTPException(status_code=404, detail="pedido não encontrado")
    return RedirectResponse("/pedidos", status_code=303)


# ------------------------------------------------------- bancos de referência
@app.get("/bancos", response_class=HTMLResponse)
def pagina_bancos(request: Request, feito: str = ""):
    u = _exigir(request)
    grupos: dict[str, list] = {}
    for c in bancos.CATALOGO:
        grupos.setdefault(c.grupo, []).append(
            {"c": c, "estado": bancos.estado(cfg.data_dir, c.id)})
    return TEMPLATES.TemplateResponse(request, "bancos.html", {
        **_casca(u), "grupos": grupos,
        "meus": bancos.meus_bancos(cfg.data_dir, u.email),
        "erro": _pegar_recado(request), "feito": feito,
    })


@app.post("/bancos/{banco_id}/montar")
def montar_banco(request: Request, banco_id: str):
    """Baixa do NCBI e monta. Síncrono de propósito: são segundos para os
    pequenos, e quem aperta está olhando. Os grandes avisam o tamanho antes."""
    u = _exigir(request)
    limites.conferir("banco", request, u.email)
    if banco_id not in bancos.POR_ID:
        raise HTTPException(status_code=404, detail="banco desconhecido")
    try:
        bancos.montar(cfg.data_dir, banco_id, blast_bin=cfg.blast_bin)
    except Exception as e:                      # noqa: BLE001
        _por_recado(request, str(e)[:200])
        return RedirectResponse("/bancos", status_code=303)
    return RedirectResponse(f"/bancos?feito={quote(banco_id)}", status_code=303)


def _e_administrador(u: auth.Usuario) -> bool:
    """Quem pode mexer no que é de todo mundo. Vazio = ninguém, de propósito."""
    lista = {e.strip().lower()
             for e in os.environ.get("EASYCONTIG_ADMINS", "").split(",") if e.strip()}
    return u.email.lower() in lista


@app.post("/bancos/{banco_id}/remover")
def remover_banco(request: Request, banco_id: str):
    u = _exigir(request)
    if banco_id.startswith("meu_"):
        # banco de usuário só some pela mão do dono
        if banco_id not in {b["id"] for b in bancos.meus_bancos(cfg.data_dir, u.email)}:
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
    bancos.remover(cfg.data_dir, banco_id)
    return RedirectResponse("/bancos", status_code=303)


@app.post("/bancos/meu")
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
    quanto = cotas.situacao(cfg.sqlite_path, cfg.lotes_dir, u.email, cfg.data_dir)
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
            await run_in_threadpool(bancos.montar_do_usuario, cfg.data_dir,
                                    banco_id, dados, blast_bin=cfg.blast_bin)
    except HTTPException:
        # ⚠️ ANTES do `except Exception`, senão o 503 de "sem vaga" é engolido e
        # volta como um texto de erro na página — a trava existiria e ninguém
        # veria que ela agiu.
        raise
    except Exception as e:                      # noqa: BLE001
        _por_recado(request, str(e)[:200])
        return RedirectResponse("/bancos", status_code=303)
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
    # UMA vaga para os dois passos: soltar entre o tracy e o blastn deixaria a
    # janela em que 40 requisições passam pelo primeiro teto e se acumulam no
    # segundo, que é o caro (timeout de 120 s).
    with _vaga_pesada():
        asm = traco.montar(cfg, executor.pastas_do_lote(cfg, lote_id)["raiz"], am)
        if asm is None:
            raise HTTPException(status_code=409,
                                detail="não foi possível remontar a amostra")
        try:
            hits = bancos.consultar(asm.consensus_nogap,
                                    bancos.prefixo(cfg.data_dir, banco),
                                    blast_bin=cfg.blast_bin)
        except Exception as e:                  # noqa: BLE001
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
        # Só se pergunta quando importa: um lote esperando na fila é o único
        # caso em que a tela promete que alguém vai pegar.
        "fila_atendida": (fila.fila_atendida(cfg.sqlite_path)
                          if lote["status"] == fila.NA_FILA else True),
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
    with _vaga_pesada():
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
        # só os bancos montados: oferecer o que não está pronto é oferecer erro
        "consultaveis": [r for r in _referencias(u)
                         if r["id"] != (lote.get("referencia") or "curado")],
        "referencia_usada": lote.get("referencia") or "curado",
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
@app.api_route("/saude", methods=["GET", "HEAD"])
def saude(request: Request):
    """De pé ou não. Detalhe só para quem entrou.

    ⚠️ **HEAD, não só GET** (2026-08-08). O FastAPI — ao contrário do Starlette
    puro — NÃO acrescenta HEAD numa rota declarada com `@app.get`, então a rota
    respondia `405 Method Not Allowed` a quem falasse HEAD. O UptimeRobot usa
    HEAD, e escolher o método é recurso PAGO lá: o monitor externo nasceu em
    "down" e assim ficou, apontado para um site que estava de pé o tempo todo.
    O 503 que a nota abaixo descreve não servia para nada, porque o monitor
    nunca chegava a ler código de saúde algum. Corrigir aqui é o que não depende
    do plano de ninguém.


    ⚠️ Isto respondia a QUALQUER UM com os caminhos absolutos do contêiner
    (`/usr/local/bin/tracy`, `/bancos/referencias_18S`), quais ferramentas
    científicas estão instaladas e onde, e a profundidade da fila **sem filtro
    de dono** — repetindo a chamada, um estranho acompanha quando o laboratório
    trabalha e quanto. Não vaza `.ab1` nem credencial, mas é reconhecimento e um
    oráculo de atividade, de graça.

    A rota continua aberta porque o `healthcheck` do compose a usa sem cookie —
    e para ele `{"ok": true}` com 200 basta. Quem precisa do detalhe é quem
    opera, e quem opera tem sessão.

    ⚠️ **DOENTE RESPONDE 503, NÃO 200** (2026-08-08). Antes, `ok: false` saía com
    **200**, e o efeito é que a rota mentia para todo mundo que fala HTTP em vez
    de ler JSON: um monitor externo olhando o código de status veria "no ar" com
    o `tracy` sumido ou os bancos sem índice — que é justamente o estado em que
    o serviço aceita corrida e não consegue identificar nada. O `healthcheck` do
    compose tinha o mesmo buraco: conferia `status == 200`, e 200 era o que
    chegava sempre.

    Os quatro itens de `config.diagnostico` são estáveis dentro do contêiner
    (dois binários da imagem e dois índices do volume), então 503 aqui não
    pisca: quando aparecer, quebrou mesmo.
    """
    itens = config.diagnostico(cfg)
    ok = all(x for _, x, _ in itens)
    codigo = 200 if ok else 503
    if not _u(request):
        return JSONResponse({"ok": ok}, status_code=codigo)
    return JSONResponse({
        "ok": ok,
        "dependencias": [{"item": i, "ok": x, "detalhe": d} for i, x, d in itens],
        "na_fila": sum(1 for x in fila.listar(cfg.sqlite_path, limite=500)
                       if x["status"] == fila.NA_FILA),
    }, status_code=codigo)
