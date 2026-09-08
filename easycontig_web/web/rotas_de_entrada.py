"""
rotas_de_entrada.py — a porta: pagina inicial, entrar (dev e Google), sair.

    GET  /                    inicio          sem sessao -> 303 /entrar; com sessao -> inicio.html (corridas, cota, referencias, diagnostico)
    GET  /entrar              pagina_entrar   entrar.html (modo, google_ok, dominio, erro = recado, proximo)
    POST /entrar              entrar_dev      so no modo dev: e-mail -> sessao -> 303 proximo   (limitador "login")
    GET  /auth/google         entrar_google   303 para o Google                                   (limitador "login")
    GET  /auth/google/volta   volta_google    code+state -> Usuario -> sessao -> 303 /
    POST /sair                sair            apaga a sessao -> 303 /entrar?saiu=1

    proximo (de onde a pessoa veio) ---> destino_local(proximo) ---> so caminho local ("/x"), nunca outro site; senao "/"

COMO FUNCIONA

    O modo vem de EASYCONTIG_AUTH: dev (qualquer e-mail entra, com a restricao
    de dominio se houver) ou google (OAuth, contas.autenticacao.Google). Nos
    dois, entrar e: conferir o dominio, gravar o Usuario na sessao e garantir
    o registro do perfil.

COMO USAR

    app.include_router(rotas_de_entrada.router)   # servidor_web.criar_app faz isso

O QUE MUDOU (em relacao ao original)

 1. O cabecalho de 84 linhas (config.carregar, PRODUCAO, queixa, imports de
    comum) sumiu: a rota pega tudo de comum.instalacao_de(request).

 2. _destino -> destino_local, _fora_do_dominio -> fora_do_dominio(cfg),
    _redirect_uri -> redirect_uri(request, url_base). O aviso do retorno do
    OAuth com erro vai para o logger em vez do stderr.

 3. As anotacoes de tipo ficam SO nas assinaturas das rotas (request: Request,
    Form(...), int): e por elas que o FastAPI descobre de onde vem cada
    parametro. No resto do pacote nao ha anotacao.

 4. A rota / nao passa mais `lotes` ao inicio.html: o template nunca leu a
    chave (a lateral usa `corridas`, da casca), e era uma consulta ao SQLite
    por abertura de pagina a toa. Regra do sem-chamador (NAO-PORTADO.md).

POR QUE nao nasce classe

    Cada rota e uma funcao de request para resposta; o estado esta na Instalacao.
"""

import logging
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from easycontig_web import configuracao
from easycontig_web.contas import autenticacao
from easycontig_web.contas.autenticacao import Usuario
from easycontig_web.web import comum


REGISTRO = logging.getLogger('easycontig.entrada')

MEGA = 1024 * 1024

CARACTERES_PROIBIDOS = '\t\n\r\\'  #* num destino de redirecionamento

router = APIRouter()




@router.get('/', response_class=HTMLResponse)
def inicio(request: Request):

    instalacao = comum.instalacao_de(request)

    u = comum.usuario(request)

    if not u:
        return RedirectResponse('/entrar', status_code=303)

    cfg = instalacao.cfg

    return comum.TEMPLATES.TemplateResponse(request, 'inicio.html', {
        **instalacao.casca(u),
        'pagina': 'inicio',
        'diagnostico': configuracao.diagnostico(cfg),
        'trim': cfg.trim,
        'max_arquivos': cfg.maximo_de_arquivos,
        'max_mb': cfg.maximo_de_bytes // MEGA,
        'cota': instalacao.cotas.situacao(u.email),
        'retencao_dias': cfg.retencao_dias,
        'referencias': instalacao.referencias(u.email),
    })




def destino_local(proximo):

    #* para onde mandar depois do login: so caminho local. `proximo` vem da URL, entao e entrada do usuario:
    #* "//outro.site" o navegador le como outro host, e a nossa pagina de login despejaria a pessoa la

    if not proximo.startswith('/') or proximo.startswith(('//', '/\\')):
        return '/'

    if any(c in proximo for c in CARACTERES_PROIBIDOS):
        return '/'

    return proximo




