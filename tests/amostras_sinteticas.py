#? AMOSTRAS SINTÉTICAS — Decisão sobre de onde vêm os .ab1 dos testes 17/08/2026
#!
#! 1. Antes, quatro arquivos de teste apontavam para um caminho ABSOLUTO da
#!    máquina do autor:
#!       /home/deck/Desktop/EasyContig-BR-Demo-Deck/ab1_por_especie/...
#! 2. Esse caminho só existe naquele computador. Fora dele, 23 testes falhavam
#!    com FileNotFoundError — e falhariam também em qualquer integração
#!    contínua, na VPS ou na máquina de outra pessoa.
#! 3. Os .ab1 REAIS do laboratório não entram em repositório: são dados de
#!    terceiro, não publicados. Copiá-los para cá resolveria o teste e criaria
#!    um problema maior.
#! 4. Então os arquivos são FABRICADOS na hora, pelo gerador que já existe no
#!    repositório (`tools/make_ab1.py`). O resultado é ABIF de verdade — o
#!    Biopython o abre como abriria um de sequenciador.
#! 5. NÃO é biologia. A sequência é sorteada e os picos são gaussianas. Serve
#!    para exercitar o caminho do arquivo (upload, gravação, fila, recusa), que
#!    é o que estes testes verificam. Nenhum deles afirma nada sobre espécie.
#! 6. A semente é fixa por arquivo: a mesma execução dá sempre o mesmo byte, e
#!    um teste que dependa do conteúdo não fica intermitente.

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

#* Os quatro nomes que os testes pedem. O padrão do nome importa: o app lê o
#* sentido (F/R) e o primer a partir dele, então trocar por "a.ab1" mudaria o
#* que está sendo exercitado.
NOMES = {
    "amostra12_F_BTF2.ab1": 12,
    "amostra12_R_BTR2.ab1": 13,
    "amostra28_F_BTF2.ab1": 28,
    "amostra28_R_BTR2.ab1": 29,
}

#* Uma pasta por execução da suíte, apagada com o resto do temporário.
PASTA = Path(tempfile.gettempdir()) / "easycontig-ab1-sinteticos"


def _fabricar() -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    for nome, semente in NOMES.items():
        destino = PASTA / nome
        if destino.exists() and destino.stat().st_size > 0:
            continue
        subprocess.run(
            [sys.executable, str(RAIZ / "tools" / "make_ab1.py"), str(destino),
             "--bases", "420", "--semente", str(semente), "--amostra", nome[:-4]],
            check=True, capture_output=True,
        )


_fabricar()
