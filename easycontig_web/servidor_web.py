"""
servidor_web.py — o app FastAPI do EasyContig BR: middlewares, paredes de erro, /saude e as seis familias de rotas.

    uvicorn easycontig_web.servidor_web:app
                 |
                 v
    criar_app(instalacao)                       Instalacao(configuracao.carregar()).preparar()  -> app.state.instalacao
      |
      |   pilha de middlewares (de fora para dentro, na ordem em que o pedido passa):
      |     cabecalhos_de_seguranca   nonce por resposta -> request.state.nonce; CSP, nosniff, DENY, noindex, HSTS
      |     sem_cache                 no-store em tudo que nao e /estatico/
      |     SessionMiddleware         cookie assinado (EASYCONTIG_SECRET_KEY), SameSite=Lax
      |     limite_de_leitura         POST: origem confere? corpo cabe no teto? tem sessao?   GET/HEAD: limitador "leitura"
      |     GZipMiddleware
      |     ExceptionMiddleware       HTTPException -> erro_http (JSON para API, erro.html para navegador, 401 -> /entrar)
      |       |
      |       '-- /saude, /estatico, rotas_de_entrada, rotas_de_envio_de_lote, rotas_de_perfil,
      |           rotas_de_labs_e_pedidos, rotas_de_bancos, rotas_de_consulta_de_lote
      |
      '-- ServerErrorMiddleware (o Starlette poe no ponto MAIS de fora): Exception -> erro_nao_previsto
             que por isso carimba os cabecalhos e o no-store A MAO, porque nao passa por middleware nenhum

COMO FUNCIONA

    O import do modulo monta `app` com a configuracao do ambiente — e o que o
    Dockerfile chama (uvicorn easycontig_web.servidor_web:app). Um teste chama
    criar_app(Instalacao(cfg_de_teste)) e ganha outro app inteiro, com outra
    pasta e outro SQLite, sem recarregar modulo nenhum.

    Cada rota acha o que precisa em comum.instalacao_de(request): a fila, os
    perfis, a estante, o NCBI, o semaforo. Nada e global de modulo.

COMO USAR

    uvicorn easycontig_web.servidor_web:app --host 0.0.0.0 --port 8000
    python -m easycontig_web.servidor_web           # desenvolvimento: 127.0.0.1:8000

O QUE MUDOU (em relacao ao servidor_web.py original)

 1. O app e montado por criar_app(instalacao): a configuracao, o SQLite, as
    filas, a estante e o NCBI moram numa Instalacao (web/instalacao.py) presa
    em app.state, em vez de globais de modulo relidos em cada arquivo de rota.

 2. O esquema do SQLite e criado UMA vez (instalacao.preparar), nao tres
    (fila, perfil, pedidos) — o banco_sqlite.py unificou o esquema.

 3. As rotas continuam entrando por app.router.routes.extend, na mesma
    ordem, como o original. Medido no fastapi 0.141 instalado: include_router
    guarda um unico _IncludedRouter (sem .path) em app.routes, e quem percorre
    a lista atras dos caminhos (o /saude de amanha, um teste, o main daqui)
    nao ve rota nenhuma. Acrescentar deixa app.routes plano: um APIRoute por
    caminho, como quando tudo morava num arquivo so.

 4. Os avisos que iam para o stderr com print (erro nao previsto, queixa do
    client_id) vao para o logger 'easycontig.servidor' — sem handler, o
    Python os imprime no stderr do mesmo jeito; com handler, vao onde o
    operador mandou. A queixa do client_id saia SETE vezes (uma por modulo
    que carregava a configuracao); sai uma.

 5. `_destino = rotas_de_entrada._destino` era reexportado so para a suite
    antiga; a suite sera reescrita, entao nao ha o que reexportar.

 6. MAX_CORPO, MAX_CORPO_SEM_SESSAO, MAX_PESADAS e o semaforo _VAGAS_PESADAS
    sao atributos da Instalacao (max_corpo, max_corpo_sem_sessao, vagas).

POR QUE nao nasce classe

    O que tem estado ja e a Instalacao; o app e um objeto do FastAPI. As
    funcoes daqui sao os middlewares e as paredes de erro, que recebem o
    request e devolvem a resposta.
"""

