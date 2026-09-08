"""
gerar_ab1.py — fabrica um .ab1 sintetico (ABIF de verdade) so com a biblioteca padrao.

O .ab1 e o arquivo que sai do sequenciador Sanger: a marca 'ABIF', um cabecalho,
um diretorio de entradas (nome de 4 letras + numero) e, em cada entrada, um dado:
as quatro curvas do traco, a posicao de cada base chamada, as bases, a
qualidade, a ordem dos canais, o nome da amostra, a data da corrida. Este
arquivo escreve um desses do zero, com uma sequencia sorteada ou informada,
para a suite e o arnes de carga terem dado sem dado real: o .ab1 de verdade e
sequenciamento nao publicado do laboratorio e nao entra no repositorio.

NAO E BIOLOGIA. O traco e uma gaussiana por base, com vazamento pequeno para
os outros canais e fundo aleatorio; a qualidade sobe nas primeiras 40 bases e
cai nas ultimas 120, como um cromatograma tipico faz. Serve para exercitar o
desenho e o pipeline (o tracy le, chama bases e monta), nao para tirar
conclusao de nada.


COMO FUNCIONA
=============

    main()
      |-- argumentos: destino, --bases 650, --sequencia, --semente 7, --amostra SINTETICA-01
      |
      |-- gerar_sequencia(n, sorteio)              ou a --sequencia informada (so A, C, G, T e N)
      |-- gerar_qualidade(n, sorteio)              Phred 2..62: sobe em 40 bases, cai nas 120 finais
      |-- gerar_tracos(sequencia, qualidade, sorteio)
      |       PASSO amostras por base; um pico gaussiano de LARGURA no canal da base;
      |       1 a 5% do pico vaza para os outros canais; fundo ~N(18, 7); teto 32000 (int16)
      |       -> {A, C, G, T: [inteiros]}, picos (a amostra central de cada base)
      |
      '-- Escritor
              .texto(nome, numero, valor)      entrada de tipo char
              .shorts(nome, numero, valores)   entrada de int16 big-endian
              .gravar(destino)                 'ABIF' + cabecalho + dados (a partir do byte 128) + diretorio
                                               dado de ate 4 bytes mora dentro da propria entrada do diretorio

    entradas gravadas: DATA 9..12 (um canal por letra de ORDEM), PLOC 2, PBAS 2, PCON 2,
                       FWO_ 1, SMPL 1, RUND 1 e 2, RUNT 1 e 2


COMO USAR
=========

    python3 tools/gerar_ab1.py saida.ab1
    python3 tools/gerar_ab1.py saida.ab1 --bases 700 --semente 42
    python3 tools/gerar_ab1.py saida.ab1 --sequencia ATGCGC... --amostra amostra12_F

Mesma semente e mesmos argumentos: mesmo arquivo, byte a byte. E disso que a
suite (tests/amostras_sinteticas.py) e o sinteticas.py dependem.


O QUE MUDOU DO make_ab1.py ORIGINAL
===================================

 1. Nome em portugues e estilo da casa: sem type hints, sem docstring de funcao,
    comentario de fim de linha. A logica e a ordem dos sorteios sao as mesmas, e
    a saida e identica a do original para qualquer sequencia sem N.
 2. Uma --sequencia com N derrubava o gerador (KeyError: 'N' em gerar_tracos)
    apesar de a mensagem de erro prometer que N era aceito. Agora o pico do N e
    repartido em partes iguais entre os quatro canais, que e o que um N e num
    cromatograma: nenhum canal domina.
 3. PASSO continua escrito como `PASSO = 18` neste arquivo, de proposito: o
    sinteticas.py confere esse texto para garantir que a densidade do traco (a
    calibracao do arnes de carga) nao mudou.


POR QUE AQUI NASCE CLASSE
=========================

O Escritor guarda a lista de entradas enquanto o main() as acrescenta uma a
uma, e so no fim resolve os deslocamentos e escreve o arquivo: e estado que
atravessa varias chamadas. As tres funcoes de geracao sao puras (entrada ->
saida) e ficam funcoes.
"""

from pathlib import Path

import argparse

import math

import random