@router.get('/entrar', response_class=HTMLResponse)
def pagina_entrar(request: Request, proximo: str = ''):

    cfg = comum.instalacao_de(request).cfg

    return comum.TEMPLATES.TemplateResponse(request, 'entrar.html', {
        'modo': autenticacao.modo(),
        'google_ok': autenticacao.google_configurado(),
        'dominio': autenticacao.dominios_publicos(cfg.dominio_permitido),
        'erro': comum.pegar_recado(request),
        'proximo': destino_local(proximo),
    })




def fora_do_dominio(cfg):

    #* a recusa volta para QUEM TENTOU entrar (no modo dev, qualquer anonimo): so os dominios publicos, nunca os e-mails nomeados

    publicos = autenticacao.dominios_publicos(cfg.dominio_permitido)

    return f'conta fora do domínio {publicos}' if publicos else 'conta não autorizada'




@router.post('/entrar')
def entrar_dev(request: Request, email: str = Form(...), proximo: str = Form('')):

    instalacao = comum.instalacao_de(request)

    if autenticacao.modo() != 'dev':
        raise HTTPException(403, 'login de desenvolvimento desligado')

    instalacao.limitador.conferir('login', request)

    email = email.strip().lower()

    destino = destino_local(proximo)

    volta = f'?proximo={quote(destino)}' if destino != '/' else ''

    if '@' not in email:

        comum.por_recado(request, 'e-mail inválido')

        return RedirectResponse(f'/entrar{volta}', status_code=303)

    if not autenticacao.dominio_ok(email, instalacao.cfg.dominio_permitido):

        comum.por_recado(request, fora_do_dominio(instalacao.cfg))

        return RedirectResponse(f'/entrar{volta}', status_code=303)

    autenticacao.entrar_na_sessao(request.session, Usuario(email, nome=email.split('@')[0]))

    instalacao.perfis.garantir_registro(email)

    return RedirectResponse(destino, status_code=303)




def redirect_uri(request, url_base=''):

    #* o endereco de volta do Google tem de casar EXATAMENTE com o cadastrado no console: atras do proxy, a URL do request vem com o host errado

    return (url_base or str(request.base_url).rstrip('/')) + '/auth/google/volta'




def google_ligado():

    return autenticacao.modo() == 'google' and autenticacao.google_configurado()




@router.get('/auth/google')
def entrar_google(request: Request):

    instalacao = comum.instalacao_de(request)

    if not google_ligado():
        raise HTTPException(404, 'login pelo Google não configurado')

    instalacao.limitador.conferir('login', request)

    ida = autenticacao.provedor_google().url_de_ida(request.session, redirect_uri(request, instalacao.url_base))

    return RedirectResponse(ida, status_code=303)




@router.get('/auth/google/volta')
def volta_google(request: Request, code: str = '', state: str = '', error: str = ''):

    instalacao = comum.instalacao_de(request)

    if not google_ligado():
        raise HTTPException(404, 'login pelo Google não configurado')

    if error or not code:

        if error:
            REGISTRO.warning('retorno do OAuth com error=%r', error)

        comum.por_recado(request, autenticacao.mensagem_de_erro_oauth(error) if error else 'entrada cancelada')

        return RedirectResponse('/entrar', status_code=303)

    u = autenticacao.provedor_google().usuario_da_volta(request.session, code, state, redirect_uri(request, instalacao.url_base))

    if not autenticacao.dominio_ok(u.email, instalacao.cfg.dominio_permitido):

        comum.por_recado(request, fora_do_dominio(instalacao.cfg))

        return RedirectResponse('/entrar', status_code=303)

    autenticacao.entrar_na_sessao(request.session, u)

    instalacao.perfis.garantir_registro(u.email, u.nome_google)

    return RedirectResponse('/', status_code=303)




@router.post('/sair')
def sair(request: Request):

    #* POST, nao GET: como GET, um <img src=".../sair"> em qualquer pagina derrubava a sessao de quem a abrisse

    autenticacao.sair_da_sessao(request.session)

    return RedirectResponse('/entrar?saiu=1', status_code=303)




def main():

    #* mostra as rotas e o que destino_local faz com entradas tortas

    for rota in router.routes:
        print(f'  {",".join(sorted(rota.methods)):9} {rota.path:22} {rota.endpoint.__name__}')

    for proximo in ('/lotes/abc', '//outro.site', '/\\x', 'https://x', '/a\nb', ''):
        print(f'  destino_local({proximo!r}) = {destino_local(proximo)!r}')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
