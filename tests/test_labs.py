"""
Labs — o diretório dos laboratórios cadastrados.

Pedido do autor (2026-08-08): um botão "Labs" na lateral, abaixo do Readme e ao
lado de Sair, abrindo a lista de perfis cadastrados no site. A funcionalidade
final será informada depois; por ora a página é a vitrine de quem se cadastrou.

O que estes testes travam:
  • a página exige sessão, como todo o resto;
  • lista SÓ o que a pessoa declarou (nome, laboratório, portfólio) — nunca
    corridas nem o que o BLAST achou;
  • ENTRAR já cadastra (mudou em 2026-08-08 — antes só salvar cadastrava, e o
    diretório mostrava uma pessoa só num site com duas contas em uso);
  • o cadastro automático nunca grava o local-part do e-mail (achado L1);
  • a foto do cartão sai por nome de arquivo sorteado, nunca por e-mail;
  • o botão está na lateral e marca a página quando aberta.
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


def _sem_sessao(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "d2"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
    from easycontig_web import main
    importlib.reload(main)
    return TestClient(main.app)


# ───────────────────────────────── porta fechada
def test_labs_exige_sessao(tmp_path, monkeypatch):
    c = _sem_sessao(tmp_path, monkeypatch)
    assert c.get("/labs").status_code == 401


# ───────────────────────────────── o botão está na lateral
def test_o_botao_labs_esta_na_lateral_e_aponta_para_labs(cliente):
    html = cliente.get("/").text
    assert 'href="/labs"' in html, "não há botão Labs na lateral"


def test_a_pagina_labs_fica_marcada_na_lateral_quando_aberta(cliente):
    html = cliente.get("/labs").text
    # o mesmo padrão `viva` que marca o Workspace e o Perfil abertos
    assert 'href="/labs"' in html and "viva" in html.split('href="/labs"')[0][-80:] \
        or 'class="ac viva"' in html, "a página aberta não fica marcada"


# ───────────────────────────────── entrar já cadastra; declarar é o que enche
def test_entrar_no_site_ja_cadastra_no_diretorio(cliente):
    """Mudou em 2026-08-08. Antes, só quem SALVAVA o perfil ganhava linha em
    `perfis` — e o efeito prático foi o autor abrir o `/labs` e ver só a si
    mesmo, embora a orientadora já tivesse entrado e mandado uma corrida no dia
    anterior. Um diretório para um laboratório achar outro (ADR 0052) não pode
    depender de um formulário que ninguém pediu para preencher.

    A `cliente` já entrou na fixture, então há de existir um cartão.
    """
    html = cliente.get("/labs").text
    assert "Ainda não há perfis cadastrados" not in html
    assert "perfil sem nome" in html, "entrou, mas não apareceu no diretório"


def test_cadastro_automatico_nao_grava_o_local_part_do_email(cliente):
    """A trava que impede o cadastro automático de reabrir o achado L1.

    O `Usuario.nome` cai em `email.split("@")[0]` quando não há nome declarado —
    inofensivo na lateral (é o endereço de quem lê), fatal no diretório. Quem
    grava é `garantir_registro`, e ele só aceita nome que o PROVEDOR declarou.
    """
    from easycontig_web import main as m
    guardado = perfil.listar_perfis(m.cfg.sqlite_path)[0]
    assert guardado["nome"] == "", (
        f"gravou {guardado['nome']!r} no cadastro automático")
    # Os NOMES DOS CARTÕES, e não a página inteira: a lateral mostra o nome de
    # quem está logado (ali o local-part é o próprio endereço de quem lê) e o
    # rodapé traz a autoria do software. Nenhum dos dois é vazamento, e varrer
    # por substring confundia os três.
    import re
    html = cliente.get("/labs").text
    nomes = re.findall(r'<div class="nm">(.*?)</div>', html, re.S)
    assert nomes, "nenhum cartão no diretório"
    assert all("gustavo" not in n.lower() for n in nomes), (
        f"o cartão remontou o e-mail: {nomes}")
    assert "perfil sem nome" in nomes[0]


def test_nome_declarado_pelo_provedor_entra_no_cadastro(cliente):
    """O caminho do Google: a pessoa não digitou nada, mas a conta dela declara
    um nome — e é esse que o diretório mostra. É o que faz a orientadora
    aparecer como ela mesma sem preencher formulário nenhum."""
    from easycontig_web import main as m
    perfil.garantir_registro(m.cfg.sqlite_path, "maristela@ufrrj.br",
                             "Maristela Peckle")
    html = cliente.get("/labs").text
    assert "Maristela Peckle" in html
    assert "maristela@ufrrj.br" not in html, "o e-mail foi para a tela"


def test_cadastro_automatico_nao_pisa_no_que_a_pessoa_declarou(cliente):
    """Entrar de novo não pode apagar o perfil de quem editou — por isso o
    UPDATE do conflito é condicionado a `perfis.nome=''`."""
    from easycontig_web import main as m
    cliente.post("/perfil", data={"nome": "Gustavo Freitas", "laboratorio": "LHV"},
                 follow_redirects=False)
    perfil.garantir_registro(m.cfg.sqlite_path, "gustavo@ufrrj.br", "Outro Nome")
    p = perfil.pegar(m.cfg.sqlite_path, "gustavo@ufrrj.br")
    assert (p["nome"], p["laboratorio"]) == ("Gustavo Freitas", "LHV")


def test_perfil_salvo_aparece_com_o_que_foi_declarado(cliente):
    cliente.post("/perfil", data={
        "nome": "Gustavo Freitas",
        "laboratorio": "Laboratório de Hemoparasitas e Vetores",
        "instituicao": "UFRRJ",
        "especies": "Babesia vogeli\nHepatozoon canis",
        "marcadores": "18S rRNA, 16S rRNA",
    }, follow_redirects=False)

    html = cliente.get("/labs").text
    assert "Gustavo Freitas" in html
    assert "Laboratório de Hemoparasitas e Vetores" in html
    assert "Babesia vogeli" in html
    assert "18S rRNA" in html
    # é a própria conta: ganha o selo "você"
    assert "você" in html


def test_labs_nao_expoe_corridas_nem_email_de_terceiro_por_engano(cliente):
    """A lista é declaração, não atividade: não deve vazar nomes de corrida."""
    from easycontig_web import fila
    from easycontig_web import main as m
    lid = fila.novo_lote(m.cfg.sqlite_path, dono="gustavo@ufrrj.br",
                         nome="F13719 sequencia sigilosa", n_arquivos=2)
    fila.liberar_para_fila(m.cfg.sqlite_path, lid, 2)
    cliente.post("/perfil", data={"nome": "Gustavo"}, follow_redirects=False)

    # A área de trabalho (o diretório em si), sem a lateral — a lateral mostra as
    # corridas DA PRÓPRIA conta de propósito (WorkFolder), e isso não é vazamento.
    corpo = cliente.get("/labs").text.split('class="trabalho"', 1)[1]
    assert "sequencia sigilosa" not in corpo, "o diretório vazou nome de corrida"


# ───────────────────────────────── o helper em si
def test_listar_perfis_ordena_e_so_traz_declaracao(cliente):
    from easycontig_web import main as m
    perfil.salvar(m.cfg.sqlite_path, "zulmira@ufrrj.br", nome="Zulmira")
    perfil.salvar(m.cfg.sqlite_path, "ana@ufrrj.br", nome="Ana",
                  especies="Babesia vogeli")

    lista = perfil.listar_perfis(m.cfg.sqlite_path, "ana@ufrrj.br")
    nomes = [p["nome"] for p in lista]
    assert nomes == sorted(nomes, key=str.lower), "não veio ordenado por nome"
    ana = next(p for p in lista if p["eu"])
    assert ana["email"] == "ana@ufrrj.br"          # o próprio, ela já o conhece
    assert ana["especies"] == ["Babesia vogeli"]   # lista, não JSON cru

    # Achado L1: o e-mail de TERCEIRO não sai do helper. Quem pergunta recebe um
    # booleano para marcar "você" na tela — não a lista de endereços de todos.
    zulmira = next(p for p in lista if p["nome"] == "Zulmira")
    assert "email" not in zulmira, "o diretório devolveu o e-mail de terceiro"


def test_quem_nao_digitou_nome_nao_vira_o_local_part_do_email(cliente):
    """Achado L1 de 2026-08-08 — o vazamento era pela via do NOME.

    `perfil.pegar` sintetiza `nome = email.split("@")[0]` para quem ainda não
    tem registro, e o `salvar` herdava esse padrão com `nome or atual["nome"]`.
    Resultado: quem salvasse o perfil sem digitar nome ficava GRAVADO com o
    local-part do próprio endereço — e o `/labs` mostra o nome de todo mundo.
    Com o domínio anunciado na tela de entrada (`EASYCONTIG_DOMINIO`), qualquer
    conta autenticada reconstruía o e-mail institucional dos demais. O endereço
    completo nunca chegou ao HTML; ele era remontado, que dá no mesmo.
    """
    from easycontig_web import main as m
    perfil.salvar(m.cfg.sqlite_path, "joao.silva@ufrrj.br", laboratorio="LHV")

    guardado = perfil.listar_perfis(m.cfg.sqlite_path)[0]
    assert guardado["nome"] == "", (
        f"gravou {guardado['nome']!r} — o local-part do e-mail virou nome")

    corpo = cliente.get("/labs").text
    assert "joao.silva" not in corpo, "o /labs remontou o e-mail de terceiro"
    assert "perfil sem nome" in corpo


# ───────────────────────────────── a foto no cartão (pedido do autor, 08/08)
def _com_foto(c, nome="Gustavo"):
    """Salva um perfil com foto e devolve o nome sorteado do arquivo."""
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    c.post("/perfil", data={"nome": nome},
           files={"foto": ("minha.png", png, "image/png")},
           follow_redirects=False)
    from easycontig_web import main as m
    return perfil.pegar(m.cfg.sqlite_path, "gustavo@ufrrj.br")["foto"]


def test_o_cartao_do_labs_usa_a_foto_do_perfil(cliente):
    """O autor reparou que a lateral mostrava a foto e o cartão do diretório
    continuava com a inicial — a mesma pessoa aparecendo de dois jeitos."""
    arquivo = _com_foto(cliente)
    assert arquivo, "o perfil não guardou foto"
    cartoes = cliente.get("/labs").text.split('class="trabalho"', 1)[1]
    assert f'/labs/foto/{arquivo}' in cartoes, "o cartão continuou na inicial"
    assert cliente.get(f"/labs/foto/{arquivo}").status_code == 200


def test_a_foto_do_diretorio_nao_sai_sem_sessao(cliente, tmp_path, monkeypatch):
    arquivo = _com_foto(cliente)
    from easycontig_web import main as m
    anonimo = TestClient(m.app)
    r = anonimo.get(f"/labs/foto/{arquivo}", headers={"accept": "application/json"})
    assert r.status_code == 401


def test_a_rota_de_foto_so_entrega_foto_de_perfil(cliente):
    """A trava que impede a rota de virar leitor da pasta: sai só o que está na
    coluna `foto` de algum perfil, não qualquer arquivo que caia no volume."""
    from easycontig_web import main as m
    _com_foto(cliente)
    intruso = m.cfg.data_dir / "fotos" / "naoehperfil.png"
    intruso.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert cliente.get("/labs/foto/naoehperfil.png").status_code == 404


@pytest.mark.parametrize("mau", ["../fila.sqlite3", "..%2ffila.sqlite3",
                                 "....//fila.sqlite3", "/etc/passwd"])
def test_a_rota_de_foto_nao_atravessa_pasta(cliente, mau):
    assert cliente.get(f"/labs/foto/{mau}").status_code in (404, 400)


def test_o_nome_do_provedor_preenche_cadastro_que_estava_vazio(cliente):
    """A armadilha que o `ON CONFLICT DO NOTHING` teria criado.

    Quem entra pela primeira vez sem nome declarado ganha linha VAZIA. Se o
    conflito não fizesse nada, o login seguinte — já com o nome vindo do
    provedor — não escreveria, e a pessoa ficaria "perfil sem nome" para
    sempre. O UPDATE condicional preenche o vazio e só o vazio.
    """
    from easycontig_web import main as m
    perfil.garantir_registro(m.cfg.sqlite_path, "zulmira@ufrrj.br")        # sem nome
    assert perfil.pegar(m.cfg.sqlite_path, "zulmira@ufrrj.br")["nome"] == ""

    perfil.garantir_registro(m.cfg.sqlite_path, "zulmira@ufrrj.br", "Zulmira Dias")
    assert perfil.pegar(m.cfg.sqlite_path, "zulmira@ufrrj.br")["nome"] == "Zulmira Dias"

    # e um login posterior sem nome não apaga o que já está lá
    perfil.garantir_registro(m.cfg.sqlite_path, "zulmira@ufrrj.br")
    assert perfil.pegar(m.cfg.sqlite_path, "zulmira@ufrrj.br")["nome"] == "Zulmira Dias"
