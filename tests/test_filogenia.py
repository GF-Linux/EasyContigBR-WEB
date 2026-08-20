"""
Testes da árvore Bayesiana (`app.core.phylogeny`).

Cada caso aqui guarda um erro que aconteceu de verdade ao montar a árvore da
corrida F13719 (40 consensos, 4 marcadores) — não hipóteses. Os dois que mais
importam:

* misturar 16S com groEL numa matriz só (erro científico, sai bonito e falso);
* ler a linha "Maximum" no lugar da "Average" e concluir que uma corrida
  convergida não convergiu.

A maioria não precisa do MrBayes instalado: as travas são de código, e é onde
o produto protege o usuário. Só o teste de ponta a ponta pede o `mb`.
"""
from __future__ import annotations

import shutil

import pytest

from app.core import phylogeny as filo


def _consensos(marcador: str, n: int, semente: str = "ACGT") -> list[filo.Consensus]:
    """n consensos parecidos, mas não idênticos, do mesmo marcador."""
    base = (semente * 40)[:120]
    saida = []
    for i in range(n):
        s = list(base)
        s[i * 3 % len(s)] = "T" if s[i * 3 % len(s)] != "T" else "A"
        saida.append(filo.Consensus(f"F13719_am{i:02d}_CAP{i}_{marcador}_A0{i}-B0{i}",
                                    "".join(s), marcador))
    return saida


# ------------------------------------------------- a trava anti-Frankenstein

def test_marcadores_diferentes_nunca_entram_na_mesma_matriz():
    """16S e groEL não são homólogos: alinhar junto é ruído com cara de resultado."""
    misturado = _consensos("16S", 3) + _consensos("groEL", 3)
    with pytest.raises(filo.MixedMarkers):
        filo.align(misturado)


def test_agrupar_separa_por_marcador_e_nao_perde_amostra():
    todos = _consensos("16S", 21) + _consensos("groEL", 12) + _consensos("dsb", 5)
    grupos = {g.marker: g for g in filo.group_by_marker(todos)}
    assert {m: len(g.members) for m, g in grupos.items()} == \
        {"16S": 21, "groEL": 12, "dsb": 5}


def test_marcador_com_poucas_amostras_e_recusado_com_motivo_legivel():
    """O sodB da F13719 tinha 2 amostras. Sumir em silêncio seria pior que recusar."""
    grupos = {g.marker: g for g in filo.group_by_marker(_consensos("sodB", 2))}
    sodb = grupos["sodB"]
    assert not sodb.buildable
    assert "2 amostra" in sodb.skipped and "sodB" in sodb.skipped
    assert filo.build_tree(sodb, "/nao/deve/ser/usado").skipped == sodb.skipped


# ------------------------------------------------------ o boletim do alinhamento

def test_aparar_corta_ponta_raida_de_consenso_parcial():
    """Na F13719 o 16S tinha 262–805 pb: 48% das colunas eram ponta raída."""
    alinhamento = [("cheia", "ACGTACGT"),
                   ("cheia2", "ACGTACGT"),
                   ("parcial", "----ACGT")]
    aparado, antes, depois = filo.trim_columns(alinhamento)
    assert (antes, depois) == (8, 8), "2 de 3 sustentam as 4 primeiras: fica"
    alinhamento[1] = ("cheia2", "----ACGT")
    aparado, antes, depois = filo.trim_columns(alinhamento)
    assert (antes, depois) == (8, 4), "agora só 1 de 3 sustenta: sai"
    assert all(len(s) == 4 for _, s in aparado)


def test_avisa_quando_as_amostras_sao_quase_identicas():
    """groEL e dsb deram 99% de identidade — o leque é o resultado honesto,
    e o usuário precisa ouvir isso do programa."""
    identicas = [("a", "ACGTACGTAC"), ("b", "ACGTACGTAC"), ("c", "ACGTACGTAC")]
    boletim = filo.assess(identicas, 10, 10)
    assert boletim.identity_median == 100.0
    assert any("quase idênticas" in w for w in boletim.warnings)


def test_nao_avisa_de_pouco_sinal_quando_ha_divergencia():
    divergentes = [("a", "ACGTACGTAC"), ("b", "TGCATGCATG"), ("c", "ACGTTGCATG")]
    boletim = filo.assess(divergentes, 10, 10)
    assert boletim.identity_median < filo.LOW_SIGNAL_IDENTITY
    assert not any("quase idênticas" in w for w in boletim.warnings)


# ------------------------------------------------------------------ o NEXUS

def test_nome_de_amostra_sobrevive_a_ida_e_volta(tmp_path):
    """O MrBayes recusa '-' e '.'; o usuário precisa reconhecer a amostra dele."""
    original = "F13719_am01_CAP08_16S_A09-B09"
    mapa = filo.write_nexus([(original, "ACGT"), ("outra.amostra", "ACGA")],
                            tmp_path / "x.nex", marker="16S")
    texto = (tmp_path / "x.nex").read_text()
    assert "-" not in texto.split("MATRIX")[1].split(";")[0], "hífen quebra o parser"
    assert mapa["F13719_am01_CAP08_16S_A09_B09"] == original, "sem o mapa de volta, o usuário não se reconhece"


