#!/usr/bin/env python3
"""Gera um `.ab1` sintético, para desenvolver e testar o cromatograma.

**Por que isto existe.** Os 36 `.ab1` reais do LHV são dados não publicados e
não entram em repositório, mockup ou material que circula. Sem um arquivo de
exemplo não dá para desenvolver o visualizador — então se fabrica um.

O arquivo produzido é um ABIF de verdade: o Biopython o abre com
`SeqIO.read(..., "abi")` como abriria um de sequenciador. O que ele **não** é:
dado biológico. A sequência é aleatória (ou dada), os picos são gaussianas
regulares e o ruído é sintético. Serve para exercitar o desenho, não para
tirar conclusão de nada.

Uso:
    python3 tools/make_ab1.py saida.ab1
    python3 tools/make_ab1.py saida.ab1 --bases 700 --semente 42
    python3 tools/make_ab1.py saida.ab1 --sequencia ATGCGC...
"""

from __future__ import annotations

import argparse
import math
import random
import struct
from pathlib import Path

# Formato ABIF, conforme Bio/SeqIO/AbiIO.py lê:
#   marcador "ABIF" + cabeçalho de 26 bytes + diretório de entradas de 28 bytes.
CABECALHO = ">H4sI2H3I"
ENTRADA = ">4sI2H4I"
TAM_ENTRADA = struct.calcsize(ENTRADA)  # 28

# Códigos de tipo do ABIF usados aqui.
TIPO_CHAR = 2
TIPO_SHORT = 4

# Ordem dos canais. O FWO_ diz qual DATA corresponde a qual base; "GATC" é o
# que os sequenciadores da ABI usam, e é o que o leitor espera encontrar.
ORDEM = "GATC"

# Espaçamento entre picos, em amostras. Sanger real fica por volta disso.
PASSO = 18
LARGURA = 6.3  # desvio da gaussiana de cada pico


def gerar_sequencia(n: int, rnd: random.Random) -> str:
    return "".join(rnd.choice("ACGT") for _ in range(n))


def gerar_qualidade(n: int, rnd: random.Random) -> list[int]:
    """Phred alto no meio e ruim nas pontas — a forma de uma corrida real.

    Não é modelo de nada: é só para o visualizador ter o que desenhar e para o
    corte por qualidade ter em que morder.
    """
    q = []
    for i in range(n):
        # rampa de subida nos primeiros ~40, queda nos últimos ~120
        subida = min(1.0, i / 40)
        queda = min(1.0, (n - i) / 120)
        base = 8 + 46 * subida * queda
        q.append(max(2, min(62, int(base + rnd.gauss(0, 3)))))
    return q


def gerar_tracos(
    seq: str, qualidade: list[int], rnd: random.Random
) -> tuple[dict[str, list[int]], list[int]]:
    """Uma gaussiana por base no canal dela, mais ruído de fundo nos outros."""
    n_amostras = PASSO * (len(seq) + 8)
    tracos = {b: [0.0] * n_amostras for b in "ACGT"}
    picos: list[int] = []

    for i, base in enumerate(seq):
        centro = PASSO * (i + 4)
        picos.append(centro)
        # Pico mais baixo onde a qualidade é ruim: é o que faz a leitura ruim
        # *parecer* ruim no desenho, em vez de só ter um número pequeno ao lado.
        altura = 300 + 1200 * (qualidade[i] / 62) ** 1.5 + rnd.gauss(0, 60)

        de = max(0, centro - PASSO)
        ate = min(n_amostras, centro + PASSO + 1)
        for x in range(de, ate):
            valor = altura * math.exp(-(((x - centro) / LARGURA) ** 2) / 2)
            tracos[base][x] += valor
            # Vazamento espectral: o sinal de um corante aparece fraco nos
            # outros canais. Sem isso o desenho fica limpo demais para ser útil
            # como teste.
            for outra in "ACGT":
                if outra != base:
                    tracos[outra][x] += valor * rnd.uniform(0.01, 0.05)

    for b in "ACGT":
        for x in range(n_amostras):
            tracos[b][x] += max(0.0, rnd.gauss(18, 7))

    return {b: [int(min(32000, v)) for v in tracos[b]] for b in "ACGT"}, picos


