"""
autenticacao.py — quem e o usuario e se ele pode entrar: dois provedores na mesma costura, e o dominio decidindo a porta.

`dev` — digita o e-mail e entra. So para desenvolvimento local; conferir_producao
(configuracao.py) recusa subir assim em servidor. `google` — OAuth do Google: o
Google prova QUEM e, e EASYCONTIG_DOMINIO decide SE entra. E isso que permite
usar conta Google comum e ainda ser servico da universidade. Gov.br foi
descartado (ADR 0050).

O caminho `google` NUNCA foi exercitado com credencial real — depende de
CLIENT_ID/SECRET que so o autor cria no console. O fluxo esta inteiro (ida,
volta, troca do codigo por token, leitura do e-mail), e a bancada o prova
contra um Google FALSO (servidor HTTP local); ate alguem rodar com credencial
de verdade, considere NAO TESTADO contra o Google.


COMO FUNCIONA — a entrada
=========================

    modo()  ->  'dev'                        POST /entrar: e-mail digitado
                  |  dominio_ok(email, cfg.dominio_permitido)?  nao -> recado "conta fora do dominio ..."
                  v
                entrar_na_sessao(sessao, Usuario(email, nome=local-part))   (o nome so serve para a lateral)

            ->  'google'                     GET /auth/google
                  |  Google(client_id, secret).url_de_ida(sessao, redirect_uri)   guarda `state` na sessao, manda ao Google
                  v                          GET /auth/google/volta?code&state   (ou ?error -> mensagem_de_erro_oauth)
                Google.usuario_da_volta(sessao, code, state, redirect_uri)
                  |  state confere? (senao 400)  ->  troca o code por token  ->  le o perfil  ->  email_verified? (senao 403)
                  |  o Google recusou (HTTP 4xx/5xx)? -> 502 com o codigo publico dele, "nao adianta tentar de novo"
                  |  o Google nao respondeu (rede, DNS, tempo)? -> 502 "tente de novo"
                  v
                Usuario(email, nome=declarado ou local-part, nome_google=declarado)
                  |  dominio_ok?  nao -> recado
                  v
                entrar_na_sessao(sessao, usuario)      grava {email, nome} no cookie assinado; nome_google fica FORA

    usuario_da_sessao(sessao) -> Usuario ou None       (toda pagina; `Usuario(**dict)` monta a partir do cookie)
    sair_da_sessao(sessao)

    dominio_ok: sem dominio configurado, qualquer conta entra (desenvolvimento). `*` = aberto POR ESCRITO.
    `ufrrj.br` cobre `lhv.ufrrj.br` (universidade reparte o e-mail por unidade) e NAO cobre `falso-ufrrj.br`;
    `=ufrrj.br` e o dominio exato; `@ufrrj.br` digitado ainda e dominio; item com `@` no meio e UMA pessoa, exata.
    dominios_publicos: a lista SEM as pessoas nomeadas — e o que a pagina /entrar (publica) pode mostrar.


COMO USAR
=========

    from easycontig_web.contas import autenticacao

    autenticacao.modo(), autenticacao.google_configurado(), autenticacao.queixa_do_client_id()
    autenticacao.dominio_ok('ana@lhv.ufrrj.br', cfg.dominio_permitido)
    autenticacao.dominios_publicos('ufrrj.br, juaredbr@gmail.com')        #* 'ufrrj.br'

    google = autenticacao.provedor_google()                                #* Google(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
    ida = google.url_de_ida(request.session, redirect_uri)
    usuario = google.usuario_da_volta(request.session, code, state, redirect_uri)   #* HTTPException 400/502/403

    autenticacao.entrar_na_sessao(request.session, usuario)
    autenticacao.usuario_da_sessao(request.session)                       #* Usuario ou None
    autenticacao.sair_da_sessao(request.session)

    python -m easycontig_web.contas.autenticacao      #* modo, Google, dominio, e confere um e-mail contra a lista


O QUE MUDOU DO autenticacao.py ORIGINAL (323 linhas)
====================================================

 1. Google e uma classe: dona do client_id/secret e das tres URLs, e a unica
    coisa aqui que fala com a rede. url_de_ida e usuario_da_volta viraram
    metodos; provedor_google() monta a instancia a partir do ambiente (o
    original lia os.environ dentro de cada funcao). A troca do codigo por
    token e a leitura do perfil ganharam metodos proprios (trocar_codigo,
    perfil_do), que e o que a bancada exercita contra um Google falso.

 2. As funcoes de sessao recebem a SESSAO (request.session, um dict), nao o
    Request: e so nela que mexem, e assim rodam sem FastAPI na bancada.

 3. exigir_usuario nao veio: nenhum chamador (o comum.py tem o seu proprio
    `_exigir`, que redireciona para /entrar em vez de responder 401).
    Usuario.dominio tambem nao: nenhum chamador.

 4. Usuario e uma classe simples (era dataclass congelada); `Usuario(**dict)`
    continua montando a partir do cookie.

 5. Os avisos vao para o logging (REGISTRO), nao para print em stderr.

 6. Nomes: _e_pessoa_nomeada -> e_pessoa_nomeada; _ERRO_OAUTH ->
    ERROS_DO_OAUTH (+ ERRO_GENERICO); FORMA_CLIENT_ID -> FORMA_DO_CLIENT_ID;
    _erro_do_google -> codigo_do_erro_do_google; MODO_DEV/MODO_GOOGLE;
    ESCOPO; TEMPO_LIMITE.

 7. Nada mais mudou: as chaves do cookie (`usuario`, `oauth_estado`) e o que
    vai dentro dele sao os mesmos — a sessao de quem esta logado na VPS
    sobrevive a troca; os textos de tela, os codigos HTTP e a regra do
    dominio sao letra por letra os do original.


POR QUE NASCE CLASSE
====================

    Google e dono de um recurso externo (o provedor OAuth, com credencial):
    classe. Usuario e o registro da sessao como objeto. modo, dominio_ok,
    dominios_publicos, queixa_do_client_id e as tres funcoes de sessao sao
    texto/dict -> valor: funcoes.
"""

