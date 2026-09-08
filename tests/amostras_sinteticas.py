"""
amostras_sinteticas.py — fabrica os .ab1 da suite na hora, com o tools/gerar_ab1.py.

    amostras() --> pasta temporaria da maquina --> amostra12_F_BTF2.ab1  (amplicon sorteado, semente 12)
                                                   amostra12_R_BTR2.ab1  (o reverso-complemento do mesmo amplicon)
                                                   amostra28_F_BTF2.ab1  (outro amplicon, semente 28)
                                                   amostra28_R_BTR2.ab1

COMO FUNCIONA
  1. os .ab1 REAIS do laboratorio sao dados de terceiro, nao publicados: nao entram no repositorio.
     E um caminho absoluto da maquina do autor derrubava 23 testes em qualquer outra maquina.
  2. entao os arquivos sao fabricados pelo gerador que ja existe no repositorio (tools/gerar_ab1.py):
     o resultado e ABIF de verdade, que o tracy abre como abriria um do sequenciador — so nao e biologia.
  3. o NOME decide a identidade da amostra (amostra12 e uma, amostra28 e outra): e por ele que o
     pareamento junta as leituras. O SENTIDO (F ou R) sai do DNA, nunca do nome — por isso o par
     F/R e gerado como reverso-complemento um do outro, e nao com duas sequencias sorteadas a toa:
     duas sequencias independentes com nome de par seriam um dado que mente.
  4. a semente e fixa: a mesma execucao da sempre o mesmo byte, e nenhum teste fica intermitente.

COMO USAR
  from amostras_sinteticas import amostras
  arquivos = amostras()              #* {nome: bytes}
  amostras()['amostra12_F_BTF2.ab1']

O QUE MUDOU
  - o modulo original fabricava no import; aqui e uma funcao (amostras()) que a fixture ab1 chama,
    e a fabricacao so acontece no primeiro teste que precisa dela.
"""

import random
import subprocess
import sys
import tempfile
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent  #* a raiz do repositorio, onde mora tools/gerar_ab1.py

GERADOR = RAIZ / 'tools' / 'gerar_ab1.py'

PASTA = Path(tempfile.gettempdir()) / 'easycontig-ab1-sinteticos'  #* uma pasta por maquina, apagada junto com o resto do temporario

COMPLEMENTO = {'A': 'T', 'T': 'A', 'C': 'G', 'G': 'C'}

AMOSTRAS = {'amostra12': ('BTF2', 'BTR2', 12), 'amostra28': ('BTF2', 'BTR2', 28)}  #* amostra -> (primer F, primer R, semente)

BASES = 420  #* comprimento do amplicon sorteado




def reverso_complemento(sequencia):

    return ''.join(COMPLEMENTO[base] for base in reversed(sequencia))




def fabricar(destino, sequencia, semente):

    #* chama o gerador por subprocesso, como um usuario faria na linha de comando

    subprocess.run([sys.executable, str(GERADOR), str(destino), '--sequencia', sequencia, '--semente', str(semente), '--amostra', destino.stem],
                   check=True, capture_output=True)




def amostras():

    #* fabrica o que falta e devolve {nome do arquivo: bytes}

    PASTA.mkdir(parents=True, exist_ok=True)

    arquivos = {}

    for amostra, (primer_f, primer_r, semente) in AMOSTRAS.items():

        adiante = PASTA / f'{amostra}_F_{primer_f}.ab1'

        reverso = PASTA / f'{amostra}_R_{primer_r}.ab1'

        if not (adiante.exists() and reverso.exists()):

            amplicon = ''.join(random.Random(semente).choice('ACGT') for _ in range(BASES))

            fabricar(adiante, amplicon, semente)

            fabricar(reverso, reverso_complemento(amplicon), semente)

        arquivos[adiante.name] = adiante.read_bytes()

        arquivos[reverso.name] = reverso.read_bytes()

    return arquivos




def main():

    #* python tests/amostras_sinteticas.py: fabrica e diz onde ficou

    for nome, conteudo in amostras().items():
        print(f'{PASTA / nome}  {len(conteudo)} bytes')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
