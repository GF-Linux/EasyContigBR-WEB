'''
auth_re.py : Quem é o usuario, e se ele pode entrar.

Dois provedores atrás da mesma requisção 

#! 'dev' - digita o email e entra, Só para desenvolvimento local

#! 'google' - Outh do google, como pedido na reunião com Felipe, gov e sigaa descartados


'EASYCONTIG_DOMINIO=ufrrj.br, restringe a entrada ao dominio da UFRRJ, dessa forma permite
utilizar o login google, um serviço comum. O google prova quem é e o Dominio decide quenm entra

Oq ele faz ? 
1. manda para o google, que pede login e senha, e devolve um token
2. o token é enviado para o servidor, que pede para o google validar, e devolve o email do usuario
3. o email é verificado se é do dominio permitido, e se sim, o usuario é criado e logado, se não, o acesso é negado

#* Autor: Gustavo Gonçalves Freitas - LHV
#* Copyright (c) 2026 Gustavo Gonçalves Freitas. Todos os direitos reservados.

'''


from __future__ import annotations

import json
import os 
import re 
from dataclasses import dataclass

from socket import timeout
import sys
import urllib

from fastapi import HTTPException, Request

SESSAO_CHAVE = 'usuario'


@dataclass(frozen=True)
class Usuario:
    email: str
    nome: str = ''

    nome_google: str = ''  # Nome do google, que pode ser diferente do nome do usuario

    @property

    def dominio(self) -> str:
        """O domínio do email, que decide se o usuário pode entrar."""
        return self.email.rsplit('@', 1)[-1].lower() if '@' in self.email else ''



def modo() -> str:
    """Qual provedor de autenticação está ativo."""
    return os.environ.get('EASYCONTIG_AUTENTICACAO', 'dev').lower()


def google_configurado() -> bool:
    """Se o provedor do Google está configurado."""
    return bool(os.environ.get('GOOGLE_CLIENT_ID') and os.environ.get('GOOGLE_CLIENT_SECRET'))


#! Um ID de cliente google é '<números>.apps.googleusercontent.com'

FORMA_CLIENT_ID = re.compile(r"^\d+-[a-z0-9_]+\.apps\.googleusercontent\.com$") 
#? Aqui é verificado se o ID do cliente google é valido, caso não seja, o servidor não inicia


def queixa_do_client_id(valor: str | None = None) -> str:
    """Verifica se o ID do cliente google é valido, caso não seja, o servidor não inicia
    #?Um valor que não corresponde a cliente nenhum so se revela na página do google.
    """
    bruto = os.environ.get("GOOGLE_CLIENT_ID", "") if valor is None else valor

    if not bruto:
        return ''

    if bruto != bruto.strip():

        return ('GOOGLE_CLIENT_ID não pode ter espaços no começo ou quebra de linhas nas pontas')

    if bruto.strip('"\'') != bruto:
        return ('GOOGLE_CLIENT_ID não pode vir com aspas em volta; no `.env` o valor vai cru, sem aspas')


    if not FORMA_CLIENT_ID.match(bruto):
        return (f'GOOGLE_CLIENT_ID não tem a forma de um ID de cliente do Google'
                 f'(`<números>-<letras>.apps.googleusercontent.com`): {bruto!r}.'
                 'Se ainda tem `...` ou `<>` é o exemplo da receita, não a credencial')
    
    return ''

def dominio_ok(email: str, dominio_permitido: str) -> bool:
    ''' Sem dominio configurado qualquer um entra via login google
    O ENV define o 'EASYCONTIG_DOMINIO' 

    Em 2026-08-06, a comparação era do dominio inteiro, e a universidade Brasileira reparte.
    com 'ufrrj.br na lista, @pppcv.ufrrj.br e @lhv.ufrrj.br eram recusados. 

    Endereço Nomeado, item com '@' no meio vale por uma pessoa só, exata.
    O criador(eu) Precisa acessar o sistema mesmo que sem o dominio da UFRRJ para enventuais problemas
'''

    if not dominio_permitido:
        return True

    if '@' not in email:
        return False


    if '*' in {d.strip() for d in dominio_permitido.split(',') if d.strip()}:
        return True

    completo = email.strip().lower().rstrip('.')

    de = completo.rsplit('@', 1)[-1]

    for bruto in dominio_permitido.split(','):

        d = bruto.strip().lower().rstrip('.')

        if d.startswith('@'): #* "@ufrrj.br"

            d = d[1:] #? Aqui é para que o dominio seja comparado sem o '@' no começo, pois o email ja foi separado do dominio

            continue

        if not d:

            continue

        if '@' in d: #* "@pppcv.ufrrj.br"

            if completo == d:

                return True
        elif d.startswith('='): 

            if de == d[1:]:

                return True

        elif de == d or de.endswith('.' + d): #* "ufrrj.br"

            return True

    return False