class Escritor:
    """Monta o arquivo: dados primeiro, diretório depois, cabeçalho no fim."""

    def __init__(self) -> None:
        self.entradas: list[tuple[bytes, int, int, int, int, bytes]] = []

    def texto(self, nome: str, numero: int, valor: str) -> None:
        self.entradas.append((nome.encode(), numero, TIPO_CHAR, 1, len(valor), valor.encode()))

    def shorts(self, nome: str, numero: int, valores: list[int]) -> None:
        dados = struct.pack(f">{len(valores)}h", *valores)
        self.entradas.append((nome.encode(), numero, TIPO_SHORT, 2, len(valores), dados))

    def gravar(self, destino: Path) -> None:
        corpo = bytearray()
        # O diretório vive depois dos dados; 128 é onde os arquivos da ABI
        # costumam começar os dados, e o leitor só usa o offset declarado.
        inicio_dados = 128
        blocos: list[int | bytes] = []

        for *_, dados in self.entradas:
            # **Dado de até 4 bytes mora DENTRO da entrada do diretório**, no
            # lugar do campo de offset — é assim que o leitor do Biopython
            # procura (`if data_size <= 4: data_offset = tag_offset + 20`).
            # Escrever fora faz o `FWO_` voltar como lixo.
            if len(dados) <= 4:
                blocos.append(dados.ljust(4, b"\0"))
                continue
            deslocamento = inicio_dados + len(corpo)
            corpo += dados
            # Alinhamento em 4 bytes: não é exigido pelo leitor, mas é o que os
            # arquivos reais fazem, e custa nada.
            while len(corpo) % 4:
                corpo += b"\0"
            blocos.append(deslocamento)

        inicio_diretorio = inicio_dados + len(corpo)
        diretorio = bytearray()
        for (nome, numero, tipo, tam_elem, n_elem, dados), bloco in zip(self.entradas, blocos):
            if isinstance(bloco, bytes):
                entrada = bytearray(
                    struct.pack(ENTRADA, nome, numero, tipo, tam_elem, n_elem, len(dados), 0, 0)
                )
                entrada[20:24] = bloco  # o campo de offset vira o próprio dado
                diretorio += entrada
            else:
                diretorio += struct.pack(
                    ENTRADA, nome, numero, tipo, tam_elem, n_elem, len(dados), bloco, 0
                )

        cabecalho = struct.pack(
            CABECALHO,
            101,               # versão
            b"tdir",           # o diretório se descreve como uma entrada
            1,
            1023,              # tipo "root"
            TAM_ENTRADA,       # header[4] — tamanho de cada entrada
            len(self.entradas),  # header[5] — quantas entradas
            len(diretorio),
            inicio_diretorio,  # header[7] — onde o diretório começa
        )

        saida = bytearray(b"ABIF")
        saida += cabecalho
        saida += b"\0" * (inicio_dados - len(saida))
        saida += corpo
        saida += diretorio
        destino.write_bytes(bytes(saida))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("destino", type=Path)
    p.add_argument("--bases", type=int, default=650)
    p.add_argument("--sequencia", default=None, help="usa esta sequência em vez de sortear")
    p.add_argument("--semente", type=int, default=7)
    p.add_argument("--amostra", default="SINTETICA-01")
    args = p.parse_args()

    rnd = random.Random(args.semente)
    seq = (args.sequencia or gerar_sequencia(args.bases, rnd)).upper()
    if set(seq) - set("ACGTN"):
        raise SystemExit("ERRO: a sequência só pode ter A, C, G, T ou N.")

    qualidade = gerar_qualidade(len(seq), rnd)
    tracos, picos = gerar_tracos(seq, qualidade, rnd)

    e = Escritor()
    for i, base in enumerate(ORDEM):
        e.shorts("DATA", 9 + i, tracos[base])
    e.shorts("PLOC", 2, picos)
    e.texto("PBAS", 2, seq)
    e.texto("PCON", 2, "".join(chr(q) for q in qualidade))
    e.texto("FWO_", 1, ORDEM)
    e.texto("SMPL", 1, args.amostra)
    e.texto("RUND", 1, "2026-07-30")
    e.texto("RUND", 2, "2026-07-30")
    e.texto("RUNT", 1, "00:00:00")
    e.texto("RUNT", 2, "00:20:00")

    args.destino.parent.mkdir(parents=True, exist_ok=True)
    e.gravar(args.destino)

    print(f"gravado : {args.destino}  ({args.destino.stat().st_size / 1024:.0f} KB)")
    print(f"bases   : {len(seq)}   amostras: {len(tracos['A'])}")
    print(f"Phred   : mín {min(qualidade)}  média {sum(qualidade) / len(qualidade):.0f}  máx {max(qualidade)}")
    print("AVISO   : dado sintético. Não é biologia — serve para exercitar o desenho.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
