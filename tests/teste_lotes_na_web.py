"""
teste_lotes_na_web.py — o envio e a consulta de lotes pela web: quem ve o que, o que e recusado
e o que NAO acontece dentro da requisicao.

    POST /lotes ---> recusas (401, 400, 413) ---> lote RECEBENDO ---> NA_FILA (nada pesado no ciclo HTTP)
    GET /lotes/{id}... ---> so o dono (404 para os outros: 403 ja confirmaria que o lote existe)

O QUE SE PROTEGE
  1. sem sessao nada do lote responde; com sessao de outra conta, nenhuma rota do lote vaza.
  2. o envio e uma fronteira: so .ab1/.abi/.scf entram, o nome do arquivo vira so o nome (sem
     caminho), o teto de arquivos vale, e toda corrida declara contra o que identifica.
  3. o que a pessoa digita (nome da corrida) nao vira HTML nem SQL.
  4. pagina com sessao nao vai para o cache do navegador; o estatico continua cacheavel.

Os .ab1 sao fabricados (amostras_sinteticas.py): aqui se exercita o caminho do arquivo, nao a biologia.
"""

import re

import pytest

from conftest import EMAIL, OUTRO_EMAIL
from easycontig_web.processamento.fila_de_lotes import NA_FILA, PRONTO


ROTAS_DO_LOTE = ['/lotes/{id}', '/lotes/{id}?tela_do_lote=1', '/api/lotes/{id}', '/lotes/{id}/relatorio', '/lotes/{id}/relatorio.json',
                 '/lotes/{id}/resultado.csv', '/lotes/{id}/amostras/amostra12', '/api/lotes/{id}/amostras/amostra12/traco',
                 '/api/lotes/{id}/amostras/amostra12/consultar?banco=curado', '/lotes/{id}/arvore', '/api/lotes/{id}/arvore',
                 '/lotes/{id}/arvore/18S/arvore.png']  #! enumeradas de proposito: rota nova que esqueca o dono falha aqui, nao no incidente

MALICIOSOS = ['../etc/passwd', '..%2f..%2fetc', '....//etc', '/etc/passwd', '..\\..\\windows', '%2e%2e%2f', 'a/../../b']

HOSTIS = ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>', "'; DROP TABLE lotes;--", '{{ 7*7 }}', 'javascript:alert(1)']

JSON = {'accept': 'application/json'}




def um_par(ab1):

    return [(nome, ab1[nome]) for nome in ('amostra12_F_BTF2.ab1', 'amostra12_R_BTR2.ab1')]




def uma_leitura(ab1):

    return [('amostra12_F_BTF2.ab1', ab1['amostra12_F_BTF2.ab1'])]




def teste_sem_login_a_raiz_manda_para_entrar(bancada):

    resposta = bancada.cliente().get('/')

    assert resposta.status_code == 303 and resposta.headers['location'] == '/entrar'




def teste_sem_login_nao_se_cria_lote(bancada, ab1):

    resposta = bancada.cliente().post('/lotes', files=[('arquivos', ('a.ab1', ab1['amostra12_F_BTF2.ab1'], 'application/octet-stream'))], data={'referencia': 'curado'})

    assert resposta.status_code == 401




def teste_lote_criado_fica_na_fila_e_nao_processado(bancada, ab1):

    #* a requisicao termina com o lote PRONTO PARA a fila: nada pesado acontece dentro do ciclo HTTP

    cliente = bancada.entrar()

    lote_id = bancada.enviar_lote(cliente, um_par(ab1))

    lote = bancada.lote(lote_id)

    assert lote.status == NA_FILA

    assert lote.n_arquivos == 2

    assert lote.dono == EMAIL




def teste_a_referencia_escolhida_fica_gravada_no_lote(bancada, ab1):

    #* sem isto o relatorio nao sabe dizer contra o que comparou

    lote_id = bancada.enviar_lote(bancada.entrar(), uma_leitura(ab1))

    assert bancada.lote(lote_id).referencia == 'curado'




def teste_sem_referencia_nao_processa(bancada, ab1):

    #* montar consenso sem ter contra o que comparar produz um "nao achei" que nao significa nada

    resposta = bancada.entrar().post('/lotes', files=[('arquivos', ('a.ab1', ab1['amostra12_F_BTF2.ab1'], 'application/octet-stream'))])

    assert resposta.status_code == 400

    assert 'referência' in resposta.json()['detail']




@pytest.mark.parametrize('referencia', ['../etc/passwd', 'inventada', 'cestoda_coi'])
def teste_referencia_inventada_ou_nao_montada_e_recusada(bancada, ab1, referencia):

    #* cestoda_coi existe no catalogo mas ninguem montou: oferecer seria oferecer um erro

    resposta = bancada.entrar().post('/lotes', files=[('arquivos', ('a.ab1', ab1['amostra12_F_BTF2.ab1'], 'application/octet-stream'))], data={'referencia': referencia})

    assert resposta.status_code == 400

    if referencia == 'cestoda_coi':
        assert 'montada' in resposta.json()['detail']




