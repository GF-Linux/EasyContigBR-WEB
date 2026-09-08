"""
cromatograma.py — remonta UMA amostra a partir do que o lote deixou em disco, para a tela do traco.

A pagina da amostra mostra os numeros na hora e pede o cromatograma depois,
por API. O traco nao fica guardado: e remontado sob demanda a partir dos .ab1
que ainda estao em entrada/ (ou do .json que o tracy assemble deixou em
trabalho/). Nada de ciencia aqui: quem monta e o montagem.py, quem troca o
eixo do traco para coluna do consenso e o tracado.py. Este arquivo so acha os
arquivos, chama os dois e transforma qualquer falha em "sem cromatograma" —
a pagina de numeros continua valendo.


COMO FUNCIONA — da FatosDaAmostra ao dict do oficina.js
=======================================================

    fatos = relatorio.amostra(chave)                 (RelatorioDoLote lido do relatorio.json do lote)
           |
           v
    remontar(cfg, lote_id, fatos)
           |
           |-- pastas = cfg.pastas_do_lote(lote_id)
           |
           |-- trabalho/<chave>.json existe?  (o lote montou pelo `tracy assemble`: 3+ leituras, ou 2 que nao sao par)
           |       sim -> montagem.montagem_do_assemble(json, trabalho/<chave>.cons.fa)   de graca, e e o que o relatorio usou
           |
           |-- leituras_em_disco(pastas, fatos)       o .ab1 que o relatorio gravou, se ainda existe;
           |       |                                  senao o trace de mesmo nome dentro de entrada/
           |       '-- menos de 2?  -> None            (retencao apagou, ou a amostra e uma leitura so)
           |
           '-- MontadorDeAmostra(motor_do_tracy(), trabalho, trim=cfg.trim).montar(chave, leituras)
                   o MESMO montador do lote: o DNA diz se e par, o forward vai primeiro
                   falhou (tracy, montagem, ferramenta ausente)? -> None, com a causa no log

    traco_da_amostra(cfg, lote_id, fatos) = tracado.para_navegador(remontar(...))  -> dict do oficina.js, ou None


COMO USAR
=========

    from easycontig_web import configuracao
    from easycontig_web.processamento import cromatograma
    import relatorio

    cfg = configuracao.carregar()
    lote = relatorio.RelatorioDoLote.de_json(cfg.pastas_do_lote('v2-UNsPd').relatorio_json.read_text())

    dados = cromatograma.traco_da_amostra(cfg, 'v2-UNsPd', lote.amostra('amostra12'))
    if dados is None:                                   #* os arquivos ja nao estao no servidor, ou nao ha par para desenhar
        ...

    montagem = cromatograma.remontar(cfg, 'v2-UNsPd', lote.amostra('amostra12'))   #* a consulta a outro banco usa .consenso_limpo


O QUE MUDOU DO dados/cromatograma.py ORIGINAL
=============================================

 1. Mudou de pasta: dados/ -> processamento/. Ele roda o tracy; e um executor
    pequeno, nao uma leitura de dados.

 2. amostra_do_relatorio(rep, key) sumiu: e relatorio.amostra(chave), no
    RelatorioDoLote do nucleo. A amostra chega como FatosDaAmostra, nao como
    dict de chaves em ingles.

 3. montar(cfg, lote_dir, amostra) -> remontar(cfg, lote_id, fatos). O
    montador e o MESMO do lote (MontadorDeAmostra): quem decide se as duas
    leituras sao um par e o DNA, e o forward vai primeiro — o original
    escolhia pelo sentido gravado no JSON e chamava o consensus sempre. Um par
    que o lote montou pelo assemble (dois forward) e remontado do .json.

 4. O .json do assemble e reaproveitado sempre que existe, e nao so quando ha
    mais de duas leituras; e quando ele NAO existe mais mas os .ab1 ainda
    estao la, remonta em vez de desistir (o original devolvia None). Caso
    real que isso corrige: amostra com 3 .ab1 em que o tracy assemble
    descarta 1 — o relatorio fica com 2 leituras e o original, vendo "2",
    re-rodava o tracy consensus e desenhava o REVERSO-COMPLEMENTO do consenso
    que o proprio relatorio, o CSV e o .cons.fa mostravam. Agora o traco e
    sempre o da montagem que gerou os numeros da pagina.

 5. Falha vira None COM a causa no log do servidor (o original engolia a
    excecao em silencio). Sem tracy na instalacao, idem: o traco e acessorio.

 6. para_navegador(asm, amostra) e _pontos foram para o tracado.py do nucleo
    (passo 9): traco_da_amostra so junta remontar + tracado.para_navegador.

 7. Leitura sem caminho gravado: o original fazia Path('') e achava a PASTA
    entrada/ como se fosse o arquivo. Agora procura o trace de mesmo nome
    (.ab1, .abi ou .scf) dentro de entrada/, e so isso.

 8. Nada foi cortado: montar e para_navegador tem os mesmos dois chamadores
    (rotas api_traco e api_consultar); _motor virou executor_de_lote.motor_do_tracy.


POR QUE NAO NASCE CLASSE
========================

    O dono do tracy e o MotorTracy; o dono da pasta e do trim e o
    MontadorDeAmostra; os caminhos sao da PastasDoLote. Aqui so se acha
    arquivo e se chama na ordem. Ficam funcoes.
"""