import struct




CABECALHO = '>H4sI2H3I'  #* versao, 'tdir', numero, tipo, tamanho da entrada, quantas entradas, tamanho do diretorio, onde ele comeca

ENTRADA = '>4sI2H4I'  #* nome, numero, tipo, tamanho do elemento, quantos elementos, tamanho do dado, deslocamento, reservado

TAM_ENTRADA = struct.calcsize(ENTRADA)  #* 28 bytes por entrada do diretorio

TIPO_CHAR = 2  #* codigo ABIF de entrada de texto

TIPO_SHORT = 4  #* codigo ABIF de entrada de inteiros de 16 bits

ORDEM = 'GATC'  #* qual DATA e qual base (FWO_): a ordem dos sequenciadores da ABI, a que o leitor espera

PASSO = 18  #! amostras do traco por base. e a calibracao do arnes de carga: o sinteticas.py confere este texto e recusa outro valor

LARGURA = 6.3  #* desvio da gaussiana de cada pico, em amostras

INICIO_DOS_DADOS = 128  #* onde os arquivos da ABI costumam comecar os dados; o leitor so usa o deslocamento declarado




def gerar_sequencia(n, sorteio):

    return ''.join(sorteio.choice('ACGT') for _ in range(n))




def gerar_qualidade(n, sorteio):

    #* Phred alto no meio e ruim nas pontas, a forma de uma corrida real. nao e modelo de nada

    qualidade = []

    for i in range(n):

        subida = min(1.0, i / 40)  #* rampa de subida nas primeiras ~40 bases

        queda = min(1.0, (n - i) / 120)  #* queda nas ultimas ~120

        base = 8 + 46 * subida * queda

        qualidade.append(max(2, min(62, int(base + sorteio.gauss(0, 3)))))

    return qualidade




def gerar_tracos(sequencia, qualidade, sorteio):

    #* uma gaussiana por base no canal dela, vazamento fraco nos outros e fundo aleatorio em todos

    n_amostras = PASSO * (len(sequencia) + 8)

    tracos = {canal: [0.0] * n_amostras for canal in 'ACGT'}

    picos = []

    for i, base in enumerate(sequencia):

        centro = PASSO * (i + 4)

        picos.append(centro)

        altura = 300 + 1200 * (qualidade[i] / 62) ** 1.5 + sorteio.gauss(0, 60)  #* pico mais baixo onde a qualidade e ruim: a leitura ruim PARECE ruim no desenho

        de = max(0, centro - PASSO)

        ate = min(n_amostras, centro + PASSO + 1)

        for x in range(de, ate):

            valor = altura * math.exp(-(((x - centro) / LARGURA) ** 2) / 2)

            if base == 'N':  #! o N nao tem canal: o pico se reparte igualmente entre os quatro (o original quebrava aqui)
                for canal in 'ACGT':
                    tracos[canal][x] += valor / 4

                continue

            tracos[base][x] += valor

            for outra in 'ACGT':  #* vazamento espectral: o corante aparece fraco nos outros canais; sem isso o desenho fica limpo demais para testar
                if outra != base:
                    tracos[outra][x] += valor * sorteio.uniform(0.01, 0.05)

    for canal in 'ACGT':
        for x in range(n_amostras):
            tracos[canal][x] += max(0.0, sorteio.gauss(18, 7))

    return {canal: [int(min(32000, v)) for v in tracos[canal]] for canal in 'ACGT'}, picos




