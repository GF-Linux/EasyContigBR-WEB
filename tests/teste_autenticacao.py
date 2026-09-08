"""
teste_autenticacao.py — quem entra: a regra do dominio, a porta de desenvolvimento, a ida e a
volta do Google, e o que a pagina publica de entrada NAO pode contar.

    dominio_ok(email, EASYCONTIG_DOMINIO) ---> "esta pessoa pode entrar" (o Google so prova QUEM e)
    POST /entrar (modo dev) ---> e-mail -> sessao -> 303   |   modo google -> 403
    GET /auth/google ---> state na sessao -> accounts.google.com
    GET /auth/google/volta ---> Google.usuario_da_volta (state, token, perfil) -> dominio_ok -> sessao
                           '-> ?error= vira frase DO SERVIDOR, nunca o texto recebido

O QUE SE PROTEGE
  1. universidade reparte o e-mail por unidade: `ufrrj.br` na lista deixa entrar `@ppgcv.ufrrj.br`,
     mas nao `@falso-ufrrj.br` (o ponto separa filho de sosia); `=dominio` prende no exato; `*` abre
     por escrito; `pessoa@x.com` libera UMA pessoa sem abrir o provedor dela.
  2. a pagina /entrar e publica: os e-mails nomeados da lista nao saem nela nem na recusa.
  3. recusa do Google e recusa (com o codigo publico dele), nao "nao respondeu; tente de novo".
  4. o `state` da volta e a trava de CSRF do login: forjado, 400 antes de qualquer conversa com o Google.
  5. /sair e POST: como GET, um <img src=/sair> em pagina alheia derrubava a sessao de quem a abrisse.
"""

import io
import urllib.error

import pytest
from fastapi import HTTPException

from conftest import EMAIL
from easycontig_web import configuracao
from easycontig_web.contas import autenticacao
from easycontig_web.contas.autenticacao import Google, Usuario, dominio_ok, dominios_publicos, mensagem_de_erro_oauth, queixa_do_client_id
from easycontig_web.web.rotas_de_entrada import destino_local


LISTA_COM_PESSOA = 'ufrrj.br, juaredbr@gmail.com'

FRASE_DO_ATACANTE = 'Sua sessao expirou. Reenvie suas credenciais em easycontig-ufrrj.com'

CLIENT_ID_BOM = '123456789012-ab1cd2ef3gh4ij5kl6mn7op8qr9st0uv.apps.googleusercontent.com'

VOLTA = 'http://testserver/auth/google/volta'




class GoogleDeMentira:

    #* o provedor sem rede: devolve o usuario que o teste mandar, ou levanta o que o teste mandar

    def __init__(self, usuario=None, erro=None):

        self.usuario = usuario
        self.erro = erro
        self.chamadas = []


    def url_de_ida(self, sessao, redirect_uri):

        sessao[autenticacao.ESTADO_CHAVE] = 'estado-de-mentira'

        return 'https://accounts.google.com/o/oauth2/v2/auth?state=estado-de-mentira'


    def usuario_da_volta(self, sessao, code, estado, redirect_uri):

        self.chamadas.append((code, estado, redirect_uri))

        if self.erro:
            raise self.erro

        return self.usuario




def volta_com_falha(monkeypatch, erro):

    #* roda usuario_da_volta com o urlopen trocado por `erro` e devolve a HTTPException que saiu

    def urlopen(*args, **kwargs):

        raise erro

    monkeypatch.setattr('urllib.request.urlopen', urlopen)

    with pytest.raises(HTTPException) as saida:
        Google('1-a.apps.googleusercontent.com', 'GOCSPX-x').usuario_da_volta({autenticacao.ESTADO_CHAVE: 's'}, 'cod', 's', VOLTA)

    return saida.value




def teste_sem_dominio_configurado_qualquer_conta_entra():

    #* o desenvolvimento local nao pode exigir configuracao; em producao conferir_producao() cobra a decisao

    assert dominio_ok('qualquer@gmail.com', '')




