"""
teste_amostras_no_banco.py — as amostras de cada lote pronto na tabela `amostras` (migracao 7, decisao de 02/11/2026).

    trabalhador --> FilaDeLotes.concluir(lote, eu, relatorio) --> UPDATE lotes (PRONTO) + gravar_amostras, numa transacao
    lote antigo --> carga_de_amostras.carregar(cfg)           --> relatorio.json + consenso remontado --> gravar_amostras
    dono apaga  --> expurgo_por_retencao.apagar_lote          --> linhas de `amostras` saem junto

O QUE SE PROTEGE
  1. lote pronto e amostras no banco andam JUNTOS: o trabalhador que perdeu o lote nao grava nem o carimbo nem as amostras.
  2. regravar substitui: a carga rodada duas vezes nao duplica amostra.
  3. o banco em producao (versao 6) ganha a tabela sem perder lote.
  4. a carga inicial le os relatorio.json de antes e diz quantos consensos nao voltaram, em vez de inventar um.
"""

import json

from conftest import EMAIL, relatorio_fabricado

from easycontig_web.dados import expurgo_por_retencao
from easycontig_web.dados.banco_sqlite import BancoSqlite, VERSAO_ALVO
from easycontig_web.processamento import carga_de_amostras
from easycontig_web.processamento.fila_de_lotes import FilaDeLotes, gravar_amostras




def lote_rodando(tmp_path, trabalhador='deck:1', dono='a@ufrrj.br'):

    banco = BancoSqlite(tmp_path / 'fila.sqlite3')

    banco.preparar()

    lotes = FilaDeLotes(banco)

    lote_id = lotes.novo_lote(dono, 'corrida', 2)

    lotes.liberar_para_fila(lote_id, 2)

    assert lotes.reivindicar(trabalhador).id == lote_id

    return banco, lotes, lote_id




def amostras(banco, lote_id):

    with banco.conectar() as con:
        return con.execute('SELECT chave, dono, situacao, organismo, marcador, identidade, consenso, fatos FROM amostras WHERE lote_id=? ORDER BY chave', (lote_id,)).fetchall()




def teste_concluir_grava_as_amostras_do_relatorio(tmp_path):

    banco, lotes, lote_id = lote_rodando(tmp_path)

    relatorio = relatorio_fabricado(chaves=('amostra12', 'amostra28'))

    relatorio.amostras[0].consenso = 'ACGT' * 100

    assert lotes.concluir(lote_id, 'deck:1', relatorio)

    linhas = amostras(banco, lote_id)

    assert [linha[:7] for linha in linhas] == [('amostra12', 'a@ufrrj.br', 'ok', 'Babesia vogeli', '18S', 99.5, 'ACGT' * 100),
                                               ('amostra28', 'a@ufrrj.br', 'ok', 'Babesia vogeli', '18S', 99.5, '')]

    fatos = json.loads(linhas[0][7])

    assert [leitura['nome'] for leitura in fatos['leituras']] == ['amostra12_F_BTF2', 'amostra12_R_BTR2'] and fatos['ressalvas'][0]['codigo'] == 'cobertura_1'

    assert lotes.pegar(lote_id).status == 'pronto'




def teste_trabalhador_desapossado_nao_grava_amostra(tmp_path):

    banco, lotes, lote_id = lote_rodando(tmp_path)

    assert lotes.concluir(lote_id, 'deck:outro', relatorio_fabricado()) is False

    assert amostras(banco, lote_id) == [] and lotes.pegar(lote_id).status == 'rodando'




def teste_regravar_substitui_em_vez_de_duplicar(tmp_path):

    banco, lotes, lote_id = lote_rodando(tmp_path)

    assert lotes.concluir(lote_id, 'deck:1', relatorio_fabricado(chaves=('amostra12', 'amostra28')))

    with banco.transacao() as con:
        gravar_amostras(con, lote_id, relatorio_fabricado(chaves=('amostra12',)))

    assert [linha[0] for linha in amostras(banco, lote_id)] == ['amostra12']




def teste_banco_na_versao_6_ganha_a_tabela_sem_perder_lote(tmp_path):

    banco, lotes, lote_id = lote_rodando(tmp_path)

    with banco.conectar() as con:
        con.execute('DROP TABLE amostras')
        con.execute('PRAGMA user_version = 6')

    assert banco.preparar() == 1 and banco.versao == VERSAO_ALVO == 7

    assert amostras(banco, lote_id) == [] and lotes.pegar(lote_id) is not None




def teste_apagar_o_lote_leva_as_amostras(tmp_path):

    banco, lotes, lote_id = lote_rodando(tmp_path)

    assert lotes.concluir(lote_id, 'deck:1', relatorio_fabricado())

    assert expurgo_por_retencao.apagar_lote(banco, tmp_path / 'lotes', lote_id)

    assert amostras(banco, lote_id) == []




def teste_carga_inicial_grava_os_lotes_de_antes(bancada):

    #* bancada.concluir faz o que o trabalhador antigo fazia: relatorio.json no disco, lote PRONTO, nenhuma linha em `amostras`

    cliente = bancada.entrar()

    lote_id = bancada.enviar_lote(cliente, [('amostra12_F_BTF2.ab1', b'x'), ('amostra12_R_BTR2.ab1', b'y')])

    bancada.concluir(lote_id)

    banco = bancada.instalacao.banco

    assert amostras(banco, lote_id) == []

    lotes, n_amostras, sem_consenso = carga_de_amostras.carregar(bancada.cfg)

    assert (lotes, n_amostras) == (1, 1)

    assert sem_consenso == 1, 'os .ab1 de mentira nao remontam: o consenso fica vazio e a carga CONTA, em vez de inventar'

    (linha,) = amostras(banco, lote_id)

    assert linha[:5] == ('amostra12', EMAIL, 'ok', 'Babesia vogeli', '18S') and linha[6] == ''

    assert carga_de_amostras.carregar(bancada.cfg)[:2] == (1, 1) and len(amostras(banco, lote_id)) == 1, 'rodar de novo nao duplica'
