"""
teste_servidor_web.py — a casca do servidor: o que os middlewares e os manipuladores de erro
garantem ANTES de qualquer rota rodar.

    POST --> origem confere? (403) --> Content-Length <= teto? (413) --> tem sessao? (401/303)
         --> corpo chunked lido ate o teto (413) --> rota
    GET  --> limitador 'leitura' (429 + Retry-After) --> rota --> no-store (menos /estatico)
    qualquer excecao --> pagina de 500 COM os cabecalhos de seguranca (ela nao passa por middleware)
    /saude --> 200/503 pelo codigo; o detalhe so para quem entrou

O QUE SE PROTEGE
  1. o teto de corpo age pelo cabecalho, antes de o corpo tocar o disco: medido em 08/08/2026,
     12.000.296 bytes subiam inteiros num POST de teto 2 MB e so entao voltava o 413.
  2. quem nao tem sessao nao escolhe quanta memoria o processo segura: /entrar paga um teto de
     64 KiB, o resto nem tem o corpo lido (401 com zero byte consumido).
  3. no modo google o POST /entrar devolve 403 ANTES de gastar o orcamento de login, que atras
     do nginx e compartilhado por todo anonimo.
  4. trabalho externo (tracy, blastn) tem no maximo max_pesadas vagas; sem vaga, 503 na hora.
  5. o /saude fala HTTP (503 quando doente), senao o monitor externo ve "no ar" com o tracy sumido.
  6. em producao a documentacao automatica da API (/api/docs, /openapi.json) nao e montada.
"""

import threading

import pytest
from fastapi import HTTPException

from conftest import EMAIL
from easycontig_web import configuracao


FORMULARIO = {'content-type': 'application/x-www-form-urlencoded'}

JSON = {'accept': 'application/json'}




def corpo_em_pedacos(vezes=64, tamanho=4096, contador=None):

    #* um corpo chunked: sem Content-Length, o servidor so descobre o tamanho lendo

    for _ in range(vezes):

        if contador is not None:
            contador['bytes'] += tamanho

        yield b'x' * tamanho




def teste_corpo_acima_do_teto_leva_413(montar):

    bancada = montar(MAX_CORPO='4096')

    resposta = bancada.entrar().post('/perfil', content=b'x' * 8192, headers=FORMULARIO)

    assert resposta.status_code == 413

    assert 'teto' in resposta.json()['detail']




def teste_corpo_dentro_do_teto_passa(montar):

    bancada = montar(MAX_CORPO='4096')

    assert bancada.entrar().post('/perfil', data={'nome': 'Gustavo'}).status_code == 303




def teste_o_teto_de_corpo_vem_antes_da_sessao(montar):

    #* sem sessao o middleware ja devolvia 401; o teto vem ANTES, senao um anonimo ainda decidiria quantos bytes o servidor recebe

    bancada = montar(MAX_CORPO='4096')

    resposta = bancada.cliente().post('/lotes', content=b'x' * 8192, headers={**FORMULARIO, **JSON})

    assert resposta.status_code == 413, f'veio {resposta.status_code}: o teto nao e o primeiro'




def teste_corpo_chunked_acima_do_teto_leva_413(montar):

    #* a porta sem cabecalho: um POST chunked nao declara Content-Length e o parser spoolava as partes em /tmp antes da rota

    bancada = montar(MAX_CORPO='4096')

    resposta = bancada.entrar().post('/perfil', content=corpo_em_pedacos(), headers={**FORMULARIO, **JSON})

    assert resposta.status_code == 413

    assert 'teto' in resposta.json()['detail']




def teste_corpo_chunked_sob_o_teto_chega_inteiro_a_rota(montar):

    #* ler o corpo no middleware para conferir nao pode PERDE-LO

    bancada = montar(MAX_CORPO='4096')

    cliente = bancada.entrar()

    assert cliente.post('/perfil', content=iter([b'nome=Gustavo']), headers=FORMULARIO).status_code == 303

    assert bancada.instalacao.perfis.pegar(EMAIL).nome == 'Gustavo'




def teste_chunked_sem_sessao_e_recusado_sem_ler_o_corpo(bancada):

    #* o 401 mora ANTES da leitura do corpo: zero byte consumido de quem nao tem sessao

    lido = {'bytes': 0}

    resposta = bancada.cliente().post('/lotes', content=corpo_em_pedacos(contador=lido), headers={'content-type': 'multipart/form-data; boundary=z', **JSON})

    assert resposta.status_code == 401

    assert lido['bytes'] == 0, f'leu {lido["bytes"]} bytes de quem nao tem sessao'