def teste_sem_arroba_nao_entra_com_dominio_na_lista():

    #* com a lista vazia a porta esta aberta e "gustavo" passa: quem barra o texto sem arroba na porta de dev e a propria rota

    assert not dominio_ok('gustavo', 'ufrrj.br')

    assert not dominio_ok('gustavo', 'ufrrj.br, ')




@pytest.mark.parametrize('email', ['gustavo@ufrrj.br', 'gustavo@ppgcv.ufrrj.br', 'gustavo@lhv.ufrrj.br', 'gustavo@a.b.ufrrj.br'])
def teste_subdominio_da_instituicao_entra(email):

    assert dominio_ok(email, 'ufrrj.br'), f'{email} recusado: e a unidade dentro da propria universidade'




@pytest.mark.parametrize('email', ['alguem@falso-ufrrj.br', 'alguem@ufrrj.br.com', 'alguem@naoufrrj.br', 'alguem@gmail.com'])
def teste_dominio_parecido_nao_entra(email):

    assert not dominio_ok(email, 'ufrrj.br')




def teste_o_ponto_e_o_que_separa_filho_de_sosia():

    assert dominio_ok('a@x.ufrrj.br', 'ufrrj.br')

    assert not dominio_ok('a@xufrrj.br', 'ufrrj.br')




def teste_lista_de_instituicoes():

    lista = 'ufrrj.br, usp.br ,unicamp.br'

    for bom in ('a@ufrrj.br', 'a@vet.usp.br', 'a@unicamp.br'):
        assert dominio_ok(bom, lista), bom

    assert not dominio_ok('a@ufmg.br', lista)




@pytest.mark.parametrize('escrito', ['@ufrrj.br', 'ufrrj.br.', ' UFRRJ.BR '])
def teste_a_forma_de_escrever_no_env_nao_muda_o_resultado(escrito):

    #* arroba na frente, ponto no fim e maiuscula sao erros de digitacao provaveis num .env; nenhum pode trancar o laboratorio fora

    assert dominio_ok('Gustavo.Freittas@UFRRJ.BR', escrito)




def teste_igual_na_frente_prende_no_dominio_exato():

    assert dominio_ok('a@ufrrj.br', '=ufrrj.br')

    assert not dominio_ok('a@lhv.ufrrj.br', '=ufrrj.br')




def teste_um_tld_na_lista_abre_o_pais_inteiro():

    #* nao e defeito, e consequencia: quem escrever `br` no .env precisa saber que liberou qualquer endereco brasileiro

    assert dominio_ok('alguem@empresa-privada.br', 'br')




def teste_asterisco_abre_por_escrito_mesmo_numa_lista():

    #* medido em 06/08/2026: a lista de usuarios de teste do Google nao restringe nada com escopos nao sensiveis; quem restringe e so o DOMINIO

    for email in ('a@gmail.com', 'b@empresa.com', 'c@ufrrj.br'):
        assert dominio_ok(email, '*'), email

    assert dominio_ok('a@gmail.com', 'ufrrj.br,*')




def teste_a_pessoa_nomeada_entra_sem_abrir_o_provedor_dela():

    assert dominio_ok('juaredbr@gmail.com', LISTA_COM_PESSOA)

    for outro in ('juaredpokemon@gmail.com', 'estranho@gmail.com', 'juaredbr@outro.com', 'juaredbr@mail.gmail.com'):
        assert not dominio_ok(outro, LISTA_COM_PESSOA), outro

    assert dominio_ok('jared@ufrrj.br', LISTA_COM_PESSOA) and dominio_ok('aluno@ppgcv.ufrrj.br', LISTA_COM_PESSOA)

    assert dominio_ok('  JuaredBR@Gmail.com ', ' ufrrj.br ,  JUAREDBR@GMAIL.COM ')




@pytest.mark.parametrize('entrada, esperado', [('ufrrj.br, juaredbr@gmail.com', 'ufrrj.br'), ('ufrrj.br, ana@lhv.ufrrj.br, uff.br', 'ufrrj.br, uff.br'),
                                               ('@ufrrj.br', '@ufrrj.br'), ('=ufrrj.br', '=ufrrj.br'), ('*', '*'), ('juaredbr@gmail.com', ''),
                                               ('a@x.com, b@y.com', ''), ('', ''), ('ufrrj.br ,  , juaredbr@gmail.com', 'ufrrj.br')])
