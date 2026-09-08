"""
sinteticas.py — fabrica uma corrida SINTETICA inteira (pares F/R) para medir uma maquina.

ISTO NAO E DADO BIOLOGICO. A sequencia e sorteada, os picos sao gaussianas
regulares e o ruido e sintetico. Serve para cronometrar a maquina, e para nada
alem disso: qualquer identificacao que saia daqui e lixo.

POR QUE EXISTE. O carga.py precisa enviar corridas de verdade para medir o
caminho real (tracy + BLAST + disco). Mas os .ab1 do laboratorio sao dado nao
publicado, e leva-los para uma maquina nova so para conseguir um numero e
atropelar uma decisao de governanca. Com isto, mede-se qualquer VPS sem que um
unico arquivo do laboratorio saia do lugar.

O F E O R SAO REVERSO-COMPLEMENTARES DE VERDADE. Sem isso o tracy falha depressa
e o arnes cronometra um caminho de fracasso rapido achando que mediu montagem:
o erro classico de medir com entrada invalida.


A CALIBRACAO, medida e cara (2026-08-07)
========================================

Duas grandezas do .ab1 estao SOLTAS uma da outra, e confundi-las produz um
numero falso convincente:

    tamanho do arquivo  -> pesa no tempo de ENVIO
    densidade do traco  -> pesa no CALCULO (rota do traco e tracy)

Medido num .ab1 real do laboratorio (F13719_am01_CAP08_16S_A09-B09_F.ab1):

    334 KB . 909 bases . 12 canais DATA (crus + analisados)
    DATA9 (analisado) = 16 302 pontos  ->  17,9 pontos por base

Ou seja: os 334 KB vem da QUANTIDADE DE CANAIS, nao de um traco denso. O
gerador escreve 4 canais, entao nao ha como casar as duas coisas. Casa-se a
densidade, porque e o calculo que dimensiona a maquina, e declara-se que a fase
de envio sai otimista (arquivo de ~95 KB contra 334 KB reais, ~3,5x).

O ERRO QUE ISTO EVITA. Tentando casar o TAMANHO, o passo subiu para 48
pontos/base, 2,7x o real. A rota do traco custa linear no numero de pontos,
entao inflou-se justamente a rota que estava sendo medida: 1 036 ms contra
110 ms. Ia-se reportar que a VPS entregava metade do prometido. O que desmentiu
foi medir a CPU das duas maquinas (a da VPS e 17% mais rapida) e o tempo roubado
pelo hipervisor (zero): a maquina estava certa, o dado e que estava errado.
Passo em 48 tambem estoura o PLOC do ABIF, que e int16: com 650 bases o teto e
32767/658 ~ 49 pontos por base.


COMO FUNCIONA
=============

    main()
      |-- argumentos: destino, --pares 40, --bases 650, --semente 20260807, --gerador tools/gerar_ab1.py
      |
      |-- conferir_gerador(gerador, bases)    o gerador existe? o PASSO cabe no int16 do PLOC?
      |                                       o texto do gerador ainda diz `PASSO = 18`? (senao: sai com 2)
      |
      '-- fabricar_corrida(destino, pares, bases, semente, gerador)
              para cada par i: sorteia a sequencia F; R = reverso-complemento
              SINT_am{i:02d}_F.ab1 e SINT_am{i:02d}_R.ab1 via subprocesso do gerador
              (--semente i, --amostra SINT-{i:02d})
              -> lista dos .ab1 gravados


COMO USAR
=========

    python3 tools/sinteticas.py /caminho/destino [--pares 40] [--bases 650]

Precisa do gerar_ab1.py (biblioteca padrao so) ao lado, ou apontado por
--gerador. Mesma semente, mesma corrida, byte a byte.


O QUE MUDOU DO ORIGINAL
=======================

 1. Estilo da casa: sem type hints, comentario de fim de linha, main() no fim.
    Os erros viram SystemExit(2) com a mesma mensagem, e o sucesso a mesma
    saida; os arquivos gerados sao identicos aos do original.
 2. O gerador agora se chama gerar_ab1.py (era make_ab1.py).
 3. A conferencia do gerador e a fabricacao sairam do main() para duas funcoes,
    para o main() ler como a lista do fluxograma acima.


POR QUE AQUI NAO NASCE CLASSE
=============================

E um roteiro de linha de comando que roda uma vez e termina: nao ha estado que
atravesse chamadas. Funcoes bastam.
"""

