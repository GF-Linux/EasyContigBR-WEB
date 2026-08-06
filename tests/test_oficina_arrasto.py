"""
Arrastar o traço para o lado — o gesto tem que ir para onde a mão vai.

Vem da observação do autor em 2026-08-06: "dentro do cromatograma arrastar para
o lado está invertido, ao puxar para a direita vai para a esquerda".

Medido antes de mexer, no navegador, com a amostra real `amostra28`: puxando de
25% para 75% da largura do traço, a primeira coluna visível ia de **211 para
230** — a janela andava para a FRENTE, então o desenho andava para trás, contra
a mão. A causa não era um sinal trocado: **não havia arrasto nenhum**. O gesto
chegava ao fim como um `click` na coluna onde a mão soltou, e clicar recentra a
janela ali.

Este arquivo exercita o `oficina.js` DE VERDADE num navegador, com o traço
servido por um `fetch` falso — dado sintético, porque o que está sob teste é a
aritmética da janela, não o alinhamento. Roda contra o arquivo em disco: se o
sinal do arrasto voltar a inverter, estes testes caem.

Pula-se sozinho quando o Playwright ou o Chromium não estão instalados — a
suíte não pode depender de navegador para rodar.
"""
from __future__ import annotations

from pathlib import Path

import pytest

sync_playwright = pytest.importorskip(
    "playwright.sync_api", reason="Playwright não instalado"
).sync_playwright

OFICINA = Path(__file__).resolve().parents[1] / "easycontig_web" / "estatico" / "oficina.js"

LARGURA = 200          # colunas do contig sintético
JAN = 74               # a janela do oficina.js; se mudar lá, muda aqui
MEIA = JAN // 2
COL_MM = 100           # a única discordância: é nela que a tela abre


def _traco() -> dict:
    """Duas leituras que cobrem o contig inteiro e discordam numa coluna só."""
    a = ["ACGT"[c % 4] for c in range(LARGURA)]
    b = list(a)
    b[COL_MM] = "T" if a[COL_MM] != "T" else "A"

    def leitura(nome, sentido, seq):
        canais = {}
        for ch in "ACGT":
            pontos = []
            for c in range(LARGURA):
                # y=100 é a linha de base; o pico sobe na direção de y menor
                pontos += [float(c), 30.0 if seq[c] == ch else 98.0]
            canais[ch] = pontos
        return {
            "nome": nome, "sentido": sentido, "primer": "", "q_medio": 40,
            "q_rotulo": "bom", "canais": canais,
            "bases": [[float(c), seq[c], 40] for c in range(LARGURA)],
            "alinhada": "".join(seq),
        }

    return {
        "disponivel": True, "largura": LARGURA,
        "consenso": "".join(a), "consenso_pb": LARGURA,
        "cobertura": [2] * LARGURA, "mismatches": [COL_MM],
        "leituras": [leitura("amostra_F.ab1", "F", a),
                     leitura("amostra_R.ab1", "R", b)],
    }


ARNES = """
<style>
  .tela-tr{ position:relative; width:800px }
  .tela-tr canvas.tr-cv{ display:block; width:100%; height:112px }
  .bases-lin{ position:relative; height:19px }
  .bases-lin .bl{ position:absolute }
</style>
<div class="oficina" data-traco="/falso">
  <div id="aln"></div>
  <div id="cromatogramas"></div>
  <div id="acoes" style="display:none">
    <button id="b-erro"></button>
    <span id="botoes-remover"></span>
    <button id="b-undo" disabled></button>
    <button id="b-amp-menos"></button><b id="b-amp-valor"></b>
    <button id="b-amp-mais"></button>
  </div>
  <span id="m-pb"></span><span id="m-mm"></span><span id="q-ed"></span>
  <div id="rascunho"></div>
  <div id="rascunho-antigo"><time></time></div>
  <button id="b-fasta"></button><button id="b-txt"></button>
</div>
"""


@pytest.fixture(scope="module")
def _navegador():
    with sync_playwright() as p:
        try:
            nav = p.chromium.launch()
        except Exception as e:                       # navegador não instalado
            pytest.skip(f"Chromium indisponível: {e}")
        yield nav
        nav.close()


@pytest.fixture()
def pagina(_navegador):
    """Documento novo a cada teste: o estado de abertura é a referência de todos
    os números daqui, e não há caminho pela interface que o restaure (voltar à
    coluna 0 exigiria justamente o arrasto sob teste)."""
    pg = _navegador.new_page(viewport={"width": 1100, "height": 800})
    pg.set_content(ARNES)
    pg.evaluate("d => { window.fetch = () => Promise.resolve("
                "{ ok:true, json: () => Promise.resolve(d) }); }", _traco())
    pg.add_script_tag(content=OFICINA.read_text(encoding="utf-8"))
    pg.wait_for_selector("canvas.tr-cv")
    pg.wait_for_function("() => document.querySelector('.b.sel') !== null")
    yield pg
    pg.close()