def teste_arquivo_que_nao_e_trace_e_recusado(bancada):

    resposta = bancada.entrar().post('/lotes', files=[('arquivos', ('notas.txt', b'nada', 'text/plain'))], data={'referencia': 'curado'})

    assert resposta.status_code == 400




def teste_extensao_estranha_e_ignorada_mas_o_ab1_passa(bancada, ab1):

    lote_id = bancada.enviar_lote(bancada.entrar(), um_par(ab1) + [('leia-me.txt', b'nada')])

    assert bancada.lote(lote_id).n_arquivos == 2




def teste_arquivo_vazio_com_extensao_certa_nao_derruba_o_servidor(bancada):

    #* um .ab1 de 0 byte e aceito no upload e falha na montagem; o que nao pode e a requisicao estourar

    lote_id = bancada.enviar_lote(bancada.entrar(), [('vazio.ab1', b'')])

    assert bancada.lote(lote_id).n_arquivos == 1




def teste_teto_de_arquivos_por_lote(montar, ab1):

    bancada = montar(MAX_ARQUIVOS='1')

    resposta = bancada.entrar().post('/lotes', files=[('arquivos', (nome, conteudo, 'application/octet-stream')) for nome, conteudo in um_par(ab1)], data={'referencia': 'curado'})

    assert resposta.status_code == 413




def teste_nome_com_travessia_de_caminho_vira_so_o_nome(bancada, ab1):

    #* o navegador manda 'pasta/sub/arquivo.ab1' quando se escolhe uma pasta; um nome com '..' escreveria fora do lote

    conteudo = ab1['amostra12_F_BTF2.ab1']

    lote_id = bancada.enviar_lote(bancada.entrar(), [('../../../fora.ab1', conteudo), ('sub/dir/amostra12_R_BTR2.ab1', conteudo)])

    entrada = bancada.cfg.pastas_do_lote(lote_id).entrada

    assert sorted(caminho.name for caminho in entrada.iterdir()) == ['amostra12_R_BTR2.ab1', 'fora.ab1']

    assert not (bancada.cfg.pasta_de_lotes / 'fora.ab1').exists()

    assert not (bancada.pasta / 'fora.ab1').exists()

    assert not (bancada.pasta.parent / 'fora.ab1').exists()




def teste_nenhuma_rota_do_lote_vaza_para_outra_conta(bancada, ab1):

    #* 404 e nao 403: "proibido" ja confirmaria que o lote existe, e o id vai em link que se cola no grupo errado

    dono, intruso = bancada.entrar(EMAIL), bancada.entrar(OUTRO_EMAIL)

    lote_id = bancada.enviar_lote(dono, uma_leitura(ab1))

    for rota in ROTAS_DO_LOTE:
        assert intruso.get(rota.format(id=lote_id)).status_code == 404, rota

    assert intruso.post(f'/lotes/{lote_id}/apagar').status_code == 404

    assert intruso.post(f'/lotes/{lote_id}/arvore').status_code == 404

    assert bancada.lote(lote_id) is not None




def teste_sem_sessao_nada_do_lote_responde(bancada, ab1):

    lote_id = bancada.enviar_lote(bancada.entrar(), uma_leitura(ab1))

    anonimo = bancada.cliente()

    for rota in ROTAS_DO_LOTE:
        assert anonimo.get(rota.format(id=lote_id), headers=JSON).status_code == 401, rota

    assert anonimo.post(f'/lotes/{lote_id}/apagar', headers=JSON).status_code == 401




def teste_sem_sessao_quem_pede_html_e_levado_ao_login_com_a_volta(bancada):

    resposta = bancada.cliente().get('/lotes/abc', headers={'accept': 'text/html'})

    assert resposta.status_code == 303

    assert resposta.headers['location'] == '/entrar?proximo=/lotes/abc'




def teste_resultado_so_sai_com_o_lote_pronto(bancada, ab1):

    cliente = bancada.entrar()

    lote_id = bancada.enviar_lote(cliente, uma_leitura(ab1))

    assert cliente.get(f'/lotes/{lote_id}/relatorio').status_code == 409

    assert cliente.get(f'/lotes/{lote_id}/resultado.csv').status_code == 409

    assert cliente.get(f'/lotes/{lote_id}/relatorio.json').status_code == 409




