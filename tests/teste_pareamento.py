"""
teste_pareamento.py — quais arquivos viram a mesma amostra, lendo so o NOME. Os nomes aqui sao os dos lotes reais.

    pareamento.agrupar_por_amostra(nomes) ---> chave + sentido de cada nome ---> grupos
    lote.grupos_do_envio(traces)          ---> o mesmo, e DOIS arquivos no envio viram UM grupo

O QUE SE PROTEGE
  1. o que ja pareava continua pareando igual: _F/_R, sentido no meio seguido do primer, 4 primers por amostra, poco.
  2. sentido COLADO ao numero ('Trypanosoma_10F', '12R') pareia — 12 arquivos reais falhavam sozinhos (VPS, 02/11/2026).
  3. o colado NAO engole primer nem corrida: 'Hep1615R' (letras antes) e 'F13383' (F na frente) nao sao sentido.
  4. dois arquivos enviados juntos sao um par mesmo com nomes que nao se ligam ('A11_F' + 'B11_R'); tres ou mais, nao.
"""

from pathlib import Path

import pytest

import lote
import orientacao
import pareamento




def grupos(nomes):

    return {grupo.chave: sorted(caminho.name for caminho in grupo.todas) for grupo in pareamento.agrupar_por_amostra(nomes)}




@pytest.mark.parametrize('nome, chave, sentido', [
    ('amostra12_F_BTF2.ab1', 'amostra12', 'F'),
    ('amostra11_R_Hep1615R.ab1', 'amostra11', 'R'),
    ('CapillariaM10_R.ab1', 'CapillariaM10', 'R'),
    ('101 ST_F.ab1', '101 ST', 'F'),
    ('x_1_F.ab1', 'x', 'F'),
    ('x-rev.ab1', 'x', 'R'),
    ('Sequenciamento_F13501_#75192_28042026_02_D08.ab1', 'Sequenciamento_F13501_#75192_28042026', 'R'),
    ('Trypanosoma_10F.ab1', 'Trypanosoma_10', 'F'),
    ('Trypanosoma_10r.ab1', 'Trypanosoma_10', 'R'),
    ('12R.ab1', '12', 'R'),
    ('HEP1615R.ab1', 'HEP1615R', None),
    ('Sequenciamento_F13383_#71712-24022026_C01.ab1', 'Sequenciamento_F13383_#71712-24022026_C01', None),
    ('hepatozoonx24.ab1', 'hepatozoonx24', None),
])
def teste_chave_e_sentido_pelo_nome(nome, chave, sentido):

    assert (pareamento.chave_da_amostra(nome), pareamento.sentido_pelo_nome(nome)) == (chave, sentido)




def teste_palavra_inteira_vence_o_colado():

    #* 'amostra11_R_Hep1615R': o _R_ decide; o primer no fim nao pode ser lido como "1615 + R"

    assert pareamento.primer_pelo_nome('amostra11_R_Hep1615R.ab1') == 'Hep1615R'

    assert pareamento.chave_da_amostra('amostra11_R_Hep1615R.ab1') == 'amostra11'




def teste_lote_com_sentido_colado_pareia():

    nomes = ['Trypanosoma_10F.ab1', 'Trypanosoma_10R.ab1', 'Trypanosoma_11F.ab1', 'Trypanosoma_11R.ab1']

    assert grupos(nomes) == {'Trypanosoma_10': ['Trypanosoma_10F.ab1', 'Trypanosoma_10R.ab1'],
                             'Trypanosoma_11': ['Trypanosoma_11F.ab1', 'Trypanosoma_11R.ab1']}




def teste_quatro_primers_por_amostra_continuam_juntos():

    nomes = ['amostra11_F_Hep300.ab1', 'amostra11_F_HepRF_M.ab1', 'amostra11_R_Hep1615R.ab1', 'amostra11_R_Hep900.ab1', 'amostra127_F_Hep300.ab1']

    assert [len(caminhos) for caminhos in grupos(nomes).values()] == [4, 1]




@pytest.mark.parametrize('nomes, chave', [
    (['A11_F.ab1', 'B11_R.ab1'], 'A11 + B11'),
    (['amostra165_F_Hep900.ab1', 'amostra70_R_Hep900.ab1'], 'amostra165 + amostra70'),
    (['Sequenciamento_F13383_#71712-24022026_A01.ab1', 'Sequenciamento_F13383_#71712-24022026_B01.ab1'],
     'Sequenciamento_F13383_#71712-24022026_A01 + Sequenciamento_F13383_#71712-24022026_B01'),
])
def teste_dois_arquivos_no_envio_sao_um_par(nomes, chave):

    (grupo,) = lote.grupos_do_envio([Path(nome) for nome in nomes])

    assert grupo.chave == chave and sorted(caminho.name for caminho in grupo.todas) == sorted(nomes)




def teste_par_que_o_nome_ja_liga_fica_como_esta():

    (grupo,) = lote.grupos_do_envio([Path('amostra12_F_BTF2.ab1'), Path('amostra12_R_BTR2.ab1')])

    assert grupo.chave == 'amostra12' and [caminho.name for caminho in grupo.todas] == ['amostra12_F_BTF2.ab1', 'amostra12_R_BTR2.ab1']




def teste_tres_arquivos_soltos_nao_viram_um_grupo():

    #! a excecao e SO para dois: num lote, arquivo sem par continua sozinho e o relatorio diz por que

    assert len(lote.grupos_do_envio([Path('A01.ab1'), Path('B01.ab1'), Path('C01.ab1')])) == 3




def teste_o_orientacao_le_o_sentido_colado_como_a_mesma_amostra():

    #* o par Trypanosoma_10F/10R saia do montagem.py com "os nomes nao confirmam que sao o mesmo individuo"

    nomes = ['Trypanosoma_10F.ab1', 'Trypanosoma_10R.ab1', 'Trypanosoma_11F.ab1', 'Trypanosoma_11R.ab1', 'Trypanosoma_12F.ab1', 'Trypanosoma_12R.ab1']

    identificadores = orientacao.tokens_identificadores(nomes)

    assert orientacao.afinidade_dos_nomes('Trypanosoma_10F.ab1', 'Trypanosoma_10R.ab1', identificadores) == 1.0

    assert orientacao.afinidade_dos_nomes('Trypanosoma_10F.ab1', 'Trypanosoma_11R.ab1', identificadores) == 0.0
