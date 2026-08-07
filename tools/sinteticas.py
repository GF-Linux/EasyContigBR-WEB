#!/usr/bin/env python3
"""
sinteticas.py — fabrica uma corrida SINTÉTICA para medir uma máquina.

⚠️ ISTO NÃO É DADO BIOLÓGICO. A sequência é aleatória, os picos são gaussianas
regulares e o ruído é sintético. Serve para cronometrar a máquina, e para
NADA além disso: qualquer identificação que saia daqui é lixo.

**Por que existe.** O `carga.py` precisa enviar corridas de verdade para medir
o caminho real (tracy + BLAST + disco). Mas os `.ab1` do LHV são dado não
publicado, e levá-los para uma máquina nova só para conseguir um número é
atropelar uma decisão de governança que não é do agente. Com isto, mede-se
qualquer VPS sem que um único arquivo do laboratório saia do lugar.

**O F e o R são reverso-complementares DE VERDADE.** Sem isso o Tracy falha
depressa e o arnês cronometra um caminho de fracasso rápido achando que mediu
montagem — erro clássico de medir com entrada inválida.

────────────────────────────────────────────────────────────────────────────
A CALIBRAÇÃO, que foi medida e custou caro (2026-08-07)
────────────────────────────────────────────────────────────────────────────

Duas grandezas do `.ab1` estão SOLTAS uma da outra, e confundi-las produz um
número falso convincente:

  tamanho do arquivo  → pesa no tempo de ENVIO
  densidade do traço  → pesa no CÁLCULO (endpoint do traço e Tracy)

Medido num `.ab1` real do LHV (`F13719_am01_CAP08_16S_A09-B09_F.ab1`):

    334 KB · 909 bases · **12 canais DATA** (crus + analisados)
    DATA9 (analisado) = 16 302 pontos  →  **17,9 pontos por base**

Ou seja: os 334 KB vêm da QUANTIDADE DE CANAIS, não de um traço denso. Este
gerador escreve 4 canais, então **não há como casar as duas coisas**. Casa-se a
densidade, porque é o cálculo que dimensiona a máquina, e declara-se que a fase
de envio sai otimista (arquivo de ~95 KB contra 334 KB reais, ~3,5×).

⚠️ **O ERRO QUE ISTO EVITA.** Tentando casar o TAMANHO, subi o passo para 48
pontos/base — 2,7× o real. O endpoint do traço custa linear no número de
pontos, então **inflei justamente a rota que estava medindo**: 1 036 ms contra
110 ms. Ia reportar que a VPS entregava metade do prometido. O que desmentiu
foi medir a CPU das duas máquinas (a da VPS é 17% mais rápida) e o tempo
roubado pelo hipervisor (zero) — a máquina estava certa, o dado é que estava
errado. Passo em 48 também estoura o `PLOC` do ABIF, que é **int16**: com 650
bases o teto é 32767/658 ≈ 49 pontos por base.

Uso:
    python3 tools/sinteticas.py /caminho/destino [--pares 40] [--bases 650]

Precisa do `make_ab1.py` (projeto Bancada, biblioteca padrão só) ao lado ou
apontado por --gerador.
"""
from __future__ import annotations

import argparse
import random
import subprocess
import sys
from pathlib import Path

# 17,9 medido; 18 é o inteiro. Mexer aqui muda o custo do traço proporcionalmente.
PASSO_REAL = 18
COMP = str.maketrans("ACGT", "TGCA")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("destino", type=Path)
    p.add_argument("--pares", type=int, default=40,
                   help="pares F/R; 40 é o tamanho da corrida F13719")
    p.add_argument("--bases", type=int, default=650)
    p.add_argument("--semente", type=int, default=20260807)
    p.add_argument("--gerador", type=Path,
                   default=Path(__file__).parent / "make_ab1.py")
    args = p.parse_args()

    if not args.gerador.exists():
        print(f"não achei o gerador em {args.gerador}.\n"
              f"Ele vem do projeto Bancada (tools/make_ab1.py) e só usa a "
              f"biblioteca padrão; copie-o para cá ou passe --gerador.",
              file=sys.stderr)
        return 2

    teto = 32767 // (args.bases + 8)
    if PASSO_REAL > teto:
        print(f"passo {PASSO_REAL} estoura o PLOC (int16) com {args.bases} "
              f"bases; o teto é {teto}", file=sys.stderr)
        return 2

    texto = args.gerador.read_text()
    if f"PASSO = {PASSO_REAL}" not in texto:
        print(f"⚠️  {args.gerador} não está com PASSO = {PASSO_REAL}.\n"
              f"    A densidade do traço é a calibração inteira deste arquivo "
              f"(ver o cabeçalho); com outro valor o número medido não é "
              f"comparável com os das outras máquinas.", file=sys.stderr)
        return 2

    args.destino.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(args.semente)
    for i in range(1, args.pares + 1):
        seq = "".join(rnd.choice("ACGT") for _ in range(args.bases))
        rev = seq.translate(COMP)[::-1]
        for rotulo, s in (("F", seq), ("R", rev)):
            alvo = args.destino / f"SINT_am{i:02d}_{rotulo}.ab1"
            subprocess.run([sys.executable, str(args.gerador), str(alvo),
                            "--sequencia", s, "--semente", str(i),
                            "--amostra", f"SINT-{i:02d}"],
                           check=True, stdout=subprocess.DEVNULL)

    arqs = sorted(args.destino.glob("*.ab1"))
    total = sum(a.stat().st_size for a in arqs)
    print(f"{len(arqs)} arquivos, {total/1024/1024:.1f} MB "
          f"(média {total/len(arqs)/1024:.0f} KB)")
    print(f"⚠️  o .ab1 real do LHV tem 334 KB (12 canais); a fase de ENVIO "
          f"sai otimista. A densidade do traço, que é o que pesa no cálculo, "
          f"está calibrada em {PASSO_REAL} pontos/base.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
