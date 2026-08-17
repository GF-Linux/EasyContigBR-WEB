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

from .web import (
    rotas_de_bancos, rotas_de_consulta_de_lote, rotas_de_entrada,
    rotas_de_envio_de_lote, rotas_de_labs_e_pedidos, rotas_de_perfil,
)
from .web.comum import (  # o que toda rota usa; mora fora para não haver ciclo
    EXT_ACEITAS, TEMPLATES, _vaga_pesada, _br, _casca, _exigir, _lote_do_usuario,
    _marcar_resposta, _pegar_recado, _por_recado, _referencias, _u,
)

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

# O JS do workspace sai em arquivo, e não embutido no template, porque o
# navegador o guarda em cache entre amostras — a página da amostra é a que mais
# se reabre, uma por par.
app.mount("/estatico",
          StaticFiles(directory=str(Path(__file__).parent / "estatico")),
          name="estatico")


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


#* As rotas moram em arquivos por assunto, em `web/`.
#! As rotas são ACRESCENTADAS uma a uma, e não por `include_router`. Motivo
#!   medido: nesta versão do FastAPI o `include_router` guarda um embrulho em
#!   `app.routes`, e quem percorre a lista procurando `.path` deixa de achar a
#!   rota — foi o que quebrou o teste do leia-me. Acrescentar mantém
#!   `app.routes` com o mesmo formato de quando tudo morava num arquivo só.
#! A ORDEM é a mesma de antes: o FastAPI casa a primeira rota que bater.
for _modulo in (rotas_de_entrada, rotas_de_envio_de_lote, rotas_de_perfil,
                rotas_de_labs_e_pedidos, rotas_de_bancos, rotas_de_consulta_de_lote):
    app.router.routes.extend(_modulo.router.routes)

#! Reexportado de propósito: a suíte confere o `_destino` direto no servidor
#!   (`main._destino(...)`), e ele mora no arquivo das rotas de entrada. Tirar
#!   esta linha não quebra o site — quebra o teste que guarda o redirecionamento
#!   contra destino externo.
_destino = rotas_de_entrada._destino
