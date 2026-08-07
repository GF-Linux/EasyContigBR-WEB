"""
Testes de fronteira: o que uma conta NÃO pode alcançar, e o que o servidor
recusa como entrada.

Por que existem como teste e não como revisão: revisão de segurança acontece uma
vez e envelhece na primeira rota nova. Estes casos rodam sempre — e o objetivo é
que quem acrescentar a 23ª rota descubra pelo teste, não pelo incidente.

O bem a proteger é concreto: os `.ab1` são sequenciamento **não publicado** do
laboratório, e a ADR 0050 registra que na web eles passam a morar num servidor.
Vazamento aqui não é abstrato.
"""
from __future__ import annotations

import importlib
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

AB1 = Path("/home/deck/Desktop/EasyContig-BR-Demo-Deck/ab1_por_especie/Babesia_vogeli")
REF = {"referencia": "curado"}

# `makeblastdb` só existe onde o BLAST está instalado. O que depende dele se
# pula sozinho, como faz `tests/test_executor.py` — teste que exige ferramenta
# externa não pode quebrar a suíte de quem só quer conferir a fronteira.
BLAST = os.environ.get("EASYCONTIG_BLAST_BIN") or ""
TEM_BLAST = bool(BLAST and (Path(BLAST) / "makeblastdb").exists()) or bool(
    shutil.which("makeblastdb"))


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    if BLAST:
        monkeypatch.setenv("EASYCONTIG_BLAST_BIN", BLAST)
    from easycontig_web import main
    importlib.reload(main)
    return main


def _cli(app):
    return TestClient(app.app)


def _entrar(c, email):
    c.post("/entrar", data={"email": email}, follow_redirects=False)
    return c


def _envio(nomes=("amostra12_F_BTF2.ab1",)):
    return [("arquivos", (n, (AB1 / n).read_bytes(), "application/octet-stream"))
            for n in nomes]


def _lote_de(c, nome="corrida"):
    r = c.post("/lotes", files=_envio(), data={"nome": nome, **REF},
               follow_redirects=False)
    return r.headers["location"].rsplit("/", 1)[-1]


# ════════════════════════════════════════════════ isolamento entre contas
# O teste enumera as rotas de propósito: uma rota nova que esqueça a checagem de
# dono passa a falhar aqui em vez de vazar em silêncio.
ROTAS_DO_LOTE = [
    "/lotes/{id}",
    "/lotes/{id}?tela_do_lote=1",
    "/api/lotes/{id}",
    "/lotes/{id}/relatorio",
    "/lotes/{id}/relatorio.json",
    "/lotes/{id}/resultado.csv",
    "/lotes/{id}/amostras/amostra12",
    "/api/lotes/{id}/amostras/amostra12/traco",
    "/api/lotes/{id}/amostras/amostra12/consultar?banco=curado",
]


def test_nenhuma_rota_do_lote_vaza_para_outra_conta(app):
    """404 e não 403: responder "proibido" já confirmaria que o protocolo existe,
    e o id vai em link que se cola no grupo errado."""
    dono, intruso = _cli(app), _cli(app)
    _entrar(dono, "dono@ufrrj.br")
    _entrar(intruso, "intruso@ufrrj.br")
    lote = _lote_de(dono)

    for rota in ROTAS_DO_LOTE:
        r = intruso.get(rota.format(id=lote))
        assert r.status_code == 404, f"{rota} devolveu {r.status_code}"
    assert intruso.post(f"/lotes/{lote}/apagar").status_code == 404


def test_sem_sessao_nada_do_lote_responde(app):
    dono, anonimo = _cli(app), _cli(app)
    _entrar(dono, "dono@ufrrj.br")
    lote = _lote_de(dono)
    for rota in ROTAS_DO_LOTE:
        r = anonimo.get(rota.format(id=lote), headers={"accept": "application/json"})
        assert r.status_code == 401, f"{rota} devolveu {r.status_code}"