class Escritor:
    #* acumula as entradas e monta o arquivo: dados primeiro, diretorio depois, cabecalho no inicio

    def __init__(self):

        self.entradas = []  #* (nome em bytes, numero, tipo, tamanho do elemento, quantos elementos, dado em bytes)


    def texto(self, nome, numero, valor):

        self.entradas.append((nome.encode(), numero, TIPO_CHAR, 1, len(valor), valor.encode()))


    def shorts(self, nome, numero, valores):

        dados = struct.pack(f'>{len(valores)}h', *valores)

        self.entradas.append((nome.encode(), numero, TIPO_SHORT, 2, len(valores), dados))


    def gravar(self, destino):

        corpo = bytearray()

        blocos = []  #* por entrada: o deslocamento do dado no arquivo, ou o proprio dado quando cabe em 4 bytes

        for *_, dados in self.entradas:

            if len(dados) <= 4:  #! dado de ate 4 bytes mora DENTRO da entrada, no lugar do deslocamento: e assim que o leitor procura; fora dali o FWO_ volta como lixo
                blocos.append(dados.ljust(4, b'\0'))

                continue

            deslocamento = INICIO_DOS_DADOS + len(corpo)

            corpo += dados

            while len(corpo) % 4:  #* alinhamento em 4 bytes: o leitor nao exige, os arquivos reais fazem
                corpo += b'\0'

            blocos.append(deslocamento)

        inicio_do_diretorio = INICIO_DOS_DADOS + len(corpo)

        diretorio = bytearray()

        for (nome, numero, tipo, tam_elemento, n_elementos, dados), bloco in zip(self.entradas, blocos):

            if isinstance(bloco, bytes):

                entrada = bytearray(struct.pack(ENTRADA, nome, numero, tipo, tam_elemento, n_elementos, len(dados), 0, 0))

                entrada[20:24] = bloco  #* o campo do deslocamento vira o proprio dado

                diretorio += entrada

            else:
                diretorio += struct.pack(ENTRADA, nome, numero, tipo, tam_elemento, n_elementos, len(dados), bloco, 0)

        cabecalho = struct.pack(CABECALHO, 101, b'tdir', 1, 1023, TAM_ENTRADA, len(self.entradas), len(diretorio), inicio_do_diretorio)  #* versao 101; o diretorio se descreve como uma entrada de tipo 1023 (raiz)

        saida = bytearray(b'ABIF')

        saida += cabecalho

        saida += b'\0' * (INICIO_DOS_DADOS - len(saida))

        saida += corpo

        saida += diretorio

        destino.write_bytes(bytes(saida))




def main():

    parser = argparse.ArgumentParser(description='Fabrica um .ab1 sintético (ABIF) para desenvolver e testar; não é biologia.')

    parser.add_argument('destino', type=Path)

    parser.add_argument('--bases', type=int, default=650)

    parser.add_argument('--sequencia', default=None, help='usa esta sequência em vez de sortear')

    parser.add_argument('--semente', type=int, default=7)

    parser.add_argument('--amostra', default='SINTETICA-01')

    argumentos = parser.parse_args()


    sorteio = random.Random(argumentos.semente)

    sequencia = (argumentos.sequencia or gerar_sequencia(argumentos.bases, sorteio)).upper()

    if set(sequencia) - set('ACGTN'):
        raise SystemExit('ERRO: a sequência só pode ter A, C, G, T ou N.')


    qualidade = gerar_qualidade(len(sequencia), sorteio)

    tracos, picos = gerar_tracos(sequencia, qualidade, sorteio)


    escritor = Escritor()

    for i, base in enumerate(ORDEM):
        escritor.shorts('DATA', 9 + i, tracos[base])

    escritor.shorts('PLOC', 2, picos)

    escritor.texto('PBAS', 2, sequencia)

    escritor.texto('PCON', 2, ''.join(chr(q) for q in qualidade))

    escritor.texto('FWO_', 1, ORDEM)

    escritor.texto('SMPL', 1, argumentos.amostra)

    escritor.texto('RUND', 1, '2026-07-30')

    escritor.texto('RUND', 2, '2026-07-30')

    escritor.texto('RUNT', 1, '00:00:00')

    escritor.texto('RUNT', 2, '00:20:00')


    argumentos.destino.parent.mkdir(parents=True, exist_ok=True)

    escritor.gravar(argumentos.destino)


    print(f'gravado : {argumentos.destino}  ({argumentos.destino.stat().st_size / 1024:.0f} KB)')

    print(f'bases   : {len(sequencia)}   amostras: {len(tracos["A"])}')

    print(f'Phred   : mín {min(qualidade)}  média {sum(qualidade) / len(qualidade):.0f}  máx {max(qualidade)}')

    print('AVISO   : dado sintético. Não é biologia — serve para exercitar o desenho.')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