def teste_dominios_publicos_tira_so_as_pessoas(entrada, esperado):

    #* arroba na FRENTE ainda e dominio (dominio_ok tira o @ inicial); arroba no MEIO e pessoa

    assert dominios_publicos(entrada) == esperado




def teste_a_regra_de_pessoa_e_a_mesma_do_dominio_ok():

    #* "some da vista publica" tem de significar "e pessoa nomeada" na gramatica de dominio_ok, e nada mais

    lista = 'ufrrj.br, @uff.br, =lhv.ufrrj.br, *, juaredbr@gmail.com, ana@x.br'

    publicos = set(dominios_publicos(lista).split(', '))

    for bruto in lista.split(','):

        item = bruto.strip()

        e_pessoa = '@' in (item[1:] if item.startswith('@') else item)

        assert (item not in publicos) == e_pessoa, f'{item!r}: visivel={item in publicos}, pessoa={e_pessoa}'




def teste_client_id_bem_formado_ou_ausente_nao_gera_queixa():

    assert queixa_do_client_id(CLIENT_ID_BOM) == ''

    assert queixa_do_client_id('') == ''




@pytest.mark.parametrize('valor, palavra', [('…apps.googleusercontent.com', 'exemplo da receita'), ('"123-abc.apps.googleusercontent.com"', 'aspas'),
                                            (' 123-abc.apps.googleusercontent.com\n', 'espaço'), ('meu-app-id', '')])
def teste_client_id_torto_e_apontado(valor, palavra):

    #* 06/08/2026: o Google respondeu `401 invalid_client` depois de a pessoa escolher a conta, e nada deste lado protestou antes

    queixa = queixa_do_client_id(valor)

    assert queixa and palavra in queixa




def teste_codigo_conhecido_do_oauth_vira_frase_do_servidor():

    assert mensagem_de_erro_oauth('access_denied') == 'entrada cancelada'

    assert 'organização' in mensagem_de_erro_oauth('admin_policy_enforced')




@pytest.mark.parametrize('entrada', [FRASE_DO_ATACANTE, '', '   ', 'ACCESS_DENIED', 'access_denied  '])
def teste_codigo_desconhecido_nunca_devolve_o_texto_recebido(entrada):

    saida = mensagem_de_erro_oauth(entrada)

    assert saida in ('entrada cancelada', 'não foi possível entrar pelo Google')

    assert 'credenciais' not in saida and 'easycontig-ufrrj.com' not in saida




def teste_recusa_do_google_nao_e_anunciada_como_silencio(monkeypatch):

    #* 06/08/2026: `401 invalid_client — client secret is invalid` chegava como "o Google nao respondeu; tente de novo"

    corpo = b'{"error":"invalid_client","error_description":"The provided client secret is invalid."}'

    erro = volta_com_falha(monkeypatch, urllib.error.HTTPError('u', 401, 'Unauthorized', {}, io.BytesIO(corpo)))

    assert erro.status_code == 502

    assert 'não respondeu' not in erro.detail

    assert 'invalid_client' in erro.detail and 'não adianta tentar de novo' in erro.detail

    assert 'client secret is invalid' not in erro.detail, 'a descricao do Google e para o log, nao para a tela'




def teste_o_codigo_do_google_chega_qualquer_que_seja(monkeypatch):

    erro = volta_com_falha(monkeypatch, urllib.error.HTTPError('u', 400, 'Bad Request', {}, io.BytesIO(b'{"error":"redirect_uri_mismatch"}')))

    assert 'redirect_uri_mismatch' in erro.detail




def teste_google_realmente_mudo_continua_dizendo_que_esta_mudo(monkeypatch):

    #* a frase antiga sobrevive onde ela e VERDADE: rede, DNS, tempo esgotado

    erro = volta_com_falha(monkeypatch, TimeoutError('tempo esgotado'))

    assert erro.status_code == 502 and 'não respondeu' in erro.detail and 'tente de novo' in erro.detail




