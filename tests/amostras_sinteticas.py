#? AMOSTRAS SINTÉTICAS — Decisão sobre de onde vêm os .ab1 dos testes 17/08/2026
#!
#! 1. Antes, quatro arquivos de teste apontavam para um caminho ABSOLUTO da
#!    máquina do autor:
#!       /home/deck/Desktop/EasyContig-BR-Demo-Deck/ab1_por_especie/...
#! 2. Esse caminho só existe naquele computador. Fora dele, 23 testes falhavam
#!    com FileNotFoundError — e falhariam também numa integração contínua, na
#!    VPS ou na máquina de outra pessoa.
#! 3. Os .ab1 REAIS do laboratório não entram em repositório: são dados de
#!    terceiro, não publicados. Copiá-los resolveria o teste e criaria um
#!    problema maior.
#! 4. Então os arquivos são FABRICADOS na hora, pelo gerador que já existe no
#!    repositório (`tools/make_ab1.py`). O resultado é ABIF de verdade.
#!
#? O QUE O NOME DECIDE, E O QUE O DNA DECIDE
#!
#! 5. O SENTIDO (forward ou reverse) sai do DNA, nunca do nome. Duas leituras da
#!    mesma região em sentidos opostos são reverso-complementares, e isso se
#!    mede: `app/core/orientation.py`. O detector por nome acerta 0 de 36 nos
#!    arquivos reais do laboratório.
#! 6. O NOME decide outra coisa: QUAIS duas leituras são da mesma amostra. Isso
#!    o DNA não resolve — duas amostras da mesma espécie são quase idênticas, e
#!    um par cruzado chega a parecer melhor que o verdadeiro.
#! 7. Por isso os quatro nomes originais foram preservados: é deles que sai a
#!    identidade (`amostra12` é uma amostra, `amostra28` é outra).
#! 8. E por isso o par F/R é gerado como REVERSO-COMPLEMENTO um do outro, e não
#!    com duas sequências sorteadas à toa. Duas sequências independentes com
#!    nome de par seriam um dado que mente: pelo nome pareceriam par, pelo DNA
#!    não seriam. Um teste futuro de pareamento leria o dado errado e o defeito
#!    apareceria longe daqui.

from __future__ import annotations

import random
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

#* Uma pasta por máquina, apagada junto com o resto do temporário.
PASTA = Path(tempfile.gettempdir()) / "easycontig-ab1-sinteticos"

COMPLEMENTO = {"A": "T", "T": "A", "C": "G", "G": "C"}


def _reverso_complemento(seq: str) -> str:
    return "".join(COMPLEMENTO[b] for b in reversed(seq))


#* Uma amostra = um amplicon sorteado + a leitura dele ao contrário.
#* A semente é fixa: a mesma execução dá sempre o mesmo byte, e nenhum teste
#* fica intermitente.
AMOSTRAS = {"amostra12": ("BTF2", "BTR2", 12), "amostra28": ("BTF2", "BTR2", 28)}
BASES = 420


def _fabricar() -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    for amostra, (pf, pr, semente) in AMOSTRAS.items():
        adiante = PASTA / f"{amostra}_F_{pf}.ab1"
        reverso = PASTA / f"{amostra}_R_{pr}.ab1"
        if adiante.exists() and reverso.exists():
            continue
        rnd = random.Random(semente)
        amplicon = "".join(rnd.choice("ACGT") for _ in range(BASES))
        for destino, seq in ((adiante, amplicon),
                             (reverso, _reverso_complemento(amplicon))):
            subprocess.run(
                [sys.executable, str(RAIZ / "tools" / "make_ab1.py"), str(destino),
                 "--sequencia", seq, "--semente", str(semente),
                 "--amostra", destino.stem],
                check=True, capture_output=True,
            )


_fabricar()