import json

import logging

import os

import re

import secrets

import urllib.error

import urllib.parse

import urllib.request

from fastapi import HTTPException



REGISTRO = logging.getLogger('easycontig.autenticacao')

SESSAO_CHAVE = 'usuario'  #* a chave do cookie de sessao onde mora {email, nome}; NAO mudar: e o que ja esta no navegador de quem entrou

ESTADO_CHAVE = 'oauth_estado'  #* onde a ida guarda o `state` que a volta confere

MODO_DEV, MODO_GOOGLE = 'dev', 'google'  #* os dois provedores (EASYCONTIG_AUTH)

FORMA_DO_CLIENT_ID = re.compile(r'^\d+-[a-z0-9_]+\.apps\.googleusercontent\.com$')  #* um ID de cliente do Google e `<numeros>-<letras>.apps.googleusercontent.com`

AUTORIZA = 'https://accounts.google.com/o/oauth2/v2/auth'  #* para onde a pessoa vai

TOKEN = 'https://oauth2.googleapis.com/token'  #* onde o codigo vira token

PERFIL = 'https://openidconnect.googleapis.com/v1/userinfo'  #* onde o token vira e-mail

ESCOPO = 'openid email profile'  #* so o que decide a entrada: e-mail confirmado e nome declarado

TEMPO_LIMITE = 20  #* segundos por chamada ao Google

ERROS_DO_OAUTH = {
    'access_denied': 'entrada cancelada',
    'admin_policy_enforced': 'a política da sua organização não permitiu a entrada',
    'invalid_request': 'não foi possível entrar pelo Google; tente de novo',
    'unauthorized_client': 'não foi possível entrar pelo Google; tente de novo',
    'unsupported_response_type': 'não foi possível entrar pelo Google; tente de novo',
    'invalid_scope': 'não foi possível entrar pelo Google; tente de novo',
    'server_error': 'o Google recusou a entrada agora; tente de novo em instantes',
    'temporarily_unavailable': 'o Google recusou a entrada agora; tente de novo em instantes',
}  #* as frases do retorno do OAuth, ESCRITAS PELO SERVIDOR: o `error` da query e texto de quem monta o link, e ecoa-lo na
   #* pagina de login deixaria um atacante redigir a frase que a vitima le no nosso site (achado F3). RFC 6749 4.1.2.1 + Google

