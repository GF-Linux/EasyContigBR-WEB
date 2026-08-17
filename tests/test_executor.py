"""
Testes do executor — a ponte para o núcleo científico.

Estes rodam o pipeline DE VERDADE (tracy + blastn + banco local). São pulados
quando as dependências não estão configuradas, para o `pytest` continuar
servindo numa máquina de desenvolvimento sem o Tracy instalado.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from easycontig_web import configuracao as config
from easycontig_web.processamento import executor_de_lote as executor

#! Aqui, e SÓ aqui, o .ab1 precisa ser REAL.
#!   Os outros testes exercitam o caminho do arquivo (upload, fila, recusa), e
#!   para isso o sintético serve. Este roda o pipeline inteiro e confere a
#!   ESPÉCIE identificada — coisa que DNA sorteado nunca vai dar.
#!   Sem os arquivos reais, o teste é PULADO. Rodá-lo com dado sintético daria
#!   uma falha que não é defeito do programa.
#* Onde procurar, em ordem: a variável de ambiente, ou a pasta da demo ao lado.
import os

def _pasta_real() -> Path | None:
    candidatos = [os.environ.get("EASYCONTIG_AB1_REAIS", ""),
                  str(Path.home() / "Desktop/EasyContig-BR-Demo-Deck/ab1_por_especie/Babesia_vogeli"),
                  "/home/deck/Desktop/EasyContig-BR-Demo-Deck/ab1_por_especie/Babesia_vogeli"]
    for c in candidatos:
        if c and Path(c).is_dir():
            return Path(c)
    return None


AB1 = _pasta_real()
PAR = ["amostra28_F_BTF2.ab1", "amostra28_R_BTR2.ab1"]


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    c = config.carregar()
    if not all(ok for _, ok, _ in config.diagnostico(c)):
        pytest.skip("tracy/blastn/bancos não configurados neste ambiente")
    if AB1 is None:
        pytest.skip("os .ab1 reais não estão nesta máquina "
                    "(defina EASYCONTIG_AB1_REAIS para rodar)")
    return c


def _semear(cfg, lote_id, nomes):
    p = executor.pastas_do_lote(cfg, lote_id)
    p["entrada"].mkdir(parents=True, exist_ok=True)
    for n in nomes:
        shutil.copy(AB1 / n, p["entrada"] / n)
    return p


def test_lote_vazio_falha_em_vez_de_gerar_relatorio_em_branco(cfg):
    _semear(cfg, "vazio", [])
    with pytest.raises(ValueError, match="nenhum arquivo"):
        executor.executar(cfg, "vazio")


def test_envio_incompleto_para_tudo(cfg):
    """A trava do defeito de 33 de 40: relatório a menos é indistinguível de
    uma corrida menor, então o certo é falhar e não processar."""
    _semear(cfg, "curto", PAR)
    with pytest.raises(ValueError, match="envio incompleto"):
        executor.executar(cfg, "curto", n_esperado=80)


def test_par_real_monta_identifica_e_grava_os_tres_artefatos(cfg):
    p = _semear(cfg, "par", PAR)
    rep = executor.executar(cfg, "par", n_esperado=2)

    assert len(rep.samples) == 1
    a = rep.samples[0]
    assert a.error is None
    assert a.organism == "Babesia vogeli"
    # o mesmo número que o desktop entrega para esta amostra
    assert 99.0 < a.pct_identity < 100.0
    assert a.id_source == "18S" and a.id_status == "ok"

    for chave in ("relatorio_html", "relatorio_json", "resultado_csv"):
        assert p[chave].exists() and p[chave].stat().st_size > 0

    assert "Tracy" in p["relatorio_html"].read_text(encoding="utf-8")


def test_csv_deixa_identidade_vazia_quando_nao_houve_acerto():
    """Zero é um número e seria lido como medida; ausência tem que ser ausência."""
    from app.core.report import BatchReport, SampleFacts
    rep = BatchReport(samples=[SampleFacts(key="x", consensus_len=300,
                                           id_status="sem_acerto")])
    linha = executor.para_csv(rep).splitlines()[1].split(",")
    cab = executor.para_csv(rep).splitlines()[0].split(",")
    assert linha[cab.index("identidade_%")] == ""
    assert linha[cab.index("situacao_id")] == "sem_acerto"


def test_csv_separa_falta_de_ferramenta_de_falta_de_acerto():
    """ADR 0047: `blastn` ausente NÃO é 'nenhum acerto'."""
    from app.core.report import BatchReport, SampleFacts
    rep = BatchReport(samples=[
        SampleFacts(key="a", consensus_len=300, id_status="sem_acerto"),
        SampleFacts(key="b", consensus_len=300, id_status="blast_indisponivel"),
    ])
    situacoes = [l.split(",")[11] for l in executor.para_csv(rep).splitlines()[1:]]
    assert situacoes == ["sem_acerto", "blast_indisponivel"]