def teste_content_length_invalido_e_recusado(bancada):

    resposta = bancada.entrar().post('/perfil', content=b'abc', headers={'content-length': 'nao-e-numero', **FORMULARIO})

    assert resposta.status_code in (400, 413)




def teste_o_teto_padrao_acompanha_o_max_bytes(montar):

    #* um teto de corpo menor que o lote permitido recusaria envio legitimo; um fixo envelheceria calado quando MAX_BYTES mudasse

    bancada = montar(MAX_BYTES=str(50 * 1024 * 1024))

    assert bancada.instalacao.max_corpo > bancada.cfg.maximo_de_bytes




def teste_todo_post_que_nao_e_o_login_recusa_sem_sessao(bancada):

    #* lista de permitidos, nao de proibidos: rota POST nova nasce fechada, e e a lista do app que se percorre, nao uma copiada aqui

    from easycontig_web.servidor_web import POST_SEM_SESSAO  #! depois da bancada, pela mesma razao do conftest: importar o modulo cria o app do uvicorn na pasta apontada

    anonimo = bancada.cliente()

    caminhos = sorted(rota.path for rota in bancada.app.routes if 'POST' in (getattr(rota, 'methods', None) or set()))

    assert '/entrar' in caminhos and len(caminhos) >= 12  #* a lista tem de ser a de verdade, nao um app vazio

    for caminho in caminhos:

        resposta = anonimo.post(caminho.replace('{', 'x-').replace('}', ''), headers=JSON)

        assert resposta.status_code == (401 if caminho not in POST_SEM_SESSAO else 422), caminho  #* o /entrar sem e-mail e 422 (formulario invalido): passou da porta




def teste_o_teto_sem_sessao_e_muito_menor_que_o_de_envio(bancada):

    #* a relacao e o ponto: se um dia alguem igualar os dois, o buraco volta

    assert bancada.instalacao.max_corpo_sem_sessao < bancada.instalacao.max_corpo / 100




def teste_entrar_com_corpo_grande_leva_413(bancada):

    resposta = bancada.cliente().post('/entrar', content=b'x' * (128 * 1024), headers={**FORMULARIO, **JSON})

    assert resposta.status_code == 413

    assert 'teto' in resposta.json()['detail']




def teste_entrar_chunked_acima_do_teto_leva_413(bancada):

    resposta = bancada.cliente().post('/entrar', content=corpo_em_pedacos(), headers={**FORMULARIO, **JSON})

    assert resposta.status_code == 413




def teste_a_rota_com_sessao_mantem_o_teto_grande(bancada):

    #* o teto pequeno e SO de quem nao tem sessao; se vazasse para o envio, um laboratorio nao mandaria mais uma corrida

    resposta = bancada.entrar().post('/perfil', content=b'x' * (128 * 1024), headers=FORMULARIO)

    assert resposta.status_code != 413




def teste_o_403_do_modo_google_vem_antes_do_teto_de_login(montar):

    #* cada batida numa rota desligada nao pode queimar o orcamento de login, que atras do nginx e de todo anonimo junto

    bancada = montar(AUTH='google', LIM_LOGIN='5')

    cliente = bancada.cliente()

    vistos = {cliente.post('/entrar', data={'email': EMAIL}, headers=JSON).status_code for _ in range(12)}

    assert vistos == {403}, f'vieram {sorted(vistos)}: a rota desligada voltou a gastar o orcamento de login'




def teste_post_de_outra_origem_e_recusado(bancada):

    #* o cookie e SameSite=Lax, que e same-SITE: um subdominio mandaria o cookie junto; so o Origin separa

    resposta = bancada.entrar().post('/perfil', data={'nome': 'Invasor'}, headers={'origin': 'https://evento.ufrrj.br'})

    assert resposta.status_code == 403

    assert bancada.instalacao.perfis.pegar(EMAIL).nome != 'Invasor'




def teste_post_da_propria_origem_passa(bancada):

    resposta = bancada.entrar().post('/perfil', data={'nome': 'Gustavo'}, headers={'origin': 'http://testserver'})

    assert resposta.status_code == 303

    assert bancada.instalacao.perfis.pegar(EMAIL).nome == 'Gustavo'




def teste_post_sem_origin_passa(bancada):

    #* quem nao manda Origin e curl/script, que nao carrega cookie de terceiro: recusar quebraria os tres sem fechar nada

    assert bancada.entrar().post('/perfil', data={'nome': 'Script'}).status_code == 303