ERRO_GENERICO = 'não foi possível entrar pelo Google'  #* qualquer codigo fora da tabela; o valor cru vai para o log, nunca para a tela




class Usuario:

    #* quem esta na sessao. nome_google e o que o provedor DECLAROU, separado do nome porque este cai no local-part do e-mail
    #* quando nao ha nada: para a lateral o tombo e inofensivo (e o proprio endereco de quem le); para o diretorio /labs nao
    #* e — foi assim que o e-mail alheio vazava (achado L1). quem grava perfil no primeiro login usa nome_google, que fica
    #* vazio quando ninguem declarou nada. nome_google NAO entra na sessao (entrar_na_sessao grava so email e nome): tem
    #* padrao para que Usuario(**dict) continue montando a partir do cookie

    def __init__(self, email, nome='', nome_google=''):

        self.email = email

        self.nome = nome  #* o que a lateral mostra

        self.nome_google = nome_google  #* o que o provedor declarou; '' no modo dev e no cookie


    def __repr__(self):

        return f'Usuario({self.email!r}, nome={self.nome!r})'




def modo():

    #* 'dev' ou 'google' (EASYCONTIG_AUTH; padrao dev)

    return os.environ.get('EASYCONTIG_AUTH', MODO_DEV).lower()




def google_configurado():

    #* as duas credenciais estao no ambiente? nao diz se sao validas — so o Google sabe

    return bool(os.environ.get('GOOGLE_CLIENT_ID') and os.environ.get('GOOGLE_CLIENT_SECRET'))




def queixa_do_client_id(valor=None):

    #* o que ha de errado com o GOOGLE_CLIENT_ID (do ambiente, ou `valor`), ou '' se esta bem. existe porque o erro do outro
    #* lado e mudo: um valor que nao corresponde a cliente nenhum so se revela na pagina do Google (`401 invalid_client`)
    #* depois de a pessoa ja ter escolhido a conta. os tres casos sao os que da para ver SEM falar com o Google e cobrem o que
    #* de fato aconteceu (06/08/2026): reticencias do exemplo da receita, aspas vindas do .env, espaco de copiar-e-colar.
    #! nao e validacao de credencial: quem sabe se o cliente existe e o Google

    bruto = os.environ.get('GOOGLE_CLIENT_ID', '') if valor is None else valor

    if not bruto:
        return ''  #* ausente e outro assunto (google_configurado)

    if bruto != bruto.strip():
        return 'GOOGLE_CLIENT_ID tem espaço ou quebra de linha nas pontas — o Google compara a string inteira'

    if bruto.strip('"\'') != bruto:
        return 'GOOGLE_CLIENT_ID veio com aspas em volta; no `.env` o valor vai cru, sem aspas'

    if not FORMA_DO_CLIENT_ID.match(bruto):
        return (f'GOOGLE_CLIENT_ID não tem a forma de um ID do Google (`<números>-<letras>.apps.googleusercontent.com`): {bruto!r}. '
                'Se ainda tem `…` ou `<>`, é o exemplo da receita, não a credencial')

    return ''