import logging

from pathlib import Path

import lote as modulo_lote

import montagem as modulo_montagem

import tracado

from easycontig_web.processamento import executor_de_lote



REGISTRO = logging.getLogger('easycontig.cromatograma')

SUFIXO_DO_ASSEMBLE = '.json'  #* trabalho/<chave>.json: o que o `tracy assemble` grava, com os tracos ja alinhados

SUFIXO_DO_CONSENSO = '.cons.fa'  #* o FASTA do mesmo assemble (o arquivo_do_consenso da Montagem)




def leituras_em_disco(pastas, fatos):

    #* os traces das leituras da amostra que ainda estao no servidor, na ordem do relatorio.
    #! o relatorio guarda o caminho absoluto de quando o lote rodou; se o volume mudou de lugar,
    #! o trace de mesmo nome dentro de entrada/ do proprio lote ainda vale

    em_entrada = {trace.stem: trace for trace in modulo_lote.achar_traces(pastas.entrada)}

    caminhos = []

    for leitura in fatos.leituras:

        gravado = Path(leitura.arquivo) if leitura.arquivo else None

        if gravado is not None and gravado.is_absolute() and gravado.is_file():
            caminhos.append(gravado)

        elif leitura.nome in em_entrada:
            caminhos.append(em_entrada[leitura.nome])

    return caminhos




def remontar(cfg, lote_id, fatos):

    #* a Montagem da amostra de novo, a partir do que ficou em disco; None quando nao da (e a causa vai para o log).
    #! None e resposta, nao erro: a tela mostra os numeros e diz que nao ha traco

    if fatos is None:
        return None


    pastas = cfg.pastas_do_lote(lote_id)

    json_do_assemble = pastas.trabalho / f'{fatos.chave}{SUFIXO_DO_ASSEMBLE}'

    try:

        if json_do_assemble.is_file():

            consenso = pastas.trabalho / f'{fatos.chave}{SUFIXO_DO_CONSENSO}'

            return modulo_montagem.montagem_do_assemble(json_do_assemble, consenso if consenso.is_file() else None)


        leituras = leituras_em_disco(pastas, fatos)

        if len(leituras) < 2:
            REGISTRO.info('sem cromatograma para %s/%s: %d trace(s) em disco (relatorio: %d)', lote_id, fatos.chave, len(leituras), len(fatos.leituras))
            return None


        montador = modulo_montagem.MontadorDeAmostra(executor_de_lote.motor_do_tracy(), pastas.trabalho, trim=cfg.trim)

        return montador.montar(fatos.chave, leituras)


    except Exception as erro:  #* tracy, montagem, ferramenta ausente, json corrompido: o traco e acessorio, a pagina segue

        REGISTRO.warning('nao remontou %s/%s: %s: %s', lote_id, fatos.chave, type(erro).__name__, erro)

        return None




def traco_da_amostra(cfg, lote_id, fatos):

    #* o dict que o oficina.js desenha (tracado.para_navegador), ou None quando nao ha o que desenhar

    return tracado.para_navegador(remontar(cfg, lote_id, fatos))




def main():

    import json

    import relatorio as modulo_relatorio

    from easycontig_web import configuracao

    lote_id = input('Id de um lote ja processado (ou enter para sair): ').strip()

    if not lote_id:
        raise SystemExit(0)

    cfg = configuracao.carregar()

    pastas = cfg.pastas_do_lote(lote_id)

    lote = modulo_relatorio.RelatorioDoLote.de_json(pastas.relatorio_json.read_text(encoding='utf-8'))

    print(f'{lote!r}: ' + ', '.join(amostra.chave for amostra in lote.amostras))

    chave = input('Chave da amostra: ').strip()

    fatos = lote.amostra(chave)

    if fatos is None:
        raise SystemExit(f'nao ha amostra {chave!r} neste lote')


    montagem = remontar(cfg, lote_id, fatos)

    print(f'\n{montagem!r}')

    dados = tracado.para_navegador(montagem)

    if dados is None:
        print('sem cromatograma: os traces nao estao mais no servidor, ou a amostra nao tem duas leituras')
        return


    for leitura in dados['leituras']:
        print(f"  {leitura['nome']} ({leitura['sentido']}, {leitura['q_rotulo']} Q{leitura['q_medio']}): {len(leitura['bases'])} bases, {len(leitura['canais']['A']) // 2} pontos por canal")

    print(f"\n{dados['largura']} colunas, {dados['consenso_pb']} pb, {len(dados['mismatches'])} discordancia(s); JSON de {len(json.dumps(dados)) // 1024} kB")




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