def teste_a_origem_e_conferida_antes_de_o_corpo_ser_lido(bancada):

    resposta = bancada.cliente().post('/entrar', data={'email': EMAIL}, headers={'origin': 'https://sitealheio.example'})

    assert resposta.status_code == 403




def teste_a_origem_vem_da_url_base_quando_ela_existe(montar):

    #* em producao o Host e escolhido pelo cliente; com URL_BASE escrita, e ela que manda

    bancada = montar(URL_BASE='https://easycontigbr.com.br')

    cliente = bancada.entrar()

    assert cliente.post('/perfil', data={'nome': 'x'}, headers={'origin': 'http://testserver'}).status_code == 403

    assert cliente.post('/perfil', data={'nome': 'x'}, headers={'origin': 'https://easycontigbr.com.br'}).status_code == 303




def teste_cookie_de_sessao_e_httponly_e_samesite(bancada):

    #* cookie legivel por JavaScript e sessao roubavel por XSS

    resposta = bancada.cliente().post('/entrar', data={'email': EMAIL})

    bruto = resposta.headers.get('set-cookie', '').lower().replace(' ', '')

    assert 'httponly' in bruto

    assert 'samesite=lax' in bruto




def teste_leitura_esbarra_no_teto(montar):

    #* medido em 06/08/2026: 200 requisicoes passavam em 1,4 s sem resistencia

    cliente = montar(LIM_LEITURA='5').entrar()

    codigos = [cliente.get('/saude').status_code for _ in range(12)]

    assert 429 in codigos

    assert codigos.count(429) >= 6




def teste_envio_esbarra_no_teto(montar, ab1):

    cliente = montar(LIM_ENVIO='2').entrar()

    arquivos = [('arquivos', ('amostra12_F_BTF2.ab1', ab1['amostra12_F_BTF2.ab1'], 'application/octet-stream'))]

    codigos = [cliente.post('/lotes', files=arquivos, data={'referencia': 'curado'}).status_code for _ in range(5)]

    assert 429 in codigos




def teste_login_repetido_e_barrado(montar):

    #* sem teto, tentar e-mail atras de e-mail e adivinhacao de graca

    cliente = montar(LIM_LOGIN='3').cliente()

    codigos = [cliente.post('/entrar', data={'email': f'x{i}@ufrrj.br'}).status_code for i in range(8)]

    assert 429 in codigos




def teste_teto_zero_desliga_a_regra(montar):

    cliente = montar(LIM_LEITURA='0').entrar()

    assert all(cliente.get('/saude').status_code != 429 for _ in range(30))




def teste_a_resposta_de_429_diz_quando_tentar_de_novo(montar):

    cliente = montar(LIM_LEITURA='2').cliente()

    for _ in range(5):

        resposta = cliente.get('/saude', headers=JSON)

        if resposta.status_code == 429:

            assert 'Retry-After' in resposta.headers

            assert 'tente de novo' in resposta.json()['detail']

            return

    pytest.fail('nao barrou')




def teste_o_teto_de_pesadas_sai_do_ambiente(montar):

    assert montar(MAX_PESADAS='2').instalacao.max_pesadas == 2




def teste_a_vaga_pesada_recusa_na_hora_quando_lota(montar):

    #* a recusa e NAO bloqueante de proposito: esperar por vaga seguraria a thread, que e o recurso que se quer devolver ao site

    instalacao = montar(MAX_PESADAS='2').instalacao

    with instalacao.vaga_pesada(), instalacao.vaga_pesada():

        with pytest.raises(HTTPException) as erro:

            with instalacao.vaga_pesada():
                pass

    assert erro.value.status_code == 503

    assert erro.value.headers.get('Retry-After') == '5'




def teste_a_vaga_volta_depois_de_usada(montar):

    #* sem o finally, uma excecao la dentro vazaria a vaga e o teto encolheria a cada erro ate o site parar

    instalacao = montar(MAX_PESADAS='2').instalacao

    for _ in range(10):

        with pytest.raises(RuntimeError):

            with instalacao.vaga_pesada():
                raise RuntimeError('o tracy explodiu')

    with instalacao.vaga_pesada():
        pass




