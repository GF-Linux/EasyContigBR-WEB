# O que NAO foi portado do nucleo original — e por que

Regra do Jared (02/09/2026): **sem chamador na web, nao porta.** Se uma
funcao nao tem quem a chame, ou foi enviada junto por engano ou foi
descontinuada e ficou como resquicio. Este arquivo e a referencia: se algum
dia algo quebrar e a suspeita cair em algo que "sumiu", a lista esta aqui,
com a linha exata no original.

Onde esta o original inteiro:

- copia pristina: `~/incoming/deck/easycontig-web/nucleo/app/core/`
- backup da VPS (versao em producao) — a ser feito pelo Jared antes da troca
- GitHub do EasyContigBR-WEB (historico do git)


## Como foi medido

`grep -rnw` pelo nome de cada `def`/constante em todo `.py` de
`EasyContigBR-WEB/` (fora `.venv/` e `build/`), descontando a linha da
propria definicao. Dois graus:

- **producao**: chamador em `easycontig_web/` ou em outro modulo do nucleo
  que a web usa;
- **so teste**: chamador apenas em `tests/`.

A web importa do nucleo, em producao, so isto (roadmap secao 2.1, conferido
de novo em 02/09/2026):

    executor_de_lote.py     app.core.batch  (find_traces, run_batch)
                            app.core.report (BatchReport, render_html)
                            app.core.tracy_engine (TracyEngine)
    executor_de_arvore.py   app.core.phylogeny
    dados/cromatograma.py   app.core.assembly (build_pair_assembly, chromatogram_data_columns, parse_assembly)
                            app.core.tracy_engine (TracyEngine)
    dados/leitura_de_amostras.py   le o relatorio.json gravado pelo executor (dict, chaves em ingles)

E o proprio nucleo, por dentro, so chama de `TracyEngine`: `assemble`
(batch.py:134), `consensus` e `basecall` (assembly.py, build_pair_assembly).


## Achado que mudou o roadmap

**`orientation.py` nunca foi chamado pelo pipeline da web.** O `batch.py`
original decide F/R so pelo NOME (`pairing.pair_traces`, batch.py:115). O
leia-me do site promete "lendo o DNA" e o teste
`tests/test_orientacao_sai_do_dna.py` prova que o MODULO funciona — mas
ninguem o ligava a montagem. O roadmap ja tinha visto isso (secao 2.5:
"unico consumidor no universo: um teste") e recomendou a opcao (a) do Passo
10: o montador passa a usa-lo. Foi o que o Passo 7 fez: `montagem.py`
chama `orientacao.analisar()` antes de decidir consensus/assemble. E uma
MUDANCA DE COMPORTAMENTO em relacao a web em producao — a desejada.

Consequencia para os passos restantes do roadmap:

| passo | como estava | como fica |
|---|---|---|
| 8 — batch absorve o cache | `run_batch(cache_dir=...)` com `load_cached`/`save_cached` | o cache nao tem chamador na web ("sem cache por desenho"): `lote.py` nasce sem ele |
| 9 — `set_base` recompoe | edicao de base na grade Qt | `set_base` nao foi portado |
| 10 — interface morta | deletar `base_columns`, `trace_x_at_column`; decidir `orientation.py` | ja feito por omissao; `orientation.py` -> `orientacao.py`, chamado pelo montador |


## Nao portado, modulo a modulo

Linhas referem-se a copia pristina.

### assembly.py (350 linhas) -> montagem.py

| item | linhas | o que fazia | chamador |
|---|---|---|---|
| `Assembly.set_base` | 57-66 | troca uma base de uma leitura na grade e remonta o consenso | so a janela Qt da demo (`assembly_window.py`) |
| `Assembly.recompute_consensus` | 68-85 | voto majoritario por coluna | `set_base`; o mesmo voto vive em `montagem.votar_consenso` (usado por `montagem_do_par`) |
| `Assembly.trace_x_at_column` | 114-131 | coordenada x do cromatograma numa coluna | nenhum |
| `base_columns` | 134-139 | colunas em que a leitura tem base | nenhum |
| `chromatogram_data` | 232-250 | cromatograma em coordenada de coluna | web `/oficina` (`cromatograma.py`) — PORTADO em `tracado.py` (`traco_com_sequencia`), secao da web abaixo: e desenho, nao montagem |
| `chromatogram_data_columns` | 163-229 | idem, por coluna | idem (`traco_em_colunas`) |
| `_estimate_offset` | 142-160 | ajuda dos dois acima | idem (`deslocamento_no_traco`) |

`Assembly.depth` (33-34) foi portado como `Montagem.profundidade`: e uma
linha e o `__repr__` a usa.

### batch.py (153 linhas) -> lote.py (Passo 8)

| item | linhas | o que fazia | chamador |
|---|---|---|---|
| `fingerprint` | 27-43 | identidade nome+tamanho do lote, para o cache | so a demo (`main_window.py:904-910`) |
| `cache_path` | 46-47 | caminho do `report_<fp>.json` | so `load_cached`/`save_cached` |
| `load_cached` | 50-57 | le relatorio do cache | so a demo |
| `save_cached` | 60-64 | grava relatorio no cache | so a demo |
| `TRACE_EXT` | 23 | `{'.ab1', '.abi', '.scf'}` | so `main_window.py` (a web lista os traces por conta propria) |

### identify.py (395 linhas) -> identificacao.py

Saiu tudo que era da demo do RIW (arvore de um par e identificacao com
NCBI remoto), a pedido do Jared em 01/09/2026 — ver
`identificacao.antes-do-corte.py` no scratchpad para a versao portada antes
do corte.

| item | linhas | o que fazia | chamador |
|---|---|---|---|
| `identify` | 359-395 | identificacao completa de UMA amostra (local + NCBI + arvore) | so a demo |
| `place_phylo` | 275-344 | arvore de um par amostra x congeneres | so `identify` |
| `Identification` | 348-356 | resultado do `identify` | so a demo |
| `NoLocalHits` | 116-130 | excecao do `identify` | so a demo |
| `_REF_DISTANCE_CACHE` | 192 | `parents[2]/demo/refs/...` — caminho que nao existe no pacote instalado | so `place_phylo` |
| `_make_aligner`, `_alignment_distance`, `_pair_key`, `_load_ref_distance_cache`, `_tip_label` | 195-272 | ajuda de `place_phylo` | so `place_phylo` |

### ncbi.py (256 linhas) -> cliente_ncbi.py

Cliente unico da web (decisao do Jared): so o que
`bancos_de_referencia.montar()` precisa (esearch paginado + efetch).

| item | linhas | o que fazia | chamador |
|---|---|---|---|
| `BLAST_URL` | 24 | URL do BLAST remoto | so `remote_blast` |
| `parse_genbank` | 35-62 | le um registro GenBank | so `genbank` |
| `parse_pubmed_count` | 65-74 | conta citacoes no PubMed | so `pubmed_count` |
| `parse_blast_get` | 77-92 | le a resposta do BLAST remoto | so `remote_blast` |
| `NcbiClient._rate_limit_gate` | 121-131 | pausa entre pedidos | interno; o `ClienteNcbi` faz a pausa de 0,4 s por dentro |
| `NcbiClient._cache_read`, `_cache_write` | 133-159 | cache em disco das respostas | interno da demo — a web nao tem cache por desenho |
| `NcbiClient._get`, `_post` | 161-184 | HTTP cru | interno |
| `NcbiClient.genbank` | 186-200 | registro GenBank de um accession | so `identify` (demo) |
| `NcbiClient.pubmed_count` | 202-216 | citacoes de um accession | so `identify` (demo) |
| `NcbiClient.remote_blast` | 218-256 | BLAST no NCBI | so `identify` (demo) |

### orientation.py (302 linhas) -> orientacao.py

`Pair.certain`, `Pair.sense_label` e `Pair.explain` (156-191) nao tinham
chamador no original — porque o modulo inteiro nao tinha. Foram portados
(`.certo`, `.rotulos`, `.explicar()`) e GANHARAM chamador: `montagem.py`
guarda `par.explicar()` em `Montagem.evidencia`, para o relatorio.

### primers.py (97 linhas) — INTEIRO

Remocao de primer (com IUPAC e mismatch) das pontas do consenso. Unico
consumidor: `main_window.py:1268` (demo). Nao portado.

### vector.py (139 linhas) — INTEIRO

Screening de vetor/adaptador contra o UniVec (k-mers canonicos, no espirito
do VecScreen). Unico consumidor: `main_window.py:1220-1224` e `app/main.py`
(demo). Tinha sido portado no Passo 2 (`vetor.py`, antes da regra) e foi
RETIRADO em 02/09/2026: esta em `Refactoring/demo-retirada-do-web/vetor.py`
ate o Jared apagar a quarentena.

Junto com ele saiu da `CaixaDeFerramentas` (`ferramentas.py`) a vaga do
UniVec: `_default_univec()` (vector.py 21-32) tinha virado
`procurar_arquivo` + variavel `EASYCONTIG_UNIVEC` + `caixa.univec`. Sem
`vetor.py` ninguem le o arquivo. A vaga passou a ser o `makeblastdb`
(`caixa.makeblastdb`, mesma pasta/variavel do blastn: `EASYCONTIG_BLAST_BIN`),
que tem chamador — a `EstanteDeBancos` da web. A versao com o UniVec esta em
`scratchpad/ferramentas.antes-do-makeblastdb.py`.

### report.py (532 linhas) -> relatorio.py (Passo 8)

| item | linhas | o que fazia | chamador |
|---|---|---|---|
| `ReadFacts.span_pct` | 84-85 | `(col_start, col_end)` | nenhum |
| nota do `_CSS` sobre o QTextDocument | 408-412 | comentario: o QTextDocument do Qt cai na cor da paleta, por isso a cor fixa no `body` | era da janela Qt da demo (`render_html` era "entrada do QTextDocument que vira PDF", l. 439); a web renderiza no navegador — o CSS ficou identico |

O que MUDOU (nao e corte, e decisao — esta na docstring do relatorio.py):
chaves do JSON em portugues (`de_json` nao le o relatorio.json antigo),
`situacao='nao_montou'` na amostra que falhou (o original deixava
`sem_acerto`, e a web remendava em `leitura_de_amostras._situacao`),
ressalva nova `nao_e_par`, e `contig_invertido` so e calculado quando nao
ha referencia 18S.

### tracy_engine.py (323 linhas) -> motor_tracy.py

Tinham sido portados no Passo 3 (antes da regra) e foram RETIRADOS em
02/09/2026. A versao com eles esta em
`scratchpad/motor_tracy.antes-do-corte.py` (e o harness antigo em
`teste_motor.antes-do-corte.py`).

| item | linhas | o que fazia | chamador |
|---|---|---|---|
| `TracyEngine.version` | 139-144 | versao do tracy pelo `--help` | nenhum na web (`sys.version` enganava o grep) |
| `TracyEngine.trim_bounds` | 167-189 | limites do corte de qualidade | so a UI Qt |
| `TracyEngine.align` | 255-280 | `tracy align` | so a UI Qt |
| `TracyEngine.decompose` | 283-311 | `tracy decompose` (variantes) | so a UI Qt |
| `TracyEngine.read_variants` | 313-318 | le o JSON do decompose | so a UI Qt |

### pairing.py

Tudo tem chamador. `pairing.py` -> `pareamento.py` (feito).

### phylogeny.py (442 linhas) -> filogenia.py (Passo 9)

| item | linhas | o que fazia | chamador |
|---|---|---|---|
| `build_trees` | 436-442 | `group_by_marker` + `build_tree` por grupo, numa chamada | so `tests/test_filogenia.py:194`; a web (`executor_de_arvore.py`) agrupa e monta um grupo de cada vez, porque precisa do `recusado` de cada marcador antes de rodar o mb |
| `resolve_mrbayes_bin` | 315-334 | acha o `mb` por `EASYCONTIG_MRBAYES_BIN` e depois no PATH | so `run_mrbayes` (367); hoje quem acha o mb e a `CaixaDeFerramentas.montar()` do `ferramentas.py`, a mesma escada de todos os programas externos. o `MrBayes(caminho)` recebe o caminho pronto |
| `run_mrbayes(nexus, mb_bin=)` | 359-372 | `subprocess.run` do mb, log ao lado do NEXUS | so `build_tree` (428); virou o metodo `MrBayes.rodar`, com tempo limite (3600 s) e `ErroDoMrBayes` carregando as linhas de erro do log — o original estourava `CalledProcessError` sem dizer o motivo |

`ascii_only`, `pairwise_identity`, `MIN_TAXA`, `MIN_OCCUPANCY` e `MODEL`
sao internos e foram portados com o nome em portugues (`so_ascii`,
`identidade_entre_pares`, `TAXONS_MINIMOS`, `OCUPACAO_MINIMA`, `MODELO`).

O que MUDOU de comportamento (nao e corte): `montar_arvore` passa os
consensos por `mesma_polaridade` antes de alinhar. O original alinhava os
`.fa` como o tracy os gravou, e o tracy escreve o consenso no sentido da
primeira leitura — num lote real de 6 consensos 18S, 2 estavam invertidos, a
identidade mediana do alinhamento deu 45,8 % e a arvore juntou Babesia com
Hepatozoon. Com a correcao: 66,6 % e um genero de cada lado. O `Boletim`
ganhou `.invertidos` e um aviso que aponta as amostras para o usuario
conferir o F/R no relatorio do lote. E o bloco MRBAYES ganhou uma linha,
`set seed=13719 swapseed=13719;`: o mesmo NEXUS da a mesma arvore em qualquer
maquina (o original sorteava a semente a cada corrida). Tirando essa linha, o
NEXUS e byte a byte o do original sempre que os consensos ja estao no mesmo
sentido (medido: 300 matrizes sinteticas). Aprovado pelo Jared em 02/09/2026.


## Modulos da web (fase 2)

Mesma regra, mesmo metodo: `grep` de cada nome em `easycontig_web/` inteiro
(rotas, executores, templates, `estatico/*.js`).

### easycontig_web/dados/bancos_de_referencia.py (338 linhas) -> bancos_de_referencia.py

Tudo tem chamador, menos tres campos do dicionario de `consultar`:

| item | linhas | o que fazia | chamador |
|---|---|---|---|
| `consultar(consenso, prefixo_db, blast_bin, n)` | 314-338 | `blastn -outfmt 6 sacc stitle pident qcovs evalue bitscore length` e lista de dicionarios | so `rotas_de_consulta_de_lote.py:95-135`; nao foi portado COMO FUNCAO porque ja existe: e `BuscaDeReferencia.acertos(consenso, estante.banco(id))` do `identificacao.py` (mesmo blastn, mesmas colunas menos `sacc` -> `saccver`). O `Acerto` ganhou `.titulo` para a consulta da web mostrar o que o `oficina.js:640-644` mostra (`titulo`, `identidade`, `cobertura`, `accession` -> `acesso`) |
| campo `alinhado` (`length`) | 337 | comprimento do alinhamento | nenhum: o `oficina.js` nao le |
| campo `e_value` e `bits` (texto) | 336-337 | valor-e e bitscore como string | nenhum na tela; o `Acerto` tem `valor_e` e `pontuacao` como numero, de graca |
| `_entrez`, `EUTILS`, `PAUSA`, paginacao do efetch | 33-37, 144-150, 194-207 | o NCBI cru | virou o `ClienteNcbi` (`cliente_ncbi.py`), o cliente unico; `montar(id, ncbi)` recebe o cliente pronto |
| `_LIMITE_BLASTN` | 154 | 120 s do blastn | e o `tempo_limite=120` da `BuscaDeReferencia` |

