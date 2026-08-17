#? ORIENTAÇÃO SAI DO DNA — a tese do produto, exercitada 17/08/2026
#!
#! 1. O leia-me do site afirma: o programa sabe quem é forward e quem é reverse
#!    LENDO O DNA, não o nome do arquivo nem metadado do .ab1.
#! 2. Até aqui, nenhum teste exercitava isso. O `test_leiame` confere que a
#!    PÁGINA diz a frase; ninguém conferia que o programa faz o que ela promete.
#! 3. Estes testes fecham essa lacuna, e rodam em qualquer máquina: orientação é
#!    reverso-complementaridade, que é geometria da sequência — não depende de a
#!    amostra ser Babesia nem de haver banco instalado.
#! 4. O par sintético é um amplicon sorteado mais a leitura dele ao contrário
#!    (ver `amostras_sinteticas.py`), que é exatamente o que um par F/R é.
#!
#? O LIMITE, MEDIDO E DECLARADO
#!
#! 5. Sem uma referência, o detector diz que as duas leituras estão em sentidos
#!    OPOSTOS — e isso ele tira do DNA. Qual das duas é a forward em termos
#!    ABSOLUTOS ele não decide: para isso compara com as referências 18S, que
#!    estão na orientação canônica.
#! 6. Por isso o teste do nome embaralhado afirma o que é verdade — o PAR
#!    sobrevive à troca de nomes — e não afirma o que seria falso, que o rótulo
#!    absoluto sobreviveria. DNA sorteado não bate com referência nenhuma.

from __future__ import annotations

import shutil

import pytest

from amostras_sinteticas import PASTA

orientacao = pytest.importorskip("app.core.orientation")

ADIANTE = PASTA / "amostra12_F_BTF2.ab1"
REVERSO = PASTA / "amostra12_R_BTR2.ab1"
OUTRA_AMOSTRA = PASTA / "amostra28_F_BTF2.ab1"


#* Duas leituras da mesma região em sentidos opostos têm que se reconhecer.
def test_um_par_de_verdade_e_reconhecido_pelo_dna():
    pares, _ = orientacao.analyse([str(ADIANTE), str(REVERSO)])
    assert len(pares) == 1, "o par não foi reconhecido"
    p = pares[0]
    assert p.score > 0.9, f"sobreposição invertida fraca demais: {p.score}"
    assert p.same_sense < 0.1, (
        f"as duas se pareceram no MESMO sentido ({p.same_sense}) — se isso "
        "acontece, não são um par F/R, são duas leituras parecidas")


#* Duas amostras diferentes não podem virar par, nem que o nome sugira.
def test_amostras_diferentes_nao_viram_par():
    pares, _ = orientacao.analyse([str(ADIANTE), str(OUTRA_AMOSTRA)])
    assert pares == [], "duas leituras de amostras distintas foram pareadas"


#* O caso que o leia-me descreve: nomes embaralhados de propósito.
#* Quem responde é a sequência, então o par continua sendo par.
def test_o_par_sobrevive_a_nome_trocado(tmp_path):
    #! O conteúdo ADIANTE recebe nome de reverso, e vice-versa.
    shutil.copy(ADIANTE, tmp_path / "amostra12_R_BTR2.ab1")
    shutil.copy(REVERSO, tmp_path / "amostra12_F_BTF2.ab1")

    pares, _ = orientacao.analyse([str(tmp_path / "amostra12_F_BTF2.ab1"),
                                   str(tmp_path / "amostra12_R_BTR2.ab1")])
    assert len(pares) == 1, "com o nome trocado o par se perdeu — sinal de que "
    p = pares[0]
    assert p.score > 0.9 and p.same_sense < 0.1


#* A distância entre os dois casos tem que ser larga, não apertada — é o que o
#* leia-me afirma ("sem meio-termo"). Aqui se mede a folga.
def test_a_diferenca_entre_par_e_nao_par_e_larga():
    par, _ = orientacao.analyse([str(ADIANTE), str(REVERSO)])
    assert par, "sem par não há o que comparar"
    invertido = par[0].score
    mesmo_sentido = par[0].same_sense
    assert invertido - mesmo_sentido > 0.5, (
        f"a decisão ficou apertada: {invertido} invertido contra "
        f"{mesmo_sentido} no mesmo sentido")