def teste_o_traco_recusa_com_503_quando_o_teto_esta_cheio(montar, ab1):

    bancada = montar(MAX_PESADAS='1')

    cliente = bancada.entrar()

    lote_id = bancada.enviar_lote(cliente, [('amostra12_F_BTF2.ab1', ab1['amostra12_F_BTF2.ab1'])])

    bancada.concluir(lote_id)  #* a vaga so e pedida depois de dono e amostra conferidos: um lote sem relatorio daria 404 e mediria outra coisa

    with bancada.instalacao.vaga_pesada():
        resposta = cliente.get(f'/api/lotes/{lote_id}/amostras/amostra12/traco')

    assert resposta.status_code == 503

    assert resposta.headers.get('Retry-After') == '5'




def teste_o_teto_segura_a_concorrencia_de_verdade(montar):

    #* N threads batendo juntas: o pico de trabalho simultaneo, medido de dentro, nunca passa de max_pesadas

    instalacao = montar(MAX_PESADAS='2').instalacao

    estado = {'simultaneos': 0, 'pico': 0}

    trava, solta, recusas = threading.Lock(), threading.Event(), []

    def tentar():

        try:

            with instalacao.vaga_pesada():

                with trava:

                    estado['simultaneos'] += 1

                    estado['pico'] = max(estado['pico'], estado['simultaneos'])

                solta.wait(timeout=5)

                with trava:
                    estado['simultaneos'] -= 1

        except HTTPException as erro:
            recusas.append(erro.status_code)

    fios = [threading.Thread(target=tentar) for _ in range(12)]

    for fio in fios:
        fio.start()

    threading.Event().wait(0.3)  #* deixa as duas vagas se firmarem antes de conferir o pico

    assert estado['pico'] <= 2, f'{estado["pico"]} trabalhos pesados ao mesmo tempo'

    solta.set()

    for fio in fios:
        fio.join(timeout=5)

    assert recusas and all(codigo == 503 for codigo in recusas)

    assert len(recusas) == 10




def teste_saude_ruim_responde_503(bancada, monkeypatch):

    monkeypatch.setattr(configuracao, 'diagnostico', lambda cfg: [('tracy', False, 'não encontrado'), ('blastn', True, '/usr/bin/blastn')])

    resposta = bancada.cliente().get('/saude')

    assert resposta.status_code == 503, 'doente respondeu 200: o monitor nao veria nada'

    assert resposta.json() == {'ok': False}

    assert bancada.cliente().head('/saude').status_code == 503, 'o monitor fala HEAD: um 200 fixo no HEAD o deixaria cego por outro caminho'




def teste_saude_boa_responde_200_tambem_em_head(bancada, monkeypatch):

    monkeypatch.setattr(configuracao, 'diagnostico', lambda cfg: [('tracy', True, '/usr/local/bin/tracy')])

    cliente = bancada.cliente()

    assert cliente.get('/saude').status_code == 200 and cliente.get('/saude').json() == {'ok': True}

    assert cliente.head('/saude').status_code == 200




def teste_saude_com_sessao_leva_o_detalhe_e_a_fila(bancada, monkeypatch, ab1):

    monkeypatch.setattr(configuracao, 'diagnostico', lambda cfg: [('banco 16S', False, 'sem índice .nsq')])

    cliente = bancada.entrar()

    bancada.enviar_lote(cliente, [('amostra12_F_BTF2.ab1', ab1['amostra12_F_BTF2.ab1'])])

    resposta = cliente.get('/saude')

    assert resposta.status_code == 503

    assert resposta.json()['dependencias'] == [{'item': 'banco 16S', 'ok': False, 'detalhe': 'sem índice .nsq'}]

    assert resposta.json()['na_fila'] == 1




def teste_saude_nao_vaza_detalhe_para_quem_nao_entrou(bancada):

    assert set(bancada.cliente().get('/saude').json()) == {'ok'}




def teste_a_pagina_de_500_sai_com_os_cabecalhos_de_seguranca(bancada, monkeypatch):

    #* a pagina de 500 e servida FORA dos middlewares (ServerErrorMiddleware e o mais de fora): CSP, nosniff, noindex e no-store a mao
    #! a falha entra por uma dependencia da rota, nao pela casca: o proprio manipulador monta a casca para desenhar a pagina

    def explodir(*args, **kwargs):

        raise RuntimeError('disco pegou fogo')

    monkeypatch.setattr(bancada.instalacao.perfis, 'listar', explodir)

    resposta = bancada.entrar(tolerante=True).get('/labs', headers={'accept': 'text/html'})

    assert resposta.status_code == 500

    cabecalhos = resposta.headers

    assert 'noindex' in cabecalhos.get('x-robots-tag', '')

    assert cabecalhos.get('x-content-type-options') == 'nosniff'

    assert cabecalhos.get('x-frame-options') == 'DENY'

    assert "default-src 'none'" in cabecalhos.get('content-security-policy', '')

    assert 'no-store' in cabecalhos.get('cache-control', '')

    assert 'disco pegou fogo' not in resposta.text, 'a excecao vazou para a pagina'