def teste_resposta_ilegivel_nao_quebra_a_volta(monkeypatch):

    erro = volta_com_falha(monkeypatch, urllib.error.HTTPError('u', 500, 'erro', {}, io.BytesIO(b'<html>ops')))

    assert erro.status_code == 502 and 'recusou' in erro.detail




def teste_estado_forjado_e_400_antes_de_falar_com_o_google(monkeypatch):

    def urlopen(*args, **kwargs):

        raise AssertionError('falou com o Google com o state errado')

    monkeypatch.setattr('urllib.request.urlopen', urlopen)

    with pytest.raises(HTTPException) as erro:
        Google('id', 'segredo').usuario_da_volta({autenticacao.ESTADO_CHAVE: 'certo'}, 'cod', 'forjado', VOLTA)

    assert erro.value.status_code == 400




def teste_email_nao_confirmado_pelo_google_nao_entra(monkeypatch):

    #* conta sem e-mail confirmado nao prova dominio, e e o dominio que decide quem entra

    google = Google('id', 'segredo')

    monkeypatch.setattr(google, 'trocar_codigo', lambda code, redirect_uri: 'token')

    monkeypatch.setattr(google, 'perfil_do', lambda token: {'email': 'a@ufrrj.br', 'email_verified': False, 'name': 'A'})

    with pytest.raises(HTTPException) as erro:
        google.usuario_da_volta({autenticacao.ESTADO_CHAVE: 's'}, 'cod', 's', VOLTA)

    assert erro.value.status_code == 403




def teste_o_usuario_da_volta_sai_com_o_nome_do_google(monkeypatch):

    google = Google('id', 'segredo')

    monkeypatch.setattr(google, 'trocar_codigo', lambda code, redirect_uri: 'token')

    monkeypatch.setattr(google, 'perfil_do', lambda token: {'email': ' Gustavo@UFRRJ.br ', 'email_verified': True, 'name': 'Gustavo F.'})

    u = google.usuario_da_volta({autenticacao.ESTADO_CHAVE: 's'}, 'cod', 's', VOLTA)

    assert (u.email, u.nome, u.nome_google) == ('gustavo@ufrrj.br', 'Gustavo F.', 'Gustavo F.')

    assert 'segredo' not in repr(google)




def teste_a_url_de_ida_guarda_o_state_e_pede_a_escolha_da_conta():

    sessao = {}

    url = Google(CLIENT_ID_BOM, 'segredo').url_de_ida(sessao, VOLTA)

    assert url.startswith(autenticacao.AUTORIZA)

    assert sessao[autenticacao.ESTADO_CHAVE] in url

    assert 'prompt=select_account' in url and 'segredo' not in url




@pytest.mark.parametrize('fora', ['https://evil.example', '//evil.example', 'http://x', 'javascript:alert(1)', '/\\evil', '/\tevil.com', '/a\nb', ''])
def teste_destino_do_login_nao_leva_para_fora(fora):

    #* ?proximo= e entrada do usuario: sem filtro, a pagina de login despejaria a pessoa noutro dominio logo depois do e-mail

    assert destino_local(fora) == '/'




def teste_destino_local_fica():

    assert destino_local('/lotes/abc') == '/lotes/abc'




def teste_entrar_em_dev_abre_a_sessao_e_registra_o_perfil(bancada):

    cliente = bancada.cliente()

    resposta = cliente.post('/entrar', data={'email': '  Gustavo@UFRRJ.br ', 'proximo': '/lotes/abc'})

    assert resposta.status_code == 303 and resposta.headers['location'] == '/lotes/abc'

    assert cliente.get('/').status_code == 200

    assert bancada.instalacao.perfis.pegar('gustavo@ufrrj.br') is not None, 'o perfil nao foi criado na entrada'




def teste_email_invalido_volta_com_recado(bancada):

    cliente = bancada.cliente()

    resposta = cliente.post('/entrar', data={'email': 'sem-arroba'})

    assert resposta.status_code == 303 and resposta.headers['location'] == '/entrar'

    assert 'e-mail inválido' in cliente.get('/entrar').text

    assert cliente.get('/').status_code == 303, 'entrou mesmo assim'