# ── leitura do estado pela TELA, não por variável interna ────────────────────
def _primeira_coluna(pg) -> int:
    return pg.evaluate("() => +document.querySelector('.aln-l .b[data-col]').dataset.col")


def _marcada(pg):
    return pg.evaluate("() => { const s = document.querySelector('.b.sel');"
                       "return s ? +s.dataset.col : null; }")


def _clicar_base(pg, col: int):
    pg.evaluate("c => document.querySelector('#aln .b[data-col=\"' + c + '\"]')"
                ".dispatchEvent(new MouseEvent('click', {bubbles:true}))", col)
    pg.wait_for_timeout(60)


def _puxar(pg, de: float, ate: float):
    """Arrasta dentro do primeiro traço, de uma fração da largura até outra."""
    cx = pg.locator("canvas.tr-cv").first.bounding_box()
    y = cx["y"] + cx["height"] / 2
    pg.mouse.move(cx["x"] + cx["width"] * de, y)
    pg.mouse.down()
    for k in range(1, 13):
        pg.mouse.move(cx["x"] + cx["width"] * (de + (ate - de) * k / 12), y)
    pg.mouse.up()
    pg.wait_for_timeout(80)


# ── o defeito relatado ───────────────────────────────────────────────────────
def test_a_tela_abre_centrada_na_discordancia(pagina):
    """Fiança dos outros testes: se a abertura mudar, os números abaixo mudam."""
    assert _marcada(pagina) == COL_MM
    assert _primeira_coluna(pagina) == COL_MM - MEIA


def test_puxar_para_a_direita_traz_o_que_esta_a_esquerda(pagina):
    """O defeito relatado. Antes disto o gesto virava clique e a janela ia para
    a FRENTE — o desenho andava contra a mão."""
    antes = _primeira_coluna(pagina)
    _puxar(pagina, 0.25, 0.75)
    depois = _primeira_coluna(pagina)
    assert depois < antes, (
        f"puxar para a direita levou a janela de {antes} para {depois}: "
        "o desenho está indo contra a mão")
    # meia largura de traço = metade da janela, e o arrasto é proporcional
    assert abs((antes - depois) - JAN / 2) <= 2


def test_puxar_para_a_esquerda_traz_o_que_esta_a_direita(pagina):
    antes = _primeira_coluna(pagina)
    _puxar(pagina, 0.75, 0.25)
    assert _primeira_coluna(pagina) > antes


def test_arrastar_nao_marca_a_base_onde_a_mao_soltou(pagina):
    """A marca é referência de trabalho — é nela que "remover base" age. Quem
    arrasta está andando pelo contig, não escolhendo uma base."""
    antes = _marcada(pagina)
    _puxar(pagina, 0.30, 0.55)
    assert _marcada(pagina) == antes


def test_ir_e_voltar_devolve_ao_mesmo_lugar(pagina):
    antes = _primeira_coluna(pagina)
    _puxar(pagina, 0.25, 0.75)
    _puxar(pagina, 0.75, 0.25)
    assert _primeira_coluna(pagina) == antes


# ── o que o arrasto não pode quebrar ─────────────────────────────────────────
def test_clicar_numa_base_visivel_nao_move_a_janela(pagina):
    """Quem arrastou até aqui e clicou no pico que veio ver perderia o lugar no
    instante do clique, porque marcar recentrava a janela."""
    _puxar(pagina, 0.25, 0.60)
    antes = _primeira_coluna(pagina)
    alvo = antes + 5
    _clicar_base(pagina, alvo)
    assert _marcada(pagina) == alvo
    assert _primeira_coluna(pagina) == antes


def test_clique_curto_dentro_do_traco_continua_marcando(pagina):
    """O limiar de 4px separa clique de arrasto; abaixo dele o clique tem que
    continuar valendo, senão marcar base pelo traço deixa de funcionar."""
    antes = _marcada(pagina)
    cx = pagina.locator("canvas.tr-cv").first.bounding_box()
    pagina.mouse.click(cx["x"] + cx["width"] * 0.75, cx["y"] + cx["height"] / 2)
    pagina.wait_for_timeout(60)
    assert _marcada(pagina) != antes


def test_o_arrasto_para_nas_pontas_do_contig(pagina):
    """Sem prender o deslocamento, puxar cinco telas além do fim guardaria cinco
    telas de dívida e o caminho de volta começaria por um trecho morto."""
    for _ in range(12):
        _puxar(pagina, 0.85, 0.15)
    assert _primeira_coluna(pagina) == LARGURA - JAN
    for _ in range(12):
        _puxar(pagina, 0.15, 0.85)
    assert _primeira_coluna(pagina) == 0
    # e a volta é imediata, não depois de desfazer a dívida
    _puxar(pagina, 0.85, 0.15)
    assert _primeira_coluna(pagina) > 0