def teste_quando_ate_a_pagina_de_erro_falha_sai_a_pagina_de_socorro_com_os_mesmos_cabecalhos(bancada, monkeypatch):

    #* a pagina de 500 monta a casca (lateral de quem entrou) para se desenhar. se a falha esta NA casca, a pagina de erro
    #* tambem cai: sobra a PAGINA_DE_SOCORRO, sem template, e ela sai com os mesmos cabecalhos e sem contar a excecao

    def explodir(*args, **kwargs):

        raise RuntimeError('perfil /dados/segredo corrompido')

    monkeypatch.setattr(bancada.instalacao.perfis, 'pegar', explodir)

    from easycontig_web.servidor_web import PAGINA_DE_SOCORRO  #! depois da bancada, pela mesma razao do conftest

    resposta = bancada.entrar(tolerante=True).get('/labs', headers={'accept': 'text/html'})

    assert resposta.status_code == 500 and resposta.text == PAGINA_DE_SOCORRO

    cabecalhos = resposta.headers

    assert 'noindex' in cabecalhos.get('x-robots-tag', '') and cabecalhos.get('x-content-type-options') == 'nosniff'

    assert cabecalhos.get('x-frame-options') == 'DENY' and "default-src 'none'" in cabecalhos.get('content-security-policy', '')

    assert 'no-store' in cabecalhos.get('cache-control', '')

    assert 'segredo' not in resposta.text and 'RuntimeError' not in resposta.text, 'a excecao vazou para a pagina'




def teste_o_500_em_json_nao_conta_a_excecao(bancada, monkeypatch):

    def explodir(*args, **kwargs):

        raise RuntimeError('caminho /dados/segredo')

    monkeypatch.setattr(bancada.instalacao.perfis, 'listar', explodir)

    resposta = bancada.entrar(tolerante=True).get('/labs', headers=JSON)

    assert resposta.status_code == 500

    assert resposta.json() == {'detail': 'erro interno do servidor'}




def teste_o_erro_http_no_navegador_vira_pagina_e_na_api_vira_json(bancada):

    cliente = bancada.entrar()

    pagina = cliente.get('/lotes/nao-existe', headers={'accept': 'text/html'})

    assert pagina.status_code == 404 and 'text/html' in pagina.headers['content-type']

    api = cliente.get('/lotes/nao-existe', headers=JSON)

    assert api.status_code == 404 and 'detail' in api.json()




def teste_toda_resposta_leva_csp_com_nonce(bancada):

    #* o script inline do base.html carrega o nonce; sem ele nenhum script executa

    resposta = bancada.entrar().get('/')

    csp = resposta.headers.get('content-security-policy', '')

    assert "'nonce-" in csp

    assert "'unsafe-inline'" not in csp.split('script-src')[1].split(';')[0]

    assert "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp, 'sem elas a pagina entra num iframe alheio ou tem a base das URLs trocada'




def teste_producao_recusa_subir_com_a_porta_aberta(montar):

    #* aviso no .env.exemplo nao e trava: quem sobe o servidor le o comando

    with pytest.raises(RuntimeError) as erro:
        montar(PRODUCAO='1', AUTH='dev', HTTPS_ONLY='0', SECRET_KEY='')

    assert 'não está pronta' in str(erro.value)




def teste_em_producao_a_documentacao_da_api_sai_do_ar(montar):

    #* /api/docs e /openapi.json listam toda rota e todo campo sem pedir sessao: util na maquina de quem desenvolve, mapa do servico na VPS

    de_desenvolvimento = montar().cliente()

    assert de_desenvolvimento.get('/openapi.json').status_code == 200 and de_desenvolvimento.get('/api/docs').status_code == 200

    de_producao = montar(PRODUCAO='1', AUTH='google', SECRET_KEY='x' * 32, HTTPS_ONLY='1', DOMINIO='ufrrj.br',
                         URL_BASE='https://easycontigbr.com.br', PROXIES_CONFIAVEIS='nenhum').cliente()

    assert de_producao.get('/openapi.json').status_code == 404 and de_producao.get('/api/docs').status_code == 404

    assert de_producao.get('/docs').status_code == 404 and de_producao.get('/redoc').status_code == 404  #* os caminhos padrao do FastAPI tambem nao existem




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
