"""
carga_de_amostras.py — poe no banco as amostras dos lotes que ficaram prontos ANTES da tabela `amostras` existir.

Desde a migracao 7 o trabalhador grava as amostras de cada lote no mesmo
instante em que o carimba pronto (FilaDeLotes.concluir). Os lotes de antes so
tem o relatorio.json — e o relatorio.json de antes nao guardava a sequencia
consenso, so o tamanho dela. Este arquivo le cada relatorio, remonta o
consenso a partir do que esta em disco (cromatograma.remontar: o mesmo tracy,
os mesmos .ab1) e grava.


COMO FUNCIONA
=============

    carregar(cfg)
       |
       |-- para cada lote PRONTO:
       |       leitura_de_amostras.carregar(relatorio.json)        sem relatorio legivel -> pula, e diz
       |       amostra que montou e nao tem consenso?
       |           cromatograma.remontar(cfg, lote, fatos)          .consenso_limpo; None -> fica '' e o log diz
       |       transacao(): gravar_amostras(con, lote, relatorio)   substitui as linhas do lote: rodar de novo nao duplica
       |
       v
    (lotes gravados, amostras gravadas, consensos que nao voltaram)


COMO USAR
=========

    docker exec easycontig-trabalhador-1 python -m easycontig_web.processamento.carga_de_amostras


POR QUE NAO NASCE CLASSE
========================

    Roda uma vez, de ponta a ponta, sem estado entre chamadas.
"""

import logging

from easycontig_web import configuracao
from easycontig_web.dados import leitura_de_amostras
from easycontig_web.dados.banco_sqlite import BancoSqlite
from easycontig_web.processamento import cromatograma
from easycontig_web.processamento.fila import PRONTO
from easycontig_web.processamento.fila_de_lotes import FilaDeLotes, gravar_amostras


REGISTRO = logging.getLogger('easycontig.carga')




def carregar(cfg):

    #* grava as amostras de todos os lotes prontos; devolve (lotes, amostras, consensos que nao voltaram)

    banco = BancoSqlite(cfg.caminho_do_sqlite)

    banco.preparar()  #* garante a migracao 7, mesmo que o web ainda nao tenha subido com ela

    lotes = n_amostras = sem_consenso = 0

    for lote in FilaDeLotes(banco).listar(limite=0):

        if lote.status != PRONTO:
            continue

        relatorio = leitura_de_amostras.carregar(cfg.pastas_do_lote(lote.id).relatorio_json)

        if relatorio is None:
            REGISTRO.warning('lote %s: sem relatorio legivel, nada gravado', lote.id)
            continue

        for fatos in relatorio.amostras:

            if fatos.montou and not fatos.consenso:

                montagem = cromatograma.remontar(cfg, lote.id, fatos)

                fatos.consenso = montagem.consenso_limpo if montagem else ''

                sem_consenso += not fatos.consenso

        with banco.transacao() as con:
            gravar_amostras(con, lote.id, relatorio)

        lotes += 1

        n_amostras += len(relatorio.amostras)

        REGISTRO.info('lote %s: %d amostra(s) gravada(s)', lote.id, len(relatorio.amostras))

    return lotes, n_amostras, sem_consenso




def main():

    logging.basicConfig(level='INFO', format='%(asctime)s %(levelname)s %(name)s %(message)s')

    lotes, n_amostras, sem_consenso = carregar(configuracao.carregar())

    print(f'{lotes} lote(s), {n_amostras} amostra(s) no banco; {sem_consenso} amostra(s) montada(s) cujo consenso nao voltou')




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