def test_nexus_sai_fechado_para_rodar_sozinho(tmp_path):
    """Sem autoclose o `mb` fica esperando alguém digitar; num trabalhador de
    fila isso é um processo pendurado para sempre."""
    filo.write_nexus([("a", "ACGT"), ("b", "ACGA")], tmp_path / "x.nex", marker="16S")
    texto = (tmp_path / "x.nex").read_text()
    assert "autoclose=yes" in texto and "nowarn=yes" in texto
    assert "stoprule=yes" in texto, "sem stoprule a corrida gasta gerações à toa"
    assert f"stopval={filo.ASDSF_TARGET}" in texto


def test_nexus_sai_em_ascii_puro(tmp_path):
    """REGRESSÃO: um travessão no comentário do cabeçalho fazia o MrBayes abortar
    com 'Reached end of file while in comment', apontando o fim do arquivo em
    vez da linha 3. Projeto em português + parser que só lê ASCII."""
    filo.write_nexus([("a", "ACGT"), ("b", "ACGA")], tmp_path / "x.nex",
                     marker="16S — rRNA")
    bruto = (tmp_path / "x.nex").read_bytes()
    assert all(b < 128 for b in bruto), "o MrBayes não lê UTF-8"


def test_nexus_recusa_nomes_que_colidem_depois_de_sanitizar(tmp_path):
    with pytest.raises(ValueError):
        filo.write_nexus([("am-01", "ACGT"), ("am.01", "ACGA")],
                         tmp_path / "x.nex", marker="16S")


# ------------------------------------------- o defeito do leitor de convergência

def test_le_a_media_e_nao_o_maximo_do_asdsf():
    """REGRESSÃO: o `sump` imprime as duas linhas, nesta ordem. Um regex frouxo
    pega a Máxima (0.035), e uma corrida convergida (0.0097) é reportada como
    não-convergida."""
    log = (
        "      Average standard deviation of split frequencies: 0.010376\n"
        "      Average standard deviation of split frequencies: 0.009704\n"
        "   + Convergence diagnostic (standard deviation of split frequencies)\n"
        "       Average standard deviation of split frequencies = 0.009704\n"
        "       Maximum standard deviation of split frequencies = 0.035460\n"
    )
    assert filo.read_asdsf(log) == 0.009704


def test_sem_diagnostico_no_log_nao_inventa_numero():
    assert filo.read_asdsf("Analysis completed in 6 seconds\n") is None


def test_convergencia_vira_frase_e_nao_numero_cru():
    convergiu = filo.TreeResult(marker="16S", asdsf=0.0097)
    faltou = filo.TreeResult(marker="16S", asdsf=0.042)
    assert convergiu.converged and "convergiu" in convergiu.verdict
    assert not faltou.converged
    assert "rode mais gerações" in faltou.verdict


# --------------------------------------------------------------- ponta a ponta

def test_arvore_do_mrbayes_e_lida_apesar_das_anotacoes_de_figtree(tmp_path):
    """REGRESSÃO: `Bio.Phylo` estoura neste arquivo com 'Two string taxonomies?'.
    Como o resto do projeto é Biopython, quem mexer aqui tentaria isso primeiro."""
    #! Formato copiado de uma saída real (F13719/dsb): o que quebra o Biopython
    #! é a anotação vir DEPOIS do comprimento do ramo, e trazer texto entre
    #! aspas (`prob(percent)="100"`).
    folha = ('[&prob=1.00000000e+00,prob(percent)="100",prob+-sd="100+-0"]'
             ':8.119431e-03[&length_mean=9.11731704e-03,'
             'length_95%HPD={1.08711300e-03,1.97661100e-02}]')
    interno = ('[&prob=9.85000000e-01,prob(percent)="99",prob+-sd="99+-1"]'
               ':4.000000e-03[&length_mean=4.00000000e-03,'
               'length_95%HPD={1.00000000e-03,9.00000000e-03}]')
    con = tmp_path / "x.con.tre"
    con.write_text(
        "#NEXUS\nbegin trees;\n"
        "    translate\n        1 am01,\n        2 am02,\n        3 am03;\n"
        f"    tree con_50_majrule = [&U] (1{folha},(2{folha},3{folha}){interno});\n"
        "end;\n")

    from Bio import Phylo
    with pytest.raises(Exception):
        Phylo.read(str(con), "nexus")   # o caminho "óbvio" não funciona

    newick = filo.read_consensus_tree(con)
    assert "0.98" in newick, "a probabilidade posterior tem de sobreviver"
    assert "am01" in newick and "length_95" not in newick


@pytest.mark.skipif(shutil.which("mb") is None, reason="MrBayes (mb) não instalado")
def test_da_lista_de_consensos_a_arvore_por_marcador(tmp_path):
    """O fluxo inteiro: 2 marcadores entram, 1 vira árvore, o outro é recusado
    por ser pequeno — e nenhuma matriz mistura os dois."""
    entrada = _consensos("16S", 5) + _consensos("sodB", 2)
    resultados = {r.marker: r for r in
                  filo.build_trees(entrada, tmp_path, ngen=20_000)}

    assert resultados["sodB"].skipped, "sodB com 2 amostras não pode virar árvore"
    arvore = resultados["16S"]
    assert arvore.consensus_tree is not None and arvore.consensus_tree.exists()
    assert arvore.asdsf is not None, "o ASDSF tem de sair do log"
    assert arvore.assessment.n_taxa == 5
    assert "16S" in (tmp_path / "16S" / "16S.nex").read_text()