from pathlib import Path

import argparse

import random

import subprocess

import sys




PASSO_REAL = 18  #! 17,9 medido no .ab1 real; 18 e o inteiro. mexer aqui muda o custo do traco proporcionalmente

COMPLEMENTO = str.maketrans('ACGT', 'TGCA')

GERADOR_PADRAO = Path(__file__).parent / 'gerar_ab1.py'




def conferir_gerador(gerador, bases):

    #* o numero medido so e comparavel entre maquinas se o gerador for este e com esta densidade

    if not gerador.exists():
        raise SystemExit(f'não achei o gerador em {gerador}.\nEle vem do projeto Bancada (tools/gerar_ab1.py) e só usa a biblioteca padrão; copie-o para cá ou passe --gerador.')

    teto = 32767 // (bases + 8)  #* o PLOC do ABIF e int16: a ultima posicao de pico tem de caber

    if PASSO_REAL > teto:
        raise SystemExit(f'passo {PASSO_REAL} estoura o PLOC (int16) com {bases} bases; o teto é {teto}')

    if f'PASSO = {PASSO_REAL}' not in gerador.read_text():
        raise SystemExit(f'⚠️  {gerador} não está com PASSO = {PASSO_REAL}.\n    A densidade do traço é a calibração inteira deste arquivo (ver o cabeçalho); com outro valor o número medido não é comparável com os das outras máquinas.')




def fabricar_corrida(destino, pares, bases, semente, gerador):

    #* sorteia cada amplicon e chama o gerador duas vezes por par: F com a sequencia, R com o reverso-complemento

    destino.mkdir(parents=True, exist_ok=True)

    sorteio = random.Random(semente)

    for i in range(1, pares + 1):

        adiante = ''.join(sorteio.choice('ACGT') for _ in range(bases))

        reverso = adiante.translate(COMPLEMENTO)[::-1]

        for rotulo, sequencia in (('F', adiante), ('R', reverso)):

            alvo = destino / f'SINT_am{i:02d}_{rotulo}.ab1'

            subprocess.run([sys.executable, str(gerador), str(alvo), '--sequencia', sequencia, '--semente', str(i), '--amostra', f'SINT-{i:02d}'],
                           check=True, stdout=subprocess.DEVNULL)

    return sorted(destino.glob('*.ab1'))




def main():

    parser = argparse.ArgumentParser(description='Fabrica uma corrida sintética (pares F/R reverso-complementares) para medir uma máquina. Não é dado biológico.')

    parser.add_argument('destino', type=Path)

    parser.add_argument('--pares', type=int, default=40, help='pares F/R; 40 é o tamanho da corrida F13719')

    parser.add_argument('--bases', type=int, default=650)

    parser.add_argument('--semente', type=int, default=20260807)

    parser.add_argument('--gerador', type=Path, default=GERADOR_PADRAO)

    argumentos = parser.parse_args()


    try:
        conferir_gerador(argumentos.gerador, argumentos.bases)

    except SystemExit as erro:  #* a mensagem vai para o stderr e o codigo de saida e 2, como o carga.sh espera
        print(erro, file=sys.stderr)

        raise SystemExit(2)


    arquivos = fabricar_corrida(argumentos.destino, argumentos.pares, argumentos.bases, argumentos.semente, argumentos.gerador)

    total = sum(arquivo.stat().st_size for arquivo in arquivos)

    print(f'{len(arquivos)} arquivos, {total / 1024 / 1024:.1f} MB (média {total / len(arquivos) / 1024:.0f} KB)')

    print(f'⚠️  o .ab1 real do LHV tem 334 KB (12 canais); a fase de ENVIO sai otimista. A densidade do traço, que é o que pesa no cálculo, está calibrada em {PASSO_REAL} pontos/base.')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