def test_perfil_e_sempre_o_da_sessao(app):
    """Não existe caminho para o perfil de outra pessoa, e passar o e-mail dela
    na URL não muda isso."""
    a, b = _cli(app), _cli(app)
    _entrar(a, "a@ufrrj.br")
    _entrar(b, "b@ufrrj.br")
    a.post("/perfil", data={"nome": "Nome do A", "especies": "Babesia vogeli"})

    corpo = b.get("/perfil?email=a@ufrrj.br").text
    assert "Nome do A" not in corpo
    assert "a@ufrrj.br" not in corpo
    assert b.get("/perfil/a@ufrrj.br").status_code == 404


@pytest.mark.skipif(not TEM_BLAST, reason="makeblastdb não está nesta máquina")
def test_banco_do_usuario_nao_e_visivel_nem_removivel_por_outro(app):
    a, b = _cli(app), _cli(app)
    _entrar(a, "a@ufrrj.br")
    _entrar(b, "b@ufrrj.br")
    a.post("/bancos/meu", data={"apelido": "refs-do-a"},
           files={"fasta": ("r.fa", b">x\nACGTACGTACGTACGTACGT\n", "text/plain")})

    from easycontig_web import bancos
    meus = bancos.meus_bancos(app.cfg.data_dir, "a@ufrrj.br")
    assert len(meus) == 1
    banco_id = meus[0]["id"]

    assert "refs-do-a" not in b.get("/bancos").text
    b.post(f"/bancos/{banco_id}/remover")
    assert bancos.existe(app.cfg.data_dir, banco_id), "o banco do A foi removido pelo B"


# ════════════════════════════════════════════════════ travessia de caminho
MALICIOSOS = ["../etc/passwd", "..%2f..%2fetc", "....//etc", "/etc/passwd",
              "..\\..\\windows", "%2e%2e%2f", "a/../../b"]


@pytest.mark.parametrize("mau", MALICIOSOS)
def test_id_de_lote_malicioso_nao_alcanca_o_disco(app, mau):
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    r = c.get(f"/lotes/{mau}")
    # O que importa não é o código e sim NÃO entregar um lote: alguns desses o
    # servidor normaliza para a raiz, e a raiz é a página de envio, não um lote.
    assert r.status_code in (404, 400) or "Adicionar arquivos" in r.text, \
        f"{mau} devolveu {r.status_code} com conteúdo de lote"


@pytest.mark.parametrize("mau", MALICIOSOS)
def test_apelido_de_banco_malicioso_e_recusado(app, mau):
    """`bancos.id_do_usuario` vira caminho em disco — o filtro é a fronteira."""
    from easycontig_web import bancos
    with pytest.raises(ValueError):
        bancos.id_do_usuario("a@ufrrj.br", mau)


def test_banco_do_catalogo_inventado_e_recusado(app):
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    assert c.post("/bancos/../../etc/montar").status_code in (404, 405)
    assert c.post("/bancos/nao_existe/montar").status_code == 404


def test_nome_de_arquivo_com_caminho_vira_so_o_nome(app):
    """O navegador manda "pasta/sub/arquivo.ab1" quando se escolhe uma PASTA, e
    um nome com ".." escreveria fora do lote."""
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    dados = (AB1 / "amostra12_F_BTF2.ab1").read_bytes()
    r = c.post("/lotes", data={"nome": "x", **REF}, follow_redirects=False,
               files=[("arquivos", ("../../fora.ab1", dados, "application/octet-stream"))])
    assert r.status_code == 303
    lote = r.headers["location"].rsplit("/", 1)[-1]
    entrada = app.cfg.lotes_dir / lote / "entrada"
    assert [p.name for p in entrada.iterdir()] == ["fora.ab1"]
    assert not (app.cfg.lotes_dir / "fora.ab1").exists()
    assert not (app.cfg.data_dir / "fora.ab1").exists()


# ═══════════════════════════════════════════════════════════ envio hostil
def test_arquivo_de_outro_tipo_e_ignorado(app):
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    r = c.post("/lotes", data={"nome": "x", **REF},
               files=[("arquivos", ("t.txt", b"nada", "text/plain"))])
    assert r.status_code == 400