def teste_login_de_dev_recusa_quando_o_modo_e_google(montar):

    #* a porta sem senha nao pode ficar aberta atras do provedor de verdade

    assert montar(AUTH='google').cliente().post('/entrar', data={'email': EMAIL}).status_code == 403




def teste_restricao_de_dominio_vale_na_porta_de_dev(montar):

    bancada = montar(DOMINIO='ufrrj.br')

    cliente = bancada.cliente()

    resposta = cliente.post('/entrar', data={'email': 'alguem@gmail.com'})

    assert resposta.status_code == 303 and resposta.headers['location'] == '/entrar'

    assert cliente.get('/').status_code == 303

    assert 'conta fora do domínio ufrrj.br' in cliente.get('/entrar').text

    assert bancada.entrar('aluno@ppgcv.ufrrj.br').get('/').status_code == 200




def teste_a_pagina_de_entrada_nao_mostra_endereco_nomeado(montar):

    #* achado F4 de 20/08/2026: a rota e publica e imprimia a lista crua, com o e-mail exato de cada pessoa autorizada

    cliente = montar(DOMINIO='ufrrj.br, jared@ufrrj.br, juaredbr@gmail.com').cliente()

    pagina = cliente.get('/entrar').text

    assert 'ufrrj.br' in pagina, 'o dominio institucional deve continuar orientando'

    assert 'juaredbr@gmail.com' not in pagina and 'jared@ufrrj.br' not in pagina

    recusa = cliente.post('/entrar', data={'email': 'intruso@gmail.com'}, follow_redirects=True).text

    assert 'juaredbr@gmail.com' not in recusa and 'jared@ufrrj.br' not in recusa




def teste_so_pessoas_na_lista_nao_vira_recado_com_dominio_em_branco(montar):

    cliente = montar(DOMINIO='juaredbr@gmail.com').cliente()

    recusa = cliente.post('/entrar', data={'email': 'x@y.com'}, follow_redirects=True).text

    assert 'juaredbr@gmail.com' not in recusa

    assert 'conta não autorizada' in recusa and 'conta fora do domínio ' not in recusa




def teste_sair_derruba_a_sessao_e_so_atende_em_post(bancada):

    cliente = bancada.entrar()

    assert cliente.get('/sair').status_code == 405

    assert cliente.get('/').status_code == 200, 'a sessao caiu por um GET'

    resposta = cliente.post('/sair')

    assert resposta.status_code == 303 and resposta.headers['location'] == '/entrar?saiu=1'  #* ?saiu=1: a pagina de entrada apaga os rascunhos da aba

    assert cliente.get('/').status_code == 303




def teste_sem_google_configurado_as_rotas_do_google_nao_existem(bancada):

    cliente = bancada.cliente()

    assert cliente.get('/auth/google').status_code == 404

    assert cliente.get('/auth/google/volta?code=x&state=y').status_code == 404




def teste_a_ida_ao_google_guarda_o_state_e_redireciona(montar):

    bancada = montar(AUTH='google', GOOGLE_CLIENT_ID=CLIENT_ID_BOM, GOOGLE_CLIENT_SECRET='GOCSPX-x')

    resposta = bancada.cliente().get('/auth/google')

    assert resposta.status_code == 303

    assert resposta.headers['location'].startswith(autenticacao.AUTORIZA)

    assert 'redirect_uri=http%3A%2F%2Ftestserver%2Fauth%2Fgoogle%2Fvolta' in resposta.headers['location']




def teste_a_volta_do_google_abre_a_sessao_e_registra_o_nome(montar, monkeypatch):

    bancada = montar(AUTH='google', GOOGLE_CLIENT_ID=CLIENT_ID_BOM, GOOGLE_CLIENT_SECRET='GOCSPX-x', DOMINIO='ufrrj.br', URL_BASE='https://easycontigbr.com.br')

    google = GoogleDeMentira(Usuario('gustavo@ufrrj.br', nome='Gustavo F.', nome_google='Gustavo F.'))

    monkeypatch.setattr(autenticacao, 'provedor_google', lambda: google)

    cliente = bancada.cliente()

    resposta = cliente.get('/auth/google/volta?code=cod&state=estado-de-mentira')

    assert resposta.status_code == 303 and resposta.headers['location'] == '/'

    assert google.chamadas == [('cod', 'estado-de-mentira', 'https://easycontigbr.com.br/auth/google/volta')]  #* a URL de volta sai da URL_BASE, nao do Host

    assert cliente.get('/').status_code == 200

    assert bancada.instalacao.perfis.pegar('gustavo@ufrrj.br').nome == 'Gustavo F.'