def dominio_ok(email, dominio_permitido):

    #* este e-mail pode entrar, segundo a lista EASYCONTIG_DOMINIO? sem lista, qualquer conta entra — de proposito, para o
    #* desenvolvimento local nao exigir configuracao (em producao conferir_producao recusa a lista em branco).
    #* cada item da lista, separado por virgula: `*` aberto por escrito; `ufrrj.br` o dominio e tudo abaixo dele
    #* (`lhv.ufrrj.br` sim, `falso-ufrrj.br` nao — o ponto antes do sufixo separa filho de sosia); `=ufrrj.br` so o exato;
    #* `@ufrrj.br` digitado ainda e dominio; `pessoa@gmail.com` e UMA pessoa, exata — e o que faz da lista uma porta com
    #* nomes escritos, e nao `gmail.com` inteiro para tirar uma pessoa do frio

    if not dominio_permitido:
        return True

    if '@' not in email:
        return False

    if '*' in {item.strip() for item in dominio_permitido.split(',')}:
        return True

    completo = email.strip().lower().rstrip('.')

    de = completo.rsplit('@', 1)[-1]

    for bruto in dominio_permitido.split(','):

        item = bruto.strip().lower().rstrip('.')

        if item.startswith('@'):
            item = item[1:]

        if not item:
            continue

        if '@' in item:
            if completo == item:
                return True

        elif item.startswith('='):
            if de == item[1:]:
                return True

        elif de == item or de.endswith('.' + item):
            return True

    return False




def e_pessoa_nomeada(bruto):

    #* um item da lista identifica UMA pessoa (tem `@` no meio)? espelha a regra de dominio_ok: tira o `@` da frente — que
    #* ali ainda e dominio — e, se sobrar um `@`, e endereco nomeado. a definicao existe UMA vez, senao o filtro publico
    #* e a checagem de acesso divergem no dia em que alguem mudar a sintaxe

    item = bruto.strip().lower()

    if item.startswith('@'):
        item = item[1:]

    return '@' in item




def dominios_publicos(dominio_permitido):

    #* a lista SEM os enderecos nomeados: o que pode ser mostrado a quem ainda nao entrou. a pagina /entrar e publica, e a
    #* lista crua entregaria a qualquer visitante o e-mail exato de cada pessoa autorizada (dado pessoal e mapa de phishing).
    #* o dominio institucional continua: orienta ("e com a conta da universidade") e nao identifica ninguem. lista so de
    #* pessoas devolve '' — e a tela nao promete restricao alguma em vez de listar gente

    itens = [item.strip() for item in dominio_permitido.split(',')]

    return ', '.join(item for item in itens if item and not e_pessoa_nomeada(item))




def mensagem_de_erro_oauth(codigo):

    #* o `error` do provedor como frase do servidor. NUNCA devolve o texto recebido: codigo desconhecido vira a generica

    return ERROS_DO_OAUTH.get((codigo or '').strip().lower(), ERRO_GENERICO)




def codigo_do_erro_do_google(bruto):

    #* o campo `error` do JSON de recusa do Google (ate 60 caracteres), ou '' se o corpo nao e JSON com ele

    try:
        return str(json.loads(bruto).get('error') or '')[:60]

    except Exception:
        return ''




def usuario_da_sessao(sessao):

    #* quem esta logado, ou None

    dados = sessao.get(SESSAO_CHAVE)

    return Usuario(**dados) if dados else None




def entrar_na_sessao(sessao, usuario):

    #* grava so email e nome no cookie assinado; nome_google fica fora (ver Usuario)

    sessao[SESSAO_CHAVE] = {'email': usuario.email, 'nome': usuario.nome}




def sair_da_sessao(sessao):

    sessao.pop(SESSAO_CHAVE, None)