def test_arquivo_vazio_com_extensao_certa_nao_derruba_o_servidor(app):
    """Um `.ab1` de 0 byte é aceito no upload e falha na montagem — o que não
    pode é a requisição estourar."""
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    r = c.post("/lotes", data={"nome": "x", **REF}, follow_redirects=False,
               files=[("arquivos", ("vazio.ab1", b"", "application/octet-stream"))])
    assert r.status_code == 303


def test_teto_de_arquivos_por_lote(app, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_MAX_ARQUIVOS", "3")
    importlib.reload(app)
    c = TestClient(app.app)
    _entrar(c, "a@ufrrj.br")
    r = c.post("/lotes", data={"nome": "x", **REF},
               files=[("arquivos", (f"a{i}.ab1", b"x" * 10, "application/octet-stream"))
                      for i in range(10)])
    assert r.status_code == 413


# ══════════════════════════════════════════ texto do usuário na tela
HOSTIS = ["<script>alert(1)</script>", "\"><img src=x onerror=alert(1)>",
          "'; DROP TABLE lotes;--", "{{ 7*7 }}", "javascript:alert(1)"]


@pytest.mark.parametrize("mau", HOSTIS)
def test_nome_da_corrida_nao_vira_html_nem_sql(app, mau):
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    r = c.post("/lotes", files=_envio(), data={"nome": mau, **REF},
               follow_redirects=False)
    assert r.status_code == 303
    corpo = c.get("/").text
    # A forma PERIGOSA é a tag crua. O texto escapado (`&lt;img …`) contém as
    # mesmas letras e é inofensivo — conferir a substring solta daria falso
    # positivo e esconderia o que realmente importa.
    assert "<script>alert" not in corpo
    assert "<img src=x" not in corpo
    assert "<img src=\"x\"" not in corpo
    # ⚠️ ESTA ASSERÇÃO NÃO TESTAVA NADA. Era `corpo.split("<style>")[0]`, ou
    # seja, só o pedaço ANTES da folha de estilo — e a folha abre na linha 7 do
    # `base.html`, dentro do `<head>`. O nome da corrida é impresso no `<body>`,
    # depois da linha 190: o teste olhava um trecho onde o payload jamais
    # estaria, e passava sem conferir coisa alguma. Achado pelo painel de
    # verificação em 2026-08-06 — um guarda que dormia desde que foi escrito.
    #
    # Agora olha o corpo. A primeira asserção é a FIANÇA: se o payload deixar de
    # chegar ali (porque a página mudou de forma), o teste reclama em vez de
    # voltar a passar por vacuidade.
    # Procurar "49" na página INTEIRA também não serve: o SVG da marca tem
    # centenas de coordenadas e `M497.90` contém "49". O lugar certo é o
    # elemento onde o nome é impresso, e só ele.
    import re as _re
    nomes = _re.findall(r'<span class="nome">(.*?)</span>', corpo, _re.S)
    if mau == "{{ 7*7 }}":
        assert nomes, ("o nome da corrida não foi impresso — o teste está "
                       "olhando o lugar errado outra vez")
        assert "7*7" in "".join(nomes), "o payload não chegou ao elemento"
        assert "49" not in "".join(nomes), "o Jinja reavaliou o nome da corrida"
    # e a tabela continua de pé depois do payload de SQL
    from easycontig_web import fila
    assert isinstance(fila.listar(app.cfg.sqlite_path), list)


@pytest.mark.parametrize("mau", HOSTIS)
def test_campos_do_perfil_nao_viram_html(app, mau):
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    c.post("/perfil", data={"nome": mau, "laboratorio": mau, "sobre": mau,
                            "especies": mau, "marcadores": mau})
    corpo = c.get("/perfil").text
    assert "<script>alert" not in corpo
    assert "<img src=x" not in corpo


def test_foto_do_perfil_so_aceita_imagem(app):
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    r = c.post("/perfil", data={"nome": "A"},
               files={"foto": ("x.svg", b"<svg onload=alert(1)>", "image/svg+xml")})
    assert r.status_code == 400, "SVG carrega script e não pode passar como foto"


# ═══════════════════════════════════════════════════════════════ sessão
def test_cookie_de_sessao_e_httponly_e_samesite(app):
    """Cookie legível por JavaScript é sessão roubável por XSS."""
    c = _cli(app)
    r = c.post("/entrar", data={"email": "a@ufrrj.br"}, follow_redirects=False)
    bruto = r.headers.get("set-cookie", "")
    assert "httponly" in bruto.lower()
    assert "samesite=lax" in bruto.lower().replace(" ", "")


def test_sair_derruba_a_sessao_de_verdade(app):
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    lote = _lote_de(c)
    c.get("/sair")
    assert c.get(f"/lotes/{lote}", headers={"accept": "application/json"}
                 ).status_code == 401


def test_login_de_dev_recusa_quando_o_modo_e_google(app, monkeypatch):
    """A porta sem senha não pode ficar aberta atrás do provedor de verdade."""
    monkeypatch.setenv("EASYCONTIG_AUTH", "google")
    importlib.reload(app)
    c = TestClient(app.app)
    assert c.post("/entrar", data={"email": "a@ufrrj.br"}).status_code == 403


def test_restricao_de_dominio_vale(app, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "ufrrj.br")
    importlib.reload(app)
    c = TestClient(app.app)
    c.post("/entrar", data={"email": "alguem@gmail.com"}, follow_redirects=False)
    assert c.get("/", follow_redirects=False).status_code == 303   # não entrou


def test_destino_do_login_nao_leva_para_fora(app):
    """`?proximo=` é entrada do usuário: sem filtro, a nossa página de login
    despejaria a pessoa noutro domínio logo depois de ela digitar o e-mail."""
    from easycontig_web.main import _destino
    for fora in ["https://evil.example", "//evil.example", "http://x",
                 "javascript:alert(1)"]:
        assert _destino(fora) == "/"
    assert _destino("/lotes/abc") == "/lotes/abc"


# ═════════════════════════════════════════════════════ limite de requisições
def test_leitura_esbarra_no_teto(app, monkeypatch):
    """Medido em 06/08: 200 requisições passavam em 1,4 s sem resistência."""
    monkeypatch.setenv("EASYCONTIG_LIM_LEITURA", "5")
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    codigos = [c.get("/saude").status_code for _ in range(12)]
    assert 429 in codigos, "nenhuma requisição foi barrada"
    assert codigos.count(200) <= 6


def test_envio_esbarra_no_teto(app, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_LIM_ENVIO", "2")
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    codigos = [c.post("/lotes", files=_envio(), data={"nome": "x", **REF},
                      follow_redirects=False).status_code for _ in range(5)]
    assert 429 in codigos


def test_login_errado_repetido_e_barrado(app, monkeypatch):
    """Sem teto, tentar e-mail atrás de e-mail é adivinhação de graça."""
    monkeypatch.setenv("EASYCONTIG_LIM_LOGIN", "3")
    c = _cli(app)
    codigos = [c.post("/entrar", data={"email": f"x{i}@ufrrj.br"},
                      follow_redirects=False).status_code for i in range(8)]
    assert 429 in codigos


def test_teto_zero_desliga_a_regra(app, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_LIM_LEITURA", "0")
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    assert all(c.get("/saude").status_code == 200 for _ in range(30))


def test_resposta_de_429_diz_quando_tentar_de_novo(app, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_LIM_LEITURA", "2")
    c = _cli(app)
    for _ in range(5):
        r = c.get("/saude")
        if r.status_code == 429:
            assert "Retry-After" in r.headers
            assert "tente de novo" in r.json()["detail"]
            return
    pytest.fail("não barrou")


# ══════════════════════════════════════════════════ trava de produção
def test_producao_recusa_subir_com_a_porta_aberta(monkeypatch):
    """Aviso em `.env.example` não é trava: quem sobe o servidor lê o comando.

    Passaram a ser QUATRO em 2026-08-06: o autor mediu que a lista de usuários
    de teste do Google não restringe nada com escopos não sensíveis — três
    contas entraram com uma só na lista. Então `EASYCONTIG_DOMINIO` em branco
    significa "ninguém decidiu quem entra", e isso não sobe."""
    from easycontig_web import config
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.delenv("EASYCONTIG_SECRET_KEY", raising=False)
    monkeypatch.setenv("EASYCONTIG_HTTPS_ONLY", "0")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    p = config.conferir_producao()
    assert len(p) == 4
    assert any("dev" in x for x in p)
    assert any("SECRET_KEY" in x for x in p)
    assert any("HTTPS" in x for x in p)
    assert any("EASYCONTIG_DOMINIO" in x for x in p)


def test_producao_bem_configurada_nao_reclama(monkeypatch):
    from easycontig_web import config
    monkeypatch.setenv("EASYCONTIG_AUTH", "google")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("EASYCONTIG_HTTPS_ONLY", "1")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "ufrrj.br")
    assert config.conferir_producao() == []


# ══════════════════════════════ XSS pelo cabeçalho do FASTA (achado em 06/08)
def test_o_titulo_do_banco_sai_escapado_do_javascript():
    """XSS ARMAZENADO confirmado com prova de execução em 2026-08-06.

    A cadeia: um FASTA com cabeçalho `>REF001 <img src=x onerror=…>` vira o
    `stitle` do BLAST → vira `titulo` na resposta da consulta → caía em
    `innerHTML` no `oficina.js`. Provado no navegador: `window.INVADIU` executou
    e o `<img>` entrou no DOM.

    A mesma porta valia para o NOME DA LEITURA (que sai do nome do arquivo
    enviado) e vale para qualquer texto vindo do NCBI, que é de fora.

    Este teste guarda a regra no arquivo: todo dado externo passa por `esc()`
    antes de virar HTML. Ler o fonte é o jeito de travar isso sem navegador.
    """
    js = (Path(__file__).parent.parent / "easycontig_web" / "estatico"
          / "oficina.js").read_text()
    assert "const esc =" in js, "a função de escape sumiu"

    # nenhuma interpolação de dado externo pode aparecer sem esc()
    for cru in ["${h.titulo}", "${h.accession}", "${l.nome}", "${l.primer}",
                "${l.q_rotulo}", "+ motivo +"]:
        assert cru not in js, f"{cru} entra em innerHTML sem escapar"

    for esperado in ["${esc(h.titulo)}", "${esc(h.accession)}", "${esc(l.nome)}"]:
        assert esperado in js, f"{esperado} não está escapado"


def test_cabecalho_de_fasta_hostil_nao_quebra_a_montagem_do_banco(app):
    """O payload pode entrar no banco — o que não pode é virar HTML depois."""
    from easycontig_web import bancos
    if not TEM_BLAST:
        pytest.skip("makeblastdb não está nesta máquina")
    bid = bancos.id_do_usuario("a@ufrrj.br", "hostil")
    e = bancos.montar_do_usuario(
        app.cfg.data_dir, bid,
        ">REF001 <img src=x onerror=alert(1)>\n" + "ACGT" * 30 + "\n",
        blast_bin=app.cfg.blast_bin)
    assert e["montado"] and e["sequencias"] == 1


# ══════════════════════ o banco do usuário também tem teto (achado em 06/08)
def test_enviar_banco_proprio_respeita_o_teto_de_taxa(app):
    """Era a única rota que escreve em disco sem teto de taxa nenhum, e os
    bancos do usuário também não entram na cota da conta — uma conta comum
    enchia o volume onde moram os `.ab1` de todo mundo, 20 MB por vez, sem nada
    que recolhesse depois. Achado pelo painel de verificação em 2026-08-06.

    O teto vale ANTES de qualquer trabalho, então o teste não depende do
    `makeblastdb`: o que se afere é a recusa, não a montagem."""
    c = _cli(app)
    _entrar(c, "a@ufrrj.br")
    fasta = b">REF001 teste\nACGTACGTACGTACGTACGT\n"
    codigos = [
        c.post("/bancos/meu", data={"apelido": f"b{i}"},
               files={"fasta": (f"b{i}.fasta", fasta, "text/plain")},
               follow_redirects=False).status_code
        for i in range(14)
    ]
    assert 429 in codigos, (
        f"14 envios seguidos e nenhuma recusa: {sorted(set(codigos))}")