def teste_o_lote_pronto_entrega_pagina_json_e_csv(bancada, ab1):

    cliente = bancada.entrar()

    lote_id = bancada.enviar_lote(cliente, um_par(ab1), nome='corrida pronta')

    bancada.concluir(lote_id)

    assert bancada.lote(lote_id).status == PRONTO

    atalho = cliente.get(f'/lotes/{lote_id}')  #* lote com UMA amostra abre direto nela; ?tela_do_lote=1 e o caminho de volta para a tela do lote

    assert atalho.status_code == 303 and atalho.headers['location'] == f'/lotes/{lote_id}/amostras/amostra12'

    pagina = cliente.get(f'/lotes/{lote_id}?tela_do_lote=1')

    assert pagina.status_code == 200 and 'Babesia vogeli' in pagina.text

    relatorio = cliente.get(f'/lotes/{lote_id}/relatorio')

    assert relatorio.status_code == 200 and '<title>EasyContig BR — corrida pronta</title>' in relatorio.text

    dados = cliente.get(f'/lotes/{lote_id}/relatorio.json').json()

    assert [amostra['chave'] for amostra in dados['amostras']] == ['amostra12']

    csv = cliente.get(f'/lotes/{lote_id}/resultado.csv')

    assert csv.status_code == 200 and 'amostra12' in csv.text and 'Babesia vogeli' in csv.text

    amostra = cliente.get(f'/lotes/{lote_id}/amostras/amostra12')

    assert amostra.status_code == 200 and 'KY290979.1' in amostra.text

    assert cliente.get(f'/lotes/{lote_id}/amostras/nao-existe').status_code == 404




def teste_a_pagina_do_lote_na_fila_abre_e_diz_a_situacao(bancada, ab1):

    cliente = bancada.entrar()

    lote_id = bancada.enviar_lote(cliente, um_par(ab1), nome='minha corrida')

    resposta = cliente.get(f'/lotes/{lote_id}')

    assert resposta.status_code == 200

    assert 'minha corrida' in resposta.text

    api = cliente.get(f'/api/lotes/{lote_id}').json()

    assert api['status'] == NA_FILA and api['n_arquivos'] == 2




def teste_apagar_o_lote_tira_a_pasta_e_a_linha(bancada, ab1):

    cliente = bancada.entrar()

    lote_id = bancada.enviar_lote(cliente, uma_leitura(ab1))

    pasta = bancada.cfg.pastas_do_lote(lote_id).raiz

    assert pasta.exists()

    resposta = cliente.post(f'/lotes/{lote_id}/apagar')

    assert resposta.status_code == 303 and resposta.headers['location'] == '/'

    assert bancada.lote(lote_id) is None

    assert not pasta.exists()




@pytest.mark.parametrize('mau', MALICIOSOS)
def teste_id_de_lote_malicioso_nao_alcanca_o_disco(bancada, mau):

    #* o que importa nao e o codigo e sim NAO entregar um lote: alguns o servidor normaliza para a raiz, que e a pagina de envio (seguindo o redirect)

    resposta = bancada.entrar().get(f'/lotes/{mau}', follow_redirects=True)

    assert resposta.status_code in (400, 404) or 'Adicionar arquivos' in resposta.text, (mau, resposta.status_code)




@pytest.mark.parametrize('mau', HOSTIS)
def teste_nome_da_corrida_nao_vira_html_nem_sql(bancada, ab1, mau):

    cliente = bancada.entrar()

    bancada.enviar_lote(cliente, uma_leitura(ab1), nome=mau)

    corpo = cliente.get('/').text

    assert '<script>alert' not in corpo

    assert '<img src=x' not in corpo and '<img src="x"' not in corpo

    nomes = re.findall(r'<span class="nome">(.*?)</span>', corpo, re.S)  #! o lugar onde o nome e impresso, e so ele: o SVG da marca tem "49" nas coordenadas

    assert nomes, 'o nome da corrida nao foi impresso: o teste esta olhando o lugar errado'

    if mau == '{{ 7*7 }}':

        assert '7*7' in ''.join(nomes), 'o payload nao chegou ao elemento'

        assert '49' not in ''.join(nomes), 'o Jinja reavaliou o nome da corrida'

    assert isinstance(bancada.instalacao.lotes.listar(), list)  #* a tabela continua de pe depois do payload de SQL




def teste_pagina_com_sessao_nao_vai_para_o_cache(bancada):

    #* sem isto, apagar uma corrida e apertar VOLTAR trazia a pagina dela do cache, com o botao de apagar e tudo

    resposta = bancada.entrar().get('/')

    assert 'no-store' in resposta.headers.get('cache-control', '')

    assert resposta.headers.get('pragma') == 'no-cache'




def teste_estatico_continua_cacheavel(bancada):

    resposta = bancada.cliente().get('/estatico/oficina.js')

    assert resposta.status_code == 200

    assert 'no-store' not in resposta.headers.get('cache-control', '')




def teste_a_lista_da_lateral_mostra_so_as_corridas_da_conta(bancada, ab1):

    dono, outro = bancada.entrar(EMAIL), bancada.entrar(OUTRO_EMAIL)

    bancada.enviar_lote(dono, uma_leitura(ab1), nome='corrida-do-dono')

    bancada.enviar_lote(outro, uma_leitura(ab1), nome='corrida-do-outro')

    assert 'corrida-do-dono' in dono.get('/').text and 'corrida-do-outro' not in dono.get('/').text

    assert 'corrida-do-outro' in outro.get('/').text and 'corrida-do-dono' not in outro.get('/').text




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
