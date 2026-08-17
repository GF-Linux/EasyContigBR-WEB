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