AUTORIZA = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
PERFIL = "https://openidconnect.googleapis.com/v1/userinfo"
ESTADO_CHAVE = "oauth_estado"


def url_de_ida(request: Request, redirect_uri: str) -> str:
    """URL para onde o usuário deve ser enviado para autenticação.

    O `redirect_uri` é a URL do servidor que vai receber a resposta do Google.
    """
    import secrets
    import urllib.parse


    estado = secrets.token_urlsafe(24)
    request.session[ESTADO_CHAVE] = estado
    return AUTORIZA + "?" + urllib.parse.urlencode({
        "client_id": os.environ['GOOGLE_CLIENT_ID'],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": estado,
        #* Select_account evita o login silencioso em máquina compartilhada
        "prompt": "select_account",
    })


def usuario_da_volta(request: Request, code: str, estado: str, redirect_uri: str) -> Usuario:
    """Pega o email do usuário a partir do código que o Google devolveu.

    O `redirect_uri` é a URL do servidor que recebeu a resposta do Google.
    """
    import json
    import sys
    import urllib.error
    import urllib.parse
    import urllib.request

    esperado = request.session.pop(ESTADO_CHAVE, None)

    #* Sem isso um link forjado completaria o login da conta do usuario, mesmo que ele não tivesse clicado no link do Google. O estado é um segredo

    if not esperado or estado != esperado:

        raise HTTPException(status_code=400, detail="estado inválido, tente entrar de novo")


    corpo = urllib.parse.urlencode({
        "code": code,
        "client_id": os.environ['GOOGLE_CLIENT_ID'],
        "client_secret": os.environ['GOOGLE_CLIENT_SECRET'],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()


    def _erro_do_google(bruto: bytes) -> str:
        """Tenta extrair uma mensagem de erro do Google."""
        try:
            d = json.loads(bruto)
            return str(d.get("error") or '')[:600]
        except Exception:
            return ''

    try:

        with urllib.request.urlopen(urllib.request.Request(TOKEN, data=corpo), timeout=20) as r:
            token = json.loads(r.read())["access_token"]
        req = urllib.request.Request(PERFIL, headers={"Authorization": f"Bearer {token}"})

        with urllib.request.urlopen(req, timeout=20) as r:
            perfil = json.loads(r.read())


    except HTTPException:
        raise

    except urllib.error.HTTPError as e:
        corpo_erro = b''

        try:
    
            corpo_erro = e.read()

        except Exception:
            pass
        print(f"⚠️Google recusou a entrada (HTTP {e.code}): "
              f"{corpo_erro.decode('utf-8', 'replace')[:400]}",
              file=sys.stderr, flush=True) 
        codigo = _erro_do_google(corpo_erro)
        raise HTTPException(
            status_code=502,
            detail=("o Google recusou esta entrada"
                    + (f" ({codigo})" if codigo else "")
                    + " — é configuração do servidor, não adianta tentar de novo. "
                      "O motivo completo está no log."))

    except Exception as e:

        print(f"⚠️ Falha ao falar com o Google: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        raise HTTPException(
            status_code=502,
            detail=("falha ao falar com o Google - Tente de novo mais tarde"))


    email = (perfil.get("email") or '').strip().lower()

    #* aqui é verificado se o email do usuario é valido e se o email foi verificado pelo google, caso não seja, o acesso é negado 

    if not email or not perfil.get("email_verified"):
        raise HTTPException(
            status_code=403,
            detail="o Google não confirmou o email desta conta, não é possível entrar com ela")

    declarado = (perfil.get("name") or '').strip()
    return Usuario(email=email, nome=declarado or email.split('@')[0], nome_google=declarado)


def usuario_da_sessao(request: Request) -> Usuario | None:
    """Pega o usuário da sessão, se houver."""

    d = request.session.get(SESSAO_CHAVE)

    return Usuario(**d) if d else None

def entrar_na_sessao(request: Request, u: Usuario) -> None:
    """Coloca o usuário na sessão."""
    request.session[SESSAO_CHAVE] = {'email': u.email, 'nome': u.nome}

def sair_da_sessao(request: Request) -> None:
    """Tira o usuário da sessão."""
    request.session.pop(SESSAO_CHAVE, None)


def exigir_usuario(request: Request) -> Usuario:
    """Levanta 401 se não houver usuário na sessão."""
    u = usuario_da_sessao(request)
    if not u:
        raise HTTPException(status_code=401, detail="não autenticado")
    return u