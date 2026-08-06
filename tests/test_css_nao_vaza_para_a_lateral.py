"""
O CSS de uma página não pode pintar a lateral.

2026-08-06, terceira vez que o autor apontou "o dimensionamento incorreto da box
perfil/workspace". Nas duas primeiras eu reproduzi na página INICIAL, onde o
defeito não existe, e disse que estava consertado. Ele estava olhando a página
da AMOSTRA.

Lá, `.acoes` e `.ac` da `amostra.html` — a barra de ferramentas do cromatograma —
caem na mesma folha de estilo, depois da `base.html`, e alcançam a caixa do
usuário na lateral, que usa os mesmos nomes. Medido: a caixa ia de **195 px em
x=8** para **123,6 px em x=43,7**, com borda violeta, raio de 11 px e sombra —
virava um cartãozinho flutuante e fora de esquadro.

Este teste não conhece `.acoes` nem `.ac`: ele compara os nomes que a LATERAL usa
com os nomes que cada página redefine. Serve para a próxima colisão, que virá de
outro par de nomes.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "easycontig_web" / "templates"


def _classes_da_lateral() -> set[str]:
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    trecho = base.split('<aside class="lateral">', 1)
    assert len(trecho) == 2, "a lateral mudou de marcação; este teste precisa acompanhar"
    lateral = trecho[1].split("</aside>", 1)[0]
    nomes: set[str] = set()
    for atrib in re.findall(r'class="([^"{}]*)"', lateral):
        nomes.update(p for p in atrib.split() if p)
    return nomes


def _bloco_de_estilo(arquivo: Path) -> str:
    texto = arquivo.read_text(encoding="utf-8")
    if "{% block estilo %}" not in texto:
        return ""
    return texto.split("{% block estilo %}", 1)[1].split("{% endblock %}", 1)[0]


def _seletores(css: str) -> list[str]:
    """Os seletores de cada regra, sem comentários nem corpo."""
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    fora = []
    for bloco in re.findall(r"([^{}]+)\{[^{}]*\}", css):
        for sel in bloco.split(","):
            sel = sel.strip()
            if sel and not sel.startswith("@"):
                fora.append(sel)
    return fora


def _classe_da_ponta(seletor: str) -> str | None:
    """A classe do compound MAIS À ESQUERDA, que é quem decide o alcance.

    `.oficina .ac` é seguro (a ponta é `.oficina`, que não existe na lateral);
    `.ac:hover` não é."""
    ponta = re.split(r"[\s>+~]", seletor.strip())[0]
    m = re.match(r"^\.([A-Za-z0-9_-]+)", ponta)
    return m.group(1) if m else None


PAGINAS = sorted(p for p in TEMPLATES.glob("*.html")
                 if not p.name.startswith("_") and p.name != "base.html")


@pytest.mark.parametrize("pagina", PAGINAS, ids=lambda p: p.name)
def test_a_pagina_nao_redefine_nome_que_a_lateral_usa(pagina):
    lateral = _classes_da_lateral()
    culpados = []
    for sel in _seletores(_bloco_de_estilo(pagina)):
        c = _classe_da_ponta(sel)
        if c and c in lateral:
            culpados.append(sel)
    assert not culpados, (
        f"{pagina.name} redefine nomes que a lateral usa: {culpados}. "
        "O bloco de estilo da página entra na MESMA folha da base.html, depois "
        "dela — então isto repinta a caixa do usuário. Prefixe com `.oficina`, "
        "`.meio` ou o contêiner da página.")


def test_o_teste_pega_o_defeito_que_o_motivou():
    """Fiança: sem isto, o teste acima poderia estar passando por não achar
    nada — seletor nenhum, classe nenhuma — e ninguém perceberia."""
    lateral = _classes_da_lateral()
    assert {"acoes", "ac"} <= lateral, "a lateral deixou de usar estes nomes"
    assert _classe_da_ponta(".acoes") == "acoes"
    assert _classe_da_ponta(".ac:hover:not(:disabled)") == "ac"
    assert _classe_da_ponta(".oficina .ac") == "oficina"      # escopado: passa
    assert _classe_da_ponta(".meio > .acoes") == "meio"       # escopado: passa
