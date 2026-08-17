# Decisão sobre as 23 falhas da suíte 17/08/2026

1. A suíte vinha com **23 falhas** desde o commit `46eb91a`, que as anunciava no
   título: *"WIP, suíte vermelha"*.
2. Ao rodar a suíte inteira pela primeira vez fora do computador do autor, as 23
   se revelaram **uma causa só**, não vinte e três.
3. Quatro arquivos de teste apontavam para um **caminho absoluto da máquina do
   autor**:

   ```
   /home/deck/Desktop/EasyContig-BR-Demo-Deck/ab1_por_especie/Babesia_vogeli
   ```

4. Esse caminho existe **só naquele computador**. Em qualquer outro — nesta
   máquina, na VPS, numa integração contínua, no computador de outra pessoa — os
   23 quebravam com `FileNotFoundError` antes de testar coisa alguma.
5. O defeito não era o teste estar errado: era ele **não poder rodar**.

## O que foi feito

1. Os `.ab1` passam a ser **fabricados na hora**, pelo gerador que já existia no
   repositório (`tools/make_ab1.py`). Ver `tests/amostras_sinteticas.py`.
2. O resultado é ABIF de verdade — o Biopython o abre como abriria um arquivo de
   sequenciador.
3. Semente fixa por arquivo: a mesma execução produz sempre o mesmo byte, e
   nenhum teste fica intermitente.
4. Os quatro nomes foram preservados, mas **não pelo motivo que escrevi na
   primeira versão desta nota**. Eu disse que o app lê o sentido F/R do nome, e
   isso está errado — o autor corrigiu, e o código confirma.

### O que o nome decide, e o que o DNA decide

5. O **sentido** (forward ou reverse) sai do DNA. Duas leituras da mesma região
   em sentidos opostos são reverso-complementares, e isso se mede
   (`app/core/orientation.py`). O detector por nome acerta **0 de 36** nos
   arquivos reais do laboratório: em `amostra12_F_BTF2.ab1` a marca está no meio
   do nome, e quando acerta é por acidente.
6. O **nome** decide outra coisa: quais duas leituras são da **mesma amostra**.
   Isso o DNA não resolve — duas amostras da mesma espécie são quase idênticas, e
   um par cruzado chega a parecer melhor que o verdadeiro.
7. É por isso que os nomes foram preservados: deles sai a identidade
   (`amostra12` é uma amostra, `amostra28` é outra).
8. E é por isso que o par F/R passou a ser gerado como **reverso-complemento** um
   do outro, e não com duas sequências sorteadas à toa. Duas sequências
   independentes com nome de par seriam um dado que mente: pelo nome pareceriam
   par, pelo DNA não seriam.

Medido depois da correção:

```
F x reverso-complemento(R)      420/420 bases iguais (100 %)   -> é par
F(amostra12) x F(amostra28)     24 % iguais                    -> acaso, como deve
```

## O que NÃO foi feito, e por quê

1. Copiar os `.ab1` reais do laboratório para o repositório resolveria o teste e
   criaria um problema maior: são dados de terceiro, não publicados.
2. Nenhum destes 23 testes afirma algo sobre espécie. Eles exercitam o caminho
   do arquivo — upload, gravação, fila, recusa, fronteira entre contas. Dado
   sintético serve para isso.

## Resultado

```
antes:  398 passam · 23 falham · 15 pulados
depois: 421 passam ·  0 falham · 15 pulados
```

Verificado duas vezes: com a pasta temporária apagada (fabricação do zero) e com
ela presente (uso do cache). O mesmo número nas duas.

## Os 15 pulados NÃO são o mesmo problema

Eles dizem o motivo e degradam com elegância — `makeblastdb não está nesta
máquina`, `Playwright não instalado`, `tracy/blastn/bancos não configurados`,
`o lote real de 40 amostras não está nesta máquina`. É a diferença entre um
teste que sabe que não pode rodar e um que estoura.

#! Fica a lição para teste novo: caminho absoluto de máquina pessoal dentro de
#! um teste é uma falha que só aparece no computador de outra pessoa — e aparece
#! como 23 defeitos, escondendo que era um.


# Decisão sobre exercitar a tese do produto 17/08/2026

1. O leia-me do site afirma que o programa descobre forward e reverse **lendo o
   DNA**, não o nome do arquivo. É a tese do produto.
2. Nenhum teste exercitava isso. O `test_leiame` confere que a **página diz** a
   frase; ninguém conferia que o programa **faz** o que ela promete.
3. Os testes que rodam o pipeline de verdade ficavam pulados por falta de
   `tracy`, `blastn` e dos bancos.

## O que foi feito

1. `tracy` v0.8.9 e BLAST+ 2.17.0 instalados em `~/.local`, sem tocar no
   sistema. Os bancos vieram do pacote do Deck.
2. Isso destravou 2 testes que estavam pulados por falta do `makeblastdb`.
3. Foi escrito `tests/test_orientacao_sai_do_dna.py`, com quatro verificações
   que rodam **em qualquer máquina** — orientação é reverso-complementaridade,
   que é geometria da sequência e não depende de biologia:
   - um par de verdade é reconhecido pelo DNA;
   - duas amostras diferentes não viram par;
   - o par sobrevive a nome trocado de propósito;
   - a distância entre par e não-par é larga, não apertada.

Medido no par sintético: `1.0` invertido contra `0.0` no mesmo sentido.

## O limite, medido e declarado

1. Sem referência, o detector afirma que as duas leituras estão em sentidos
   OPOSTOS — e isso ele tira do DNA.
2. Qual das duas é a forward em termos ABSOLUTOS ele não decide sozinho: compara
   com as referências 18S, que estão na orientação canônica.
3. Por isso o teste do nome trocado afirma o que é verdade (o PAR sobrevive) e
   não afirma o que seria falso (que o rótulo absoluto sobreviveria). DNA
   sorteado não bate com referência nenhuma.

## Como deixar esta máquina rodando o pipeline inteiro

```bash
curl -fL -o ~/.local/bin/tracy \
  https://github.com/gear-genomics/tracy/releases/download/v0.8.9/tracy-v0.8.9-linux-amd64
chmod +x ~/.local/bin/tracy

# BLAST+ do NCBI, binário estático, sem root
curl -fLO https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/LATEST/ncbi-blast-2.17.0+-x64-linux.tar.gz

export EASYCONTIG_TRACY_BIN=~/.local/bin/tracy
export EASYCONTIG_BLAST_BIN=~/.local/bin
export EASYCONTIG_DB_18S=~/projetos/bancos-easycontig/referencias_18S
export EASYCONTIG_DB_16S=~/projetos/bancos-easycontig/referencias_16S
```

Resultado: `427 passam` com as ferramentas, `425 passam` sem elas. Verde nos dois.

#! O `test_executor` continua exigindo `.ab1` REAIS, e pula sem eles. Ele confere
#! a ESPÉCIE identificada, e DNA sorteado nunca vai dar Babesia vogeli. Rodá-lo
#! com dado sintético produziria uma falha que não é defeito do programa —
#! aconteceu comigo, e foi assim que este limite ficou claro.