def teste_a_volta_do_google_fora_do_dominio_nao_entra(montar, monkeypatch):

    bancada = montar(AUTH='google', GOOGLE_CLIENT_ID=CLIENT_ID_BOM, GOOGLE_CLIENT_SECRET='GOCSPX-x', DOMINIO='ufrrj.br')

    monkeypatch.setattr(autenticacao, 'provedor_google', lambda: GoogleDeMentira(Usuario('alguem@gmail.com')))

    cliente = bancada.cliente()

    resposta = cliente.get('/auth/google/volta?code=cod&state=x')

    assert resposta.status_code == 303 and resposta.headers['location'] == '/entrar'

    assert cliente.get('/').status_code == 303

    assert 'conta fora do domínio ufrrj.br' in cliente.get('/entrar').text




def teste_a_recusa_do_google_chega_como_pagina_de_erro(montar, monkeypatch):

    bancada = montar(AUTH='google', GOOGLE_CLIENT_ID=CLIENT_ID_BOM, GOOGLE_CLIENT_SECRET='GOCSPX-x')

    monkeypatch.setattr(autenticacao, 'provedor_google', lambda: GoogleDeMentira(erro=HTTPException(502, 'o Google recusou esta entrada (invalid_client)')))

    resposta = bancada.cliente().get('/auth/google/volta?code=cod&state=x', headers={'accept': 'text/html'})

    assert resposta.status_code == 502 and 'invalid_client' in resposta.text




def teste_a_frase_do_atacante_nao_chega_a_pagina_de_login(montar):

    #* achado F3 de 20/08/2026: ?error= e escolhido por quem monta o link e era impresso na caixa de erro do site, com o certificado verdadeiro

    cliente = montar(AUTH='google', GOOGLE_CLIENT_ID=CLIENT_ID_BOM, GOOGLE_CLIENT_SECRET='GOCSPX-x').cliente()

    resposta = cliente.get(f'/auth/google/volta?error={FRASE_DO_ATACANTE}', follow_redirects=True)

    assert resposta.status_code == 200

    assert 'Reenvie suas credenciais' not in resposta.text and 'easycontig-ufrrj.com' not in resposta.text

    assert 'não foi possível entrar pelo Google' in resposta.text




def teste_cancelar_no_google_ainda_explica_o_que_houve(montar):

    cliente = montar(AUTH='google', GOOGLE_CLIENT_ID=CLIENT_ID_BOM, GOOGLE_CLIENT_SECRET='GOCSPX-x').cliente()

    assert 'entrada cancelada' in cliente.get('/auth/google/volta?error=access_denied', follow_redirects=True).text

    assert 'entrada cancelada' in cliente.get('/auth/google/volta', follow_redirects=True).text




def teste_producao_exige_a_porta_declarada(ambiente):

    #* tanto faz restrita ou aberta: o que se exige e que esteja ESCRITA

    for nome, valor in (('EASYCONTIG_AUTH', 'google'), ('EASYCONTIG_SECRET_KEY', 'x' * 32), ('EASYCONTIG_HTTPS_ONLY', '1'),
                        ('EASYCONTIG_URL_BASE', 'https://easycontigbr.com.br'), ('EASYCONTIG_PROXIES_CONFIAVEIS', 'nenhum')):
        ambiente.setenv(nome, valor)

    ambiente.setenv('EASYCONTIG_DOMINIO', '')

    assert any('EASYCONTIG_DOMINIO' in p for p in configuracao.conferir_producao())

    for valor in ('ufrrj.br', 'ufrrj.br,usp.br', '*'):

        ambiente.setenv('EASYCONTIG_DOMINIO', valor)

        assert configuracao.conferir_producao() == [], valor




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
