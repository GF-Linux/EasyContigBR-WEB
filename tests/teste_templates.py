"""
teste_templates.py — a casca das paginas: cabecalhos, nonce, lateral, o Readme, o CSS de cada pagina e o rascunho do navegador.

    GET /qualquer  -->  cabecalhos_de_seguranca (nonce novo, CSP, nosniff, DENY, noindex, HSTS se https)
                            |
                            '--> sem_cache (no-store fora de /estatico/)
                                    |
                                    '--> base.html { lateral (Workspace, Perfil, Readme, Sair, Labs, Bancos, Pedidos) | <script nonce> }
                                              |-- leiame.html         o que o motor faz e o que ele nao faz
                                              |-- entrar.html         varre os rascunhos do navegador (tudo com ?saiu=1; so o vencido sem)
                                              '-- amostra.html  -->  oficina.js: class Rascunho (sessionStorage, 12 h)

O QUE SE PROTEGE
  1. todo script inline de todo template carrega o nonce da resposta, o nonce muda a cada resposta e e o mesmo do
     cabecalho: sem isso a CSP (sem 'unsafe-inline') cala o script em silencio.
  2. o Readme diz a coisa CERTA: o sentido (F/R) sai do DNA e nao do nome nem de metadado; a identidade da amostra sai
     do nome (poco da placa); a arvore e por marcador; o leque de ramos iguais nao e falha. Se alguem trocar o motor,
     a pagina passa a mentir para o laboratorio — por isso o texto e amarrado ao mecanismo.
  3. o CSS de uma pagina nao pinta a lateral: o bloco de estilo entra na MESMA folha da base.html, depois dela, e um
     seletor cuja ponta e um nome da lateral (`.acoes`, `.ac:hover`) repinta a caixa do usuario (medido em 06/08/2026
     na pagina da amostra: 195 px -> 123,6 px, com borda e sombra). O teste nao conhece os nomes: compara.
  4. o rascunho do cromatograma (sequencias NAO publicadas, de rota de dono) mora no sessionStorage, vence em 12 h e
     a leitura apaga o vencido; a tela de entrada varre o sessionStorage (tudo em ?saiu=1, so o vencido na visita
     comum, para nao destruir o rascunho vivo de outra aba) e o localStorage legado por inteiro.
  5. a caixa de erro so mostra o que o APP escreveu: o recado vai na sessao assinada, aparece uma vez e some; texto
     que veio na URL (`?erro=sua+sessao+expirou...`) nao e impresso em pagina nenhuma.
  6. cada link da lateral leva a uma rota que existe, /sair e POST, e o favicon esta la (404 em toda visita sujava o log).

O QUE MUDOU (em relacao aos testes originais)
  - test_leiame, test_css_nao_vaza_para_a_lateral, test_rascunho_nao_sobrevive_a_aba e os achados de casca
    (test_achados_verificados, test_achados_2026_08_11) viraram este arquivo; os achados de teto/proxy/saude ficaram
    em teste_servidor_web.py, o de /bancos?erro em teste_bancos.py e o do pedido sem motivo em teste_pedidos.py.
  - guardarRascunho/lerRascunho viraram a classe Rascunho (guardar/ler/apagar) e VALIDADE_MS virou
    VALIDADE_DO_RASCUNHO_MS; a inspecao do JS le o corpo da classe, sem os comentarios.
  - os testes de template compilam TODOS os .html e conferem TODOS os scripts inline de uma vez, em vez de uma pagina.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import EMAIL, RAIZ
from easycontig_web.web import comum
from easycontig_web.web.comum import TAMANHO_DO_RECADO, pegar_recado, por_recado


TEMPLATES = RAIZ / 'easycontig_web' / 'templates'

OFICINA = RAIZ / 'easycontig_web' / 'estatico' / 'oficina.js'

PAGINAS = sorted(caminho for caminho in TEMPLATES.glob('*.html') if not caminho.name.startswith('_') and caminho.name != 'base.html')

PREFIXO_DO_RASCUNHO = 'easycontig:rascunho:'

DOZE_HORAS = '12 * 60 * 60 * 1000'  #* como esta escrito nos dois lugares (oficina.js e entrar.html): o prazo tem de ser o mesmo

ISCA = 'sua sessao expirou, reenvie suas credenciais em exemplo-malicioso'

CABECALHOS = {'x-robots-tag': 'noindex, nofollow, noarchive', 'x-content-type-options': 'nosniff', 'x-frame-options': 'DENY', 'referrer-policy': 'same-origin'}




def texto_normalizado(caminho):

    #* minusculas e espaco unico: nem caixa nem quebra de linha do HTML derrubam uma verificacao de CONTEUDO

    return re.sub(r'\s+', ' ', caminho.read_text(encoding='utf-8')).lower()




def lateral():

    #* a marcacao da lateral, cortada do base.html

    partes = (TEMPLATES / 'base.html').read_text(encoding='utf-8').split('<aside class="lateral">', 1)

    assert len(partes) == 2, 'a lateral mudou de marcacao; este teste precisa acompanhar'

    return partes[1].split('</aside>', 1)[0]




def classes_da_lateral():

    nomes = set()

    for atributo in re.findall(r'class="([^"{}]*)"', lateral()):
        nomes.update(atributo.split())

    return nomes




def bloco_de_estilo(caminho):

    texto = caminho.read_text(encoding='utf-8')

    if '{% block estilo %}' not in texto:
        return ''

    return texto.split('{% block estilo %}', 1)[1].split('{% endblock %}', 1)[0]




def seletores(css):

    #* os seletores de cada regra, sem comentarios nem corpo

    css = re.sub(r'/\*.*?\*/', ' ', css, flags=re.S)

    encontrados = []

    for bloco in re.findall(r'([^{}]+)\{[^{}]*\}', css):

        for seletor in bloco.split(','):

            seletor = seletor.strip()

            if seletor and not seletor.startswith('@'):
                encontrados.append(seletor)

    return encontrados




def classe_da_ponta(seletor):

    #* a classe do compound MAIS A ESQUERDA, que e quem decide o alcance: `.oficina .ac` e seguro, `.ac:hover` nao

    ponta = re.split(r'[\s>+~]', seletor.strip())[0]

    achado = re.match(r'^\.([A-Za-z0-9_-]+)', ponta)

    return achado.group(1) if achado else None




def sem_comentarios(js):

    #* o JS sem os // e /* */: um comentario que cita o localStorage nao pode passar por uso dele

    js = re.sub(r'//[^\n]*', '', js)  #! os de linha PRIMEIRO: o prefixo `//*` dos comentarios da casa contem `/*`, e tirando os de bloco antes engoliria o codigo ate o proximo `*/`

    return re.sub(r'/\*.*?\*/', ' ', js, flags=re.S)




def classe_do_js(nome):

    #* o corpo de uma classe do oficina.js (ate a proxima classe ou o fim), sem comentarios

    js = OFICINA.read_text(encoding='utf-8')

    corpo = js.split(f'class {nome} ' + '{', 1)[1]

    return sem_comentarios(re.split(r'\n  class \w+ \{', corpo, 1)[0])




def metodo(corpo_da_classe, nome):

    #* o corpo de um metodo dentro da classe, ate o proximo metodo no mesmo nivel

    depois = corpo_da_classe.split(f'\n    {nome}(', 1)[1]

    return re.split(r'\n    \w+\(', depois, 1)[0]




def nonce_de(resposta):

    return re.search(r"'nonce-([^']+)'", resposta.headers['content-security-policy']).group(1)




def scripts_inline(html):

    #* as tags <script> sem src=: sao as que precisam do nonce

    return [tag for tag in re.findall(r'<script\b[^>]*>', html) if 'src=' not in tag]




@pytest.mark.parametrize('caminho', ['/entrar', '/', '/leiame', '/saude', '/estatico/favicon.svg', '/nao-existe'])
def teste_toda_resposta_leva_os_cabecalhos_de_seguranca(bancada, caminho):

    resposta = bancada.entrar().get(caminho)

    for nome, valor in CABECALHOS.items():
        assert resposta.headers.get(nome) == valor, f'{caminho}: {nome} = {resposta.headers.get(nome)!r}'

    assert "'nonce-" in resposta.headers.get('content-security-policy', '')

    assert 'strict-transport-security' not in resposta.headers, 'HSTS sem TLS prende o navegador num https que nao existe'




def teste_o_hsts_so_vai_quando_ha_tls(montar):

    resposta = montar(HTTPS_ONLY='1').cliente().get('/entrar')

    assert 'max-age=' in resposta.headers.get('strict-transport-security', '') and 'includeSubDomains' in resposta.headers['strict-transport-security']




def teste_o_nonce_muda_a_cada_resposta_e_e_o_mesmo_da_pagina(bancada):

    cliente = bancada.entrar()

    primeira, segunda = cliente.get('/'), cliente.get('/')

    assert nonce_de(primeira) != nonce_de(segunda), 'nonce fixo e o mesmo que unsafe-inline'

    for resposta in (primeira, segunda):

        nonce = nonce_de(resposta)

        inline = scripts_inline(resposta.text)

        assert inline and all(f'nonce="{nonce}"' in tag for tag in inline), f'script inline sem o nonce da resposta: {inline}'

        assert resposta.text.count(f'nonce="{nonce}"') == resposta.text.count('<script')




def teste_todo_script_inline_de_todo_template_carrega_o_nonce():

    #* conferido na FONTE: a pagina que a bancada nao renderiza (amostra, arvore...) tambem entra

    sem_nonce = {}

    for caminho in TEMPLATES.glob('*.html'):

        tags = re.findall(r'<script\b[^>]*>', caminho.read_text(encoding='utf-8'))

        faltando = [tag for tag in tags if 'nonce="{{ request.state.nonce }}"' not in tag]

        if faltando:
            sem_nonce[caminho.name] = faltando

    assert not sem_nonce, f'a CSP vai calar estes scripts em silencio: {sem_nonce}'




def teste_todo_template_compila():

    for caminho in TEMPLATES.glob('*.html'):
        comum.TEMPLATES.env.get_template(caminho.name)  #* um erro de sintaxe do Jinja so apareceria na primeira visita a pagina




def teste_as_paginas_nao_vao_para_o_cache_e_o_estatico_vai(bancada):

    cliente = bancada.entrar()

    assert cliente.get('/').headers['cache-control'] == 'no-store, must-revalidate', 'apagar uma corrida e apertar VOLTAR traria a pagina do cache'

    assert cliente.get('/entrar').headers['cache-control'] == 'no-store, must-revalidate'

    assert 'cache-control' not in cliente.get('/estatico/favicon.svg').headers, 'o JS e o icone se beneficiam do cache'




def teste_o_favicon_existe_e_a_pagina_o_declara(bancada):

    cliente = bancada.cliente()

    resposta = cliente.get('/estatico/favicon.svg')

    assert resposta.status_code == 200 and resposta.headers['content-type'].startswith('image/svg+xml')

    assert resposta.content.lstrip().startswith((b'<?xml', b'<svg'))

    assert 'rel="icon" href="/estatico/favicon.svg"' in cliente.get('/entrar').text




def teste_sem_sessao_a_casca_sai_sem_a_lateral(bancada):

    cliente = bancada.cliente()

    assert '<aside class="lateral">' not in cliente.get('/entrar').text

    assert '<aside class="lateral">' in bancada.entrar(cliente=cliente).get('/').text




def teste_cada_link_da_lateral_leva_a_uma_rota_que_existe(bancada):

    rotas = {}

    for rota in bancada.app.routes:
        rotas.setdefault(rota.path, set()).update(getattr(rota, 'methods', None) or set())  #* /perfil tem GET e POST em rotas separadas: um dicionario direto ficaria so com a ultima

    hrefs = [href for href in re.findall(r'href="([^"]+)"', lateral()) if '{{' not in href]

    assert {'/', '/perfil', '/leiame', '/labs', '/bancos', '/pedidos'} <= set(hrefs), f'a lateral perdeu um atalho: {hrefs}'

    for href in hrefs:
        assert href in rotas and 'GET' in rotas[href], f'a lateral aponta para {href}, que nao existe'

    assert re.search(r'<form method="post" action="/sair"', lateral()), 'sair como GET: um <img src="/sair"> em pagina alheia derrubava a sessao'

    assert 'POST' in rotas['/sair'] and 'GET' not in rotas['/sair']




def teste_a_lateral_marca_a_pagina_em_que_se_esta(bancada):

    cliente = bancada.entrar()

    for caminho, rotulo in (('/', 'Workspace'), ('/perfil', 'Perfil'), ('/leiame', 'Readme'), ('/labs', 'Labs')):

        html = cliente.get(caminho).text

        vivos = re.findall(r'class="ac[^"]*\bviva\b[^"]*"[^>]*>[^<]*?(Workspace|Perfil|Readme|Labs)</a>', html)

        assert vivos == [rotulo], f'{caminho}: o botao marcado como vivo e {vivos}, nao {rotulo}'




def teste_o_readme_exige_sessao(bancada):

    cliente = bancada.cliente()

    assert cliente.get('/leiame').status_code == 401  #* sem sessao e sem pedir HTML: e o 401 seco da API

    resposta = cliente.get('/leiame', headers={'accept': 'text/html'})

    assert resposta.status_code == 303 and resposta.headers['location'].startswith('/entrar')  #* o navegador vai para a entrada

    assert bancada.entrar(cliente=cliente).get('/leiame').status_code == 200




def teste_o_readme_explica_que_o_sentido_sai_do_dna_e_a_identidade_do_nome():

    #* a tese do produto: o motor (orientacao.py) compara as leituras pelo DNA; o nome so diz de que poco veio

    texto = texto_normalizado(TEMPLATES / 'leiame.html')

    assert 'reverso-complemento' in texto

    assert 'não usa o nome do arquivo' in texto and 'não usa metadado' in texto

    assert 'identidade da amostra' in texto and 'poço da placa' in texto, 'a metade que as pessoas esquecem: sentido e do DNA, identidade e do nome'




def teste_o_readme_explica_a_arvore_por_marcador_e_o_leque():

    #* a trava anti-Frankenstein e decisao cientifica: sem a explicacao, "nao montou tudo junto" parece limitacao do programa

    texto = texto_normalizado(TEMPLATES / 'leiame.html')

    assert 'árvore filogenética' in texto and 'por marcador' in texto and 'probabilidade posterior' in texto

    assert 'leque' in texto and 'não inventa' in texto, 'o resultado que mais parece defeito sem ser: 21 amostras saindo do mesmo ponto'




@pytest.mark.parametrize('pagina', PAGINAS, ids=lambda caminho: caminho.name)
def teste_a_pagina_nao_redefine_nome_que_a_lateral_usa(pagina):

    nomes = classes_da_lateral()

    culpados = [seletor for seletor in seletores(bloco_de_estilo(pagina)) if classe_da_ponta(seletor) in nomes]

    assert not culpados, (f'{pagina.name} redefine nomes que a lateral usa: {culpados}. O bloco de estilo da pagina entra na MESMA folha '
                          f'da base.html, depois dela — isto repinta a caixa do usuario. Prefixe com o conteiner da pagina.')




def teste_o_teste_do_css_pega_o_defeito_que_o_motivou():

    #* fianca: sem isto o teste acima poderia passar por nao achar nada — seletor nenhum, classe nenhuma

    assert {'acoes', 'ac'} <= classes_da_lateral(), 'a lateral deixou de usar estes nomes'

    assert classe_da_ponta('.acoes') == 'acoes' and classe_da_ponta('.ac:hover:not(:disabled)') == 'ac'

    assert classe_da_ponta('.oficina .ac') == 'oficina' and classe_da_ponta('.meio > .acoes') == 'meio'

    assert classe_da_ponta('#topo .ac') is None and classe_da_ponta('a.ac') is None

    assert seletores('/* .ac{x} */ .a, .b > .c { color: red } @media (x) { .d { y } }') == ['.a', '.b > .c', '.d']

    assert any(seletores(bloco_de_estilo(pagina)) for pagina in PAGINAS), 'nenhuma pagina tem estilo proprio: o teste nao esta lendo os blocos'




def teste_o_rascunho_mora_no_session_storage():

    #* sequencias nao publicadas: no localStorage ficavam gravadas no perfil do navegador para o proximo a sentar na estacao

    rascunho = classe_do_js('Rascunho')

    assert 'window.sessionStorage' in rascunho and 'localStorage' not in rascunho, 'o rascunho voltou ao localStorage'

    assert f"'{PREFIXO_DO_RASCUNHO}'" in rascunho, 'a chave mudou: a varredura da tela de entrada nao vai mais acha-la'

    assert 'localStorage' not in sem_comentarios(OFICINA.read_text(encoding='utf-8')), 'alguem gravou no localStorage fora da classe Rascunho'




def teste_a_leitura_do_rascunho_confere_o_prazo_e_apaga_o_vencido():

    ler = metodo(classe_do_js('Rascunho'), 'ler')

    assert 'VALIDADE_DO_RASCUNHO_MS' in ler, 'ler sem conferir prazo devolve a edicao da vespera numa aba esquecida aberta a noite inteira'

    assert 'removeItem' in ler, 'o rascunho vencido e ignorado mas continua guardado — tem de sair'

    assert 'quando' in metodo(classe_do_js('Rascunho'), 'guardar'), 'sem a data gravada nao ha prazo para conferir'




def teste_o_prazo_e_de_doze_horas_nos_dois_lugares(bancada):

    linhas = [linha for linha in OFICINA.read_text(encoding='utf-8').splitlines() if 'VALIDADE_DO_RASCUNHO_MS =' in linha]

    assert len(linhas) == 1 and DOZE_HORAS in linhas[0], f'o prazo do rascunho mudou: {linhas}'

    assert DOZE_HORAS in bancada.cliente().get('/entrar').text, 'a varredura da tela de entrada nao confere o mesmo prazo do oficina.js'




def teste_a_tela_de_entrada_varre_os_dois_armazenamentos(bancada):

    html = bancada.cliente().get('/entrar').text

    assert f'"{PREFIXO_DO_RASCUNHO}"' in html, 'a varredura procura outro prefixo: nao vai achar os rascunhos'

    assert 'varrer(window.sessionStorage, saiu)' in html, 'o sessionStorage e onde o rascunho mora'

    assert 'varrer(window.localStorage, true)' in html, 'o localStorage legado (de antes da mudanca) tem de ser recolhido por inteiro'

    assert 'get("saiu") === "1"' in html, 'a limpeza total deixou de ser condicional: o rascunho vivo de outra aba seria destruido por uma visita a entrada'




def teste_sair_leva_a_limpeza_total_e_a_decisao_e_do_navegador(bancada):

    cliente = bancada.entrar()

    resposta = cliente.post('/sair')

    assert resposta.status_code == 303 and resposta.headers['location'] == '/entrar?saiu=1'

    comum_ = re.sub(r'nonce="[^"]+"', '', cliente.get('/entrar').text)

    saiu = re.sub(r'nonce="[^"]+"', '', cliente.get('/entrar?saiu=1').text)

    assert comum_ == saiu, 'o servidor nao decide a limpeza: e o script, lendo ?saiu=1 no navegador — a pagina tem de ser a mesma'

    assert PREFIXO_DO_RASCUNHO in saiu and 'saiu' in saiu




def teste_o_recado_vai_na_sessao_aparece_uma_vez_e_some():

    pedido = SimpleNamespace(session={})

    por_recado(pedido, 'x' * (TAMANHO_DO_RECADO + 50))

    assert len(pedido.session[comum.RECADO]) == TAMANHO_DO_RECADO, 'um recado e uma frase, nao um log'

    assert pegar_recado(pedido) == 'x' * TAMANHO_DO_RECADO

    assert pegar_recado(pedido) == '' and pedido.session == {}, 'o recado grudou: reapareceria em todo recarregamento'

    por_recado(pedido, None)

    assert pegar_recado(pedido) == ''




def teste_o_recado_do_app_chega_a_tela_uma_vez_so(bancada):

    cliente = bancada.cliente()

    assert cliente.post('/entrar', data={'email': 'sem-arroba'}).status_code == 303

    assert 'e-mail inválido' in cliente.get('/entrar').text, 'o recado do app sumiu'

    assert 'e-mail inválido' not in cliente.get('/entrar').text, 'o recado grudou na tela'




@pytest.mark.parametrize('pagina', ['/entrar', '/', '/labs', '/perfil', '/pedidos', '/leiame'])
def teste_a_pagina_nao_imprime_texto_que_veio_na_url(bancada, pagina):

    #* o padrao antigo era /labs?erro=<texto> e {{ erro }} direto do parametro: qualquer link poe a frase de quem o montou na caixa de erro do site

    cliente = bancada.cliente() if pagina == '/entrar' else bancada.entrar()

    for parametro in ('erro', 'feito', 'recado', 'aviso'):

        resposta = cliente.get(f'{pagina}?{parametro}={ISCA.replace(" ", "+")}')

        assert resposta.status_code == 200 and ISCA not in resposta.text, f'{pagina}?{parametro}= imprimiu texto vindo da URL'




def teste_a_pagina_de_erro_so_diz_o_que_o_app_escreveu(bancada):

    cliente = bancada.entrar()

    resposta = cliente.get('/lotes/' + ISCA.replace(' ', '+'), headers={'accept': 'text/html'})

    assert resposta.status_code == 404 and ISCA not in resposta.text

    assert '404' in resposta.text and 'EasyContig' in resposta.text




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
