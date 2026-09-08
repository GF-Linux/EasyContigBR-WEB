"""
teste_oficina.py — o oficina.js DE VERDADE num Chromium: a bancada de correcao do consenso, gesto a gesto.

    amostra.html                                          oficina.js
    .oficina[data-traco] #aln #cromatogramas #acoes ...      main() -> new Oficina(raiz).abrir()
                                                                   |
                                                         fetch(data-traco) --> receber(dados)   ou   semTraco(motivo)
                                                                   |
                                                +------------------+------------------+
                                                |                  |                  |
                                             Rascunho           Arrasto            botoes: remover / desfazer /
                                         (sessionStorage)     (uma por tela)       proximo erro / amplitude /
                                                                                   exportar / consultar

O QUE SE PROTEGE
  1. o arrasto: puxar para a direita traz o que esta a esquerda (o defeito de 2026-08-06, quando o gesto virava
     clique e a janela ia contra a mao), nao marca a base onde a mao soltou, para nas pontas do contig, e o
     clique curto (abaixo de ARRASTO_MIN) continua marcando.
  2. a abertura: a tela abre na primeira discordancia DENTRO da sobreposicao das leituras, com a marca nas duas.
  3. a edicao: remover vira gap e o consenso segue quem sobra; desfazer volta um passo; a pilha guarda 50 passos.
  4. o rascunho: cada remocao vai para o sessionStorage (nunca o localStorage), volta ao recarregar com o aviso de
     quando foi, e e ignorado quando venceu (12 h), nao tem data, nao casa mais com o traco ou nem e JSON.
  5. a exportacao: FASTA com o cabecalho da amostra e a nota das bases removidas, nome do arquivo pela chave da
     URL; exportar apaga o rascunho e libera o beforeunload.
  6. tudo que vem do servidor passa por escapar(): nome da leitura, motivo do indisponivel, titulo e acesso do acerto.
  7. a consulta pede o banco escolhido, mostra os acertos com virgula decimal, diz quando nao ha acerto e quando nao
     deu para consultar — e o botao volta em todos os casos.
  8. a amplitude e lente, nao dado: muda os pixels do traco e nada mais; Ctrl+roda tambem; sem para onde ir o botao
     desabilita.
  9. amostra.html entrega todos os ids que o oficina.js pede — a pagina real e o arnes daqui falam a mesma lingua.

O QUE MUDOU (em relacao aos testes originais)
  - test_oficina_arrasto.py (os 8 testes do arrasto) esta aqui inteiro; o resto do oficina.js nao tinha teste.
  - o arnes serve a pagina numa URL com a forma da real (/lotes/{id}/amostras/{chave}), porque a chave do rascunho e
    o location.pathname e o nome do arquivo exportado sai do data-traco.
  - o fetch e trocado por um duble dentro da pagina: o traco e sintetico (o que esta em teste e a aritmetica da
    janela e a edicao, nao o alinhamento) e o download e capturado sem tocar no disco.

Pula sozinho quando o Playwright ou o Chromium nao estao instalados: a suite nao depende de navegador.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from conftest import RAIZ

sync_playwright = pytest.importorskip('playwright.sync_api', reason='Playwright não instalado').sync_playwright


OFICINA = RAIZ / 'easycontig_web' / 'estatico' / 'oficina.js'

AMOSTRA_HTML = RAIZ / 'easycontig_web' / 'templates' / 'amostra.html'

LARGURA = 200  #* colunas do contig sintetico

JAN = 74  #* a janela do oficina.js; se mudar la, muda aqui

MEIA = JAN // 2

COL_MM = 100  #* a unica discordancia do par simples: e nela que a tela abre

TETO_DA_PILHA = 50  #* passos de desfazer que o oficina.js guarda

NOME_F = 'amostra_F <b>x</b> "q" &amp;.ab1'  #* nome vindo do servidor com as tres armadilhas: tag, aspas (entra em atributo) e entidade

CHAVE = 'amostra 12'  #* com espaco de proposito: a URL leva %20 e o arquivo exportado leva o espaco

PAGINA = 'http://arnes.local/lotes/L1/amostras/amostra%2012'

TRACO = '/api/lotes/L1/amostras/amostra%2012/traco'

CHAVE_DO_RASCUNHO = 'easycontig:rascunho:/lotes/L1/amostras/amostra%2012'

ESPERA = 60  #* ms depois de um gesto: o render e por requestAnimationFrame

ISCA = '<img src=x onerror=alert(1)>&lt;b&gt;'  #* o `&lt;` literal pega quem escapa < mas esquece o &: sem escapar o & ele vira um <b> de texto

ACERTOS = [
    {'titulo': f'Hepatozoon canis {ISCA} isolate', 'acesso': 'KX123 <i>', 'identidade': 99.6, 'cobertura': 100.0},
    {'titulo': 'Hepatozoon "sp"', 'acesso': 'MN1', 'identidade': 98, 'cobertura': 97.25},
]

ARNES = f'''
<style>
  .oficina{{ width:100% }}
  #aln{{ font-family:monospace; font-size:12px }}
  .aln-l{{ display:flex }} .aln-n{{ width:120px }} .b{{ display:inline-block; width:9px }}
  .tela-tr{{ position:relative; width:80% }}
  .tela-tr canvas.tr-cv{{ display:block; width:100%; height:112px }}
  .bases-lin{{ position:relative; height:19px }}
  .bases-lin .bl{{ position:absolute }}
</style>
<div class="oficina" data-traco="{TRACO}">
  <div id="aln"></div>
  <div id="cromatogramas"></div>
  <div id="acoes" style="display:none">
    <button id="b-erro" type="button"></button>
    <span id="botoes-remover"></span>
    <button id="b-undo" type="button" disabled></button>
    <button id="b-amp-menos" type="button"></button><b id="b-amp-valor"></b>
    <button id="b-amp-mais" type="button"></button>
  </div>
  <span id="m-pb"></span><span id="m-mm"></span><span id="q-ed"></span>
  <div id="rascunho" style="display:none"></div>
  <div id="rascunho-antigo" style="display:none">rascunho de <time></time></div>
  <button id="b-fasta" type="button"></button><button id="b-txt" type="button"></button>
  <select id="banco-consulta"><option value="adeleorina">Adeleorina</option><option value="meu:x y" selected>meu</option></select>
  <button id="b-consultar" type="button">Consultar</button>
  <div id="saida-consulta"></div>
</div>
'''

PREPARO = '''
([traco, consulta]) => {
  window.__fetches = [];
  window.__blobs = [];
  window.__baixados = [];
  window.__traco = traco;
  window.__consulta = consulta;
  window.fetch = (url, opcoes) => {
    window.__fetches.push([String(url), (opcoes && opcoes.headers && opcoes.headers.accept) || '']);
    const dados = String(url).includes('/consultar') ? window.__consulta : window.__traco;
    if (dados === 'falha') return Promise.resolve({ ok: false, status: 500, json: () => Promise.reject(new Error('x')) });
    if (dados === 'rede') return Promise.reject(new TypeError('rede'));
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(JSON.parse(JSON.stringify(dados))) });
  };
  URL.createObjectURL = (blob) => { window.__blobs.push(blob); return 'blob:falso/' + window.__blobs.length; };
  URL.revokeObjectURL = () => {};
  HTMLAnchorElement.prototype.click = function () { window.__baixados.push(this.download); };
}
'''




def leitura(nome, sentido, seq, inicio=0, fim=None, primer='', q=40, rotulo='bom'):

    #* uma leitura sintetica que cobre [inicio, fim) do contig: pico em y=30 na base chamada, 98 nas outras (100 e a linha de base)

    fim = LARGURA if fim is None else fim

    canais = {}

    for canal in 'ACGT':
        canais[canal] = [valor for coluna in range(inicio, fim) for valor in (float(coluna), 30.0 if seq[coluna] == canal else 98.0)]

    return {
        'nome': nome, 'sentido': sentido, 'primer': primer, 'q_medio': q, 'q_rotulo': rotulo, 'canais': canais,
        'bases': [[float(coluna), seq[coluna], 40] for coluna in range(inicio, fim)],
        'alinhada': ''.join(seq[coluna] if inicio <= coluna < fim else '-' for coluna in range(LARGURA)),
    }




def traco(leituras, mismatches, cobertura=None):

    consenso = leituras[0]['alinhada']

    return {
        'disponivel': True, 'largura': LARGURA, 'consenso': consenso, 'consenso_pb': LARGURA,
        'cobertura': cobertura or [len(leituras)] * LARGURA, 'mismatches': mismatches, 'leituras': leituras,
    }




def par_simples():

    #* duas leituras que cobrem o contig inteiro e discordam numa coluna so

    a = ['ACGT'[coluna % 4] for coluna in range(LARGURA)]

    b = list(a)

    b[COL_MM] = 'T' if a[COL_MM] != 'T' else 'A'

    return traco([leitura(NOME_F, 'F', a), leitura('amostra_R.ab1', 'R', b, primer='BTR2', q=0, rotulo='')], [COL_MM])




def par_parcial():

    #* a direta vai de 0 a 130, a reversa de 100 a 200: sobreposicao em 100..130, com tres discordancias

    a = ['ACGT'[(coluna * 7) % 4] for coluna in range(LARGURA)]

    b = list(a)

    for coluna in (110, 111, 120):
        b[coluna] = 'G' if a[coluna] != 'G' else 'C'

    cobertura = [2 if 100 <= coluna < 130 else 1 for coluna in range(LARGURA)]

    return traco([leitura('par_F.ab1', 'F', a, fim=130), leitura('par_R.ab1', 'R', b, inicio=100)], [110, 111, 120], cobertura)




def dois_pares():

    #* quatro leituras (dois pares de primers): os botoes de remover passam a se chamar pelo primer

    a = ['ACGT'[(coluna * 3) % 4] for coluna in range(LARGURA)]

    b = list(a)

    b[40] = 'A' if a[40] != 'A' else 'C'

    return traco([leitura('h_F1.ab1', 'F', a, primer='Hep300'), leitura('h_R1.ab1', 'R', b, primer='Hep1615R'),
                  leitura('h_F2.ab1', 'F', a), leitura('h_R2.ab1', 'R', a, primer='HepR2')], [40])




def rascunho(seq, removidas=1, horas=2, quando=True):

    #* o que a Rascunho do oficina.js guarda: {seq, removidas, quando}

    corpo = {'seq': seq, 'removidas': removidas}

    if quando:
        corpo['quando'] = (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat()

    return json.dumps(corpo)




class Tela:

    #* a pagina aberta no Chromium, lida pela TELA (DOM, botoes, armazenamento), nunca por variavel interna do oficina.js

    def __init__(self, pg, erros):

        self.pg = pg

        self.erros = erros  #* pageerror e console.error, na ordem


    def js(self, codigo, argumento=None):

        return self.pg.evaluate(codigo, argumento)


    def esperar(self, ms=ESPERA):

        self.pg.wait_for_timeout(ms)


    def esperar_a_bancada(self):

        self.pg.wait_for_selector('canvas.tr-cv')

        self.pg.wait_for_function("() => document.querySelector('.b.sel') !== null")


    def primeira_coluna(self):

        return self.js("() => +document.querySelector('.aln-l .b[data-col]').dataset.col")


    def marcada(self):

        return self.js("() => { const s = document.querySelector('.b.sel'); return s ? +s.dataset.col : null; }")


    def texto(self, seletor):

        return self.js('s => document.querySelector(s).textContent', seletor)


    def html(self, seletor):

        return self.js('s => document.querySelector(s).innerHTML', seletor)


    def visivel(self, seletor):

        return self.js("s => document.querySelector(s).style.display !== 'none'", seletor)


    def desabilitado(self, seletor):

        return self.js('s => document.querySelector(s).disabled', seletor)


    def contig(self):

        #* a linha do consenso como esta na tela (JAN colunas), gap como '·'

        return self.js("() => [...document.querySelectorAll('#aln .aln-l')].pop().children[1].textContent")


    def base_na_tela(self, linha, coluna):

        return self.js("([l, c]) => { const s = document.querySelectorAll('#aln .aln-l')[l].querySelector('.b[data-col=\"' + c + '\"]'); return s ? s.textContent : null; }", [linha, coluna])


    def clicar_base(self, coluna):

        self.js("c => document.querySelector('#aln .b[data-col=\"' + c + '\"]').dispatchEvent(new MouseEvent('click', {bubbles:true}))", coluna)

        self.esperar()


    def clicar_letra(self, i, coluna):

        #* a letra sob o traco da leitura i

        self.js("([i, c]) => document.querySelector('#bases-' + i + ' .bl[data-col=\"' + c + '\"]').dispatchEvent(new MouseEvent('click', {bubbles:true}))", [i, coluna])

        self.esperar()


    def botao(self, seletor):

        if self.desabilitado(seletor):
            return False  #* botao desabilitado nao recebe clique, como para quem usa

        self.pg.click(seletor)

        self.esperar()

        return True


    def canvas(self, qual=0):

        return self.pg.locator('canvas.tr-cv').nth(qual).bounding_box()


    def puxar(self, de, ate, qual=0):

        #* arrasta dentro do traco `qual`, de uma fracao da largura ate outra, em 12 passos

        caixa = self.canvas(qual)

        y = caixa['y'] + caixa['height'] / 2

        self.pg.mouse.move(caixa['x'] + caixa['width'] * de, y)

        self.pg.mouse.down()

        for k in range(1, 13):
            self.pg.mouse.move(caixa['x'] + caixa['width'] * (de + (ate - de) * k / 12), y)

        self.pg.mouse.up()

        self.esperar(80)


    def clique_curto(self, fracao, qual=0):

        caixa = self.canvas(qual)

        self.pg.mouse.click(caixa['x'] + caixa['width'] * fracao, caixa['y'] + caixa['height'] / 2)

        self.esperar()


    def rodar(self, delta, ctrl=True):

        #* a roda do mouse sobre o traco, com ou sem Ctrl

        caixa = self.canvas()

        self.pg.mouse.move(caixa['x'] + caixa['width'] / 2, caixa['y'] + caixa['height'] / 2)

        if ctrl:
            self.pg.keyboard.down('Control')

        self.pg.mouse.wheel(0, delta)

        if ctrl:
            self.pg.keyboard.up('Control')

        self.esperar()


    def pixels(self, qual=0):

        return self.js('i => document.querySelectorAll("canvas.tr-cv")[i].toDataURL()', qual)


    def sessao(self):

        return self.js('() => Object.fromEntries(Object.keys(sessionStorage).map(k => [k, sessionStorage.getItem(k)]))')


    def local(self):

        return self.js('() => Object.keys(localStorage)')


    def baixados(self):

        #* [(nome do arquivo, conteudo)] dos downloads capturados

        return self.js('async () => { const saida = []; for (let i = 0; i < window.__blobs.length; i++) saida.push([window.__baixados[i], await window.__blobs[i].text()]); return saida; }')


    def fetches(self):

        return self.js('() => window.__fetches')


    def beforeunload_segurou(self):

        return self.js("() => { const ev = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(ev); return ev.defaultPrevented; }")




@pytest.fixture(scope='module')
def navegador():

    with sync_playwright() as p:

        try:
            nav = p.chromium.launch()

        except Exception as erro:
            pytest.skip(f'Chromium indisponível: {erro}')

        yield nav

        nav.close()




@pytest.fixture
def abrir(navegador):

    #* abre o arnes numa pagina nova com o traco (e a resposta da consulta) que o duble de fetch vai entregar

    abertas = []

    def abrir(dados=None, consulta=None, sessao=None, largura=1100, esperar_a_bancada=True):

        pg = navegador.new_page(viewport={'width': largura, 'height': 800})

        erros = []

        pg.on('pageerror', lambda erro: erros.append(str(erro)))

        pg.on('console', lambda mensagem: erros.append('console: ' + mensagem.text) if mensagem.type == 'error' else None)

        pg.route(PAGINA, lambda rota: rota.fulfill(status=200, content_type='text/html; charset=utf-8', body=ARNES))

        pg.goto(PAGINA)

        if sessao:
            pg.evaluate('s => { for (const [k, v] of Object.entries(s)) sessionStorage.setItem(k, v); }', sessao)

        pg.evaluate(PREPARO, [par_simples() if dados is None else dados, {'acertos': ACERTOS} if consulta is None else consulta])

        pg.add_script_tag(content=OFICINA.read_text(encoding='utf-8'))

        tela = Tela(pg, erros)

        if esperar_a_bancada:
            tela.esperar_a_bancada()
        else:
            tela.esperar(200)

        abertas.append(pg)

        return tela

    yield abrir

    for pg in abertas:
        pg.close()




@pytest.fixture
def tela(abrir):

    #* documento novo a cada teste: o estado de abertura e a referencia de todos os numeros, e nao ha caminho pela
    #* interface que o restaure (voltar a coluna 0 exigiria justamente o arrasto sob teste)

    return abrir()




def teste_a_tela_abre_centrada_na_discordancia(tela):

    #* fianca dos outros testes: se a abertura mudar, os numeros deles mudam

    assert tela.marcada() == COL_MM

    assert tela.primeira_coluna() == COL_MM - MEIA

    assert tela.visivel('#acoes') and not tela.visivel('#rascunho') and not tela.visivel('#rascunho-antigo')

    assert (tela.texto('#m-pb'), tela.texto('#m-mm'), tela.texto('#q-ed')) == ('200', '1', '0')

    assert not tela.erros




def teste_puxar_para_a_direita_traz_o_que_esta_a_esquerda(tela):

    #* o defeito relatado em 2026-08-06: o gesto virava clique e a janela ia para a FRENTE — o desenho andava contra a mao

    antes = tela.primeira_coluna()

    tela.puxar(0.25, 0.75)

    depois = tela.primeira_coluna()

    assert depois < antes, f'puxar para a direita levou a janela de {antes} para {depois}: o desenho esta indo contra a mao'

    assert abs((antes - depois) - JAN / 2) <= 2  #* meia largura de traco = metade da janela, e o arrasto e proporcional




def teste_puxar_para_a_esquerda_traz_o_que_esta_a_direita(tela):

    antes = tela.primeira_coluna()

    tela.puxar(0.75, 0.25)

    assert tela.primeira_coluna() > antes




def teste_arrastar_nao_marca_a_base_onde_a_mao_soltou(tela):

    #* a marca e referencia de trabalho — e nela que "remover base" age; quem arrasta esta andando pelo contig

    antes = tela.marcada()

    tela.puxar(0.30, 0.55)

    assert tela.marcada() == antes




def teste_ir_e_voltar_devolve_ao_mesmo_lugar(tela):

    antes = tela.primeira_coluna()

    tela.puxar(0.25, 0.75)

    tela.puxar(0.75, 0.25)

    assert tela.primeira_coluna() == antes




def teste_o_arrasto_no_segundo_traco_move_os_dois(tela):

    #* as leituras rolam juntas: a janela e uma so

    antes = tela.primeira_coluna()

    tela.puxar(0.30, 0.55, qual=1)

    assert tela.primeira_coluna() < antes

    assert tela.js("() => document.querySelectorAll('#bases-0 .bl[data-col]')[0].dataset.col") == tela.js("() => document.querySelectorAll('#bases-1 .bl[data-col]')[0].dataset.col")




def teste_clicar_numa_base_visivel_nao_move_a_janela(tela):

    #* quem arrastou ate aqui e clicou no pico que veio ver perderia o lugar no instante do clique, se marcar recentrasse

    tela.puxar(0.25, 0.60)

    antes = tela.primeira_coluna()

    alvo = antes + 5

    tela.clicar_base(alvo)

    assert tela.marcada() == alvo

    assert tela.primeira_coluna() == antes




def teste_clicar_numa_base_fora_da_janela_recentra(tela):

    tela.clicar_base(tela.primeira_coluna() + 3)

    tela.js("() => document.querySelector('#aln .b[data-col]').dataset.col = 5")  #* um span com coluna fora da janela, como se a tela tivesse rolado

    tela.clicar_base(5)

    assert tela.marcada() == 5

    assert tela.primeira_coluna() == 0  #* 5 - MEIA, preso no inicio do contig




def teste_clique_curto_dentro_do_traco_continua_marcando(tela):

    #* ARRASTO_MIN separa clique de arrasto; abaixo dele o clique tem que continuar valendo, senao marcar pelo traco morre

    antes = tela.marcada()

    tela.clique_curto(0.75)

    assert tela.marcada() != antes




def teste_a_letra_sob_o_traco_tambem_marca(tela):

    alvo = tela.primeira_coluna() + 3

    tela.clicar_letra(1, alvo)

    assert tela.marcada() == alvo




def teste_o_arrasto_para_nas_pontas_do_contig(tela):

    #* sem prender o deslocamento, puxar cinco telas alem do fim guardaria cinco telas de divida e a volta comecaria morta

    for _ in range(12):
        tela.puxar(0.85, 0.15)

    assert tela.primeira_coluna() == LARGURA - JAN

    for _ in range(12):
        tela.puxar(0.15, 0.85)

    assert tela.primeira_coluna() == 0

    tela.puxar(0.85, 0.15)

    assert tela.primeira_coluna() > 0  #* a volta e imediata, nao depois de desfazer a divida




def teste_o_alinhamento_tem_uma_linha_por_leitura_mais_regua_e_contig(tela):

    rotulos = tela.js("() => [...document.querySelectorAll('#aln .aln-n')].map(s => s.textContent)")

    assert rotulos == ['→ ' + NOME_F, '← amostra_R.ab1', 'régua (×10)', 'Contig']

    assert tela.js("() => document.querySelectorAll('#aln .aln-l:first-child .b[data-col]').length") == JAN

    assert tela.html('#cromatogramas .nome') == 'amostra_F &lt;b&gt;x&lt;/b&gt; "q" &amp;amp;.ab1'  #* o nome vem do servidor: escapado

    assert tela.texto('#cromatogramas .nome') == NOME_F  #* o &amp; do nome continua sendo texto, nao virou &

    assert tela.js("() => document.querySelector('#cromatogramas .nome b')") is None

    assert tela.js("() => document.querySelector('#cv-0').getAttribute('aria-label')") == 'Cromatograma de ' + NOME_F  #* as aspas nao fecharam o atributo

    assert 'sentido R · primer BTR2' in tela.js("() => document.querySelectorAll('#cromatogramas .meta')[1].textContent")

    assert 'Q' not in tela.js("() => document.querySelectorAll('#cromatogramas .meta')[1].textContent")  #* q_medio 0: sem qualidade para mostrar




def teste_remover_vira_gap_e_o_consenso_segue_quem_sobra(tela):

    #* na discordancia: F diz uma base, R diz outra; o consenso e o da primeira que tem base

    base_da_f = tela.base_na_tela(0, COL_MM)

    base_da_r = tela.base_na_tela(1, COL_MM)

    assert base_da_f != base_da_r and tela.base_na_tela(3, COL_MM) == base_da_f

    assert tela.botao('#botoes-remover [data-l="0"]')

    assert tela.base_na_tela(0, COL_MM) == '·' and tela.base_na_tela(3, COL_MM) == base_da_r

    assert tela.texto('#m-pb') == '200' and tela.texto('#m-mm') == '0' and tela.texto('#q-ed') == '1'  #* sem a F nao ha mais discordancia

    assert tela.desabilitado('#botoes-remover [data-l="0"]') and not tela.desabilitado('#botoes-remover [data-l="1"]')

    assert tela.visivel('#rascunho') and not tela.desabilitado('#b-undo')

    assert tela.botao('#botoes-remover [data-l="1"]')

    assert tela.base_na_tela(3, COL_MM) == '·' and tela.texto('#m-pb') == '199' and tela.texto('#q-ed') == '2'

    assert tela.desabilitado('#b-todas')




def teste_remover_dos_dois_de_uma_vez_e_desfazer_um_passo_por_vez(tela):

    assert tela.js("() => [...document.querySelectorAll('#botoes-remover button')].map(b => b.textContent)") == ['só do F', 'só do R', 'dos dois']

    assert tela.botao('#b-todas')

    assert tela.texto('#m-pb') == '199' and tela.texto('#q-ed') == '2'

    tela.clicar_base(COL_MM + 1)

    assert tela.botao('#botoes-remover [data-l="0"]')

    assert tela.texto('#q-ed') == '3'

    assert tela.botao('#b-undo')

    assert tela.texto('#q-ed') == '2' and tela.texto('#m-pb') == '199' and not tela.desabilitado('#b-undo')

    assert tela.botao('#b-undo')

    assert tela.texto('#q-ed') == '0' and tela.texto('#m-pb') == '200' and tela.texto('#m-mm') == '1'

    assert tela.desabilitado('#b-undo') and tela.marcada() == COL_MM + 1  #* a marca nao volta com o desfazer




def teste_a_pilha_de_desfazer_guarda_cinquenta_passos(tela):

    for coluna in range(60):
        tela.clicar_base(coluna) if tela.js("c => !!document.querySelector('#aln .b[data-col=\"' + c + '\"]')", coluna) else tela.js('c => { const s = document.querySelector("#aln .b[data-col]"); s.dataset.col = c; s.dispatchEvent(new MouseEvent("click", {bubbles:true})); }', coluna)

        assert tela.botao('#b-todas')

    assert tela.texto('#m-pb') == '140'

    for _ in range(60):
        tela.botao('#b-undo')

    assert tela.texto('#m-pb') == str(200 - 60 + TETO_DA_PILHA), 'a pilha devolveu mais (ou menos) do que TETO_DA_PILHA passos'

    assert tela.desabilitado('#b-undo')




def teste_proximo_erro_percorre_as_discordancias_e_da_a_volta(abrir):

    tela = abrir(par_parcial())

    assert tela.marcada() == 110, 'abre na primeira discordancia DENTRO da sobreposicao'

    assert tela.primeira_coluna() == 110 - MEIA

    assert tela.texto('#m-mm') == '3'

    for esperada in (111, 120, 110, 111):

        assert tela.botao('#b-erro')

        assert tela.marcada() == esperada




def teste_sem_discordancia_a_tela_abre_no_comeco_da_sobreposicao(abrir):

    #* painel vazio (coluna 0, onde so uma leitura chega) le como "nao montou", e e falso

    dados = par_parcial()

    dados['leituras'][1] = leitura('par_R.ab1', 'R', list(dados['leituras'][0]['alinhada'][:130]) + list('ACGT' * 18)[:70], inicio=100)

    dados['mismatches'] = []

    tela = abrir(dados)

    assert tela.texto('#m-mm') == '0'

    assert tela.marcada() == 100 + MEIA and tela.primeira_coluna() == 100

    assert not tela.botao('#b-erro') or tela.marcada() == 100 + MEIA  #* sem discordancia o botao nao tem para onde ir




def teste_fora_da_sobreposicao_so_quem_tem_base_pode_ser_removido(abrir):

    tela = abrir(par_parcial())

    for _ in range(6):
        tela.puxar(0.15, 0.85)

    tela.clicar_base(2)

    assert not tela.desabilitado('#botoes-remover [data-l="0"]')

    assert tela.desabilitado('#botoes-remover [data-l="1"]'), 'a R nao chega na coluna 2: nao ha o que remover dela'

    assert not tela.desabilitado('#b-todas')

    assert tela.js("() => document.querySelectorAll('.tela-tr')[1].querySelector('.fora').textContent") == 'esta leitura não cobre esta região do contig'

    assert tela.js("() => document.querySelectorAll('.tela-tr')[0].querySelector('.fora')") is None

    assert tela.botao('#botoes-remover [data-l="0"]')

    assert tela.base_na_tela(3, 2) == '·' and tela.texto('#m-pb') == '199'




def teste_com_dois_pares_os_botoes_se_chamam_pelo_primer(abrir):

    tela = abrir(dois_pares())

    assert tela.js("() => [...document.querySelectorAll('#botoes-remover button')].map(b => b.textContent)") == ['Hep300', 'Hep1615R', 'F3', 'HepR2', 'de todas']

    assert tela.marcada() == 40

    assert tela.botao('#botoes-remover [data-l="2"]')

    assert tela.base_na_tela(2, 40) == '·' and tela.texto('#m-pb') == '200'

    tela.puxar(0.5, 0.2, qual=3)

    assert tela.primeira_coluna() > 40 - MEIA




def teste_a_edicao_vai_para_o_session_storage_e_nunca_para_o_local(tela):

    assert tela.sessao() == {} and tela.local() == []

    assert tela.botao('#botoes-remover [data-l="0"]')

    guardado = json.loads(tela.sessao()[CHAVE_DO_RASCUNHO])

    assert guardado['removidas'] == 1 and guardado['seq'][0][COL_MM] == '-' and guardado['seq'][1][COL_MM] != '-'

    assert datetime.fromisoformat(guardado['quando'].replace('Z', '+00:00')) > datetime.now(timezone.utc) - timedelta(minutes=1)

    assert tela.local() == []

    assert tela.beforeunload_segurou(), 'sair com edicao pendente tem de acionar o aviso do navegador'




def teste_recarregar_devolve_a_edicao_com_o_aviso_de_quando_foi(tela, abrir):

    assert tela.botao('#b-todas')

    assert tela.texto('#m-pb') == '199'

    tela.pg.reload()

    tela.js(PREPARO, [par_simples(), {'acertos': []}])

    tela.pg.add_script_tag(content=OFICINA.read_text(encoding='utf-8'))

    tela.esperar_a_bancada()

    assert tela.texto('#m-pb') == '199' and tela.texto('#q-ed') == '2', 'a edicao se perdeu no F5'

    assert tela.visivel('#rascunho-antigo') and tela.visivel('#rascunho')

    assert re.fullmatch(r'\d\d/\d\d/\d{4}, \d\d:\d\d', tela.texto('#rascunho-antigo time')), tela.texto('#rascunho-antigo time')

    assert tela.desabilitado('#b-undo'), 'a pilha de desfazer nao sobrevive ao F5, e o botao tem de dizer isso'

    assert not tela.erros




def teste_o_rascunho_vencido_e_ignorado_e_apagado(abrir):

    dados = par_simples()

    editada = [dados['leituras'][0]['alinhada'][:50] + '-' + dados['leituras'][0]['alinhada'][51:], dados['leituras'][1]['alinhada']]

    valido = abrir(dados, sessao={CHAVE_DO_RASCUNHO: rascunho(editada, horas=2)})

    assert valido.texto('#q-ed') == '1' and valido.visivel('#rascunho-antigo') and valido.visivel('#rascunho')

    valido.puxar(0.15, 0.85)  #* a coluna 50 fica fora da janela de abertura (63..137)

    valido.clicar_base(50)

    assert valido.base_na_tela(0, 50) == '·' and valido.desabilitado('#botoes-remover [data-l="0"]')

    vencido = abrir(dados, sessao={CHAVE_DO_RASCUNHO: rascunho(editada, horas=13)})

    assert vencido.texto('#q-ed') == '0' and not vencido.visivel('#rascunho-antigo')

    assert vencido.sessao() == {}, 'o rascunho vencido tem de sair do armazenamento'

    assert not valido.erros and not vencido.erros




@pytest.mark.parametrize('defeito', ['sem_data', 'menos_leituras', 'largura_errada', 'lixo'])
def teste_o_rascunho_que_nao_casa_com_o_traco_e_ignorado(abrir, defeito):

    #* corrida reprocessada, outra amostra na mesma URL, ou lixo: aplicar um rascunho torto apagaria a base errada

    dados = par_simples()

    seq = [l['alinhada'] for l in dados['leituras']]

    editada = [seq[0][:50] + '-' + seq[0][51:], seq[1]]

    guardado = {
        'sem_data': rascunho(editada, quando=False),
        'menos_leituras': rascunho(editada[:1]),
        'largura_errada': rascunho([editada[0][:-1], editada[1]]),
        'lixo': '{nao e json',
    }[defeito]

    tela = abrir(dados, sessao={CHAVE_DO_RASCUNHO: guardado})

    assert tela.texto('#q-ed') == '0' and tela.texto('#m-pb') == '200' and not tela.visivel('#rascunho-antigo')

    assert not tela.erros




def teste_exportar_fasta_leva_a_chave_a_nota_das_removidas_e_apaga_o_rascunho(tela):

    assert tela.botao('#b-todas')

    assert tela.beforeunload_segurou()

    assert tela.botao('#b-fasta')

    [(nome, conteudo)] = tela.baixados()

    assert nome == f'{CHAVE}.fasta'

    linhas = conteudo.split('\n')

    assert linhas[0] == f'>{CHAVE} | 199 pb | 2 base(s) removida(s) a mao'

    assert [len(l) for l in linhas[1:]] == [60, 60, 60, 19, 0]

    assert ''.join(linhas[1:]) == par_simples()['consenso'][:COL_MM] + par_simples()['consenso'][COL_MM + 1:]

    assert tela.sessao() == {} and not tela.visivel('#rascunho') and not tela.beforeunload_segurou()




def teste_exportar_txt_e_o_consenso_puro(tela):

    assert tela.botao('#b-txt')

    assert tela.baixados() == [[f'{CHAVE}.txt', par_simples()['consenso'] + '\n']]




def teste_a_consulta_pede_o_banco_escolhido_e_escapa_o_que_volta(tela):

    assert tela.botao('#b-consultar')

    tela.esperar(120)

    assert tela.fetches()[-1] == [TRACO.replace('/traco', '/consultar') + '?banco=meu%3Ax%20y', 'application/json']

    saida = tela.html('#saida-consulta')

    assert ISCA not in saida and '&lt;img src=x onerror=alert(1)&gt;' in saida

    assert tela.js("() => document.querySelector('#saida-consulta img')") is None

    assert tela.js("() => [...document.querySelectorAll('#saida-consulta .hit')].map(h => h.textContent)") == [
        f'Hepatozoon canis {ISCA} isolate99,6% · cobertura 100% · KX123 <i>',
        'Hepatozoon "sp"98% · cobertura 97,25% · MN1',
    ]

    assert not tela.desabilitado('#b-consultar')




@pytest.mark.parametrize('resposta, texto', [
    ({'acertos': []}, 'Nenhum acerto nesse banco. Isso descreve o banco consultado, não a amostra.'),
    ('falha', 'Não foi possível consultar agora.'),
    ('rede', 'Não foi possível consultar agora.'),
])
def teste_a_consulta_diz_quando_nao_ha_acerto_e_quando_nao_deu(abrir, resposta, texto):

    tela = abrir(consulta=resposta)

    assert tela.botao('#b-consultar')

    assert tela.desabilitado('#b-consultar') or tela.texto('#saida-consulta') == texto  #* durante a consulta o botao fica preso

    tela.esperar(120)

    assert tela.texto('#saida-consulta') == texto

    assert not tela.desabilitado('#b-consultar'), 'o botao tem de voltar tambem depois da falha'




def teste_sem_traco_a_pagina_diz_o_motivo_escapado_e_nao_abre_a_bancada(abrir):

    tela = abrir({'disponivel': False, 'motivo': 'sem <b>traço</b> &amp; tal'}, esperar_a_bancada=False)

    assert tela.html('#aln') == '<p class="sub" style="margin:0">Alinhamento indisponível: sem &lt;b&gt;traço&lt;/b&gt; &amp;amp; tal. As medidas ao lado continuam valendo.</p>'

    assert tela.texto('#aln') == 'Alinhamento indisponível: sem <b>traço</b> &amp; tal. As medidas ao lado continuam valendo.'  #* innerHTML serializa & como &amp; de qualquer jeito; e o textContent que prova o escape

    assert not tela.visivel('#acoes') and tela.js("() => document.querySelector('canvas')") is None

    assert not tela.erros




@pytest.mark.parametrize('resposta', ['falha', 'rede'])
def teste_o_fetch_que_falha_vira_aviso_e_nao_erro_de_pagina(abrir, resposta):

    tela = abrir(resposta, esperar_a_bancada=False)

    assert 'Alinhamento indisponível: não foi possível carregar o traço agora' in tela.texto('#aln')

    assert not tela.visivel('#acoes') and not tela.erros




def teste_a_amplitude_muda_o_traco_e_nada_mais(tela):

    assert tela.texto('#b-amp-valor') == '1,0×'

    pixels = tela.pixels()

    contig = tela.contig()

    for _ in range(3):
        assert tela.botao('#b-amp-mais')

    assert tela.texto('#b-amp-valor') == '2,8×'  #* 1 x raiz(2)^3

    assert tela.pixels() != pixels and tela.contig() == contig and tela.marcada() == COL_MM

    for _ in range(6):
        tela.botao('#b-amp-menos')

    assert tela.texto('#b-amp-valor') == '0,5×' and tela.desabilitado('#b-amp-menos') and not tela.desabilitado('#b-amp-mais')

    for _ in range(12):
        tela.botao('#b-amp-mais')

    assert tela.texto('#b-amp-valor') == '16,0×' and tela.desabilitado('#b-amp-mais')




def teste_ctrl_roda_sobre_o_traco_ajusta_a_amplitude_e_a_roda_solta_nao(tela):

    tela.rodar(-120)

    assert tela.texto('#b-amp-valor') == '1,4×'

    tela.rodar(120)

    tela.rodar(120)

    assert tela.texto('#b-amp-valor') == '0,7×'

    tela.rodar(-120, ctrl=False)

    assert tela.texto('#b-amp-valor') == '0,7×'




def teste_redimensionar_a_janela_reajusta_os_pixels_do_canvas(tela):

    largura = tela.js("() => document.querySelector('canvas.tr-cv').width")

    tela.pg.set_viewport_size({'width': 700, 'height': 800})

    tela.esperar(300)

    nova = tela.js("() => document.querySelector('canvas.tr-cv').width")

    assert nova < largura

    assert nova == tela.js("() => Math.round(document.querySelector('canvas.tr-cv').getBoundingClientRect().width * (window.devicePixelRatio || 1))")




def teste_o_traco_e_pedido_como_json_na_url_do_data_traco(tela):

    assert tela.fetches() == [[TRACO, 'application/json']]




def teste_a_pagina_real_entrega_todos_os_ids_que_o_oficina_pede(tela):

    #* o arnes daqui e amostra.html tem de falar a mesma lingua: um id renomeado no template quebra a bancada em silencio

    gerados = {'cv-', 'bases-', 'b-todas'}  #* estes o proprio oficina.js cria (um canvas e uma faixa por leitura, e o botao "dos dois")

    pedidos = set(re.findall(r"elemento\('([a-z-]+)'\)", OFICINA.read_text(encoding='utf-8'))) - gerados

    html = AMOSTRA_HTML.read_text(encoding='utf-8')

    faltam = [id for id in sorted(pedidos) if f'id="{id}"' not in html]

    assert not faltam, f'amostra.html nao tem os ids {faltam}'

    assert not [id for id in sorted(pedidos) if f'id="{id}"' not in ARNES], 'o arnes deste teste ficou para tras'

    assert 'data-traco="/api/lotes/{{ lote.id }}/amostras/{{ amostra.chave|urlencode }}/traco"' in html




def main():

    pytest.main([__file__, '-q'])




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