class Google:

    #* o provedor OAuth: dono das credenciais e o unico que fala com o Google

    def __init__(self, client_id, client_secret):

        self.client_id = client_id

        self.client_secret = client_secret


    def url_de_ida(self, sessao, redirect_uri):

        #* para onde mandar quem clicou em "Entrar com Google"; guarda na sessao o `state` que a volta confere

        estado = secrets.token_urlsafe(24)

        sessao[ESTADO_CHAVE] = estado

        return AUTORIZA + '?' + urllib.parse.urlencode({
            'client_id': self.client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': ESCOPO,
            'state': estado,
            'prompt': 'select_account',  #* evita o login silencioso em maquina compartilhada, que e o caso de um computador de laboratorio
        })


    def trocar_codigo(self, code, redirect_uri):

        #* o codigo da volta vira access_token (POST no TOKEN); urllib.error.HTTPError quando o Google recusa

        corpo = urllib.parse.urlencode({
            'code': code,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        }).encode()

        with urllib.request.urlopen(urllib.request.Request(TOKEN, data=corpo), timeout=TEMPO_LIMITE) as resposta:
            return json.loads(resposta.read())['access_token']


    def perfil_do(self, token):

        #* o userinfo do token: dict com email, email_verified, name

        pedido = urllib.request.Request(PERFIL, headers={'Authorization': f'Bearer {token}'})

        with urllib.request.urlopen(pedido, timeout=TEMPO_LIMITE) as resposta:
            return json.loads(resposta.read())


    def usuario_da_volta(self, sessao, code, estado, redirect_uri):

        #* confere o `state`, troca o codigo por token, le o perfil e devolve quem entrou. HTTPException com o que fazer:
        #* 400 state errado; 502 o Google recusou (com o codigo publico dele: invalid_client, invalid_grant,
        #* redirect_uri_mismatch — e configuracao do servidor, nao adianta tentar de novo); 502 o Google nao respondeu
        #* (rede, DNS, tempo: ai sim tente de novo); 403 e-mail nao confirmado
        #! "o Google nao respondeu" era MENTIRA na maioria das falhas (06/08/2026): um except Exception transformava TODA
        #! falha nisso, inclusive `401 invalid_client — The provided client secret is invalid`, que chegava com o motivo
        #! escrito dentro e mandava tentar de novo. recusa e recusa; o corpo inteiro vai para o log

        esperado = sessao.pop(ESTADO_CHAVE, None)

        if not esperado or estado != esperado:
            raise HTTPException(status_code=400, detail='estado inválido; tente entrar de novo')  #* sem isto, um link forjado completaria o login da conta do atacante no navegador da vitima

        try:
            perfil = self.perfil_do(self.trocar_codigo(code, redirect_uri))

        except urllib.error.HTTPError as erro:

            try:
                corpo = erro.read()

            except Exception:
                corpo = b''

            REGISTRO.warning('Google recusou a entrada (HTTP %s): %s', erro.code, corpo.decode('utf-8', 'replace')[:400])

            codigo = codigo_do_erro_do_google(corpo)

            raise HTTPException(status_code=502, detail='o Google recusou esta entrada' + (f' ({codigo})' if codigo else '')
                                + ' — é configuração do servidor, não adianta tentar de novo. O motivo completo está no log.')

        except Exception as erro:

            REGISTRO.warning('Não foi possível falar com o Google: %s: %s', type(erro).__name__, erro)

            raise HTTPException(status_code=502, detail='o Google não respondeu; tente de novo')

        email = (perfil.get('email') or '').strip().lower()

        if not email or not perfil.get('email_verified'):
            raise HTTPException(status_code=403, detail='o Google não confirmou este e-mail')  #* conta sem e-mail confirmado nao prova dominio, e e o dominio que decide quem entra

        declarado = (perfil.get('name') or '').strip()

        return Usuario(email, nome=declarado or email.split('@')[0], nome_google=declarado)


    def __repr__(self):

        return f'Google({self.client_id!r})'  #* o secret nunca aparece




def provedor_google():

    #* o Google com as credenciais do ambiente; KeyError se faltam (google_configurado() diz antes)

    return Google(os.environ['GOOGLE_CLIENT_ID'], os.environ['GOOGLE_CLIENT_SECRET'])




def main():

    from easycontig_web import configuracao

    cfg = configuracao.carregar()

    print(f'modo de entrada: {modo()}')

    print(f'Google configurado: {"sim" if google_configurado() else "nao"}' + (f'  ({provedor_google()!r})' if google_configurado() else ''))

    queixa = queixa_do_client_id()

    if queixa:
        print(f'  queixa: {queixa}')

    print(f'dominio permitido: {cfg.dominio_permitido or "(em branco: qualquer e-mail entra)"}')

    print(f'  o que a pagina /entrar mostra: {dominios_publicos(cfg.dominio_permitido) or "(nada)"}')

    email = input('\nE-mail para conferir contra a lista (enter = pular): ').strip().lower()

    if email:
        print(f'  {email}: {"entra" if dominio_ok(email, cfg.dominio_permitido) else "NAO entra"}')




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