import logging
import os
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from easycontig_web import configuracao
from easycontig_web.contas import autenticacao
from easycontig_web.processamento.fila_de_lotes import NA_FILA
from easycontig_web.web import comum
from easycontig_web.web.instalacao import Instalacao
from easycontig_web.web import (rotas_de_entrada, rotas_de_envio_de_lote, rotas_de_perfil, rotas_de_labs_e_pedidos,
                                rotas_de_bancos, rotas_de_consulta_de_lote)


REGISTRO = logging.getLogger('easycontig.servidor')

TITULO = 'EasyContig BR — lotes'

PASTA_ESTATICA = Path(__file__).parent / 'estatico'

ROTAS = (rotas_de_entrada, rotas_de_envio_de_lote, rotas_de_perfil, rotas_de_labs_e_pedidos, rotas_de_bancos, rotas_de_consulta_de_lote)  #* a ordem em que o app as registra

POST_SEM_SESSAO = {'/entrar'}  #* o unico POST que quem nao entrou pode fazer

MEGA = 1024 * 1024

GZIP_A_PARTIR_DE = 1024  #* bytes

TAMANHO_DO_NONCE = 16  #* bytes do token_urlsafe que vai na CSP de cada resposta

SEM_CACHE = {'Cache-Control': 'no-store, must-revalidate', 'Pragma': 'no-cache'}  #* apagar uma corrida e apertar VOLTAR nao pode trazer a pagina dela do cache

LOTES_NA_SAUDE = 500  #* quantos lotes o /saude olha para contar a fila

TEXTO_DO_ERRO_NAO_PREVISTO = ('Algo quebrou do lado do servidor, e não foi por causa do que você mandou. '
                              'O registro do erro ficou no log do servidor — avise quem cuida dele.')

PAGINA_DE_SOCORRO = ('<!doctype html><html lang=pt-br><head><meta charset=utf-8><title>Erro interno</title></head><body>'
                     f'<h1>Erro interno do servidor</h1><p>{TEXTO_DO_ERRO_NAO_PREVISTO}</p></body></html>')  #* quando ate o erro.html falha




def producao():

    return os.environ.get('EASYCONTIG_PRODUCAO') == '1'




def conferir_arranque():

    #* em producao, configuracao incompleta derruba o arranque (melhor que subir e falhar no primeiro lote); no modo google, avisa do client_id torto

    if producao():

        problemas = configuracao.conferir_producao()

        if problemas:
            raise RuntimeError('EASYCONTIG_PRODUCAO=1 e a configuração não está pronta:\n  - ' + '\n  - '.join(problemas))

    if autenticacao.modo() == 'google':

        queixa = autenticacao.queixa_do_client_id()

        if queixa:
            REGISTRO.warning(queixa)




def origem_confere(request, url_base=''):

    #* o POST veio de uma pagina DESTE site? o cookie e SameSite=Lax, que e same-SITE: qualquer host sob o mesmo dominio mandaria o cookie junto.
    #* sem Origin passa: navegador manda Origin em todo POST desde 2020; quem nao manda e curl/script, que nao carrega cookie de terceiro

    origem = request.headers.get('origin')

    if not origem:
        return True

    esperado = url_base or str(request.base_url).rstrip('/')

    return origem.rstrip('/').lower() == esperado.lower()




def teto_do_caminho(instalacao, caminho):

    #* rota aberta a quem nao tem sessao paga o teto pequeno; o resto segue no teto de envio

    return instalacao.max_corpo_sem_sessao if caminho in POST_SEM_SESSAO else instalacao.max_corpo




def recado_do_teto(teto):

    if teto < MEGA:
        return f'o envio passa do teto de {teto // 1024} KB para esta requisição'

    return f'o envio passa do teto de {teto // MEGA} MB por requisição; mande em lotes menores'




