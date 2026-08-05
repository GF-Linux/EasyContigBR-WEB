"""
Testes da visão por amostra.

O que se protege aqui não é o layout: é o que a tela tem PERMISSÃO de afirmar.
Uma amostra sem acerto no banco local é um resultado — 19 das 40 amostras da
corrida real são marcadores groEL/dsb/sodB, que não são rRNA e portanto não
estão num banco 18S/16S. Se a tela transformar isso em falha, ou preencher a
identidade ausente com 0, ela passa a dizer algo que ninguém mediu.

Os testes contra o lote real (40 amostras) são pulados quando o arquivo não
está na máquina, como faz `tests/test_executor.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from easycontig_web import amostras

LOTE_REAL = Path("/home/deck/Desktop/easycontig-web/dados/lotes/v2-UNsPdWj2E/relatorio.json")


def _gravar(tmp_path, dados) -> Path:
    p = tmp_path / "relatorio.json"
    p.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    return p


def _amostra(**campos) -> dict:
    """Uma amostra com os mesmos padrões de `SampleFacts`."""
    base = {
        "key": "am01", "reads": [], "consensus_len": 300, "contig_width": 300,
        "mean_coverage": 1.5, "n_cov1": 10, "pct_cov1": 3.3, "n_corrected": 0,
        "organism": None, "accession": None, "pct_identity": None,
        "query_cover": None, "e_value": None, "id_source": None,
        "id_status": "sem_acerto", "contig_flipped": False, "error": None,
        "caveats": [],
    }
    base.update(campos)
    return base


@pytest.fixture()
def real():
    if not LOTE_REAL.exists():
        pytest.skip("o lote real de 40 amostras não está nesta máquina")
    rep = amostras.carregar(LOTE_REAL)
    assert rep is not None
    return rep


# ------------------------------------------------------------------ carregar
def test_relatorio_ausente_devolve_none(tmp_path):
    assert amostras.carregar(tmp_path / "nao_existe.json") is None


def test_json_corrompido_devolve_none_em_vez_de_levantar(tmp_path):
    """Relatório ilegível é assunto da página, não motivo para derrubar a
    requisição: quem chama diz 'relatório indisponível'."""
    p = tmp_path / "relatorio.json"
    p.write_text('{"samples": [ isto nao é json', encoding="utf-8")
    assert amostras.carregar(p) is None


def test_json_valido_que_nao_e_relatorio_devolve_none(tmp_path):
    """Aceitá-lo daria uma tela vazia se passando por lote sem amostras."""
    assert amostras.carregar(_gravar(tmp_path, [1, 2, 3])) is None
    assert amostras.carregar(_gravar(tmp_path, {"folder": "x"})) is None


def test_carregar_le_o_relatorio(tmp_path):
    p = _gravar(tmp_path, {"folder": "corrida", "samples": [_amostra()]})
    rep = amostras.carregar(p)
    assert rep["folder"] == "corrida" and len(rep["samples"]) == 1


# -------------------------------------------------------------------- resumo
def test_resumo_conta_o_lote_real_de_40_amostras(real):
    r = amostras.resumo(real)
    assert r["total"] == 40
    assert r["por_situacao"] == {"ok": 21, "sem_acerto": 19}
    assert r["com_ressalva"] == 40
    assert r["com_erro"] == 0
    assert r["banco"] == "16S (banco local curado)"


def test_resumo_nao_emite_juizo_sobre_o_lote(real):
    """O relatório relata, não conclui: nada de nota, score ou aprovação."""
    r = amostras.resumo(real)
    assert not {"nota", "score", "qualidade", "aprovadas", "boas", "ruins"} & set(r)


def test_resumo_so_lista_as_situacoes_que_ocorreram(tmp_path):
    rep = amostras.carregar(_gravar(tmp_path, {"samples": [
        _amostra(key="a", id_status="ok", organism="X"),
        _amostra(key="b", id_status="sem_acerto"),
    ]}))
    codigos = [s["codigo"] for s in amostras.resumo(rep)["situacoes"]]
    assert codigos == ["ok", "sem_acerto"], "um '0 sem acerto' impresso sugeriria " \
        "que alguém esperava o contrário"


def test_resumo_de_lote_sem_amostras_nao_quebra(tmp_path):
    rep = amostras.carregar(_gravar(tmp_path, {"samples": []}))
    r = amostras.resumo(rep)
    assert r["total"] == 0 and r["situacoes"] == [] and amostras.listar(rep) == []


# ----------------------------------------------------- ausência é ausência
def test_amostra_sem_acerto_mantem_a_identidade_vazia_e_nunca_zero(tmp_path):
    """Zero é um número e seria lido como medida."""
    rep = amostras.carregar(_gravar(tmp_path, {"samples": [_amostra()]}))
    linha = amostras.listar(rep)[0]
    for campo in ("identidade", "cobertura_query", "e_value", "organismo",
                  "accession", "marcador"):
        assert linha[campo] == "", f"{campo} deveria ficar vazio"
        assert linha[campo] != 0 and linha[campo] != "0"


def test_no_lote_real_as_19_sem_acerto_nao_ganham_numero(real):
    sem_acerto = [a for a in amostras.listar(real) if a["situacao_id"] == "sem_acerto"]
    assert len(sem_acerto) == 19
    for a in sem_acerto:
        assert a["identidade"] == "" and a["cobertura_query"] == ""
        assert a["e_value"] == "" and a["organismo"] == ""
        # e continuam sendo amostras montadas, com consenso medido
        assert a["consenso_pb"] > 0 and a["cobertura_media"] != ""


def test_cobertura_ausente_fica_vazia(tmp_path):
    rep = amostras.carregar(_gravar(tmp_path, {"samples": [
        _amostra(mean_coverage=None, consensus_len=0, pct_cov1=0.0)]}))
    linha = amostras.listar(rep)[0]
    assert linha["cobertura_media"] == "" and linha["pct_cobertura_1x"] == ""


# ------------------------------------------ não achar ≠ não conseguir procurar
def test_falta_de_ferramenta_aparece_diferente_de_falta_de_acerto(tmp_path):
    """ADR 0047: `blastn` ausente NÃO é 'nenhum acerto' — um fala da instalação,
    o outro do banco consultado."""
    rep = amostras.carregar(_gravar(tmp_path, {"samples": [
        _amostra(key="a", id_status="sem_acerto"),
        _amostra(key="b", id_status="blast_indisponivel"),
        _amostra(key="c", id_status="sem_banco"),
    ]}))
    a, b, c = amostras.listar(rep)
    textos = {a["situacao_texto"], b["situacao_texto"], c["situacao_texto"]}
    assert len(textos) == 3
    assert a["situacao_classe"] != b["situacao_classe"]
    assert "instalação" in b["situacao_nota"] and "instalação" in c["situacao_nota"]
    assert "não a amostra" in a["situacao_nota"]


def test_sem_acerto_nunca_e_pintado_como_falha(tmp_path):
    """19 das 40 amostras da corrida real caem aqui e estão certas: são
    marcadores que não são rRNA e não estão no banco 16S/18S."""
    rep = amostras.carregar(_gravar(tmp_path, {"samples": [
        _amostra(key="a", id_status="sem_acerto"),
        _amostra(key="b", id_status="blast_indisponivel"),
    ]}))
    for linha in amostras.listar(rep):
        assert linha["situacao_classe"] not in ("falhou", "erro"), \
            "o vermelho é da amostra que não montou, não da que não tem acerto"
        assert linha["erro"] == ""


def test_situacao_desconhecida_sai_com_o_codigo_cru(tmp_path):
    """Relatório mais novo que esta tela: melhor ilegível do que traduzido errado."""
    rep = amostras.carregar(_gravar(tmp_path, {"samples": [
        _amostra(id_status="algo_que_ainda_nao_existe")]}))
    linha = amostras.listar(rep)[0]
    assert linha["situacao_texto"] == "algo_que_ainda_nao_existe"
    assert linha["situacao_classe"] not in ("falhou", "erro", "pronto")


# ----------------------------------------------------------------- ressalvas
def test_ressalvas_chegam_na_saida_com_o_limiar_junto(tmp_path):
    rep = amostras.carregar(_gravar(tmp_path, {"samples": [_amostra(caveats=[
        {"code": "cobertura_1", "text": "189 base(s) sustentadas por uma leitura.",
         "rule": "cobertura < 2"}])]}))
    linha = amostras.listar(rep)[0]
    assert linha["n_ressalvas"] == 1
    assert linha["ressalvas"][0] == {
        "codigo": "cobertura_1", "texto": "189 base(s) sustentadas por uma leitura.",
        "regra": "cobertura < 2"}


def test_no_lote_real_toda_amostra_traz_as_ressalvas_por_extenso(real):
    lista = amostras.listar(real)
    assert all(a["n_ressalvas"] >= 1 for a in lista)
    # a ressalva de cobertura 1× é a que diz que F e R quase não se sobrepõem
    uma = amostras.amostra(real, "F13719_am22_CAP49_sodB_E06-F06")
    codigos = [c["codigo"] for c in uma["ressalvas"]]
    assert "cobertura_1" in codigos and "sem_hit" in codigos
    assert all(c["texto"] and c["regra"] for c in uma["ressalvas"])


def test_contig_invertido_e_informado_como_fato(tmp_path):
    """A polaridade do contig é arbitrária; informar não é acusar."""
    rep = amostras.carregar(_gravar(tmp_path, {"samples": [
        _amostra(key="a", contig_flipped=True),
        _amostra(key="b", contig_flipped=False)]}))
    a, b = amostras.listar(rep)
    assert (a["contig_invertido"], a["invertido"]) == ("sim", True)
    assert (b["contig_invertido"], b["invertido"]) == ("nao", False)
    assert amostras.resumo(rep)["com_contig_invertido"] == 1


# ------------------------------------------------------------------- amostra
def test_amostra_por_chave(real):
    a = amostras.amostra(real, "F13719_am01_CAP08_16S_A09-B09")
    assert a["organismo"] == "Candidatus Anaplasma capybara"
    # 1 casa: `report.py` grava `round(pct_identity, 1)`, e imprimir 3
    # afirmaria precisão que o número não tem (conferido contra o blastn cru).
    assert a["identidade"] == "100.0" and a["marcador"] == "16S"
    assert a["n_leituras"] == 2 and len(a["leituras"]) == 2
    assert a["leituras"][0]["janela"] == "95–418"


def test_amostra_inexistente_devolve_none(real):
    assert amostras.amostra(real, "nao_existe") is None
    assert amostras.amostra({"samples": []}, "am01") is None


def test_a_tabela_e_a_pagina_mostram_os_mesmos_numeros(real):
    """Duas telas do mesmo sistema não podem discordar sobre a mesma medida."""
    for linha in amostras.listar(real):
        assert amostras.amostra(real, linha["key"]) == linha


# --------------------------------------------------- paridade com o CSV
def test_os_valores_batem_com_as_colunas_do_csv(real):
    """O CSV é o formato que o laboratório já recebe; a tela não pode exibir
    outro número nem chamar a coluna de outro nome."""
    report = pytest.importorskip("app.core.report")
    from easycontig_web import executor

    linhas = executor.para_csv(report.BatchReport.from_json(
        LOTE_REAL.read_text(encoding="utf-8"))).splitlines()
    cab = linhas[0].split(",")
    equivalente = {
        "n_leituras": "n_leituras", "consenso_pb": "consenso_pb",
        "cobertura_media": "cobertura_media", "pct_cobertura_1x": "pct_cobertura_1x",
        "organismo": "organismo", "accession": "accession",
        "identidade": "identidade_%", "cobertura_query": "cobertura_query_%",
        "e_value": "e_value", "marcador": "marcador", "situacao_id": "situacao_id",
        "contig_invertido": "contig_invertido",
    }
    import csv as _csv
    for linha_csv, a in zip(_csv.reader(linhas[1:]), amostras.listar(real)):
        assert linha_csv[cab.index("amostra")] == a["key"]
        for nosso, coluna in equivalente.items():
            assert str(a[nosso]) == linha_csv[cab.index(coluna)], nosso


# --- regressão: amostra que não montou não pode virar "sem acerto" -----------
# Achado testando a interface em 2026-08-05: um `.ab1` corrompido produzia uma
# amostra com a pílula cinza IDÊNTICA — palavra por palavra e cor por cor — à
# das 19 cujo "sem acerto" é o resultado certo, e ainda somava naquela contagem.
# A tela afirmava que o banco foi consultado quando não houve consenso nenhum
# para consultar. É a ADR 0039 ao contrário: lá, resultado exibido como falha;
# aqui, falha exibida como resultado.

def _que_nao_montou():
    return {"key": "RUIM", "reads": [], "consensus_len": 0, "caveats": [],
            "error": "TracyError: consensus falhou", "id_status": "sem_acerto"}


def test_amostra_que_nao_montou_nao_se_confunde_com_sem_acerto():
    ruim = amostras._formatar(_que_nao_montou())
    boa = amostras._formatar({"key": "OK", "reads": [{}], "consensus_len": 412,
                              "caveats": [], "error": "", "id_status": "sem_acerto"})

    assert ruim["situacao"] == "nao_montou"
    assert boa["situacao"] == "sem_acerto"
    assert ruim["situacao_texto"] != boa["situacao_texto"]
    assert ruim["situacao_classe"] != boa["situacao_classe"]
    # A frase não pode afirmar que o banco foi consultado.
    assert "nada chegou a ser consultado" in ruim["situacao_nota"]


def test_quem_nao_montou_sai_da_contagem_de_sem_acerto():
    rep = {"samples": [_que_nao_montou(),
                       {"key": "A", "consensus_len": 400, "id_status": "sem_acerto"},
                       {"key": "B", "consensus_len": 400, "id_status": "ok"}]}
    r = amostras.resumo(rep)
    assert r["por_situacao"]["sem_acerto"] == 1      # a falha NÃO entra aqui
    assert r["por_situacao"]["nao_montou"] == 1
    assert r["com_erro"] == 1


def test_situacao_id_continua_sendo_o_codigo_do_csv():
    """A paridade com o CSV vale para `situacao_id`; `situacao` é da tela."""
    ruim = amostras._formatar(_que_nao_montou())
    assert ruim["situacao_id"] == "sem_acerto"       # o que report.py gravou
    assert ruim["situacao"] == "nao_montou"          # o que a tela mostra


def test_a_corrida_real_nao_muda_com_o_conserto(real):
    """Nenhuma das 40 pode escorregar para `nao_montou`: todas montaram."""
    r = amostras.resumo(real)
    assert r["total"] == 40
    assert r["por_situacao"] == {"ok": 21, "sem_acerto": 19}
    assert "nao_montou" not in r["por_situacao"]