Mudou de LUGAR (nao de comportamento): a trava "uma montagem por banco"
(`rotas_de_bancos.py:103-136`, `_so_uma_montagem`, arquivo `O_EXCL`, orfa
com 1800 s) virou `EstanteDeBancos.trava(id)`, pega por `montar` e
`montar_do_usuario` sozinhos (do download ate a troca da pasta; o original
so travava o do catalogo). O arquivo saiu de
`bancos/<id>/<id>.montando.lock` para `bancos/<id>.lock`, porque a montagem
nova troca a pasta `<id>` inteira e apagaria a trava junto. `BancoOcupado`
e o que a rota transforma em 409.

O que MUDOU de comportamento (medido em `scratchpad/teste_bancos.py`, 159
checks, makeblastdb e blastn reais, NCBI falso com o mesmo FASTA dos dois
lados + 1 corrida com o NCBI real, `.nsq` byte a byte igual nos dois):

- a montagem e feita em `bancos/<id>.novo/` e a pasta e trocada no fim. O
  original rodava o makeblastdb em cima do banco que existia e, quando o
  makeblastdb falhava, o banco antigo SUMIA (o programa apaga os indices
  antes de gravar): sobrava a pasta com o `.fasta` baixado inteiro, o
  `meta.json` velho e pedacos (`.00.nog`, `.nos`, `.not`...), e a pagina
  dizia "nao montado". Em `montar_do_usuario` a falha apagava a pasta toda,
  banco anterior junto. Agora: falhou, some so a `.novo`; o banco de antes
  fica como estava.
- o makeblastdb roda com `cwd` na pasta e nomes relativos: o titulo que ele
  grava no `.nin`/`.njs` passa a ser `<id>.fasta` em vez do caminho absoluto
  do servidor (`/srv/.../bancos/<id>/<id>.fasta`). Nada le esse titulo; o
  `.nsq` e os acertos do blastn sao identicos.