def corpo_grande_demais(instalacao, request):

    #* recusa pelo Content-Length, ANTES de o corpo existir: o parser multipart do Starlette grava cada arquivo em /tmp antes de a rota rodar.
    #* sem o cabecalho nao ha o que conferir aqui (corpo chunked); esse caso e lido aos pedacos no middleware

    bruto = request.headers.get('content-length')

    if not bruto:
        return ''

    try:
        declarado = int(bruto)

    except ValueError:
        return 'content-length inválido'

    teto = teto_do_caminho(instalacao, request.url.path)

    return recado_do_teto(teto) if declarado > teto else ''




async def limite_de_leitura(request, call_next):

    #* no middleware, e nao rota a rota: rota nova ja nasce coberta. o 401 do POST tem de sair DAQUI: o FastAPI le o formulario inteiro
    #* antes de chamar a funcao da rota, entao um chamador sem cookie decidiria quantos bytes o servidor recebe

    instalacao = comum.instalacao_de(request)

    caminho = request.url.path

    if caminho.startswith('/estatico/'):
        return await call_next(request)

    if request.method == 'POST':

        if not origem_confere(request, instalacao.url_base):
            return JSONResponse({'detail': 'origem não confere'}, status_code=403)

        grande = corpo_grande_demais(instalacao, request)

        if grande:
            return JSONResponse({'detail': grande}, status_code=413)

        if caminho not in POST_SEM_SESSAO and not tem_sessao(request):

            if comum.quer_html(request):
                return RedirectResponse(f'/entrar?proximo={quote(caminho)}', status_code=303)

            return JSONResponse({'detail': 'entre para continuar'}, status_code=401)

        if not request.headers.get('content-length'):

            teto = teto_do_caminho(instalacao, caminho)

            corpo = bytearray()

            async for pedaco in request.stream():

                corpo.extend(pedaco)

                if len(corpo) > teto:
                    return JSONResponse({'detail': recado_do_teto(teto)}, status_code=413)

            request._body = bytes(corpo)  #! e assim que o Starlette guarda um corpo ja lido: o BaseHTTPMiddleware reentrega _body a rota

    if request.method in ('GET', 'HEAD'):

        try:
            instalacao.limitador.conferir('leitura', request, quem_entrou(request))

        except HTTPException as erro:
            return JSONResponse({'detail': erro.detail}, status_code=429, headers=erro.headers or {})

    return await call_next(request)




def quem_entrou(request):

    #* o e-mail da sessao, ou None; sessao ilegivel conta como ninguem

    try:
        u = comum.usuario(request)

    except Exception:
        return None

    return u.email if u else None




def tem_sessao(request):

    return quem_entrou(request) is not None




async def sem_cache(request, call_next):

    #* pagina com sessao nao vai para o cache do navegador; o estatico fica de fora (o JS se beneficia do cache)

    resposta = await call_next(request)

    if not request.url.path.startswith('/estatico/'):
        resposta.headers.update(SEM_CACHE)

    return resposta




async def cabecalhos_de_seguranca(request, call_next):

    #* um nonce por resposta: o script inline do base.html o carrega, e mais nenhum script executa (CSP sem 'unsafe-inline')

    nonce = novo_nonce()

    request.state.nonce = nonce

    resposta = await call_next(request)

    comum.marcar_resposta(resposta, nonce, comum.instalacao_de(request).https_only)

    return resposta




def novo_nonce():

    return secrets.token_urlsafe(TAMANHO_DO_NONCE)




def casca_do_erro(request):

    #* a barra lateral de quem entrou, ou nada

    u = comum.usuario(request)

    return comum.instalacao_de(request).casca(u) if u else {'usuario': None}




def erro_http(request, exc):

    #* quem veio pelo navegador recebe pagina; quem veio por API recebe JSON; 401 no navegador vai para /entrar e volta depois

    cabecalhos = dict(exc.headers or {})

    if not comum.quer_html(request):
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=cabecalhos)

    if exc.status_code == 401:
        return RedirectResponse(f'/entrar?proximo={quote(request.url.path)}', status_code=303)

    return comum.TEMPLATES.TemplateResponse(request, 'erro.html', {**casca_do_erro(request), 'codigo': exc.status_code, 'detalhe': exc.detail},
                                            status_code=exc.status_code, headers=cabecalhos)




