"""
Estrutura e navegação: o que a tela promete tem que existir.

Vem das observações do autor em 2026-08-06 olhando o site rodando:
sem caminho de volta ao workspace, a foto do perfil não chegava à lateral, e a
página não acompanhava a largura da tela.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from easycontig_web import perfil


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
    from easycontig_web import main
    importlib.reload(main)
    c = TestClient(main.app)
    c.post("/entrar", data={"email": "gustavo@ufrrj.br"}, follow_redirects=False)
    return c


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200


# ───────────────────────────────── caminho de volta ao trabalho
def test_a_marca_leva_para_o_inicio(cliente):
    """De dentro do perfil ou dos bancos não havia como voltar ao workspace a
    não ser por uma corrida já existente — e quem ainda não mandou nada não tem
    nenhuma."""
    for rota in ("/perfil", "/bancos"):
        html = cliente.get(rota).text
        assert 'class="lat-topo" href="/"' in html, f"{rota}: a marca não é link"


def test_toda_pagina_tem_botao_de_workspace(cliente):
    for rota in ("/perfil", "/bancos"):
        assert "Workspace" in cliente.get(rota).text, f"{rota}: sem volta ao workspace"


def test_a_pagina_aberta_fica_marcada_na_lateral(cliente):
    assert 'class="ac larga viva"' in cliente.get("/").text
    assert 'class="ac viva"' in cliente.get("/perfil").text


# ───────────────────────────────── a foto chega na lateral
def test_a_foto_do_perfil_aparece_na_lateral(cliente):
    """Era a queixa direta: trocava a foto e a lateral seguia com a inicial, o
    que faz concluir que o envio falhou."""
    assert "/perfil/foto" not in cliente.get("/perfil").text

    cliente.post("/perfil", data={"nome": "Gustavo Freitas"},
                 files={"foto": ("f.png", PNG, "image/png")}, follow_redirects=False)

    for rota in ("/perfil", "/bancos", "/"):
        assert '<img src="/perfil/foto"' in cliente.get(rota).text, (
            f"{rota}: a lateral não mostra a foto")


def test_sem_foto_a_lateral_usa_a_inicial_do_nome(cliente):
    cliente.post("/perfil", data={"nome": "Maristela"}, follow_redirects=False)
    html = cliente.get("/perfil").text
    assert ">MA<" in html.replace("\n", "").replace(" ", "")


# ───────────────────────────────── endereços de rede
@pytest.mark.parametrize("entrada,esperado", [
    ("github.com/fulano", "GitHub"),
    ("https://orcid.org/0000-0002-1825-0097", "ORCID"),
    ("http://lattes.cnpq.br/123", "Lattes"),
    ("www.linkedin.com/in/fulano", "LinkedIn"),
    ("instagram.com/lab", "Instagram"),
    ("ufrrj.br/lhv", "Site"),          # desconhecido cai no globo, não some
])
def test_a_rede_e_reconhecida_pelo_endereco(entrada, esperado):
    assert perfil._limpar_links(entrada)[0]["nome"] == esperado


@pytest.mark.parametrize("perigoso", [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
])
def test_esquema_que_nao_e_http_e_recusado(perigoso):
    """O Jinja escapa o TEXTO, não o ESQUEMA de uma URL: sem esta checagem o
    endereço viraria `href` clicável na própria página — o XSS armazenado do
    commit bb72803 voltando por uma porta nova."""
    assert perfil._limpar_links(perigoso) == []


def test_endereco_sem_esquema_ganha_https():
    assert perfil._limpar_links("github.com/x")[0]["url"] == "https://github.com/x"


def test_os_enderecos_sobrevivem_a_ida_e_volta(cliente, tmp_path):
    cliente.post("/perfil", data={
        "nome": "G", "links": "github.com/juaredbr-cpu\norcid.org/0000-0002-1825-0097",
    }, follow_redirects=False)
    html = cliente.get("/perfil").text
    assert html.count('class="rede"') == 2
    assert 'rel="noopener noreferrer"' in html


def test_endereco_perigoso_nao_chega_na_pagina(cliente):
    cliente.post("/perfil", data={"nome": "G", "links": "javascript:alert(1)"},
                 follow_redirects=False)
    assert "javascript:" not in cliente.get("/perfil").text


def test_ha_teto_de_enderecos(cliente):
    muitos = "\n".join(f"site{i}.com" for i in range(30))
    assert len(perfil._limpar_links(muitos)) == perfil.MAX_LINKS


# ───────────────────────────────── largura
def test_nenhuma_pagina_prende_a_largura_num_teto_proprio(cliente):
    """O `max-width` fixo de 64/74/78rem deixava 416px de vazio num monitor de
    1920 (medido). A medida de leitura passa a ser do texto, não da página."""
    import re
    from pathlib import Path
    tpl = Path(__file__).resolve().parents[1] / "easycontig_web" / "templates"
    for arquivo in tpl.glob("*.html"):
        texto = arquivo.read_text(encoding="utf-8")
        for linha in texto.splitlines():
            if linha.lstrip().startswith(("/*", "*", "#", "{#")):
                continue          # comentário explicando o histórico
            achado = re.search(r"\.interno\{[^}]*max-width:\s*(\d+)rem", linha)
            assert not achado or int(achado.group(1)) >= 100, (
                f"{arquivo.name}: `.interno` voltou a ter teto estreito "
                f"({achado.group(1)}rem)")


# ───────────────────────────────── login Google desligado é honesto
def test_botao_do_google_desligado_diz_o_motivo_sem_precisar_de_clique(tmp_path,
                                                                      monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "t")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
    from easycontig_web import main
    importlib.reload(main)
    html = TestClient(main.app).get("/entrar").text

    assert "google desligado" in html, "o botão não parece desligado"
    assert 'style="display:none' not in html.split("g-aviso")[1][:40], (
        "o motivo continua escondido até o clique")
    assert "GOOGLE_CLIENT_ID" in html and "Google Cloud" in html, (
        "o aviso não diz o que fazer")


# ───────────────────────────────── a caixa de quem entrou tem que caber
# Vem do print do autor em 2026-08-06: "a caixa do print está com as dimensões
# incorretas". Medido no navegador, lateral de 212px:
#   • o nome pedia 169px numa caixa de 138 e saía "Gustavo Gonçalves …";
#   • cada botão media 37,2px apesar de o CSS declarar `min-height:32px` —
#     herdavam `line-height:1.6` do corpo, 15% a mais em três botões;
#   • o retrato ficava em x=17 e o selo da marca logo acima em x=14 — dois
#     círculos de 30px desalinhados por 3px.
def _regra(html: str, seletor: str) -> str:
    """O bloco `{...}` da regra CSS, sem os comentários que a explicam."""
    corpo = html.split(seletor + "{", 1)
    assert len(corpo) == 2, f"regra `{seletor}` sumiu da folha"
    return corpo[1].split("}", 1)[0]


def test_o_nome_de_quem_entrou_nao_e_cortado_numa_linha(cliente):
    regra = _regra(cliente.get("/").text, ".perfil .nm")
    assert "white-space:nowrap" not in regra, (
        "o nome voltou a caber numa linha só — numa lateral de 212px isso "
        "corta o sobrenome, que é o que distingue duas contas do mesmo lab")
    assert "-webkit-line-clamp:2" in regra, "o nome perdeu o limite de 2 linhas"


def test_os_botoes_da_caixa_tem_a_altura_que_declaram(cliente):
    regra = _regra(cliente.get("/").text, ".perfil .ac")
    assert "line-height:1.2" in regra, (
        "sem `line-height` explícito o botão herda o 1.6 do corpo e sai com "
        "37,2px onde o `min-height` promete 32")


def test_o_retrato_alinha_com_o_selo_da_marca(cliente):
    """São dois círculos de 30px empilhados; 3px de diferença entre eles é o
    que fazia a caixa parecer torta."""
    html = cliente.get("/").text
    import re

    def recuo_esquerdo(regra: str) -> int:
        m = re.search(r"padding:\s*([\w. ]+)", regra)
        assert m, f"sem padding em `{regra}`"
        partes = m.group(1).split()
        # padding: cima  lados  baixo   → o lado é o segundo valor
        return int(float(partes[1].replace("px", "")))

    marca = recuo_esquerdo(_regra(html, ".lat-topo"))          # 14px
    caixa = recuo_esquerdo(_regra(html, ".perfil"))            # 8px
    ident = recuo_esquerdo(_regra(html, ".perfil .ident"))     # + 6px
    assert caixa + ident == marca, (
        f"retrato em {caixa + ident}px, selo da marca em {marca}px")
