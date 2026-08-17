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
4. Os quatro nomes foram preservados (`amostra12_F_BTF2.ab1` e os outros três)
   porque o app lê o sentido F/R e o primer **a partir do nome** — trocar por
   `a.ab1` mudaria o que está sendo exercitado.

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