def erro_nao_previsto(request, exc):

    #* a parede de qualquer excecao que ninguem previu. o texto NAO leva a excecao (caminho de arquivo, consulta): isso vai para o log.
    #! esta resposta nao passa por middleware nenhum (ServerErrorMiddleware e o mais de fora): cabecalhos e no-store carimbados a mao

    REGISTRO.error('erro não previsto em %s %s: %s: %s', request.method, request.url.path, type(exc).__name__, exc, exc_info=exc)

    nonce = getattr(request.state, 'nonce', None) or novo_nonce()

    if not comum.quer_html(request):
        resposta = JSONResponse({'detail': 'erro interno do servidor'}, status_code=500)

    else:

        try:
            resposta = comum.TEMPLATES.TemplateResponse(request, 'erro.html', {**casca_do_erro(request), 'codigo': 500, 'detalhe': TEXTO_DO_ERRO_NAO_PREVISTO}, status_code=500)

        except Exception as erro:

            REGISTRO.error('a própria página de erro falhou ao montar: %s: %s', type(erro).__name__, erro, exc_info=erro)

            resposta = HTMLResponse(PAGINA_DE_SOCORRO, status_code=500)

    comum.marcar_resposta(resposta, nonce, comum.instalacao_de(request).https_only)

    resposta.headers.update(SEM_CACHE)

    return resposta




def saude(request: Request):

    #* de pe ou nao (200/503); o detalhe (ferramentas, bancos, fila) so para quem entrou. GET e HEAD: o monitor externo fala HEAD

    instalacao = comum.instalacao_de(request)

    itens = configuracao.diagnostico(instalacao.cfg)

    ok = all(bem for _, bem, _ in itens)

    codigo = 200 if ok else 503

    if not comum.usuario(request):
        return JSONResponse({'ok': ok}, status_code=codigo)

    na_fila = sum(1 for lote in instalacao.lotes.listar(limite=LOTES_NA_SAUDE) if lote.status == NA_FILA)

    return JSONResponse({'ok': ok, 'dependencias': [{'item': item, 'ok': bem, 'detalhe': detalhe} for item, bem, detalhe in itens], 'na_fila': na_fila}, status_code=codigo)




def criar_app(instalacao=None):

    #* monta o app inteiro sobre UMA Instalacao. a ordem dos add_middleware importa: o ultimo registrado e o mais de fora

    if instalacao is None:
        instalacao = Instalacao(configuracao.carregar())

    conferir_arranque()

    instalacao.preparar()

    docs = None if producao() else '/api/docs'

    app = FastAPI(title=TITULO, docs_url=docs, redoc_url=None, openapi_url=None if docs is None else '/openapi.json')

    app.state.instalacao = instalacao

    app.add_middleware(GZipMiddleware, minimum_size=GZIP_A_PARTIR_DE)

    app.middleware('http')(limite_de_leitura)

    app.add_middleware(SessionMiddleware, secret_key=instalacao.chave_da_sessao, https_only=instalacao.https_only, same_site='lax')

    app.mount('/estatico', StaticFiles(directory=str(PASTA_ESTATICA)), name='estatico')

    app.middleware('http')(sem_cache)

    app.middleware('http')(cabecalhos_de_seguranca)

    app.add_exception_handler(HTTPException, erro_http)

    app.add_exception_handler(Exception, erro_nao_previsto)

    app.add_api_route('/saude', saude, methods=['GET', 'HEAD'])

    for modulo in ROTAS:
        app.router.routes.extend(modulo.router.routes)  #! nao include_router: no fastapi 0.141 ele embrulha tudo num _IncludedRouter sem .path (ver O QUE MUDOU 3)

    return app




app = criar_app()




def main():

    #* servidor de desenvolvimento; em producao o Dockerfile chama o uvicorn direto

    import uvicorn

    uvicorn.run(app, host='127.0.0.1', port=8000)


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