- erros viram `ErroDoBanco` (e `BancoOcupado`) com frase para a tela: a
  medida, a regra e o que conferir ("o NCBI devolveu 10 de 16 sequencias
  (menos de 90 %); banco nao montado — tente de novo mais tarde"), sem
  caminho de servidor. O original levantava `RuntimeError`/`ValueError`/
  `KeyError`/`CalledProcessError` e a rota escondia tudo atras de "nao foi
  possivel montar o banco; o motivo completo esta no log".
- `montar_do_usuario` confere o formato do id (`meu_<conta>_<apelido>`); o
  original confiava na rota.
- `estado()`/`meus_bancos()` devolvem `EstadoDoBanco` (`.montado`,
  `.sequencias`, `.baixado_em`, `.bytes`, `.termo`, `.id`, `.apelido`) em
  vez de dicionario; no Jinja `e.montado` le os dois igual.

O que NAO mudou, de proposito: `CATALOGO` (15 fichas, termos byte a byte),
`bancos/<id>/<id>.*`, `meta.json` (`sequencias`, `termo`, `baixado_em`),
`meu_<sha256(email)[:16]>_<apelido>`, as duas regex, teto 60 000, 90 %,
600 s do makeblastdb. Os bancos ja montados na VPS continuam validos.

### assembly.py 128-250 (`chromatogram_*`, `_estimate_offset`, `base_columns`) + easycontig_web/dados/cromatograma.py (176 linhas) -> tracado.py

`chromatogram_data_columns` ficou fora do `montagem.py` de proposito (item 5
do docstring dele: e desenho, nao montagem). Chamadores medidos em
`easycontig-web/` inteiro:

| item | linhas | o que fazia | chamador |
|---|---|---|---|
| `chromatogram_data_columns(asm, name)` | assembly 163-229 | traco em coordenada de coluna, NaN no gap | so `cromatograma.para_navegador` (web) -> `tracado.traco_em_colunas(montagem, nome)` |
| `chromatogram_data(gt)` | assembly 232-250 | garante `primarySeq`/`pos` (o assemble grava as bases em `basecalls`) | so o de cima -> `tracado.traco_com_sequencia` |
| `_estimate_offset(trace_seq, aligned_seq, k=18)` | assembly 142-160 | onde a leitura aparada comeca no traco inteiro | so o de cima -> `tracado.deslocamento_no_traco` |
| `base_columns(asm, name)` | assembly 134-139 | colunas nao-gap de uma leitura | NENHUM (nem no nucleo, nem na web): fora. O mesmo calculo vive dentro de `traco_em_colunas` e de `relatorio.fatos_das_leituras` |
| `Assembly.trace_x_at_column` | assembly 114-131 | x do cromatograma numa coluna | NENHUM: fora (ja na secao do assembly.py) |
| `_pontos`, `para_navegador(asm, amostra, passo)` | cromatograma 87-176 | lista plana `[x, y, ...]` e o dict do `oficina.js` | `rotas_de_consulta_de_lote.py:48/177` -> `tracado.pontos`, `tracado.para_navegador(montagem, passo)` (sem o dict do relatorio: Q medio, rotulo e primer saem da propria Montagem, mesmos numeros) |
| `montar(cfg, lote_dir, amostra)`, `_motor(cfg)`, `amostra_do_relatorio(rep, key)` | cromatograma 22-84 | remontar a amostra a partir dos `.ab1` do lote (par pelo consensus; >2 leituras pelo `.json` do assemble gravado pelo lote) | `rotas_de_consulta_de_lote.py`; precisa de `Config` e da pasta do lote: FICA PARA A FASE WEB (`cromatograma.py` novo, so o remontar, chamando `MontadorDeAmostra` + `tracado.para_navegador`) |

O que MUDOU de comportamento (medido em `scratchpad/teste_tracado.py`, 121
checks, tracy real: 1 par pelo consensus + 4 JSONs reais do assemble, 14
leituras, dicts do `traco_em_colunas` identicos ao original numero por
numero, `para_navegador` com `passo=1` identico em tudo):

- `passo=2` nao perde mais a quebra do gap. O original fazia `pos[::2]` e o
  NaN em posicao impar sumia: o navegador ligava o pico de antes do gap ao
  de depois. Medido nas 14 leituras: 27 quebras viravam 13, 17 viravam 8, 4
  viravam 0. Agora toda posicao NaN e guardada (o `oficina.js:474` ja trata
  o `null` de forma generica, nada muda nele). Tirando os `null`, os canais
  sao identicos aos do original.
- traco com uma base so: o original caia com `IndexError` (a janela da base
  usa a vizinha) e a rota respondia 500; agora `None`, como os outros "nao
  da para desenhar" (9 de 300 tracos forjados aleatorios caiam no original).
- traco todo em zero: o original caia com `ZeroDivisionError` no `_pontos`
  (topo p95 = 0); agora o topo vira 1 e a linha sai reta no chao.
- `q_medio` nunca vem `None` (no original vinha `None` se a leitura nao
  estivesse no relatorio da amostra).

O que NAO mudou: chaves do dict do `para_navegador` (contrato com o
`oficina.js`), chaves do traco (JSON do tracy), k=18, 10 ancoras, mediana,
p95 por leitura, altura 100 com 94 %, arredondamento (2 casas em x, 1 em y).


### easycontig_web/configuracao.py (173 linhas) -> easycontig_web/configuracao.py + processamento/executor_de_lote.py (172 linhas) -> processamento/executor_de_lote.py

Chamadores medidos com `grep` no `easycontig_web/` inteiro (contagem de
usos de cada atributo do `Config`: `sqlite_path` 115, `data_dir` 31,
`lotes_dir` 15, `blast_bin` 8, `tracy_bin` 5, `retencao_dias` 5, `max_bytes`
5, `dominio_permitido` 4, `max_arquivos` 3, `trim` 3, `db_18s`/`db_16s` 2).

| item | linhas | o que fazia | chamador |
|---|---|---|---|
| `Config.tracy_bin`, `Config.blast_bin` | configuracao 27-28, 124-125 | os caminhos do tracy e da pasta do blast, lidos de `EASYCONTIG_TRACY_BIN`/`_BLAST_BIN` no `carregar()` | `executor_de_lote` (que os REGRAVAVA no `os.environ` para o nucleo achar), `cromatograma._motor`, `rotas_de_consulta_de_lote` (consulta), `diagnostico`. Fora da configuracao: a escada do `ferramentas.py` do nucleo (variavel -> pasta do pacote -> PATH) e a unica verdade; as variaveis continuam valendo, so que lidas pelo nucleo, e ninguem mais escreve no ambiente |
| `executor_de_lote.pastas_do_lote(cfg, lote_id)` (dict) | executor 22-45 | `entrada/`, `trabalho/`, `relatorio.*`, `resultado.csv` + validacao do id | 7 chamadores (rotas de envio/consulta/arvore, retencao, cromatograma, executor_de_arvore) -> `configuracao.PastasDoLote` via `cfg.pastas_do_lote(lote_id)`; atributos no lugar de chaves |
| validacao do `lote_id` repetida 3x | executor 33-37, expurgo_por_retencao 146-151, executor_de_arvore 242-247 | recusar `..`, barra, nulo, vazio | as tres -> `configuracao.nome_simples(nome)`; o erro agora ORIENTA (diz o que e um id valido) |
| `expurgo_por_retencao.dias_configurados()` | retencao 43-60 | `EASYCONTIG_RETENCAO_DIAS` -> int, 0 se ausente/ilegivel/negativo | so `configuracao.carregar` -> `configuracao.dias_de_retencao()`, mesmo comportamento e mesmos avisos |
| `_rotulo` (3o valor devolvido por `bancos_do_lote`) | executor 53-70, 99 | o rotulo do banco escolhido | NENHUM: `executar` recebia como `_rotulo` e nunca usava. `bancos_do_lote` devolve so a lista |
| `os.environ["EASYCONTIG_BLAST_BIN"] = ...` | executor 97 | avisar o nucleo onde esta o blastn | ninguem precisa mais: o nucleo le a variavel |

O que MUDOU de comportamento (medido em `scratchpad/teste_executor.py`, 71
checks: lote real de 11 `.ab1` nos dois executores com tracy + blastn +
bancos reais, CSV byte a byte, HTML byte a byte tirando `gerado_em` e as 2
diferencas ja aprovadas do relatorio.py, JSON campo a campo; erros; banco
proprio e do catalogo pela `EstanteDeBancos`; sem blastn; sem tracy; `main()`
dos dois modulos rodando de verdade):

- Sem blastn o lote MONTA e cada amostra sai `blast_indisponivel` com a
  ressalva `identificacao_nao_executada` e a nota da instalacao (o original
  fazia o mesmo, mas por uma excecao por amostra dentro do `batch.py`). Sem
  tracy: `FerramentaAusente` dizendo a variavel e os lugares procurados.
- Banco escolhido pelo usuario: a coluna `marcador` do relatorio traz o
  marcador DO BANCO (o gene da ficha, ex. `18S`, ou o apelido do banco
  proprio, ex. `babesias`), e nao mais `18S` fixo. Consequencia da decisao
  aprovada no bancos_de_referencia.py; o relatorio passa a dizer contra o que
  a amostra foi comparada.
- Banco pedido que nao esta montado: o `ValueError` diz onde montar.
- `diagnostico`: o detalhe de programa que falta diz qual dos dois casos e —
  a variavel aponta para um lugar sem o programa (mostra o caminho) ou nao
  esta definida e o programa nao esta no PATH. O original mostrava so o
  caminho num caso e "defina a variavel" no outro (mesmo quando ela ja estava
  definida). Os outros textos (conferir_producao, bancos) sao identicos.
- `relatorio.json` com chaves em portugues (decisao do passo 8): um
  `relatorio.json` gravado pelo original nao e lido pelo `de_json` novo. Na
  VPS os lotes vivem no maximo `EASYCONTIG_RETENCAO_DIAS` (30); ver a lista de
  pendencias na entrega.

O que NAO mudou: as 15 colunas do CSV e a ordem, o `<title>` e o documento
HTML, a ordem de gravacao (json, html, csv), o `n_esperado` que PARA o lote
(nada gravado), o `progresso(feito, total, chave)`, `nome or lote_id` como
pasta do relatorio.


### easycontig_web/processamento/executor_de_arvore.py (247 linhas) -> processamento/executor_de_arvore.py

Chamadores medidos com `grep` no `easycontig_web/` inteiro: `executar` (o
`trabalhador_da_fila`, 1 uso), `caminho_de_saida` (a rota
`/lotes/{id}/arvore/{pasta}/{arquivo}`, 1 uso), `marcador_da_amostra`,
`consensos_do_lote` e `marcadores_conhecidos` (so os testes). Tudo portado;
nada sem chamador.

| item | linhas | o que fazia | chamador |
|---|---|---|---|
| `_ler_fasta(arquivo)` (lista de todos os registros) | 60-80 | lia o FASTA inteiro para usar so o 1o registro | `consensos_do_lote` -> `primeira_sequencia(arquivo)`: le so o 1o registro e para no 2o `>` |
| reconstrucao do mapa nome-seguro -> nome do laboratorio + 2a leitura do `.con.tre` | 140-160, 207-215 | o Newick com os nomes do laboratorio, para o arquivo e para a figura | `filogenia.montar_arvore` ja devolve `.newick` e `.mapa` no `ResultadoDaArvore`; `desenhar(resultado, destino)` perdeu o parametro `mapa` |
| `filo.resolve_mrbayes_bin()` (variavel -> PATH -> `FileNotFoundError`) | phylogeny | achar o mb | `ferramentas.exigir_programa('mb', 'EASYCONTIG_MRBAYES_BIN', 'bin')`, a mesma escada do tracy e do blastn; `FerramentaAusente` cita a variavel e os lugares |
| validacao de `pasta`/`arquivo` em `caminho_de_saida` | 242-247 | recusar `..`, barra, vazio, nulo | `configuracao.nome_simples(nome, papel)`; o erro diz QUAL parte falhou (`pasta da árvore`, `arquivo da árvore`) |

O que MUDOU de comportamento (medido em `scratchpad/teste_executor_arvore.py`,
64 checks: 64 nomes de amostra x reserva nos dois; 3 cenarios de pasta
`trabalho/` sinteticos + 1 com banco proprio; `executar` DE VERDADE nos dois
com 6 consensos 18S reais + 2 de 16S e o MrBayes real; `desenhar` com Newick
vazio/invalido/sintetico; erros; 9 casos ruins de `caminho_de_saida`; `main()`
por subprocess):

- Reserva do lote com banco proprio: o marcador das amostras sem gene no nome
  e o apelido do banco (ex. `babesias`); o original so conhecia o catalogo e
  dava `desconhecido`. Mesma regra do executor_de_lote (decisao aprovada no
  bancos_de_referencia.py).
- `marcadores_conhecidos` sai em ordem fixa (tamanho, depois alfabetica). O
  original ordenava um `set` so por tamanho: a ordem entre `16S/18S/28S/COI/dsb`
  mudava a cada reinicio do servidor (hash randomization), e um nome com dois
  genes de tres letras (`am_18S_16S`) caia ora num grupo, ora noutro. Agora
  sempre `16S`.
- Reserva so de espacos: o original estourava `IndexError` em
  `reserva.split()[0]`; agora `desconhecido`.
- Sem mb com algum grupo montavel: `FerramentaAusente` (orienta) em vez de
  `FileNotFoundError`; e ANTES de criar `arvore/`. So grupos recusados: nao
  precisa de mb, nos dois.
- Figura que nao saiu: aviso no resumo (`figura: ''` + texto dizendo que o
  Newick continua valendo para FigTree/iTOL/MEGA) e uma linha no log com a
  causa. O original devolvia `figura: ''` em silencio.
- A arvore em si e a do filogenia.py novo (polaridade corrigida + semente
  13719): na corrida real, identidade mediana 66,6 % contra 45,8 % do
  original, com o aviso das 2 amostras reverso-complementadas. O Newick tem
  o rotulo de probabilidade nos nos internos nos dois.
- `ValueError` de "nenhum consenso" ORIENTA (rode o lote, confira
  `trabalho/`). Comeca com o mesmo texto do original.

O que NAO mudou: as 12 chaves do resumo por marcador (`marcador, n, colunas,
colunas_antes, identidade, asdsf, convergiu, veredito, avisos, figura, newick,
pasta`) e o dicionario `{marcador, n, recusado}` do grupo recusado — o
`arvore.html` le os mesmos nomes; o texto de `etapa` (`'18S (6 amostras)'`);
a ordem dos grupos (marcador em ordem alfabetica); `.cons.fa` ganha de `.fa`
na mesma amostra e `.align.fa` fica fora; 1o registro do FASTA, maiusculo,
sem `-`; o nome da pasta (`nome_seguro`) e os nomes `arvore.newick` /
`arvore.png`; `PROBABILIDADE_MINIMA` 0,90, figsize, dpi 140, titulo e rotulos
da figura; `caminho_de_saida` recusa os mesmos 9 casos ruins.

### easycontig_web/dados/cromatograma.py (176 linhas) -> processamento/cromatograma.py

Chamadores medidos na web original: `montar` e `para_navegador` em
`rotas_de_consulta_de_lote.py` (`api_traco` e `api_consultar`);
`amostra_do_relatorio` nos mesmos dois. Nada sem chamador; nada cortado.
Bancada `teste_cromatograma.py`: 52 checks (lote real de 11 .ab1 rodado nos
dois executores, cada amostra remontada nos dois lados, caminhos de erro,
`main()` por subprocess).

| Original                              | Novo                                                        |
|---------------------------------------|-------------------------------------------------------------|
| `dados/cromatograma.py`               | `processamento/cromatograma.py` (roda o tracy: e executor)  |
| `amostra_do_relatorio(rep, key)`      | `RelatorioDoLote.amostra(chave)` (nucleo, relatorio.py)     |
| `montar(cfg, lote_dir, amostra)`      | `remontar(cfg, lote_id, fatos)`                             |
| `_motor(cfg)`                         | `executor_de_lote.motor_do_tracy()`                         |
| `para_navegador(asm, amostra)`/`_pontos` | `tracado.para_navegador(montagem)` (nucleo, passo 9)     |
| `build_pair_assembly` sempre (2 leituras) | `MontadorDeAmostra` — o mesmo do lote                   |
| `_` (dict de chaves em ingles)        | `FatosDaAmostra` + `traco_da_amostra(cfg, lote_id, fatos)`  |

O que mudou de comportamento (todos conferidos na bancada):
- `trabalho/<chave>.json` do assemble e reaproveitado SEMPRE que existe (o
  original so quando `len(reads) > 2`). Caso real: amostra187 tem 3 .ab1, o
  tracy assemble descarta 1, o relatorio fica com 2 leituras e
  `contig_invertido`. O original via "2 leituras", re-rodava `tracy consensus`
  F-primeiro e desenhava o REVERSO-COMPLEMENTO do consenso que o relatorio,
  o CSV e o `.cons.fa` mostravam — traco e numeros em desacordo na mesma
  pagina. Agora o traco e o da montagem que gerou os numeros.
- `.json` sumiu mas os .ab1 estao la: remonta pelo `MontadorDeAmostra`
  (3 leituras -> assemble). O original devolvia None.
- Par F+R: o DNA decide (orientacao.analisar), forward na frente — o
  original escolhia pelo `orient_content or orient_name` do JSON. Nas duas
  amostras pareadas reais (amostra12/28) o resultado e identico.
- Leitura sem caminho gravado: o original fazia `Path('')` e "achava" a
  PASTA `entrada/` como se fosse o arquivo. Agora procura o trace de mesmo
  nome (`lote.achar_traces`) dentro de `entrada/`; tambem cobre volume que
  mudou de lugar (caminho absoluto velho no relatorio).
- Falha (tracy, ferramenta ausente, JSON corrompido) -> None COM a causa no
  log `easycontig.cromatograma`. O original engolia em silencio.
- `lote_id` invalido (`../x`) -> `ValueError` de `nome_simples` (uso errado,
  nao "sem traco"); lote inexistente -> None sem criar pasta.

O que NAO mudou: solteira (1 leitura) -> None; retencao apagou `entrada/`
-> None para o par, mas quem tem `.json` do assemble continua saindo (nem
precisa do tracy); `para_navegador` -> mesmas chaves, mesmos `largura`,
`consenso`, `consenso_pb`, `cobertura`, `mismatches`, `bases`, `alinhada`,
`q_medio`, `q_rotulo`, `primer`; os pontos dos canais sao os mesmos (o novo
so guarda toda quebra NaN, aprovado no passo 9); o dict vai para JSON.
Aditivo no nucleo: `RelatorioDoLote.amostra(chave)` em relatorio.py (a
pagina de UMA amostra acha a sua no relatorio do lote).

### easycontig_web/dados/leitura_de_amostras.py (315 linhas) -> dados/leitura_de_amostras.py

Chamadores medidos na web original: `carregar`, `listar`, `resumo`, `amostra`
em `rotas_de_consulta_de_lote.py`; `contar_amostras` em `rotas_de_perfil.py`.
Os outros 4 arquivos de rotas so IMPORTAM `mod_amostras` (bloco de cabecalho
copiado) e nao usam — some com a deduplicacao dos cabecalhos. Nada cortado.
Bancada `teste_leitura_de_amostras.py`: 78 checks (lote real de 11 .ab1 nos
dois executores; 12 casos sinteticos convertidos para o JSON ingles e passados
pelo `_formatar` original; 11 JSONs ruins; a memoria de `contar_amostras`;
`main()` por subprocess).

| Original                                   | Novo                                                     |
|--------------------------------------------|----------------------------------------------------------|
| `carregar` -> dict cru do JSON              | `carregar` -> `RelatorioDoLote` (nucleo) ou None + log   |
| `_amostras(rep)`                           | `lote.amostras`                                          |
| `_situacao` / `_rotulo_situacao` / `_num`   | `situacao_na_tela` / `rotulo_da_situacao` / `numero`     |
| `_formatar` / `_leitura`                    | `formatar_amostra` / `formatar_leitura`                  |
| `_contagens`                               | `CONTAGENS` (mesma memoria: mtime_ns + tamanho -> n)     |
| `key` / `accession` / `cobertura_query` / `e_value` / `pct_cobertura_1x` / `sentido_nome` | `chave` / `acesso` / `cobertura_da_consulta` / `valor_e` / `pct_cobertura_1` / `sentido_pelo_nome` |
| `situacao_id` + `situacao`                  | so `situacao` (o nucleo ja grava `nao_montou`)           |
| `contig_invertido: 'sim'/'nao'` + `invertido: bool` | so `contig_invertido: bool`                      |
| —                                          | `par_pelo_dna`, `sentido_absoluto`, `evidencia` (fatos novos do nucleo) |

Aditivo no nucleo: `RelatorioDoLote.de_dicionario(d)` (relatorio.py);
`de_json` passou a ser `de_dicionario(json.loads(texto))` — a web confere a
forma do dict antes e nao decodifica duas vezes.

O que mudou de comportamento:
- relatorio.json ANTIGO (chaves em ingles) -> None com a causa no log
  (`relatorio ilegivel em ...: ValueError: o JSON nao tem a lista "amostras"`).
  A pagina diz "relatorio indisponivel". Alcanca so os lotes < 30 dias na VPS.
- Todo JSON ruim (quebrado, lista, dict sem `amostras`, amostra sem `chave`,
  campo desconhecido, diretorio) -> None + 1 linha de log. Arquivo que nao
  existe -> None SEM log (lote sem relatorio ainda e normal).
- `valor_e`: so None vira ''. O original apagava tambem um 0,0 numerico.

O que NAO mudou: textos de `SITUACOES` e `ORDEM_SITUACOES` letra por letra;
a guarda "erro ou 0 pb = nao_montou"; situacao desconhecida sai crua com
classe `sub`; `numero` (None/lixo -> '', 1 casa na identidade, 2 na
cobertura); `q_medio` 'Q41', `janela` '1–652'; `resumo` com as mesmas 15
chaves e os mesmos numeros no lote real (total 5, ok 4, nao_montou 1,
pareadas 4, avulsas 1, com_ressalva 5, com_erro 1, com_contig_invertido 1);
`{'amostras': []}` e relatorio valido com 0 amostras (o original idem com
`samples`); a memoria de `contar_amostras` reconta quando o arquivo muda e
esquece quando ele some ou vira lixo.

### easycontig_web/dados/esquema_e_migracoes.py (312 linhas) + `conectar`/`criar_esquema`/`_ESQUEMA` de fila_de_lotes.py, perfil_do_laboratorio.py e pedidos_entre_labs.py -> dados/banco_sqlite.py

Chamadores medidos na web original: `criar_esquema` em `servidor_web.py:73-75`
(tres, um por modulo) e `trabalhador_da_fila.py:137` (so o da fila);
`conectar` em `cota_de_espaco.py:187` e nas filas/perfil/pedidos;
`esquema_e_migracoes` so por dentro de `fila_de_lotes.criar_esquema` e pelos
testes (`test_migracoes.py`, `test_arvore_do_lote.py`,
`test_cota_bancos_e_rajada.py`). `restaurar` so em `test_migracoes.py:119`
(fica: e a ferramenta de rollback manual da VPS). Nada cortado.
Bancada `teste_banco_sqlite.py`: 98 checks (retrato tabela a tabela e indice
a indice do banco criado pelo original x pelo novo; banco v0 com corrida
gravada; banco v4 com dois perfis sem chave; banco so do trabalhador;
compatibilidade cruzada nos dois sentidos; migracao que explode; restaurar;
6 processos no mesmo banco novo e 4 no mesmo banco antigo; `main()` por
subprocess). Sonda `corrida_wal.py`: 60 rodadas x 6 processos.

| Original                                                  | Novo                                            |
|-----------------------------------------------------------|-------------------------------------------------|
| `fila.conectar(caminho)` (funcao com o caminho na mao)    | `BancoSqlite(caminho).conectar()`               |
| `fila.criar_esquema` + `perfil.criar_esquema` + `pedidos.criar_esquema` | `banco.preparar()` (uma chamada)   |
| tres `_ESQUEMA` (lotes+pulso / perfis / pedidos) + `arvores` so na migracao 6 | um `ESQUEMA` com as 5 tabelas |
| `migracoes.aplicar(con, caminho)`                         | `banco.migrar()`                                |
| `migracoes.versao(con)`                                   | `banco.versao` (propriedade)                    |
| `_v1_...` ... `_v6_...`, `_colunas`, `_agora` (3 copias)   | `migracao_1_...` ... `migracao_6_...`, `colunas`, `agora` (uma) |
| `migracoes.restaurar(backup, caminho)`                    | `banco.restaurar(backup)`                       |
| —                                                         | `banco.transacao()` (BEGIN IMMEDIATE/COMMIT/ROLLBACK) |

SQL das migracoes e das tabelas: identico letra por letra (o retrato do
`PRAGMA table_info` e do `PRAGMA index_list` e o mesmo nos dois).

O que mudou de comportamento:
- Banco NOVO nasce direto na versao 6, numa unica transacao, sem passar
  pelas 6 migracoes e SEM o backup de um banco vazio que o original deixava
  (`fila.sqlite3.antes-de-migrar-*` de 0 tabelas a cada volume novo).
- Banco EXISTENTE: as migracoes pendentes rodam todas dentro de UMA
  transacao de escrita, cada uma num SAVEPOINT. Se a N-esima explode, as
  anteriores FICAM gravadas e a versao para na ultima boa (o original rodava
  cada migracao na sua transacao, entao o resultado e o mesmo; a diferenca e
  que agora os outros processos esperam na trava em vez de ler a versao pela
  metade).
- Nome do backup reservado com `O_EXCL`: dois backups no mesmo segundo viram
  `...-<carimbo>` e `...-<carimbo>-2`. Medido no original: `restaurar` +
  `aplicar` no mesmo segundo SOBRESCREVIA o backup anterior; e 6 processos
  subindo juntos escreviam todos no MESMO arquivo de backup.
- `PRAGMA journal_mode=WAL` com espera propria (o SQLite nao consulta o
  busy_timeout para trocar o modo de journal). Medido no original com 6
  processos subindo num volume vazio: 59 de 240 processos morriam com
  `database is locked` no arranque (web + trabalhadores no mesmo instante).
  No novo: 0 de 360.
- Migracao que falha loga `migracao N (descricao) falhou; o banco fica na
  versao M` (antes: `... continua na versao M`).

O que NAO mudou: `timeout=30`, `isolation_level=None`, `Row`,
`busy_timeout=30000`; a guarda "so se perfis existe" das migracoes 2 e 5
(banco criado so pelo trabalhador continua subindo e o servidor completa
depois); a chave publica sorteada com `secrets.token_urlsafe(9)` (12
caracteres) e o log `chave publica sorteada para N perfil(is)`; o backup em
modo `journal_mode=DELETE` com checkpoint TRUNCATE (um arquivo so, sem
`-wal`); `restaurar` apaga `-wal`/`-shm` antes de copiar; um banco preparado
pelo original e aceito pelo novo sem migrar nem backup, e vice-versa.

### easycontig_web/processamento/fila_de_lotes.py (460 linhas) + fila_de_arvores.py (176 linhas) -> processamento/fila.py + fila_de_lotes.py + fila_de_arvores.py

Chamadores medidos na web original (fila de lotes): `novo_lote`,
`liberar_para_fila`, `falhar` (sem trabalhador) em `rotas_de_envio_de_lote.py`;
`pegar`, `listar` em `comum.py`, `rotas_de_entrada.py`, `rotas_de_perfil.py`,
`servidor_web.py` (/saude); `fila_atendida` e as constantes em
`rotas_de_consulta_de_lote.py`; `reivindicar`, `progresso`, `concluir`,
`falhar`, `pulsar`, `reenfileirar_orfaos`, `trabalhadores_vivos`,
`INTERVALO_PULSO` em `trabalhador_da_fila.py`; `ATIVOS`, `PRONTO`, `FALHOU`,
`conectar` em `cota_de_espaco.py`; `RECEBENDO`/`RODANDO` em
`expurgo_por_retencao.py`. Fila de arvores: `novo_pedido`, `do_lote`,
`resumo_lido`, `ATIVOS`, `NA_FILA` em `rotas_de_consulta_de_lote.py`;
`reivindicar`, `etapa`, `concluir`, `falhar`, `reenfileirar_orfaos` em
`trabalhador_da_fila.py`. `fila_de_arvores.pegar` so nos testes — fica, porque
e o `Fila.pegar` herdado (custa zero). Nada cortado.
Bancada `teste_filas.py`: 102 checks (mesma sequencia em dois bancos, um pelo
original e outro pelo novo, comparando as tabelas linha a linha; cotas de
ativos e de bytes com a reserva; dono certo/errado/vazio; cortes 120/2000;
pulso com relogio injetado; orfaos em 3 cenarios; envios abandonados;
arvores; compatibilidade cruzada nos dois sentidos; 6 processos x 30 lotes e
8 envios simultaneos com teto 1; `main()` dos tres por subprocess).

| Original                                                | Novo                                                    |
|---------------------------------------------------------|---------------------------------------------------------|
| `fila.pegar(caminho, id)` (e a copia em fila_de_arvores) | `Fila.pegar(id)` — uma vez, na base                     |
| `reivindicar`, `_dono_confere`, `falhar`, `reenfileirar_orfaos` (duplicados nos dois modulos) | `Fila.reivindicar`, `condicao_do_dono`, `Fila.falhar`, `Fila.reenfileirar_orfaos` |
| `progresso`/`concluir`/`falhar`/`etapa`: 4 UPDATEs a mao  | `Fila.gravar_do_dono(id, trabalhador, **campos)`; cada carimbo e uma linha |
| `pulsar`, `trabalhadores_vivos`, `fila_atendida` (so na fila de lotes) | na base `Fila`: as duas filas respondem     |
| `fila_de_arvores.reenfileirar_orfaos(caminho, vivos, eu)` | `FilaDeArvores(banco).reenfileirar_orfaos(eu)` (le os vivos sozinha) |
| dict do sqlite3.Row                                     | `Lote` / `PedidoDeArvore` (um atributo por coluna + `.ativo`) |
| `resumo_lido(pedido)`                                   | `pedido.resumo` ja decodificado (`ler_resumo(texto)` publica) |
| parametro `agora: datetime`                             | `momento` (para nao colidir com `banco_sqlite.agora()`) |
| `reenfileirar_orfaos` tambem marcava RECEBENDO velho como FALHOU | `FilaDeLotes.falhar_envios_abandonados()` separado, devolve quantos |
| `listar(limite=50)`                                     | `listar(dono=None, limite=50)`; `limite=0` = todos      |
| `_agora` (copia)                                        | `banco_sqlite.agora()`                                  |
| `etapa[:120]`, `erro[:2000]` (numeros soltos)           | `TAMANHO_DA_ETAPA`, `TAMANHO_DO_ERRO`                   |
| texto do envio interrompido dentro do SQL               | `AVISO_DE_ENVIO_INTERROMPIDO`                           |

O que mudou de comportamento:
- `reivindicar` devolve o registro COMO FICOU (status RODANDO, `trabalhador`,
  `iniciado_em`). O original devolvia a linha lida antes do UPDATE: um lote
  recem-reivindicado com `status='na_fila'` e `trabalhador=''` na mao do
  trabalhador. O trabalhador so usava id/nome/n_arquivos/referencia, entao
  nada quebrava — mas era um registro mentiroso.
- `novo_lote` sempre roda dentro de `transacao()` (BEGIN IMMEDIATE). O
  original so abria transacao quando havia teto; sem teto inseria em
  autocommit. Mesmo resultado, um caminho so.
- `falhar_envios_abandonados` devolve quantos marcou (o original fazia o
  UPDATE dentro de `reenfileirar_orfaos` e nao contava).

O que NAO mudou: todo SQL de leitura/escrita (mesmas colunas, mesma ordem
`criado_em`, mesmo `LIMIT`); a cota conferida DENTRO da transacao do INSERT
(8 envios simultaneos com teto 1: exatamente 1 entra); a reserva de bytes so
dos RECEBENDO e zerada em `liberar_para_fila`; `bytes_previstos` negativo
vira 0; o UPDATE condicional do dono (`trabalhador=''` grava sem conferir);
`PULSO_VELHO` inclusivo (90 s vivo, 91 s morto); data sem fuso = UTC; data
ilegivel = trabalhador ignorado em `trabalhadores_vivos` e "atendida" em
`fila_atendida`; tabela `pulso` ausente = "atendida"/ninguem vivo; a regra do
orfao (dono morto volta; sem dono so se ninguem vive; `eu` nao conta como
vivo); RECEBENDO nunca e reenfileirado; um pedido de arvore ATIVO por lote
(o segundo clique devolve o id do primeiro); `do_lote` = o mais recente;
resumo gravado com `ensure_ascii=False`; JSON quebrado no resumo = `[]`.

### easycontig_web/contas/perfil_do_laboratorio.py (456 linhas) -> contas/perfil_do_laboratorio.py

Chamadores medidos na web original: `pegar` em `rotas_de_perfil.py` (3x),
`comum.py:167` (lateral: nome e foto) e `pedidos_entre_labs.py:91` (o
portfolio declarado do alvo); `salvar`, `foto_registrada` e
`resumo_das_corridas` em `rotas_de_perfil.py`; `garantir_registro` em
`rotas_de_entrada.py:185` e `:238` (login); `listar_perfis` e
`email_da_chave` em `rotas_de_labs_e_pedidos.py:109` e `:134`;
`criar_esquema` em `servidor_web.py:74` (foi para `BancoSqlite.preparar`).
Templates: `perfil.html` le `perfil.nome/laboratorio/instituicao/sobre/
especies/marcadores/foto/atualizado/email/links` e `l.url/nome/icone`;
`labs.html` le `p.eu/nome/laboratorio/instituicao/sobre/especies/marcadores/
foto/chave` (nunca `p.email`).

**`chave_de(sqlite_path, email)` nao veio: nenhum chamador na web** (so a
suite de testes). Sorteava a chave se faltasse e a devolvia; hoje toda linha
nasce com chave em `garantir_registro` (login) e em `salvar`, e
`Perfis.pegar(email).chave` responde a pergunta. Se algum dia for preciso
"chave de quem nunca entrou", e `garantir_registro(email)` seguido de
`pegar(email).chave`.

Bancada `teste_perfil.py`: 81 checks (REDES e constantes identicos; 25 casos
de `limpar_links` — `javascript:`, `data:text/html;base64,...`, virgula
dentro do endereco, corte em 300 que cai no `[` do IPv6, repetido sem
caixa, 10 -> 8, `https://` para quem digitou so o host — comparados um a um
com `_limpar_links`; 11 casos de `links_gravados` x `_links`; `lista`,
`limpar_itens`, `rede_de` caso a caso; mesma sequencia de `salvar` /
`garantir_registro` em dois bancos comparando a tabela linha a linha; ordem
e e-mail escondido no diretorio; `email_da_chave('')`; `foto_registrada`;
compatibilidade cruzada nos dois sentidos; ROLLBACK de `salvar` e
`garantir_registro`; 8 processos na mesma conta; `main()` por subprocess).

| Original                                              | Novo                                                  |
|-------------------------------------------------------|-------------------------------------------------------|
| `pegar(caminho, email)` -> dict                       | `Perfis(banco).pegar(email)` -> `Perfil`              |
| `salvar(caminho, email, *, nome=..., ...)` -> dict    | `Perfis.salvar(email, nome=..., ...)` -> `Perfil`     |
| `garantir_registro`, `foto_registrada`, `email_da_chave` (caminho) | metodos de `Perfis`                      |
| `listar_perfis(caminho, eu)` -> dicts sem `email` dos outros | `Perfis.listar(eu)` -> `Perfil` com `eu` e `email=''` nos outros |
| `_limpar_links` -> `[{url, nome, icone}]`             | `limpar_links` -> `[Link]` (`link.url/nome/icone`)    |
| `_links(bruto)`                                       | `links_gravados(bruto)` -> `[Link]`                   |
| `_rede_de`, `_lista`, `_limpar_itens`, `_garantir_chave` | `rede_de`, `lista`, `limpar_itens`, `garantir_chave` |
| `" ".join(x.split())[:MAX_TEXTO]` (5 vezes)           | `uma_linha(texto)`                                    |
| `MAX_TEXTO`; `[:600]`; `[:300]`                       | `TAMANHO_DO_TEXTO`, `TAMANHO_DO_SOBRE`, `TAMANHO_DA_URL` |
| `("http", "https")` (3 vezes)                         | `ESQUEMAS_DE_LINK`                                    |
| `datetime.now(timezone.utc).isoformat(timespec="seconds")` | `banco_sqlite.agora()`                           |
| `resumo_das_corridas(lotes: dicts, n_amostras)`       | o mesmo, com `Lote` (`l.status`)                      |

O que mudou de comportamento:
- `salvar` le nome/foto guardados, grava e sorteia a chave numa transacao so
  (BEGIN IMMEDIATE). O original usava duas conexoes: entre ler a foto e
  gravar, outro `salvar` da mesma conta podia entrar, e a foto de um deles
  se perdia. `garantir_registro` idem (INSERT + chave juntos).
- `lista()` e `links_gravados()` com JSON que nao e lista devolvem `[]`. O
  original: `_lista('"abc"')` devolvia `['a','b','c']`, `_lista('{"a":1}')`
  as chaves, e `_links` estourava (KeyError com dict, TypeError com numero).
  Nao acontece por `salvar` (sempre grava lista), so por edicao a mao.
- `listar` le a linha inteira (`SELECT *`, links inclusive); o original nao
  lia `links` no diretorio. Custo zero no volume real.
- Os `.get` de `links`/`chave` para banco pre-migracao 2/5 sairam: o
  `preparar()` do arranque garante a versao 6.

O que NAO mudou: todo o SQL (upsert com as mesmas colunas, `WHERE
perfis.nome='' AND excluded.nome<>''`, `WHERE chave=''`, ordem `nome COLLATE
NOCASE, email COLLATE NOCASE`); os padroes de `pegar` sem linha (nome =
local-part); nome em branco herda o GUARDADO e nunca o local-part; `foto=None`
mantem, `foto=''` apaga; so o `url` vai para o disco; a chave nunca gira;
chave vazia nao casa; e-mail dos outros nao sai do diretorio; `REDES` letra
por letra.

### easycontig_web/dados/pedidos_entre_labs.py (272 linhas) -> dados/pedidos_entre_labs.py

Chamadores medidos na web original: `contar_pendentes` em `comum.py:177`
(o numero da lateral, em toda pagina); `criar` (com `except NaoPode`),
`recebidos`, `enviados`, `responder` (None -> 404 "pedido nao encontrado") e
`cancelar` (False -> 404) em `rotas_de_labs_e_pedidos.py:140/163/164/184/
200`; `criar_esquema` em `servidor_web.py` (foi para `BancoSqlite.preparar`;
a DDL da tabela `pedidos` e os dois indices ja moram no `ESQUEMA` unico).
`pegar` so tem chamador dentro do proprio modulo (`responder` devolve o
pedido atualizado) e veio como metodo publico. Template `pedidos.html` le
`p.id/estado/itens/outro/motivo/resposta/contato` e
`p.quem.nome/laboratorio/instituicao/foto`; `p.recebi/criado/respondido`
existem na visao mas o template nao os le — ficaram no `Pedido` (sao o que
descreve o pedido; custo zero).

Nada ficou de fora. Uma propriedade `pendente` que eu tinha posto no
`Pedido` saiu: o template compara `p.estado == 'pendente'` e nenhum
chamador a usaria (regra: sem chamador nao entra).

Bancada `teste_pedidos.py`: 81 checks (constantes e `texto`/`lista` caso a
caso; `CAMPOS`/`DE_ONDE` identicos ao SQL original; `criar` com os 4
`NaoPode` de texto identico — para si mesmo, motivo vazio/None/so espacos,
nada do portfolio e sem `outro`, teto de 3 pendentes por par com o total no
texto; POST a mao com espacos, duplicatas, numero, dict, None e item de
OUTRO laboratorio (18S da ana mandado para a bia) -> so o declarado entra,
normalizado; `itens` como texto cru; `outro`/`motivo` cortados em 600; 15
declarados -> 12; alvo sem perfil aceita so com `outro`; a recusa do teto
desfaz a transacao e nao trava o banco; teto por direcao; linhas identicas
nos dois bancos apos cada passo; `responder`: resposta vazia -> `NaoPode`
sem tocar o banco, quem pediu/id desconhecido/ja respondido -> None nos
dois, aceitar e recusar com visao identica, `contato` so no aceito e do
lado certo, `respondido` ISO, quem sem perfil -> `Parte` com `''` (LEFT
JOIN); `cancelar` True/False em cada estado e por cada lado; `pegar` pelas
duas partes, terceiro -> None; caixas: pendentes primeiro depois `criado`
DESC, cancelado fora de `recebidos` e dentro de `enviados`, `quem` e
`contato` por linha, `contar_pendentes` por conta, LIMIT 200 com 230 linhas;
compatibilidade cruzada nos dois sentidos (criado por um, respondido e
lido pelo outro no MESMO arquivo); `Pedido.__init__` com exatamente as
chaves da visao do original, na ordem; 8 processos pedindo ao mesmo par de
uma vez -> exatamente 3 criam e 5 recebem `NaoPode`, nos dois; tabela
ausente -> `OperationalError`; `main()` por subprocess).

| Original                                                    | Novo                                                        |
|-------------------------------------------------------------|-------------------------------------------------------------|
| `criar(caminho, *, de_email, para_email, itens, outro, motivo)` | `Pedidos(banco).criar(de_email, para_email, itens, outro='', motivo='')` |
| `responder(caminho, id, quem, *, aceitar, resposta)` -> dict/None | `Pedidos.responder(id, quem, aceitar, resposta)` -> `Pedido`/None |
| `cancelar`, `pegar`, `recebidos`, `enviados`, `contar_pendentes` (caminho) | metodos de `Pedidos`                          |
| `_visao(linha, eu)` -> dict com `quem` dict                 | `visao(linha, eu)` -> `Pedido` com `quem: Parte`            |
| `_listar(caminho, eu, coluna)`                              | `Pedidos.listar(eu, coluna, esconder=())`                   |
| `_texto`, `_lista`, `_CAMPOS`, `_DE_ONDE`                   | `texto`, `lista`, `CAMPOS`, `DE_ONDE`                       |
| `MAX_TEXTO`; `LIMIT 200`                                    | `TAMANHO_DO_TEXTO`; `LIMITE_DA_CAIXA`                       |
| `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` a mao             | `banco.transacao()`                                         |
| `mod_perfil.pegar(caminho, email).get("especies")`          | `Perfis(banco).pegar(email).especies`                       |
| `_agora()`                                                  | `banco_sqlite.agora()`                                      |

O que mudou de comportamento:
- `recebidos` esconde os cancelados no SQL (`estado NOT IN (?)`), antes do
  LIMIT. O original filtrava DEPOIS do `LIMIT 200`: com 230 pedidos e os 30
  mais novos cancelados, devolvia 170 (a bancada mede) — cancelado ocupava
  vaga na caixa de quem recebe. Abaixo de 200 pedidos por conta e identico.
- `lista()` com JSON que nao e lista devolve `[]` (o original iterava um
  texto letra a letra e quebrava com `TypeError` num numero). So por edicao
  a mao do banco: `criar` sempre grava lista.

O que NAO mudou: todo o SQL (COUNT + INSERT na mesma transacao, os UPDATE
com `estado=PENDENTE` na condicao — e isso que impede dois cliques, o
`LEFT JOIN` duplo, `ORDER BY (p.estado='pendente') DESC, p.criado DESC`);
os 4 textos de `NaoPode` letra por letra; a ordem das validacoes (para si
mesmo, motivo, portfolio, teto); o filtro de `itens` contra o portfolio do
ALVO com normalizacao de espacos e sem repetidos; `contato` so no aceito e
so o e-mail do outro lado; `None`/`False` iguais para "nao existe", "nao e
seu" e "ja respondido".

### easycontig_web/contas/autenticacao.py (323 linhas) -> contas/autenticacao.py

Chamadores medidos na web original: `modo`, `google_configurado`,
`queixa_do_client_id` (bloco de import em servidor_web.py E nos 6 arquivos
de rota), `dominios_publicos`, `dominio_ok`, `entrar_na_sessao`,
`Usuario(email=, nome=)`, `url_de_ida`, `mensagem_de_erro_oauth`,
`usuario_da_volta`, `sair_da_sessao`, `usuario_da_sessao` (comum.py e
servidor_web.py), `u.nome_google` (rotas_de_entrada -> garantir_registro).

Fora, sem chamador na web:
- `exigir_usuario(request)` (401 "faça login"). Nenhuma rota usa: todas
  fazem `usuario_da_sessao` + redirect para `/entrar` via `comum`.
- `Usuario.dominio` (property). Ninguem le; `dominio_ok` recalcula do e-mail.
- o `except HTTPException: raise` dentro do `try` da volta: no novo a
  conferencia do `state` (o unico 400) acontece ANTES do `try`, entao a
  clausula nao tem o que pegar.

Duplicacao a resolver no servidor: o bloco de import
`if modo()=='google' and queixa_do_client_id(): raise SystemExit(...)`
esta copiado 7 vezes (servidor_web.py + 6 rotas). No novo web fica UMA vez,
em servidor_web.py, antes de montar as rotas.

Bancada (`teste_autenticacao.py`, 60 checagens, ORIGINAL x NOVO): constantes,
regex, URLs e tabela de erros identicas; `Usuario.__init__` com os campos da
dataclass original na ordem; `modo`/`google_configurado`/`queixa_do_client_id`
por ambiente e por valor (13 IDs: valido, espaco, aspas simples/duplas,
reticencias, placeholder, maiusculas, sufixo .br); `dominio_ok` em 357
combinacoes (21 listas x 17 e-mails: subdominio, sosia `falso-ufrrj.br`,
`=exato`, `@prefixo`, pessoa nomeada, ponto final, caixa, `*`, lista vazia,
itens vazios, sem @); `e_pessoa_nomeada`, `dominios_publicos`;
`mensagem_de_erro_oauth` em 15 codigos (o texto recebido nunca volta);
sessao num dict cruzada com o original (cookie gravado por um, lido pelo
outro); `url_de_ida` com a mesma query menos o `state` sorteado, `state`
gravado sob `oauth_estado`; a volta inteira contra um Google FALSO
(http.server local, `TOKEN`/`PERFIL` trocados nos dois modulos): Usuario
identico (e-mail minusculo/aparado, nome declarado ou local-part,
nome_google), POST do token byte a byte igual, `Authorization: Bearer`,
state errado/ausente/vazio -> 400 e consumido, sem chamar o Google;
`email_verified` falso ou sem e-mail -> 403; recusa 401 com JSON -> 502 com
`(invalid_client)`; corpo HTML -> 502 sem codigo; recusa no userinfo;
codigo cortado em 60; resposta sem `access_token`, conexao fechada e porta
fechada -> 502 "não respondeu"; o log recebe o HTTP e o corpo; `main()` por
subprocess (google com aspas no ID, e dev sem nada).

| Original                                                    | Novo                                                        |
|-------------------------------------------------------------|-------------------------------------------------------------|
| `@dataclass(frozen=True) Usuario(email, nome, nome_google)` | `class Usuario` simples, mesmos campos, mesma ordem         |
| `url_de_ida(request, redirect_uri)`                         | `Google.url_de_ida(sessao, redirect_uri)`                   |
| `usuario_da_volta(request, code, estado, redirect_uri)`     | `Google.usuario_da_volta(sessao, code, estado, redirect_uri)` |
| `_trocar_codigo(code, redirect_uri)` / `_perfil(token)` lendo `os.environ` | `Google.trocar_codigo` / `Google.perfil_do` com as credenciais do objeto |
| (GOOGLE_CLIENT_ID/SECRET lidos em cada funcao)              | `provedor_google()` -> `Google(id, secret)` lido do ambiente na hora |
| `usuario_da_sessao(request)`, `entrar_na_sessao(request, u)`, `sair_da_sessao(request)` | as mesmas com `sessao` (= `request.session`) |
| `FORMA_CLIENT_ID`, `_ERRO_OAUTH`, `_e_pessoa_nomeada`       | `FORMA_DO_CLIENT_ID`, `ERROS_DO_OAUTH`, `e_pessoa_nomeada`  |
| `print(..., file=sys.stderr)` na recusa e na rede           | `REGISTRO.warning(...)` em `easycontig.autenticacao`        |
| `except Exception: pass` ao ler o corpo da recusa            | `codigo_do_erro_do_google(bruto)` (mesmo resultado)         |

O que mudou de comportamento: nada visivel. As chaves do cookie
(`usuario`, `oauth_estado`) e o conteudo gravado (`{email, nome}`) sao os
mesmos, entao quem ja esta logado na VPS continua logado apos a troca.
`dominio_ok('ana', '')` -> True nos dois (lista vazia abre antes de olhar o
@; a rota do dev barra o sem-@ antes de chamar).

### easycontig_web/contas/limite_de_requisicoes.py (168 linhas) -> contas/limite_de_requisicoes.py

Chamadores medidos na web original: `conferir` (rotas_de_envio_de_lote,
rotas_de_perfil, rotas_de_bancos x2, rotas_de_entrada x2,
rotas_de_labs_e_pedidos x2, e o middleware `_limite_de_leitura` em
servidor_web.py), `limpar` (conftest e 3 testes), `ip_de_origem` e
`proxies_confiaveis` (testes), `chave` (so por dentro de `conferir`),
`_registro` (um teste le as chaves `login|...`).

Nada saiu. O nome da regra (`Regra.nome`, 'envio de corrida' etc.) existia
no original e NAO era lido em lugar nenhum — o 429 dizia so "limite de 20
por 3600s atingido". Agora aparece: "limite de envio de corrida (20 por 1 h)
atingido; tente de novo em 60 min".

Bancada (`teste_limites.py`, 34 checagens, ORIGINAL x NOVO): as 5 regras
identicas nos padroes e com o ambiente mexido (0, letras, negativo, branco,
espacos); `inteiro_do_ambiente` = `_int` em 10 valores; `Regra.__init__`
com os campos da dataclass na ordem; `proxies_confiaveis` em 7 listas;
`ip_de_origem` em 10 casos (forjado sem proxy, atras do proxy, cadeia,
cadeia so de proxies, sem cliente -> '?'); `conferir` passo a passo com o
relogio na mao: 3 passam, 4a 429 com o MESMO Retry-After, balde por IP e
por conta, borda da janela (600 s ainda barra, 601 passa), regra desligada
e desconhecida passam; varredura (nao roda antes de 60 s, remove so os
baldes vencidos, nao muda quem e barrado); fuzz de 3000 operacoes com
relogio aleatorio -> passa/429 + Retry-After identicos em todos e o mesmo
numero de baldes a cada passo; 8 threads x 100 batidas -> exatamente 300
passam, nos dois; `limpar`; regras e proxies a mao; `main()` por subprocess.

| Original                                                    | Novo                                                        |
|-------------------------------------------------------------|-------------------------------------------------------------|
| `_registro`, `_LOCK`, `_ULTIMA_VARREDURA` globais do modulo | `Limitador` (registro, tranca, ultima_varredura)            |
| `conferir(nome, request, quem, agora)`                      | `Limitador.conferir(nome, request, quem=None, agora=None)`  |
| `chave(request, quem)`                                      | `Limitador.identidade(request, quem=None)`                  |
| `_talvez_varrer(agora)`                                     | `Limitador.varrer(agora)`                                   |
| `limpar()`                                                  | `Limitador.limpar()`                                        |
| `regras()` (relia o ambiente a cada requisicao)             | `regras_do_ambiente()` (uma vez, ao criar o Limitador)      |
| `ip_de_origem(request)` lendo o ambiente por dentro         | `ip_de_origem(request, confiaveis)`                         |
| `_int(nome, padrao)`                                        | `inteiro_do_ambiente(variavel, padrao)`                     |
| chave do balde `'login|ip:1.2.3.4'` (texto partido no `|`)  | `('login', 'ip:1.2.3.4')`                                   |
| `@dataclass(frozen=True) Regra`; `str` = "20 por 3600s"     | `class Regra`; `str` = "20 por 1 h" (`duracao`)             |
| `_INTERVALO_VARREDURA`                                      | `INTERVALO_DA_VARREDURA`                                    |

O que mudou de comportamento:
- o texto do 429 diz qual limite e em duracao legivel (acima). O status, o
  cabecalho `Retry-After` (segundos inteiros) e QUEM e barrado sao os mesmos
  (o fuzz confere).
- tetos e proxies lidos do ambiente ao criar o Limitador, nao a cada
  requisicao: mudar EASYCONTIG_LIM_* exige reiniciar o servidor (que e o
  que ja acontece com o resto da configuracao).
- `RequisicaoDeMentira` (client.host + headers) existe para o `main()` e
  para os testes, no lugar das classes falsas que cada teste montava.

### easycontig_web/contas/cota_de_espaco.py (241 linhas) -> contas/cota_de_espaco.py

Chamadores medidos no original: `situacao(sqlite, lotes_dir, email, data_dir)`
em rotas_de_entrada (inicio), rotas_de_perfil, rotas_de_bancos e
rotas_de_envio_de_lote; `teto_lotes_ativos()` em rotas_de_envio_de_lote
(passado ao novo_lote). servidor_web, comum, rotas_de_consulta_de_lote e
rotas_de_labs_e_pedidos importam o modulo e nao usam nada dele.

Nao portado:
- `esquecer(lote_id)`: sem chamador (a docstring dizia "existe para os
  testes", mas os testes zeravam `_medidos` direto). `Cotas.medidos` e um
  dict comum: `pop`/`clear` fazem o mesmo.
- `bytes_dos_bancos(data_dir, email)`: so `situacao` chamava; absorvida
  (soma de `EstadoDoBanco.bytes` que `meus_bancos` ja calcula). O original
  chamava `meus_bancos` duas vezes (contar e somar) e percorria cada pasta
  de banco de novo com `bytes_da_pasta`.
- `_lotes_da_conta` (SELECT proprio + `except Exception: return None`):
  virou `FilaDeLotes.listar(dono, limite=0)` dentro de um try.
- o parametro `data_dir=None` de `situacao` ("opcional para nao quebrar
  chamador antigo"): todos os 4 chamadores passavam. A estante e obrigatoria
  no construtor.

Bancada (`teste_cota.py`, 61 checagens, ORIGINAL x NOVO): uma pasta de dados
de verdade (lotes/<id> com subpastas e arquivos de tamanho conhecido;
bancos/meu_<conta>_<apelido> com .nsq/.nhr/.nin/.meta.json, um banco sem
.nsq, uma pasta `<id>.novo` com lixo e um banco de outra conta) e dois
SQLite gemeos (criar_esquema x BancoSqlite.preparar) com os mesmos lotes em
recebendo/rodando/pronto/falhou (um pronto sem pasta). `Cota.__init__` com
os 8 campos da dataclass na ordem e os mesmos padroes; tetos padrao e com o
ambiente mexido (5, 0, letras, negativo, branco, ' 7 ', '3.5'); `tamanho` =
`_tamanho` em 19 valores (0, 1 B, bordas de 10 MB e 1 GB, 10 TB);
`bytes_da_pasta` em 6 caminhos (subpastas, vazia, inexistente, arquivo);
`situacao` campo a campo em 11 cenarios (conta cheia, outra conta, conta
vazia, teto de ativos, singular "1 corrida", teto de bytes, os dois
estourados, tetos 0 = sem limite, tetos 1 com conta vazia) com os textos do
motivo identicos; memoria curta (terminada nao remede, ativa remede, tirar
da memoria remede, pasta apagada a mao some da conta e da memoria, cada
Cotas tem a sua); bancos (o quebrado, o .novo e o de outra conta ficam
fora; bancos/ apagada e recriada); fila que nao responde (banco sem a
tabela lotes; caminho que e pasta) -> mesma Cota "deixa enviar" nos dois e
o aviso no log; `main()` por subprocess (com e-mail e com enter).

| Original                                                          | Novo                                                              |
|-------------------------------------------------------------------|-------------------------------------------------------------------|
| `situacao(sqlite_path, lotes_dir, email, data_dir=None)`          | `Cotas(lotes, estante, pasta_de_lotes).situacao(email)`           |
| `_medidos` global do modulo                                       | `Cotas.medidos`                                                   |
| `_bytes_do_lote(lotes_dir, lote_id, status)`                      | `Cotas.bytes_do_lote(lote)`                                       |
| `teto_*()` lidos a cada `situacao`                                | lidos ao criar `Cotas` (`max_lotes_ativos`, `max_bytes_conta`, `max_bancos`); `teto_*()` continuam para quem quiser ler o ambiente |
| `@dataclass(frozen=True) Cota`                                    | `class Cota`, mesmos 8 campos e padroes, com `__repr__`           |
| `_inteiro_do_ambiente`                                            | `configuracao.inteiro_do_ambiente` (a mesma do limite_de_requisicoes) |
| `_tamanho`, `_corridas`, `_TERMINADOS`, `_ATIVOS` (reimportado)   | `tamanho`, `corridas`, `TERMINADOS`, `Lote.ativo`                 |

O que mudou de comportamento:
- a fila que nao responde vai para o log (`easycontig.cota`, aviso com o
  tipo e o texto do erro); o original engolia em silencio. A decisao e a
  mesma: deixa enviar, com os bytes dos bancos.
- os bytes de um banco proprio sao os arquivos do NIVEL DE CIMA da pasta
  (`EstadoDoBanco.bytes`, o mesmo numero que a pagina de bancos mostra); o
  original somava recursivo. A pasta de um banco montado e rasa (o
  makeblastdb pendura tudo no prefixo), entao o numero e o mesmo — e a cota
  passa a cobrar exatamente o que a pagina mostra.
- tetos lidos ao criar `Cotas`: mudar EASYCONTIG_MAX_* exige reiniciar o
  servidor (como o resto da configuracao).
- `main()` avisa quando a fila ainda nao existe no disco, em vez de so o
  aviso do log.

Nota para a web: o original tem TRES escritas de bytes em texto (`_tamanho`
aqui, a macro `tam` em inicio.html e a conta inline em perfil.html) e DUAS
medidas de pasta (`bytes_da_pasta` aqui, `tamanho_em_bytes` no expurgo).
`tamanho` vira filtro de template em comum.py e `bytes_da_pasta` e importada
pelo expurgo — cada coisa escrita uma vez.

### easycontig_web/dados/expurgo_por_retencao.py (354 linhas) -> dados/expurgo_por_retencao.py

Chamadores medidos no original: `dias_configurados()` em configuracao.py
(ja portado como `configuracao.dias_de_retencao`); `expira_em(lote, dias)`
em rotas_de_consulta_de_lote (data "some em" na pagina do lote);
`apagar_lote(sqlite, lotes_dir, id)` em rotas_de_consulta_de_lote (botao
apagar; False -> 409 "o lote esta em uso"); `faxina(sqlite, lotes_dir,
dias=)` em trabalhador_da_fila (le `r["apagados"]` e `r["bytes"]`).
servidor_web, rotas_de_bancos, rotas_de_perfil, rotas_de_entrada,
rotas_de_labs_e_pedidos e rotas_de_envio_de_lote importam e nao usam.

Nao portado:
- `tamanho_em_bytes(pasta)`: segunda medida de pasta da web (lstat
  recursivo). Vale `cota_de_espaco.bytes_da_pasta` (stat; a diferenca so
  apareceria com link simbolico dentro do lote, que o servidor nao cria).
- `_total_de_lotes(sqlite)`: SELECT COUNT proprio; a faxina agora recebe a
  lista inteira (`FilaDeLotes.listar(limite=0)`) e conta com `len`.
- `_campo(lote, nome)` (aceitava dict ou objeto): so ha objeto `Lote`.
- `_montando_arvore` tolerando a tabela `arvores` ausente (`except
  sqlite3.OperationalError`): `BancoSqlite.preparar()` cria as 5 tabelas
  sempre; um banco sem `arvores` e um banco que o servidor nao abriu.
- `_apagar_arvores`: um DELETE dentro da mesma transacao de `apagar_lote`.
- `lotes_expirados(sqlite, dias=, agora=)` (SELECT proprio devolvendo
  dicts): virou `expirados(lotes, dias, agora)`, pura sobre a lista de
  `Lote` — quem tem a lista (a faxina, o main) chama; nao abre banco.
- `_nome_simples`: `configuracao.nome_simples` (a mesma dos outros modulos).
- `DIAS_PADRAO`/`DIAS_SUGERIDO`/`dias_configurados`: moram em configuracao
  (`DIAS_DE_RETENCAO_PADRAO`, `DIAS_DE_RETENCAO_SUGERIDO`,
  `dias_de_retencao`), onde sao lidos.

Bancada (`teste_expurgo.py`, 123 checagens, ORIGINAL x NOVO): cada cenario
nasce do zero com dois SQLite gemeos (criar_esquema x BancoSqlite.preparar)
e duas arvores de pastas iguais (lotes/<id>/entrada/a.ab1 + r.json de
tamanho conhecido), 18 lotes: pronto/falhou/na_fila vencidos, recebendo e
rodando intocaveis, pronto sem pasta, pronto com arvore `rodando`, pronto
com pedidos de arvore pronto+na_fila, criado_em 'ontem' e '', borda exata
(criado = agora-30d entra; +1 s nao), criado_em sem fuso (vale UTC), lotes
novos e uma pasta sem linha. Compara: `quando` em 9 valores e `expira_em`
em 16 combinacoes; `expirados` = `lotes_expirados` (ids e ordem) em 6
(dias, agora) e os 2 avisos de criado_em ilegivel; `apagar_lote` em 11
casos (retorno + linhas de lotes/arvores + pastas nos dois lados) e 11 ids
invalidos -> ValueError nos dois; textos dos 3 logs; `faxina` normal
(balanco campo a campo, banco e disco iguais depois, 10 linhas de log
iguais, segunda passada vazia); freio em 11 pares (vencidos, total) e na
fila (13 de 18 travam com o texto identico e nada apagado; EM_MASSA=1
solta; EM_MASSA=sim nao vale; 3 de 5 nao trava); dias 0 e negativo; um id
`../fora` na fila -> `log.exception` e os outros seguem; pasta sem
permissao -> True + ERROR "sobrou coisa" nos dois; `main()` por subprocess
(desligada, prazo simulado com 'n' e com 's', prazo configurado sem
vencidos, fila inexistente).

| Original                                                          | Novo                                                              |
|-------------------------------------------------------------------|-------------------------------------------------------------------|
| `faxina(sqlite_path, lotes_dir, *, dias, agora=None)` -> dict     | `faxina(banco, pasta_de_lotes, dias, agora=None)` -> `BalancoDaFaxina` (`.apagados`, `.ids`, `.bytes`, `.travado`) |
| `apagar_lote(sqlite_path, lotes_dir, lote_id)`                    | `apagar_lote(banco, pasta_de_lotes, lote_id)`                     |
| `lotes_expirados(sqlite_path, *, dias, agora=None)` -> dicts      | `expirados(lotes, dias, agora=None)` -> `Lote`s                   |
| `expira_em(dict ou objeto, dias)`                                 | `expira_em(lote, dias)`                                           |
| `_quando`, `_freio_pega`, `_expurgo_em_massa_liberado`            | `quando`, `freio_pega`, `em_massa_liberado`                       |
| `log`                                                             | `REGISTRO` (mesmo nome `easycontig.retencao`, mesmos textos)      |

O que mudou de comportamento:
- `apagar_lote` e UMA transacao (`BEGIN IMMEDIATE`): conferir a arvore em
  montagem, tirar a linha e os pedidos de arvore acontecem com a fila
  travada; o original abria tres conexoes (arvore, DELETE do lote, DELETE
  das arvores) e, entre conferir a arvore e tirar o lote, um pedido de
  arvore podia comecar a rodar sobre um lote que ia sumir.
- `expirados` ordena os vencidos pela data LIDA (datetime), nao pelo texto
  de criado_em: com fusos misturados ('2026-07-10T00:00:00' sem fuso e
  '...+00:00') a ordem do texto podia divergir da cronologica. Na bancada
  as duas ordens coincidem. Os avisos de criado_em ilegivel podem sair em
  outra ordem (a lista vem de `listar`, mais novo primeiro).
- `faxina` nao conta os lotes num SELECT separado: `len(lotes)` da mesma
  lista que gerou os vencidos, entao o freio compara numeros do mesmo
  instante.
- `main()` aceita o prazo em argv (`... 30` simula sem mexer no ambiente),
  lista os vencidos com dono e tamanho, avisa se o freio pegaria e so apaga
  depois de 's'.

### easycontig_web/processamento/trabalhador_da_fila.py (215 linhas) -> processamento/trabalhador_da_fila.py

Chamadores medidos: so o `docker-compose.yml` (`python -m
easycontig_web.processamento.trabalhador_da_fila`, `replicas:
${TRABALHADORES:-2}`); nenhum modulo Python importa o trabalhador. O README
(linha 114) manda subir `python -m easycontig_web.trabalhador`, modulo que
nao existe — corrigir na reescrita do README.

Nao portado:
- `_parar` (global) e `_pedir_parada(*_)`: sao `Trabalhador.parada_pedida`
  (Event) e `Trabalhador.pedir_parada`.
- `bater_pulso(cfg, eu, parar)` como funcao de modulo: e
  `Trabalhador.bater_pulso()`, sobre os atributos.
- `rodar_um(cfg, eu)` / `rodar_uma_arvore(cfg, eu)`: metodos sem argumento
  (o cfg e o eu sao do objeto).
- `from __future__ import annotations`, type hints, `# noqa: BLE001`.

Bancada (`teste_trabalhador.py`, 75 checagens, ORIGINAL x NOVO): os dois
executores trocados por falsos que registram a chamada, avisam progresso e,
conforme o modo, devolvem, falham ou passam o lote para outro dono no meio;
dois SQLite gemeos com as mesmas linhas cruas. Compara linha a linha (menos
os carimbos de hora) e os logs (menos os segundos): fila vazia; lote normal
(pronto, feito 2/2, etapa limpa, dono, "pronto em T s (3 amostras)"); lote
sem referencia; recebendo/rodando/pronto nao sao pegos; executor que falha
(FALHOU com "ValueError: ..." e log com traceback); lote perdido para o
irmao (segue RODANDO com ele, WARNING, sem carimbo PRONTO); o mesmo para
os pedidos de arvore (resumo gravado, referencia do lote, lote inexistente
-> referencia vazia, falha, perda); prioridade lote > arvore em duas
voltas; `faxinar` (apaga no arranque, respeita a hora, quebra sem derrubar
o laco, retencao 0 em silencio); `arrancar` contra a sequencia do `main()`
original (do morto volta, do vivo fica, o meu antigo volta, sem dono fica
quando ha vivo e volta quando nao ha; upload velho FALHOU, upload de agora
fica; arvores idem; pulso registrado; thread "pulso" daemon que avanca
`visto_em` sozinha e para com `parar_pulso`); `rodar()` numa thread (atende
2 lotes e a arvore, `pedir_parada` sai na hora, SIGTERM no meio do lote NAO
para o pulso e o lote termina PRONTO antes de encerrar, erro no laco e
registrado e o laco continua); `main()` dos DOIS por subprocess com o
executor de verdade e um lote de pasta vazia (FALHOU com ValueError nos
dois), SIGTERM -> saida 0 e os mesmos textos no log.

| Original                                                          | Novo                                                              |
|-------------------------------------------------------------------|-------------------------------------------------------------------|
| `main()` fazendo o arranque inline + laco                         | `Trabalhador(cfg, eu='')` com `arrancar()` e `rodar()`            |
| `_parar` global + `_pedir_parada`                                 | `parada_pedida` (Event) + `pedir_parada(*_)`                      |
| `parar_pulso` local do main                                       | `Trabalhador.parar_pulso`                                         |
| `proxima_faxina`/faxina inline no laco                            | `faxinar()`                                                       |
| `rodar_um(cfg, eu)`, `rodar_uma_arvore(cfg, eu)`                  | `rodar_um()`, `rodar_uma_arvore()`                                |
| `fila.x(cfg.sqlite_path, ...)`, dicts                             | `self.lotes`/`self.arvores` (`FilaDeLotes`/`FilaDeArvores`), `Lote`/`PedidoDeArvore` |
| `retencao.faxina(...)["apagados"]`                                | `expurgo.faxina(...).apagados`                                    |
| `ocioso = 0.25`, `time.sleep(2)`                                  | `OCIOSO`, `PAUSA_APOS_ERRO`                                       |
| `log`                                                             | `REGISTRO` (mesmo `easycontig.trabalhador`, mesmos textos)        |

O que mudou de comportamento:
- `falhar_envios_abandonados()` e chamado pelo trabalhador no arranque (no
  original ficava dentro de `reenfileirar_orfaos`) e ganha log: "N envio(s)
  parados há mais de meia hora foram dados por interrompidos".
- os sinais sao instalados antes do arranque: SIGTERM durante o
  `reenfileirar_orfaos` nao mata mais o processo no meio.
- ocioso, o laco espera no proprio Event (`parada_pedida.wait`): a parada
  sai na hora em vez de esperar o `sleep`.
- `identidade()` publica (maquina:pid), para o servidor ou a bancada
  saberem quem e o trabalhador.


### easycontig_web/web/comum.py (212 linhas) + servidor_web.py (585 linhas) -> web/comum.py + web/instalacao.py + servidor_web.py

Chamadores medidos na web original: `cfg()` (todas as rotas, a cada uso);
`_u`/`_exigir`/`_por_recado`/`_pegar_recado`/`_casca`/`_lote_do_usuario`/
`_referencias`/`_vaga_pesada`/`_marcar_resposta`/`TEMPLATES`/`EXT_ACEITAS`
(rotas e servidor); `_destino` reexportado em servidor_web.py so para a
suite antiga (`tests/`); `MAX_CORPO`, `MAX_CORPO_SEM_SESSAO`, `MAX_PESADAS`,
`_VAGAS_PESADAS` (servidor e comum, por import tardio).

Nao portado:
- `cfg()` (comum.py:36): relia `config.carregar()` do ambiente A CADA USO,
  para a suite antiga recarregar o servidor 28 vezes. A configuracao e lida
  UMA vez, na `Instalacao`; um teste que quer outra pasta cria outra
  `Instalacao` e outro app (`servidor_web.criar_app`).
- `_vaga_pesada` (comum.py:202) importando `servidor_web` por dentro da
  funcao para escapar do import circular: e `Instalacao.vaga_pesada()`,
  sobre o semaforo do proprio objeto; nao ha mais ciclo.
- `_destino = rotas_de_entrada._destino` (servidor_web.py): reexport para a
  suite antiga; a suite sera reescrita, nao ha o que reexportar.
- `_DOCS` global lida no import: e `producao()` + o `docs` local do
  `criar_app` (mesma regra: `/api/docs` fora de producao, nada em producao).
- o texto do 413 escrito DUAS vezes (servidor_web.py:197-200 e 291-294): e
  `recado_do_teto(teto)`.
- as 84 linhas de cabecalho repetidas em cada rota (`cfg = config.carregar()`,
  a queixa do PRODUCAO, os imports de comum; linhas 7-75 identicas em quatro
  dos seis arquivos): a queixa do client_id saia SETE vezes (uma por modulo);
  sai uma, pelo logger.
- os `print(..., file=sys.stderr)` do erro nao previsto e das queixas: vao
  para o logger `easycontig.servidor` (sem handler, o Python os imprime no
  stderr do mesmo jeito).
- `from __future__ import annotations`, type hints fora das assinaturas das
  rotas, `# noqa: BLE001`.

Bancada (`teste_web.py`, 195 checagens, ORIGINAL x NOVO, os dois apps de
verdade por `TestClient`, cada um com a sua pasta e o seu SQLite): estrutura
(mesmos caminhos e metodos em `app.routes`); paredes sem sessao (401 -> 303
/entrar?proximo=..., JSON 401 para /api, 403 de origem, 413 por teto, 429
do limitador de login com o mesmo Retry-After); entrada dev e Google
desligado; paginas com sessao (inicio, perfil, labs, pedidos, bancos, leia-
me) comparadas depois de normalizar ids/chaves/fotos/nonces; labs e pedidos
(pedir, responder, cancelar, o 404 de pedido alheio); bancos (503 com
`Retry-After: 5` quando as vagas pesadas acabam, cota `max_bancos`, admin
remove do catalogo, 413 do FASTA, FASTA sem `>`); lote inteiro com o
TRABALHADOR de verdade (tracy + blastn) nos dois lados, pagina do lote,
tabela, pagina da amostra, `/api/lotes/{id}` (mesmas chaves), traco (mesmas
chaves), consulta contra banco proprio montado do consenso do proprio lote
(1 acerto nos dois, mesmos numeros), pedido de arvore, apagar com pedido na
fila, perfil antes e depois, 500 previsto pelo handler; `main()`s.

| Original                                                          | Novo                                                              |
|-------------------------------------------------------------------|-------------------------------------------------------------------|
| `cfg()` a cada uso                                                | `Instalacao(cfg)` em `app.state.instalacao`; `comum.instalacao_de(request)` |
| `_u`, `_exigir`, `_por_recado`, `_pegar_recado`, `_marcar_resposta` | `usuario`, `exigir`, `por_recado`, `pegar_recado`, `marcar_resposta(resposta, nonce, https_only)` |
| `_referencias(u)`, `_casca(u)`, `_lote_do_usuario`, `_relatorio`  | `Instalacao.referencias(email)`, `.casca(usuario)`, `.lote_do_usuario`, `.relatorio` |
| `_vaga_pesada()` + `_VAGAS_PESADAS` em servidor_web               | `Instalacao.vaga_pesada()` sobre `Instalacao.vagas`               |
| `_e_administrador(u)` relendo EASYCONTIG_ADMINS a cada clique     | `Instalacao.e_administrador(usuario)` (lista lida uma vez)        |
| `MAX_CORPO`, `MAX_CORPO_SEM_SESSAO`, `MAX_PESADAS` no import      | atributos da Instalacao (`inteiro_do_ambiente`: invalido -> padrao) |
| `_br(valor)` (filtro `br`)                                        | `decimal_brasileiro` (mesmo filtro `br`)                          |
| tres formatadores de bytes (comum, perfil, cota)                  | `cota_de_espaco.tamanho`, filtro `tamanho`                        |
| `app` montado no import, rotas por `app.router.routes.extend`     | `criar_app(instalacao)`; o `extend` FICOU (medido: no fastapi 0.141 o `include_router` guarda um `_IncludedRouter` sem `.path` e some das listas) |
| `_origem_confere`, `_teto_do_caminho`, `_corpo_grande_demais`     | `origem_confere(request, url_base)`, `teto_do_caminho(instalacao, caminho)`, `corpo_grande_demais(instalacao, request)` |
| `_limite_de_leitura`, `_sem_cache`, `_cabecalhos_de_seguranca`    | os mesmos, sem sublinhado                                         |
| `_erro`, `_erro_nao_previsto`                                     | `erro_http`, `erro_nao_previsto` (mesmo JSON, mesmo erro.html, mesma pagina de socorro) |
| `perfil_lateral = {"nome", "foto"}` na casca                      | o proprio `Perfil` (o template le `.nome` e `.foto`)              |

O que mudou de comportamento:
- a configuracao (e EASYCONTIG_ADMINS, URL_BASE, HTTPS_ONLY, SECRET_KEY, os
  tetos) e lida uma vez no arranque: mudar o ambiente exige reiniciar o
  servidor — que e o que ja acontecia com o Docker.
- o esquema do SQLite e criado uma vez (`Instalacao.preparar`), nao tres.
- o texto do 429 diz qual limite e a janela legivel (veja
  limite_de_requisicoes acima); status e Retry-After iguais.


### easycontig_web/web/rotas_de_*.py (6 arquivos, 1540 linhas) -> web/rotas_de_*.py

Todas as rotas tem chamador (o navegador e o oficina.js); nenhuma saiu.
Saiu o que era andaime:

- `rotas_de_bancos._so_uma_montagem` (rotas_de_bancos.py:104-140, trava
  O_EXCL `<prefixo>.montando.lock`): a `EstanteDeBancos.trava(id)` faz o
  mesmo por dentro de `montar` e de `montar_do_usuario`, e levanta
  `BancoOcupado`, que a rota devolve como 409 com o motivo da estante. O
  original so travava o montar do catalogo (linha 167, download incluido);
  o `enviar_banco` rodava o makeblastdb do banco do usuario SEM trava.
- `rotas_de_consulta_de_lote._relatorio` e `_pedido_do_usuario`: sao
  `Instalacao.relatorio` e `pedido_do_usuario(instalacao, lote_id, u)`.
- `rotas_de_consulta_de_lote._arquivo(lote_id, u, chave, midia, baixar_como)`
  -> `arquivo_do_lote(request, lote_id, qual, midia, baixar_como)`.
- `rotas_de_entrada._destino`/`_fora_do_dominio`/`_redirect_uri` ->
  `destino_local`, `fora_do_dominio(cfg)`, `redirect_uri(request, url_base)`.
- `criar_lote` (130 linhas numa funcao) partiu em `conferir_referencia`,
  `tamanho_declarado`, `abrir_lote`, `gravar`; `salvar_perfil` ganhou
  `guardar_foto` e `arquivo_da_foto`; `pagina_perfil` ganhou
  `relatorios_prontos`.
- `dados/leitura_de_amostras.amostra(lote, chave)` (escrita nesta
  refatoracao): ficou sem chamador quando a pagina da amostra passou a
  chamar `formatar_amostra(relatorio.amostra(chave))` — a mesma funcao da
  tabela, para linha e pagina nunca divergirem. Removida.

O que mudou de comportamento (cada item esta no "O QUE MUDOU" do modulo):
- `POST /api/lotes/{id}/amostras/{chave}/consultar` devolve `acertos` em vez
  de `hits`: cada acerto tem `organismo`, `acesso`, `titulo`, `identidade` e
  `cobertura` com 1 casa (as MESMAS que o relatorio grava; o original
  arredondava igual), `valor_e` e `pontuacao` como numeros. Sairam `bits` e
  `alinhado` (texto; o oficina.js nao os lia) e `e_value` como texto. O
  oficina.js passa a ler `acesso`. Sem blastn na instalacao, 500 com a
  orientacao (o original quebrava com FileNotFoundError e virava o 500
  generico).
- `ErroDoBanco`/`ErroDoNcbi` chegam a tela do /bancos como recado ("o arquivo
  nao parece um FASTA (nenhuma linha comeca com >); confira se enviou o
  arquivo certo"); o original mostrava so "nao foi possivel montar o banco;
  tente de novo. O motivo completo esta no log". O generico continua para o
  que nao foi previsto.
- o FASTA do usuario e medido em BYTES antes de decodificar (o original
  decodificava com 'replace' e media caracteres: 20 MB + 1 byte com acento
  multibyte passava).
- `api_lote`/`api_arvore` devolvem `vars(Lote)`/`vars(PedidoDeArvore)`:
  conjunto de chaves medido identico ao original nos dois; o `resumo` da
  arvore ja vem como lista, nao como texto JSON.
- a tabela e a pagina da amostra recebem chaves em portugues (`chave`,
  `acesso`, `valor_e`, `cobertura_da_consulta`, `pct_cobertura_1`; veja
  leitura_de_amostras); um relatorio.json de versao antiga da "relatorio
  indisponivel" (Passo 8: relatorios velhos podem ser expurgados).
- o item do bancos.html e `{"ficha": ..., "estado": ...}` (era `"c"`).
- avisos que iam para o stderr (falha de disco no envio, retorno do OAuth com
  erro, consulta que falhou) vao para os loggers.
- apagar um lote com pedido de arvore NA FILA apaga o pedido junto, como o
  original (medido: so `rodando` bloqueia, nos dois).

Decididos com o Jared (mantidos como o original):
- no teto de `max_bancos`, remontar um apelido que JA E SEU continua recusado
  ("voce ja mantem N bancos proprios (o limite e N); remova um antes de
  montar outro").
- a consulta mostra identidade com 1 casa (99,5), igual ao relatorio.


### easycontig_web/templates/*.html (14 arquivos) + estatico/oficina.js (3580 linhas) -> templates/ + estatico/oficina.js (3877 linhas)

Nada saiu: todo template tem rota que o renderiza e o oficina.js e carregado
pela amostra.html. O DESENHO VISUAL nao mudou — markup, regras de CSS e
codigo JS sao os mesmos do original; o que cresceu foram os cabecalhos e os
comentarios. Tres provas independentes de que a pagina servida e a mesma:

- `teste_web.py` (197 checks): as duas apps (original e nova) sobem com o
  mesmo banco sintetico e cada rota e comparada apos normalizar comentarios
  e espacos; so diferem onde esta documentado abaixo.
- `teste_templates.py` (16 casos): cada template renderizado com Jinja puro
  em ramos que as rotas nao alcancam facilmente (arvore pronta/erro/na fila,
  amostra com erro/sem acerto, lote em processamento, pedidos misto, perfil
  editando, labs com cartoes, bancos montado, diagnostico ruim, entrar com
  Google, erro 409/413), com diff caractere a caractere.
- `teste_oficina.py` (104 checks, Playwright): oficina.js original e novo
  lado a lado — traco, teclado, arrasto, zoom, rascunho e consulta.

O que mudou:

- **oficina.js virou tres classes**: `Oficina` (dona do traco recebido,
  das sequencias editadas, da marca, da lente, da pilha de desfazer, da
  amplitude e dos nos do alinhamento), `Rascunho` (dona do sessionStorage:
  chave `easycontig:rascunho:` + pathname, validade de 12 h) e `Arrasto`
  (o gesto em curso numa tela: ponteiro capturado, ponto de partida).
  Numeros soltos ganharam nome (`JAN`, `ARRASTO_MIN`, `AMP_MIN/AMP_MAX/
  PASSO_DA_AMP`, `TETO_DA_PILHA`, `VALIDADE_DO_RASCUNHO_MS`, `COR`).
  O contrato com o servidor e o mesmo: JSON do traco, JSON da consulta
  (`{acertos: [{titulo, identidade, cobertura, acesso}]}` — era `hits`/
  `accession`), ids do DOM e chave do sessionStorage.
- **cabecalho de todo template**: `{#* nome.html — o que e a pagina, qual
  rota a renderiza (modulo + funcao) e cada chave do contexto -#}`. O `-#}`
  come a quebra de linha seguinte: sem ele o HTML servido comecaria com uma
  linha vazia antes do `<!doctype html>` (o teste_templates.py exige que
  toda pagina comece pelo doctype). Os includes (`_logo.html`,
  `_amostras_tabela.html`) tem o mesmo cabecalho.
- **convencao de comentario** (a mesma do Python, adaptada a cada
  linguagem): Jinja `{#* o que #}` / `{#! por que / aviso #}`; CSS
  `/** o que */` / `/*! por que */`; JS `//*` / `//!` (e `/** */` em bloco).
  Os cabecalhos copiados do repositorio antigo (ADR, achados, historico de
  bug) foram condensados numa linha de "por que" onde a regra ainda vale e
  apagados onde so contavam historia.
- **bancos.html avisa o tempo do NCBI**: paragrafo `aviso-ncbi` acima dos
  grupos dizendo que Montar baixa as sequencias na hora, que a pagina fica
  carregando (segundos nos pequenos, minutos nos grandes; 3 pedidos por
  segundo) e que nao se deve recarregar nem apertar de novo. O original nao
  avisava e o usuario recarregava no meio do download (decisao: sem cache
  no cliente, aviso no front).
- **perfil.html corrige a nota do portfolio**: o original dizia "Nada aqui
  e publicado hoje: por enquanto so voce ve" — ficou falso quando o /labs
  entrou (o perfil aparece la para quem entrou no site e e por ele que
  outro laboratorio pede amostra). A nota nova diz isso e que nenhuma
  sequencia sai dali, so o que foi declarado.
- **chaves do contexto em portugues por inteiro**: amostra (`chave`,
  `acesso`, `cobertura_da_consulta`, `valor_e`, `pct_cobertura_1`), bancos
  (`ficha`), consulta (`acertos`); a chave `lotes` saiu do contexto de `/`
  (o inicio.html nunca a lia; rotas_de_entrada, item 4).

Testes antigos que vao ser reescritos junto com a suite (os nomes que eles
procuram por grep mudaram): `test_oficina_arrasto.py`,
`test_rascunho_nao_sobrevive_a_aba.py`, `test_css_nao_vaza_para_a_lateral.py`.

Os dois textos VISIVEIS que mudaram (o aviso do NCBI no bancos.html e a
nota do portfolio no perfil.html) foram lidos e aprovados por Jared em
2026-09-08 ("pode inserir o texto").

### pyproject.toml + Dockerfile + docker-compose.yml + tools/ + tests/ (39 arquivos, 453 testes) -> os mesmos + tests/ (16 arquivos, 558 testes, 702 casos)

O pacote e a suite. Nada de codigo saiu; o que saiu foi ANDAIME:

- **`tools/preparar_nucleo.sh` deixa de existir.** O nucleo e os .py da raiz
  deste repositorio, rastreados pelo git: o Dockerfile faz `COPY *.py ./` e
  `pip install .`, e o `nucleo/` (copia do repo da demo, ignorado pelo git)
  nao e mais lido por nada. O `docker-compose.yml` perde o passo "rode o
  preparar_nucleo".
- **A lista de dependencias escrita a mao no Dockerfile** (`pip install
  --no-deps .` + fastapi, uvicorn, ... , matplotlib) vira `pip install .`: uma
  lista so, a do pyproject.toml. O teste que vigiava as duas listas
  (`test_toda_dependencia_do_pyproject_esta_no_pip_do_dockerfile`) perde o
  motivo e nao volta.
- **pyproject.toml**: `py-modules` lista os doze modulos do nucleo um a um
  (modulo novo na raiz precisa entrar ali; o comentario diz por que), os
  cinco subpacotes da web um a um, e o `package-data` leva o favicon.svg
  junto com os templates e o oficina.js — a lista antiga so levava o .js e o
  icone faltava na imagem. `[tool.pytest.ini_options]`: `python_files =
  teste_*.py` e `pythonpath = ["."]`, para a suite rodar sem instalar.
  **As bibliotecas do nucleo (biopython, pyfamsa, dendropy) entram em
  `dependencies`**: o original as recebia por tabela, pelo pacote
  `easycontig-br-core`, e a primeira versao do pyproject novo tirou o pacote
  sem trazer as bibliotecas — achado em 2026-09-08 na conferencia antes do
  primeiro commit (a suite local nao sentia porque o .venv ja as tinha; a
  imagem subiria e morreria no primeiro .ab1). O numpy, que o pacote antigo
  tambem trazia, NAO entra: nenhum modulo do nucleo novo o importa. O guarda
  `teste_toda_biblioteca_que_o_codigo_importa_esta_nas_dependencias`
  (teste_arvores.py) varre os imports de terceiros e cobra cada um em
  `dependencies`, para a falta nao voltar.
- **`.env.example` -> `.env.exemplo`**: o antigo estava no .gitignore por
  trazer caminhos pessoais; o novo e rastreado e nao traz nenhum.
- **`build/` esta rastreado por engano no repositorio antigo** (34 arquivos
  de um `pip install` velho). Sai no primeiro commit da branch nova com
  `git rm -r --cached build`.
- **`ferramentas.proposta.anterior.py`** (na pasta de trabalho) e historico da
  refatoracao e NAO entra no repositorio.
- **O NUCLEO E O FRONT SAEM DO VERSIONAMENTO** (decisao de Jared em
  08/09/2026, antes do primeiro push). Ficam de fora do git os 12 modulos da
  raiz e `easycontig_web/templates/` + `easycontig_web/estatico/`: sao o
  mecanismo — a ciencia e a tela —, e o repositorio passa a carregar so o
  andaime (a web, a suite, o empacotamento, a documentacao). O repositorio e
  privado; isto e a segunda camada.
  - O nucleo NUNCA esteve neste repositorio (vinha como `nucleo/`, ignorado),
    entao ele nao aparece em nenhum commit. O FRONT, sim: `templates/*.html`,
    `oficina.js` e o favicon sao rastreados aqui desde antes da refatoracao e
    seguem no historico da `main` que ja esta no GitHub. O que fica de fora e
    a VERSAO REESCRITA; tirar a antiga exigiria reescrever o historico e um
    push forcado na main — nao feito, e nao se faz sem pedido explicito.
  - Como isso nao vira uma armadilha:
    - `.gitignore` traz `/*.py`, `/easycontig_web/templates/` e
      `/easycontig_web/estatico/`, com as consequencias escritas ali;
    - `tools/enviar_privado.sh` leva os dois grupos para o servidor (a lista
      do nucleo sai do `py-modules` do pyproject, e nao de `ls *.py`, senao um
      arquivo solto na raiz viajaria junto), recusa destino que nao seja o
      repositorio e confere do outro lado o que chegou;
    - o `Dockerfile` confere os arquivos numa camada propria: faltando, o
      BUILD falha dizendo o que rodar — em vez de subir uma imagem que morre
      no primeiro `.ab1` (nucleo) ou serve 500 em toda pagina (front);
    - o `tests/conftest.py` confere antes dos imports e diz o que falta e a
      linha de rsync que resolve, em vez de um `ImportError` cru.
  - Preco aceito: um clone limpo nao sobe nem passa na suite, e nao existe
    mais copia versionada desses arquivos em lugar nenhum — o que os protege
    e o backup da maquina de desenvolvimento e a copia que vive na VPS.
- **`tools/`** foi reescrito no mesmo estilo do resto (decisao de Jared em
  2026-09-08: "Tools pode reestilizar"). Cada arquivo tem no cabecalho o
  seu "O QUE MUDOU"; o resumo:
  - `make_ab1.py` virou `gerar_ab1.py` (classe `Escritor`, que so acumula
    entradas e grava o ABIF). Os .ab1 que ele produz sao BYTE A BYTE os
    mesmos do original (conferido em 3 casos: padrao, --bases curto e
    destino em pasta nova). Um bug do original caiu: `--sequencia` com N
    prometia aceitar N e morria com `KeyError: 'N'`; agora o pico do N se
    reparte pelos quatro canais.
  - `sinteticas.py`: mesmos 6 arquivos byte a byte, mesmas tres mensagens
    de recusa e mesmo codigo de saida 2 (o carga.sh depende dele).
  - `gerar_ics.py`: mesmo .ics byte a byte; o destino pode vir em argv[1]
    (o original so gravava no cwd) e a pasta de destino e criada.
  - `carga.py`: mesmas opcoes, mesmos textos, mesmas chaves do JSON;
    `conferir_tetos(sessao)` recebia a sessao sem usa-la e virou
    `avisos_de_teto()`; o `import statistics` que nada usava saiu.
  - `backup.sh`: mesmas etapas, mensagens, codigos de saida e formato de
    destino (um backup do original e um deste sao intercambiaveis, inclusive
    o --link-dest entre eles). Dois defeitos do ORIGINAL cairam, achados ao
    rodar os dois lado a lado em 5 cenarios: (a) origem com fila.sqlite3
    mas sem `lotes/` encerrava com saida 1 SEM MENSAGEM (o `find` falhava e
    o set -e derrubava o script, ate com PERMITIR_VAZIO=1) — agora diz que
    a pasta falta e que o app a cria ao subir; (b) o backup anterior do
    --link-dest era a ultima pasta do destino em ordem alfabetica, QUALQUER
    pasta: uma pasta estranha no destino desligava o link fisico em silencio
    (o rsync so avisava "--link-dest arg does not exist" e cada backup
    passava a custar o acervo inteiro) — agora so pastas com cara de backup
    deste script contam, como o expurgo ja fazia.
  - `carga.sh`: mesmas variaveis de ambiente exportadas (conferido por
    diff da lista de `export`), mesmos arquivos de saida.
  carga.sh troca os dois nomes de modulo
  (`easycontig_web.main:app` -> `easycontig_web.servidor_web:app`,
  `easycontig_web.trabalhador` -> `easycontig_web.processamento.
  trabalhador_da_fila`). Os dois nomes que o carga.sh original chamava NAO
  EXISTIAM no repositorio original — la o app ja morava em
  `easycontig_web.servidor_web` e o trabalhador em
  `easycontig_web.processamento.trabalhador_da_fila` (e o docker-compose.yml
  e o Dockerfile originais chamam esses); o script ficou apontando para um
  arranjo anterior e ja estava quebrado antes da refatoracao. carga.py le a
  chave da amostra em
  `amostras[].chave` do relatorio.json (era `samples[].key`).
- **`implantacao/`, `LICENSE`, `TERMOS.md`, `docs/`** nao sao codigo e nao
  foram tocados: seguem no repositorio como estao. O nginx-easycontig.conf
  continua valendo — as tres rotas com teto proprio (`/lotes`,
  `/bancos/meu`, `/perfil`) tem o mesmo caminho.

A suite foi reescrita inteira (decisao: TUDO da web e refatorado, testes
inclusive). Os 39 `test_*.py` viraram 16 `teste_*.py`, cada um com
cabecalho dizendo o que protege e por que, e `main()` no fim:

| original                                                      | agora                                   |
|---------------------------------------------------------------|-----------------------------------------|
| test_amostras, test_contagem_amostras, test_avulsas, test_executor, test_orientacao_sai_do_dna, test_fronteira_contas (teto de tempo do blastn) | teste_executor |
| test_arvore_do_lote, test_filogenia                           | teste_arvores (+ teste_filas, teste_trabalhador nas partes de fila) |
| test_dominio_de_entrada, test_entrar_nao_publica_nomes, test_oauth_nao_ecoa_erro, test_fronteira_contas (destino do ?proximo=) | teste_autenticacao |
| test_montar_banco_referencia, test_banco_compartilhado_nao_e_sobrescrito, test_fronteira_contas (bancos, administrador, teto de tempo do makeblastdb) | teste_bancos |
| test_migracoes                                                | teste_banco_sqlite |
| test_cotas, test_cotas_memoria, test_cota_bancos_e_rajada, test_limite_por_conta, test_ip_forjado, test_dia_ruim (cota) | teste_cotas_e_limites |
| test_retencao, test_dia_ruim (freio)                          | teste_expurgo |
| test_fila, test_fila_atendida                                 | teste_filas, teste_trabalhador |
| test_web, test_seguranca (lotes)                              | teste_lotes_na_web |
| test_oficina_arrasto, test_rascunho_nao_sobrevive_a_aba       | teste_oficina (+ teste_templates: a entrada recolhe o localStorage antigo) |
| test_pedidos                                                  | teste_pedidos |
| test_labs, test_perfil_e_casca, test_perfil_link_nao_derruba, test_fronteira_contas (foto) | teste_perfil_e_labs |
| test_achados_verificados, test_achados_2026_08_11, test_saude, test_teto_de_corpo, test_teto_pesado, test_teto_sem_sessao, test_seguranca (cabecalhos, origem, 500), test_fronteira_contas (docs) | teste_servidor_web |
| test_css_nao_vaza_para_a_lateral, test_leiame, test_achados_verificados (nonce) | teste_templates |
| (tetos do ambiente espalhados por varios)                     | teste_configuracao |

O que NAO voltou, e por que:

- os testes sobre "o lote real de 40 amostras" (`test_resumo_conta_o_lote_
  real_de_40_amostras`, `test_no_lote_real_*`, `test_a_corrida_real_nao_muda_
  com_o_conserto`): dependiam de um relatorio gravado fora do repositorio.
  Viraram relatorios FABRICADOS pelo conftest (`relatorio_fabricado`), com os
  mesmos casos (sem acerto, sem ferramenta, invertido, ressalvas).
- `test_o_teste_pega_o_defeito_que_o_motivou` (teste do teste): o
  teste_templates.py exercita o mesmo defeito direto (a regra `.ac` solta e
  pega; `.trabalho .ac` passa).
- o vigia das duas listas de dependencia do Dockerfile (acima).

O que a reescrita ACHOU (defeitos que a suite antiga nao via):

- **pedidos entre labs**: o recado do aceite/recusa nao chegava a tela
  (rotas_de_labs_e_pedidos; corrigido e coberto pelo teste_pedidos.py).
- **oficina.js**: a hora do rascunho recuperado saia cortada
  (`toLocaleString('pt-BR').slice(0, 16)` -> "05/09/2026, 19:3" no Chromium
  de hoje, que poe ", " entre data e hora). Agora e
  `{ dateStyle: 'short', timeStyle: 'short' }`. O original tem o mesmo bug.
- **tools/carga.py** lia `samples[].key` e pulava o traco em silencio
  (acima).
- **conftest antigo**: o ajudante que fabricava relatorio gravava `valor_e`
  como numero; o nucleo grava como texto ("1e-50"), e a tela le texto.
- o `servidor_web.py` cria o `app` do uvicorn AO SER IMPORTADO (herdado), e
  o conftest antigo o importava no topo: cada rodada da suite deixava um
  `./dados/` com fila.sqlite3 na raiz do repositorio. O conftest novo importa
  `criar_app` dentro da Bancada, com EASYCONTIG_DATA_DIR ja apontado.
- quatro buracos que a conferencia cruzada (nome a nome, 453 x 554) apontou
  na suite NOVA e foram fechados: `/api/docs` e `/openapi.json` fora do ar
  em producao; "todo POST que nao e o /entrar recusa sem sessao" — agora
  percorrendo a lista de rotas do proprio app (12 POST), nao uma lista
  copiada (os dois no teste_servidor_web.py); e os tetos de tempo do
  makeblastdb (teste_bancos.py) e do blastn (teste_executor.py). O original
  os testava com `subprocess.run` trocado por uma funcao que levanta
  TimeoutExpired — prova que o codigo TRATA o estouro, nao que ele ESTOURA.
  Os novos rodam um programa de mentira que dorme 30 s com teto de 0,3 s e
  medem o relogio: sem o `timeout=` a suite cai (e leva 60 s para cair).
- a conferencia por nome deixou passar esses dois tetos, entao a suite
  original foi lida INTEIRA, corpo a corpo (os 453 testes, em quatro
  auditorias independentes que so conheciam os dois diretorios): 443 com
  equivalente que exercita a mesma regra, 10 PARCIAIS (o teste novo existia
  mas prendia menos que o original), 0 sem equivalente. Os dez foram
  fechados: os pares `joao.silva`/`joaosilva` e dois e-mails compridos que
  so diferem no fim, e "o e-mail nao aparece na pasta" (espaco_da_conta);
  `/\tevil.com` no destino do login; CSV `situacao_id` = `sem_acerto` com
  a tela em `nao_montou` para o consenso de 0 pb (a divergencia e de
  proposito e agora esta presa dos dois lados); a arvore CONCLUIDA devolve
  o lote a faxina (a trava e da montagem, nao do pedido); a PAGINA_DE_SOCORRO
  (quando ate o erro.html cai) com os mesmos cabecalhos e sem a excecao; o
  Limitador atras de um proxy declarado separa os baldes pelo IP
  encaminhado; HEAD /saude doente = 503 (o monitor fala HEAD); a CSP com
  `frame-ancestors 'none'` e `base-uri 'none'`; PROXIES_CONFIAVEIS definida
  mas VAZIA e cobrada e um IP declarado e aceito; e `"  NENHUM  "` e o mesmo
  conjunto vazio. Cada um passou por mutacao (o codigo trocado de proposito
  tinha de derrubar o teste): os dez cairam.

Como a suite foi conferida (alem de passar): cada arquivo novo passou por
MUTACAO do codigo que protege — troca deliberada de um sinal, um teto, uma
chave, uma linha apagada — e a suite tinha de cair em cada uma. No
oficina.js foram 27 mutacoes; 26 cairam. A que sobreviveu ("o click depois
do arrasto nao e engolido") e EQUIVALENTE no Chromium: com a captura do
ponteiro, o click de compatibilidade vai para a `tela` (alvo comum do down
e do up), nunca ao canvas — medido com um ouvinte no documento. O guarda
fica como segundo cadeado, com o comentario dizendo isso.

Playwright (teste_oficina.py) entra em `[project.optional-dependencies] dev`
do pyproject (decisao de Jared em 2026-09-08); o navegador ainda precisa do
`playwright install chromium`, e sem ele o arquivo se pula dizendo como
instalar. Nenhum teste alcanca a rede: o
Google e o NCBI sao falsos, e o teste que consulta o NCBI de verdade nao
existe.
