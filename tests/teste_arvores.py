"""
teste_arvores.py — a arvore Bayesiana: o filogenia.py do nucleo, o executor_de_arvore da web, as rotas e a imagem.

    trabalho/*.fa | *.cons.fa  (nunca *.align.fa)
           |
    executor_de_arvore.consensos_do_lote(cfg, lote_id, referencia) --> [Consenso(nome, sequencia, marcador)]
           |                                                                    marcador: o NOME do arquivo ganha; o banco do lote e reserva
    filogenia.agrupar_por_marcador --> GrupoDeMarcador (< 4 amostras = recusado, com o motivo)
           |
    filogenia.montar_arvore(grupo, pasta, MrBayes) --> mesma_polaridade --> alinhar --> aparar_colunas --> avaliar (Boletim)
           |                                           escrever_nexus (ASCII, autoclose, seed fixa) --> mb --> ler_asdsf --> ler_arvore_consenso
           |
    executor_de_arvore.executar --> arvore/<marcador>/arvore.newick + arvore.png --> resumo (lista de dicts) --> FilaDeArvores.concluir
           |
    POST /lotes/{id}/arvore (303) --> GET /lotes/{id}/arvore (arvore.html) --> GET /lotes/{id}/arvore/<pasta>/<arquivo>

Cada caso do nucleo guarda um erro que aconteceu de verdade ao montar a arvore da corrida F13719 (40 consensos, 4
marcadores), nao hipoteses.

O QUE SE PROTEGE
  1. genes diferentes NUNCA entram na mesma matriz: 16S e groEL nao sao homologos, e alinhar junto e ruido com cara
     de resultado. O marcador vem do NOME da amostra, porque a F13719 subiu quatro marcadores numa pasta so — confiar
     no banco do lote juntaria os quatro numa arvore.
  2. o marcador com menos de 4 amostras e RECUSADO com o motivo, na tela: sumir em silencio com o sodB de 2 amostras
     seria pior que recusar.
  3. o consenso do PAR e o <amostra>.fa; o .cons.fa e o da avulsa; o .align.fa e o alinhamento das duas leituras e
     entrando como taxon a arvore seria das leituras, nao das amostras. Isto nasceu procurando so *.cons.fa e achou
     ZERO na primeira corrida real.
  4. o NEXUS sai ASCII, fechado (autoclose) e com semente fixa: um travessao no comentario derrubava o MrBayes
     apontando o fim do arquivo; sem autoclose o mb fica esperando alguem digitar num trabalhador de fila.
  5. o ASDSF e a linha 'Average', nao a 'Maximum': um regex frouxo reportava como nao-convergida uma corrida que
     convergiu; e a frase do veredito e o que o usuario le, nao o numero cru.
  6. o .con.tre do MrBayes e lido apesar das anotacoes de FigTree que estouram o Bio.Phylo, e os nomes voltam como o
     laboratorio os escreveu (hifen e tudo).
  7. pasta e arquivo da arvore vem da URL e sao conferidos, nao confiados; a arvore so e pedida de lote PRONTO e o
     dono e conferido pelo lote; a figura e conforto (falha avisa e segue), o Newick e o resultado.
  8. a imagem leva o mb e instala pelo pyproject: modulo do nucleo importado pela web e esquecido em py-modules
     sobe uma imagem sem ele, e o erro so aparece dentro do conteiner.

O QUE MUDOU (em relacao aos testes originais)
  - phylogeny -> filogenia: Consensus -> Consenso, align -> alinhar, group_by_marker -> agrupar_por_marcador,
    trim_columns -> aparar_colunas, assess -> avaliar, write_nexus -> escrever_nexus, read_asdsf -> ler_asdsf,
    read_consensus_tree -> ler_arvore_consenso, build_tree -> montar_arvore, TreeResult -> ResultadoDaArvore,
    MixedMarkers -> MarcadoresMisturados, ASDSF_TARGET -> ASDSF_ALVO, LOW_SIGNAL_IDENTITY -> IDENTIDADE_SEM_SINAL.
  - build_trees(consensos, pasta) nao existe mais: agrupar_por_marcador + montar_arvore por grupo, com o MrBayes
    construido por quem chama (MrBayes(caminho)); o executor da web e quem faz o laco.
  - a fila (pedir duas vezes, dono do desfecho, orfaos, resumo ilegivel) e o expurgo (arvore em montagem segura o
    lote; apagar leva os pedidos) ja estao em teste_filas.py e teste_expurgo.py: nao se repetem aqui.
  - o Dockerfile instala com `pip install .`, nao com lista a mao: o guarda virou "todo modulo do nucleo que a web
    importa esta em py-modules" e "todo subpacote esta em packages".

O mb (MrBayes) e pedido so pelos dois testes de ponta a ponta (exigir_ferramenta): sem ele, pulam dizendo o que falta.
"""

import ast
import fnmatch
import re
import sys
import tomllib
from pathlib import Path

import pytest

import ferramentas
import filogenia
import orientacao
from filogenia import Consenso, GrupoDeMarcador, MarcadoresMisturados, MrBayes, ResultadoDaArvore
from conftest import EMAIL, RAIZ, exigir_ferramenta
from easycontig_web import configuracao
from easycontig_web.processamento import executor_de_arvore
from easycontig_web.processamento.executor_de_arvore import DESCONHECIDO, caminho_de_saida, consensos_do_lote, marcador_da_amostra, primeira_sequencia, reserva_do_lote
from easycontig_web.processamento.fila import FALHOU, NA_FILA, PRONTO, RODANDO
from easycontig_web.processamento.trabalhador_da_fila import Trabalhador


BASE = 'ACGTTGCAAGCTTACGGATCCTAGGCATGCATGCTAGCTAGCTTAAGGCCTTAAGGCATCG' * 4  #* 240 pb que nao sao palindromo: o reverso-complemento e OUTRA sequencia

ARVORE_DE_TRES = '(am01:0.01,(am02:0.01,am03:0.01)0.98:0.02);'  #* um Newick como o filogenia.py escreve: a probabilidade posterior e o NOME do no interno

RAMO_DE_FIGTREE = ('[&prob=1.00000000e+00,prob(percent)="100",prob+-sd="100+-0"]:8.119431e-03'
                   '[&length_mean=9.11731704e-03,length_95%HPD={1.08711300e-03,1.97661100e-02}]')  #! copiado de uma saida real (F13719/dsb)

NO_DE_FIGTREE = ('[&prob=9.85000000e-01,prob(percent)="99",prob+-sd="99+-1"]:4.000000e-03'
                 '[&length_mean=4.00000000e-03,length_95%HPD={1.00000000e-03,9.00000000e-03}]')  #! a anotacao vem DEPOIS do comprimento e traz aspas: e o que quebra o Biopython




def nome_de_bancada(i, marcador):

    #* como o laboratorio escreve: corrida, amostra, placa, gene e pocos, com hifen

    return f'F13719_am{i:02d}_CAP{i:02d}_{marcador}_A{i:02d}-B{i:02d}'




def variante(i):

    #* a BASE com uma troca numa posicao que depende de i: parecidas, mas nao identicas

    sequencia = list(BASE)

    posicao = i * 7 % len(sequencia)

    sequencia[posicao] = 'T' if sequencia[posicao] != 'T' else 'A'

    return ''.join(sequencia)




def consensos(marcador, n, primeiro=0):

    #* n consensos do mesmo marcador, numerados a partir de `primeiro`

    return [Consenso(nome_de_bancada(primeiro + i, marcador), variante(primeiro + i), marcador) for i in range(n)]




def semear(trabalho, marcador, n, primeiro=0, sufixo='.cons.fa'):

    #* grava n consensos do marcador em trabalho/, como o tracy os deixaria; devolve os nomes das amostras

    trabalho.mkdir(parents=True, exist_ok=True)

    nomes = []

    for consenso in consensos(marcador, n, primeiro):

        (trabalho / f'{consenso.nome}{sufixo}').write_text(f'>Consensus\n{consenso.sequencia}\n')

        nomes.append(consenso.nome)


    return nomes




def boletim_fabricado(n=3, antes=240, depois=240, mediana=98.5):

    return filogenia.Boletim(n, antes, depois, 97.0, mediana, 100.0)




def con_tre_de_figtree(caminho):

    #* um .con.tre no formato real do MrBayes, com tres taxons e um no interno de probabilidade 0,985

    caminho.write_text('#NEXUS\nbegin trees;\n    translate\n        1 am01,\n        2 am02,\n        3 am03;\n'
                       f'    tree con_50_majrule = [&U] (1{RAMO_DE_FIGTREE},(2{RAMO_DE_FIGTREE},3{RAMO_DE_FIGTREE}){NO_DE_FIGTREE});\nend;\n')

    return caminho




def lote_pronto(bancada, dono=EMAIL, nome='F13719'):

    #* um lote PRONTO sem tracy: criado direto na fila e concluido pela bancada (relatorio fabricado)

    lote_id = bancada.instalacao.lotes.novo_lote(dono, nome, 2)

    bancada.instalacao.lotes.liberar_para_fila(lote_id, 2)

    bancada.concluir(lote_id)

    return lote_id




def pedido_terminado(bancada, lote_id, resumo, trabalhador='mb:1'):

    #* um pedido de arvore ja PRONTO com este resumo, como o trabalhador o deixaria

    pedido_id = bancada.instalacao.arvores.novo_pedido(lote_id, EMAIL)

    assert bancada.instalacao.arvores.reivindicar(trabalhador).id == pedido_id

    assert bancada.instalacao.arvores.concluir(pedido_id, resumo, trabalhador)

    return pedido_id




@pytest.fixture
def cfg(tmp_path, ambiente):

    #* a Configuracao de uma pasta de dados temporaria, sem banco curado nenhum

    ambiente.setenv('EASYCONTIG_DATA_DIR', str(tmp_path / 'dados'))

    return configuracao.carregar()




@pytest.fixture
def trabalho(cfg):

    #* a pasta trabalho/ de um lote qualquer, vazia

    pasta = cfg.pastas_do_lote('L1').trabalho

    pasta.mkdir(parents=True)

    return pasta




def teste_marcadores_diferentes_nunca_entram_na_mesma_matriz():

    #* 16S e groEL nao sao homologos: alinhar junto e ruido com cara de resultado. a trava vem antes de gastar CPU

    with pytest.raises(MarcadoresMisturados) as erro:
        filogenia.alinhar(consensos('16S', 3) + consensos('groEL', 3))

    assert '16S' in str(erro.value) and 'groEL' in str(erro.value) and 'por marcador' in str(erro.value)

    assert issubclass(MarcadoresMisturados, ValueError), 'quem trata ValueError tambem apanha esta'




def teste_agrupar_separa_por_marcador_sem_perder_amostra():

    grupos = filogenia.agrupar_por_marcador(consensos('16S', 21) + consensos('groEL', 12) + consensos('dsb', 5))

    assert {grupo.marcador: len(grupo.membros) for grupo in grupos} == {'16S': 21, 'groEL': 12, 'dsb': 5}

    assert [grupo.marcador for grupo in grupos] == ['16S', 'dsb', 'groEL'], 'em ordem fixa: a tela nao muda de lugar a cada pedido'

    assert all(grupo.montavel for grupo in grupos)




def teste_marcador_com_poucas_amostras_e_recusado_com_motivo_legivel(tmp_path):

    #* o sodB da F13719 tinha 2 amostras. sumir em silencio seria pior que recusar

    grupo, = filogenia.agrupar_por_marcador(consensos('sodB', 2))

    assert not grupo.montavel

    assert '2 amostra' in grupo.recusado and 'sodB' in grupo.recusado and 'pelo menos 4' in grupo.recusado


    pasta = tmp_path / 'nao_deve_ser_usada'

    resultado = filogenia.montar_arvore(grupo, pasta, mrbayes=None)

    assert resultado.recusado == grupo.recusado and resultado.veredito == grupo.recusado

    assert not pasta.exists(), 'grupo recusado nao toca o disco'




def teste_mesma_polaridade_segue_a_maioria_e_nao_o_primeiro():

    #* o tracy escreve o consenso no sentido da primeira leitura: o gravado ao contrario entra reverso-complementado, e o aviso aponta ELE

    membros = consensos('16S', 4)

    original = membros[2].sequencia

    membros[2] = Consenso(membros[2].nome, orientacao.reverso_complemento(original), '16S')

    ajustados, invertidos = filogenia.mesma_polaridade(membros)

    assert invertidos == [membros[2].nome] and ajustados[2].sequencia == original

    assert [consenso.nome for consenso in ajustados] == [consenso.nome for consenso in membros], 'a ordem nao muda'


    ao_contrario = consensos('16S', 4)

    ao_contrario[0] = Consenso(ao_contrario[0].nome, orientacao.reverso_complemento(ao_contrario[0].sequencia), '16S')

    ajustados, invertidos = filogenia.mesma_polaridade(ao_contrario)

    assert invertidos == [ao_contrario[0].nome], 'se o 1º e o invertido, a maioria manda: o aviso nao aponta os outros 3'

    assert filogenia.mesma_polaridade([]) == ([], [])




def teste_aparar_corta_ponta_raida_de_consenso_parcial():

    #* na F13719 o 16S tinha 262-805 pb: 48% das colunas eram ponta raida

    alinhamento = [('cheia', 'ACGTACGT'), ('cheia2', 'ACGTACGT'), ('parcial', '----ACGT')]

    aparado, antes, depois = filogenia.aparar_colunas(alinhamento)

    assert (antes, depois) == (8, 8), '2 de 3 sustentam as 4 primeiras: fica'


    alinhamento[1] = ('cheia2', '----ACGT')

    aparado, antes, depois = filogenia.aparar_colunas(alinhamento)

    assert (antes, depois) == (8, 4), 'agora so 1 de 3 sustenta: sai'

    assert all(len(sequencia) == 4 for _, sequencia in aparado)




def teste_avisa_quando_as_amostras_sao_quase_identicas():

    #* groEL e dsb deram 99% de identidade: o leque e o resultado honesto, e o usuario precisa ouvir isso do programa

    identicas = [('a', 'ACGTACGTAC'), ('b', 'ACGTACGTAC'), ('c', 'ACGTACGTAC')]

    boletim = filogenia.avaliar(identicas, 10, 10)

    assert boletim.identidade_mediana == 100.0 and boletim.n_taxons == 3

    assert any('quase idênticas' in aviso for aviso in boletim.avisos)




def teste_nao_avisa_de_pouco_sinal_quando_ha_divergencia():

    divergentes = [('a', 'ACGTACGTAC'), ('b', 'TGCATGCATG'), ('c', 'ACGTTGCATG')]

    boletim = filogenia.avaliar(divergentes, 10, 10)

    assert boletim.identidade_mediana < filogenia.IDENTIDADE_SEM_SINAL

    assert not any('quase idênticas' in aviso for aviso in boletim.avisos)




def teste_avisa_do_que_foi_aparado_e_de_quem_entrou_invertido():

    divergentes = [('a', 'ACGTACGTAC'), ('b', 'TGCATGCATG'), ('c', 'ACGTTGCATG')]

    boletim = filogenia.avaliar(divergentes, 100, 60, invertidos=('am03',))

    assert boletim.invertidos == ['am03']

    assert any('am03' in aviso and 'reverso-complementado' in aviso for aviso in boletim.avisos)

    assert any('40 de 100 colunas' in aviso for aviso in boletim.avisos), 'mais de 30% aparado: o usuario deve saber antes de publicar'

    assert not any('colunas' in aviso for aviso in filogenia.avaliar(divergentes, 100, 80).avisos), '20% aparado e normal'




def teste_nome_de_amostra_sobrevive_a_ida_e_volta(tmp_path):

    #* o MrBayes recusa '-' e '.'; o usuario precisa reconhecer a amostra dele

    original = 'F13719_am01_CAP08_16S_A09-B09'

    mapa = filogenia.escrever_nexus([(original, 'ACGT'), ('outra.amostra', 'ACGA')], tmp_path / 'x.nex', marcador='16S')

    texto = (tmp_path / 'x.nex').read_text()

    assert '-' not in texto.split('MATRIX')[1].split(';')[0], 'hifen quebra o parser'

    assert mapa['F13719_am01_CAP08_16S_A09_B09'] == original, 'sem o mapa de volta, o usuario nao se reconhece'

    assert mapa['outra_amostra'] == 'outra.amostra'




def teste_nexus_sai_fechado_para_rodar_sozinho(tmp_path):

    #* sem autoclose o mb fica esperando alguem digitar; num trabalhador de fila isso e um processo pendurado para sempre

    filogenia.escrever_nexus([('a', 'ACGT'), ('b', 'ACGA')], tmp_path / 'x.nex', marcador='16S')

    texto = (tmp_path / 'x.nex').read_text()

    assert 'autoclose=yes' in texto and 'nowarn=yes' in texto

    assert 'stoprule=yes' in texto, 'sem stoprule a corrida gasta geracoes a toa'

    assert f'stopval={filogenia.ASDSF_ALVO}' in texto

    assert f'seed={filogenia.SEMENTE} swapseed={filogenia.SEMENTE}' in texto, 'semente fixa: o mesmo NEXUS da a mesma arvore em qualquer maquina'

    assert f'ngen={filogenia.GERACOES}' in texto and 'NTAX=2 NCHAR=4' in texto




def teste_nexus_sai_em_ascii_puro(tmp_path):

    #! REGRESSAO: um travessao no comentario do cabecalho fazia o MrBayes abortar com 'Reached end of file while in comment',
    #! apontando o fim do arquivo em vez da linha 3. projeto em portugues + parser que so le ASCII

    filogenia.escrever_nexus([('a', 'ACGT'), ('b', 'ACGA')], tmp_path / 'x.nex', marcador='16S — rRNA')

    bruto = (tmp_path / 'x.nex').read_bytes()

    assert all(byte < 128 for byte in bruto), 'o MrBayes nao le UTF-8'

    assert b'16S ' in bruto and b'rRNA' in bruto, 'o marcador continua la, so o travessao virou ?'




def teste_nexus_recusa_nomes_que_colidem_e_alinhamento_vazio(tmp_path):

    with pytest.raises(ValueError, match='colidem'):
        filogenia.escrever_nexus([('am-01', 'ACGT'), ('am.01', 'ACGA')], tmp_path / 'x.nex', marcador='16S')

    with pytest.raises(ValueError, match='vazio'):
        filogenia.escrever_nexus([], tmp_path / 'y.nex', marcador='16S')

    assert not (tmp_path / 'x.nex').exists() and not (tmp_path / 'y.nex').exists(), 'recusou antes de escrever'




def teste_le_a_media_e_nao_o_maximo_do_asdsf():

    #! REGRESSAO: o sump imprime as duas linhas, nesta ordem. um regex frouxo pega a Maxima (0.035), e uma corrida
    #! convergida (0.0097) e reportada como nao-convergida

    log = ('      Average standard deviation of split frequencies: 0.010376\n'
           '      Average standard deviation of split frequencies: 0.009704\n'
           '   + Convergence diagnostic (standard deviation of split frequencies)\n'
           '       Average standard deviation of split frequencies = 0.009704\n'
           '       Maximum standard deviation of split frequencies = 0.035460\n')

    assert filogenia.ler_asdsf(log) == 0.009704




def teste_sem_diagnostico_no_log_nao_inventa_numero():

    assert filogenia.ler_asdsf('Analysis completed in 6 seconds\n') is None

    assert filogenia.ler_asdsf('') is None




def teste_convergencia_vira_frase_e_nao_numero_cru():

    convergiu = ResultadoDaArvore('16S', asdsf=0.0097)

    faltou = ResultadoDaArvore('16S', asdsf=0.042)

    assert convergiu.convergiu and 'convergiu' in convergiu.veredito

    assert not faltou.convergiu and 'rode mais gerações' in faltou.veredito and '0.042' in faltou.veredito

    assert ResultadoDaArvore('16S').veredito == 'não rodou', 'sem ASDSF nao ha o que julgar'

    assert ResultadoDaArvore('sodB', recusado='2 amostra(s)').veredito == '2 amostra(s)'




def teste_arvore_do_mrbayes_e_lida_apesar_das_anotacoes_de_figtree(tmp_path):

    #! REGRESSAO: Bio.Phylo estoura neste arquivo com 'Two string taxonomies?'. como o resto do projeto e Biopython,
    #! quem mexer aqui tentaria isso primeiro

    con = con_tre_de_figtree(tmp_path / 'x.con.tre')

    from Bio import Phylo

    with pytest.raises(Exception):
        Phylo.read(str(con), 'nexus')  #* o caminho "obvio" nao funciona


    newick = filogenia.ler_arvore_consenso(con)

    assert '0.98' in newick, 'a probabilidade posterior tem de sobreviver'

    assert 'am01' in newick and 'length_95' not in newick and 'prob' not in newick


    com_mapa = filogenia.ler_arvore_consenso(con, {'am01': 'F13719_am01_CAP08_16S_A09-B09'})

    assert 'F13719_am01_CAP08_16S_A09-B09' in com_mapa and 'am02' in com_mapa, 'o mapa devolve o nome do laboratorio, hifen e tudo'




def teste_mrbayes_exige_o_binario_ao_construir(tmp_path):

    with pytest.raises(FileNotFoundError, match='mb'):
        MrBayes(tmp_path / 'nao_existe')




def teste_da_lista_de_consensos_a_arvore_por_marcador(tmp_path):

    #* o fluxo inteiro do nucleo: 2 marcadores entram, 1 vira arvore, o outro e recusado por ser pequeno — e nenhuma matriz mistura os dois

    mrbayes = MrBayes(exigir_ferramenta('mb'))

    grupos = filogenia.agrupar_por_marcador(consensos('16S', 5) + consensos('sodB', 2, primeiro=90))

    resultados = {grupo.marcador: filogenia.montar_arvore(grupo, tmp_path / filogenia.nome_seguro(grupo.marcador), mrbayes, geracoes=20_000) for grupo in grupos}

    assert resultados['sodB'].recusado, 'sodB com 2 amostras nao pode virar arvore'

    assert not (tmp_path / 'sodB').exists()


    arvore = resultados['16S']

    assert arvore.arvore_consenso is not None and arvore.arvore_consenso.exists()

    assert arvore.asdsf is not None, 'o ASDSF tem de sair do log'

    assert arvore.boletim.n_taxons == 5 and arvore.boletim.colunas_depois >= 240, 'o alinhador pode abrir gaps: nunca menos colunas que a sequencia'

    assert '16S' in (tmp_path / '16S' / '16S.nex').read_text()

    assert arvore.newick.count('F13719_am') == 5 and 'A00-B00' in arvore.newick, 'os nomes voltam como o laboratorio os escreveu'

    assert len(arvore.mapa) == 5




def teste_o_marcador_vem_do_nome_da_amostra_e_nao_do_banco_do_lote():

    #! o ponto do fluxo inteiro. na F13719 as 40 amostras subiram numa pasta so e eram de quatro marcadores.
    #! confiar no banco do lote juntaria os quatro numa arvore, que e o erro que tudo isto existe para impedir

    assert marcador_da_amostra('F13719_am24_CAP08_groEL_E08-F08', '16S rRNA') == 'groEL'

    assert marcador_da_amostra('F13719_am01_CAP08_16S_A09-B09', 'groEL') == '16S'

    assert marcador_da_amostra('am01_groel_x') == 'groEL', 'sem distinguir caixa, e com a grafia do catalogo'




def teste_sem_marcador_no_nome_cai_no_banco_do_lote_e_depois_em_desconhecido():

    assert marcador_da_amostra('amostra_qualquer', '16S rRNA') == '16S'

    assert marcador_da_amostra('amostra_qualquer', 'cox1') == 'cox1', 'banco proprio: o apelido e o marcador'

    assert marcador_da_amostra('amostra_qualquer') == DESCONHECIDO

    assert marcador_da_amostra('amostra_qualquer', '   ') == DESCONHECIDO, 'reserva so de espacos: o original estourava com IndexError'




def teste_marcador_nao_casa_no_meio_de_outra_palavra():

    #* '_16S_' e marcador; 'CAP16Sul' nao e. sem a fronteira, um nome de poco ou de projeto viraria marcador e criaria um grupo fantasma

    assert marcador_da_amostra('F1_CAP16Sul_x') == DESCONHECIDO

    assert marcador_da_amostra('F1_CAP16Sul_16S_x') == '16S'

    assert marcador_da_amostra('am01-dsb.pool') == 'dsb', 'hifen e ponto tambem sao fronteira'




def teste_os_marcadores_conhecidos_saem_do_catalogo_em_ordem_fixa():

    import bancos_de_referencia

    conhecidos = executor_de_arvore.marcadores_conhecidos()

    assert set(conhecidos) == {ficha.gene for ficha in bancos_de_referencia.CATALOGO}, 'uma verdade so: banco novo no catalogo ensina o reconhecedor'

    assert conhecidos == sorted(conhecidos, key=lambda gene: (-len(gene), gene)), 'do mais longo para o mais curto, desempate alfabetico'

    assert len(set(conhecidos)) == len(conhecidos)




def teste_reserva_do_lote_e_o_marcador_do_banco_consultado(cfg):

    assert reserva_do_lote(cfg) == '' and reserva_do_lote(cfg, 'curado') == '', 'curado: dois bancos, nenhuma reserva'

    assert reserva_do_lote(cfg, 'anaplasmataceae_groel') == 'groEL', 'banco do catalogo: o gene da ficha'

    assert reserva_do_lote(cfg, 'meu_1b5d51d238c3e209_cox1') == 'cox1', 'banco proprio: o apelido'




def teste_consensos_saem_dos_cons_fa_que_o_lote_ja_montou(cfg, trabalho):

    (trabalho / 'F13719_am01_CAP08_16S_A09-B09.cons.fa').write_text('>consenso\nACGTACGT\nACGT\n')

    (trabalho / 'F13719_am24_CAP08_groEL_E08-F08.cons.fa').write_text('>consenso\nTTTTGGGG\n')

    (trabalho / 'vazio.cons.fa').write_text('>consenso\n\n')

    por_nome = {consenso.nome: consenso for consenso in consensos_do_lote(cfg, 'L1')}

    assert set(por_nome) == {'F13719_am01_CAP08_16S_A09-B09', 'F13719_am24_CAP08_groEL_E08-F08'}, 'consenso vazio nao vira taxon'

    assert por_nome['F13719_am01_CAP08_16S_A09-B09'].sequencia == 'ACGTACGTACGT', 'as linhas do FASTA sao juntadas'

    assert por_nome['F13719_am24_CAP08_groEL_E08-F08'].marcador == 'groEL'




def teste_o_consenso_de_um_par_e_o_arquivo_ponto_fa(cfg, trabalho):

    #! REGRESSAO DE IMPLANTACAO. o tracy da tres nomes, e so o do meio e o que a intuicao sugere:
    #!     <amostra>.fa          consenso do PAR F+R      <- o uso NORMAL
    #!     <amostra>.cons.fa     consenso da leitura AVULSA
    #!     <amostra>.align.fa    o ALINHAMENTO das duas leituras (2 registros)
    #! isto nasceu procurando so *.cons.fa e passou em tudo. na primeira corrida REAL (40 pares) a busca achou ZERO

    par = 'F13719_am01_CAP08_16S_A09-B09'

    (trabalho / f'{par}.fa').write_text('>Consensus\nGAACGCTGGCGGCAAGCTTAA\n')

    (trabalho / f'{par}.align.fa').write_text(f'>{par}_F\nGAACGCTGGCGG--------\n>{par}_R\n--------CAAGCTTAA\n')

    avulsa = 'Sequenciamento_F13055_64712_16S'

    (trabalho / f'{avulsa}.cons.fa').write_text('>Consensus\ncgcggtagtggcagctgaat\n')

    achados = {consenso.nome: consenso for consenso in consensos_do_lote(cfg, 'L1')}

    assert set(achados) == {par, avulsa}, 'o par entra pelo .fa, a avulsa pelo .cons.fa, e o .align.fa fica FORA'

    assert achados[par].sequencia == 'GAACGCTGGCGGCAAGCTTAA'

    assert achados[avulsa].sequencia == 'CGCGGTAGTGGCAGCTGAAT', 'maiuscula, como o alinhador espera'




def teste_o_alinhamento_nunca_entra_como_se_fosse_consenso(cfg, trabalho):

    #* .align.fa tem DOIS registros e gaps. entrando como taxon, o lote viraria uma arvore das leituras, nao das amostras

    (trabalho / 'am01_16S.align.fa').write_text('>am01_16S_F\nACGT----\n>am01_16S_R\n----ACGT\n')

    assert consensos_do_lote(cfg, 'L1') == []




def teste_primeira_sequencia_le_so_o_primeiro_registro(tmp_path):

    arquivo = tmp_path / 'x.fa'

    arquivo.write_text('>um\nac-gt\nAC\n>dois\nGGGG\n')

    assert primeira_sequencia(arquivo) == 'ACGTAC', 'maiuscula, sem gap, e o segundo registro fica de fora'

    arquivo.write_text('sem cabecalho\nACGT\n')

    assert primeira_sequencia(arquivo) == '', 'sem registro nao ha sequencia'




def teste_sem_gene_no_nome_o_banco_do_lote_da_o_marcador(cfg, trabalho):

    (trabalho / 'amostra_sem_gene.fa').write_text('>Consensus\nACGTACGT\n')

    (trabalho / 'amostra_16S_x.fa').write_text('>Consensus\nACGTACGT\n')

    por_nome = {consenso.nome: consenso.marcador for consenso in consensos_do_lote(cfg, 'L1', referencia='anaplasmataceae_groel')}

    assert por_nome == {'amostra_sem_gene': 'groEL', 'amostra_16S_x': '16S'}, 'o nome ganha do banco; sem nome, o banco'

    assert {consenso.marcador for consenso in consensos_do_lote(cfg, 'L1')} == {DESCONHECIDO, '16S'}




def teste_caminho_de_saida_recusa_sair_da_pasta_do_lote(cfg):

    #* pasta e arquivo vem da URL. mesma regra do pastas_do_lote: quem decide onde se le nao confia em o chamador ter validado antes

    for pasta, arquivo in (('..', 'x.png'), ('16S', '../../relatorio.json'), ('16S', ''), ('a/b', 'x.png'), ('16S', '.'), ('16S', 'a\\b')):

        with pytest.raises(ValueError):
            caminho_de_saida(cfg, 'L1', pasta, arquivo)


    with pytest.raises(ValueError):
        caminho_de_saida(cfg, '../L1', '16S', 'arvore.png')

    assert caminho_de_saida(cfg, 'L1', '16S', 'arvore.png') == cfg.pastas_do_lote('L1').arvore / '16S' / 'arvore.png'




def teste_executar_sem_consensos_orienta_em_vez_de_recusar(cfg):

    pastas = cfg.pastas_do_lote('L1')

    pastas.trabalho.mkdir(parents=True)

    with pytest.raises(ValueError) as erro:
        executor_de_arvore.executar(cfg, 'L1')

    assert 'rode o lote primeiro' in str(erro.value) and 'trabalho/' in str(erro.value)

    assert not pastas.arvore.exists(), 'nada foi criado'




def teste_executar_so_com_grupos_recusados_nao_exige_o_mb(cfg, trabalho, monkeypatch):

    #* o mb so e exigido se algum grupo vai usa-lo: a instalacao sem MrBayes ainda responde "sodB recusado" em vez de estourar

    semear(trabalho, 'sodB', 2, primeiro=90)

    monkeypatch.setattr(executor_de_arvore, 'mrbayes_da_instalacao', lambda: pytest.fail('nao devia procurar o mb'))

    etapas = []

    resumo = executor_de_arvore.executar(cfg, 'L1', etapa=etapas.append)

    assert resumo == [{'marcador': 'sodB', 'n': 2, 'recusado': resumo[0]['recusado']}] and 'pelo menos 4' in resumo[0]['recusado']

    assert etapas == ['sodB (2 amostras)']

    assert cfg.pastas_do_lote('L1').arvore.is_dir()




def teste_executar_exige_o_mb_antes_de_tocar_o_disco(cfg, trabalho, ambiente, tmp_path):

    #* a escada do ferramentas.py sem o mb: PATH vazio e sem EASYCONTIG_MRBAYES_BIN. o erro diz a variavel, e nada foi criado

    semear(trabalho, '16S', 4)

    ambiente.setenv('PATH', str(tmp_path / 'sem_nada'))

    with pytest.raises(ferramentas.FerramentaAusente) as erro:
        executor_de_arvore.executar(cfg, 'L1')

    assert erro.value.ferramenta == 'mb' and 'EASYCONTIG_MRBAYES_BIN' in str(erro.value)

    assert not cfg.pastas_do_lote('L1').arvore.exists()




def teste_resumo_do_marcador_e_dado_e_avisa_da_figura_que_faltou():

    grupo = GrupoDeMarcador('16S rRNA', consensos('16S', 3))

    resultado = ResultadoDaArvore('16S rRNA', asdsf=0.008, boletim=boletim_fabricado(), newick=ARVORE_DE_TRES)

    com_figura = executor_de_arvore.resumo_do_marcador(grupo, resultado, Path('/x/arvore.png'))

    assert com_figura == {'marcador': '16S rRNA', 'n': 3, 'colunas': 240, 'colunas_antes': 240, 'identidade': 98.5, 'asdsf': 0.008,
                          'convergiu': True, 'veredito': resultado.veredito, 'avisos': [], 'figura': 'arvore.png', 'newick': 'arvore.newick', 'pasta': '16S_rRNA'}


    sem_figura = executor_de_arvore.resumo_do_marcador(grupo, resultado, None)

    assert sem_figura['figura'] == '' and any('figura não foi desenhada' in aviso for aviso in sem_figura['avisos'])

    assert resultado.boletim.avisos == [], 'o aviso da figura vai para o resumo, nao para o boletim do nucleo'

    assert executor_de_arvore.resumo_do_marcador(grupo, ResultadoDaArvore('16S rRNA', boletim=boletim_fabricado()), None)['newick'] == ''




def teste_rotulo_do_ramo_so_a_partir_de_0_90():

    from Bio.Phylo.BaseTree import Clade

    interno = lambda confianca: Clade(confidence=confianca, clades=[Clade(), Clade()])

    assert executor_de_arvore.rotulo_do_ramo(interno(0.98)) == '0.98'

    assert executor_de_arvore.rotulo_do_ramo(interno(0.90)) == '0.90'

    assert executor_de_arvore.rotulo_do_ramo(interno(0.51)) == '', 'encher a figura de 0,51 daria ao olho estrutura onde o metodo diz que nao ha'

    assert executor_de_arvore.rotulo_do_ramo(interno(None)) == ''

    assert executor_de_arvore.rotulo_do_ramo(Clade(confidence=0.99)) == '', 'folha nao leva probabilidade'




def teste_desenhar_grava_a_figura_e_uma_falha_nao_derruba(tmp_path, caplog):

    resultado = ResultadoDaArvore('16S', asdsf=0.008, boletim=boletim_fabricado(), newick=ARVORE_DE_TRES)

    destino = tmp_path / 'arvore.png'

    assert executor_de_arvore.desenhar(resultado, destino) == destino and destino.stat().st_size > 1000


    assert executor_de_arvore.desenhar(ResultadoDaArvore('16S', boletim=boletim_fabricado()), tmp_path / 'sem.png') is None, 'sem Newick nao ha o que desenhar'

    with caplog.at_level('WARNING', logger='easycontig.arvore'):
        assert executor_de_arvore.desenhar(resultado, tmp_path / 'pasta_que_nao_existe' / 'ruim.png') is None, 'falha de desenho: avisa e devolve None'

    assert 'figura de 16S nao desenhada' in caplog.text and 'pasta_que_nao_existe' in caplog.text, 'o log diz a causa'




def teste_pedir_arvore_enfileira_e_leva_para_a_tela(bancada):

    cliente = bancada.entrar()

    lote_id = lote_pronto(bancada)

    resposta = cliente.post(f'/lotes/{lote_id}/arvore')

    assert resposta.status_code == 303 and resposta.headers['location'] == f'/lotes/{lote_id}/arvore'

    pedido = bancada.instalacao.arvores.do_lote(lote_id)

    assert pedido.status == NA_FILA and pedido.dono == EMAIL and pedido.resumo == []

    assert cliente.get(f'/api/lotes/{lote_id}/arvore').json()['status'] == NA_FILA




def teste_na_fila_sem_trabalhador_a_tela_avisa_quem_cuida_do_servidor(bancada):

    #* como no lote: a promessa "na fila" so e feita quando ha trabalhador para cumpri-la

    cliente = bancada.entrar()

    lote_id = lote_pronto(bancada)

    cliente.post(f'/lotes/{lote_id}/arvore')

    pagina = cliente.get(f'/lotes/{lote_id}/arvore')

    assert pagina.status_code == 200 and 'Nada está atendendo a fila' in pagina.text and 'trabalhador_da_fila' in pagina.text


    bancada.instalacao.arvores.pulsar('mb:1')

    pagina = cliente.get(f'/lotes/{lote_id}/arvore')

    assert 'Na fila' in pagina.text and 'Nada está atendendo' not in pagina.text




def teste_arvore_de_lote_que_ainda_nao_terminou_e_recusada(bancada):

    #* montar arvore de um lote em curso leria consensos que ainda nao existem — ou pior, metade deles

    cliente = bancada.entrar()

    lote_id = bancada.instalacao.lotes.novo_lote(EMAIL, 'x', 2)

    resposta = cliente.post(f'/lotes/{lote_id}/arvore')

    assert resposta.status_code == 409 and 'depois que o lote terminar' in resposta.text

    assert bancada.instalacao.arvores.do_lote(lote_id) is None, 'nenhum pedido ficou gravado'




def teste_a_tela_convida_a_montar_quando_ainda_nao_ha_pedido(bancada):

    cliente = bancada.entrar()

    lote_id = lote_pronto(bancada)

    pagina = cliente.get(f'/lotes/{lote_id}/arvore')

    assert pagina.status_code == 200 and 'Montar árvore' in pagina.text and 'Nenhuma árvore foi pedida' in pagina.text

    assert cliente.get(f'/api/lotes/{lote_id}/arvore').status_code == 404, 'a API nao inventa pedido'




def teste_a_tela_mostra_o_marcador_recusado_com_o_motivo(bancada):

    #* sumir em silencio com o sodB de 2 amostras seria pior que recusar: quem enviou nao saberia que faltou

    cliente = bancada.entrar()

    lote_id = lote_pronto(bancada)

    grupo, = filogenia.agrupar_por_marcador(consensos('sodB', 2))

    pedido_terminado(bancada, lote_id, [{'marcador': 'sodB', 'n': 2, 'recusado': grupo.recusado}])

    pagina = cliente.get(f'/lotes/{lote_id}/arvore')

    assert 'sodB' in pagina.text and 'pelo menos 4' in pagina.text and 'Não é falha' in pagina.text

    assert 'Montar de novo' in pagina.text, 'pronto: pode pedir outra'

    assert cliente.get(f'/api/lotes/{lote_id}/arvore').json()['resumo'][0]['marcador'] == 'sodB'




def teste_a_tela_acompanha_a_etapa_e_mostra_a_falha_inteira(bancada):

    cliente = bancada.entrar()

    lote_id = lote_pronto(bancada)

    pedido_id = bancada.instalacao.arvores.novo_pedido(lote_id, EMAIL)

    bancada.instalacao.arvores.reivindicar('mb:1')

    bancada.instalacao.arvores.etapa(pedido_id, '16S (12 amostras)', 'mb:1')

    pagina = cliente.get(f'/lotes/{lote_id}/arvore')

    assert 'montando 16S (12 amostras)' in pagina.text and bancada.instalacao.arvores.pegar(pedido_id).status == RODANDO


    bancada.instalacao.arvores.falhar(pedido_id, 'ErroDoMrBayes: mb saiu com codigo 1 em 16S.nex', 'mb:1')

    pagina = cliente.get(f'/lotes/{lote_id}/arvore')

    assert 'A árvore não foi montada' in pagina.text and 'mb saiu com codigo 1' in pagina.text

    assert bancada.instalacao.arvores.pegar(pedido_id).status == FALHOU




def teste_o_lote_pronto_oferece_o_caminho_da_arvore(bancada):

    cliente = bancada.entrar()

    lote_id = lote_pronto(bancada)

    assert f'/lotes/{lote_id}/arvore' in cliente.get(f'/lotes/{lote_id}?tela_do_lote=1').text


    na_fila = bancada.instalacao.lotes.novo_lote(EMAIL, 'ainda nao', 2)

    bancada.instalacao.lotes.liberar_para_fila(na_fila, 2)

    assert f'/lotes/{na_fila}/arvore' not in cliente.get(f'/lotes/{na_fila}?tela_do_lote=1').text, 'lote na fila nao convida a montar'




def teste_arquivo_da_arvore_com_caminho_forjado_da_404(bancada):

    cliente = bancada.entrar()

    lote_id = lote_pronto(bancada)

    pastas = bancada.cfg.pastas_do_lote(lote_id)

    assert pastas.relatorio_json.exists(), 'o alvo da travessia existe: o 404 e por recusa, nao por acaso'

    assert cliente.get(f'/lotes/{lote_id}/arvore/16S/%2e%2e%2f%2e%2e%2frelatorio.json').status_code == 404

    assert cliente.get(f'/lotes/{lote_id}/arvore/%2e%2e/relatorio.json').status_code == 404

    assert cliente.get(f'/lotes/{lote_id}/arvore/16S/arvore.newick').status_code == 404, 'ainda nao gerado'


    (pastas.arvore / '16S').mkdir(parents=True)

    (pastas.arvore / '16S' / 'arvore.newick').write_text(ARVORE_DE_TRES)

    (pastas.arvore / '16S' / 'arvore.png').write_bytes(b'\x89PNG\r\n\x1a\n')

    newick = cliente.get(f'/lotes/{lote_id}/arvore/16S/arvore.newick')

    assert newick.status_code == 200 and newick.text == ARVORE_DE_TRES

    assert f'16S_{lote_id}.newick' in newick.headers['content-disposition'], 'o Newick sai como download: o destino dele e o FigTree'

    figura = cliente.get(f'/lotes/{lote_id}/arvore/16S/arvore.png')

    assert figura.status_code == 200 and figura.headers['content-type'] == 'image/png' and 'attachment' not in figura.headers.get('content-disposition', '')




def teste_do_botao_ao_newick_passando_pelo_trabalhador(bancada):

    #* o caminho inteiro do usuario: clica, o trabalhador pega, a arvore sai — e o marcador pequeno demais aparece recusado em vez de sumir.
    #* reproduz a forma da F13719 em miniatura: dois marcadores na MESMA pasta, um com amostras suficientes e outro sem

    exigir_ferramenta('mb')

    cliente = bancada.entrar()

    lote_id = lote_pronto(bancada)

    trabalho = bancada.cfg.pastas_do_lote(lote_id).trabalho

    semear(trabalho, '16S', 5)

    semear(trabalho, 'sodB', 2, primeiro=90)

    assert cliente.post(f'/lotes/{lote_id}/arvore').status_code == 303


    trabalhador = Trabalhador(bancada.cfg, 'mb:1')

    assert trabalhador.rodar_uma_arvore() is True

    assert trabalhador.rodar_uma_arvore() is False, 'a fila esvaziou'

    pedido = bancada.instalacao.arvores.do_lote(lote_id)

    assert pedido.status == PRONTO, pedido.erro

    resumo = {r['marcador']: r for r in pedido.resumo}

    assert 'pelo menos 4' in resumo['sodB']['recusado']

    dezesseis = resumo['16S']

    assert dezesseis['n'] == 5 and dezesseis['asdsf'] is not None and dezesseis['colunas'] >= 240

    assert dezesseis['newick'] == 'arvore.newick' and dezesseis['pasta'] == '16S', 'o Newick e o que o usuario leva para o FigTree'

    assert dezesseis['figura'] == 'arvore.png' and (bancada.cfg.pastas_do_lote(lote_id).arvore / '16S' / 'arvore.png').exists()

    assert any('quase idênticas' in aviso for aviso in dezesseis['avisos']), 'cinco variantes da mesma sequencia: o leque e o resultado honesto, e o usuario ouve isso'

    assert not any('figura' in aviso for aviso in dezesseis['avisos'])


    pagina = cliente.get(f'/lotes/{lote_id}/arvore')

    assert 'sodB' in pagina.text and '16S' in pagina.text and 'Baixar' in pagina.text

    baixado = cliente.get(f'/lotes/{lote_id}/arvore/16S/{dezesseis["newick"]}')

    assert baixado.status_code == 200 and baixado.text.count('F13719_am') == 5, 'os nomes voltam como o laboratorio os escreveu, com hifen e tudo'

    assert cliente.get(f'/lotes/{lote_id}/arvore/16S/{dezesseis["figura"]}').headers['content-type'] == 'image/png'




def teste_a_imagem_instala_o_mrbayes():

    #* sem o mb a imagem sobe inteira e so a arvore falha — em producao, na frente do usuario

    docker = (RAIZ / 'Dockerfile').read_text()

    assert 'MrBayes' in docker

    assert 'install -m755 src/mb' in docker, 'o binario tem de ir para o PATH'

    assert 'mb | grep -q "MrBayes"' in docker, 'o mb e conferido no BUILD, nao na primeira arvore'




def teste_a_imagem_instala_pelo_pyproject_sem_lista_a_mao():

    #* uma lista so: dependencia nova no pyproject entra na imagem sem que alguem lembre do Dockerfile

    docker = (RAIZ / 'Dockerfile').read_text()

    assert 'pip install --no-cache-dir .' in docker and '--no-deps' not in docker

    assert 'COPY *.py ./' in docker and 'COPY easycontig_web/' in docker, 'o nucleo e a web entram juntos, do mesmo commit'




def teste_todo_modulo_do_nucleo_que_a_web_importa_esta_no_pyproject():

    #! o pip so leva o que py-modules lista: modulo novo na raiz esquecido ali sobe uma imagem sem ele, e o erro so
    #! aparece dentro do conteiner (em desenvolvimento o `pip install -e .` mascara a falta)

    pyproject = tomllib.loads((RAIZ / 'pyproject.toml').read_text())

    listados = set(pyproject['tool']['setuptools']['py-modules'])

    arquivos = list((RAIZ / 'easycontig_web').rglob('*.py')) + [RAIZ / f'{modulo}.py' for modulo in listados]

    importados = set()

    for arquivo in arquivos:

        for no in ast.walk(ast.parse(arquivo.read_text(), str(arquivo))):

            if isinstance(no, ast.Import):
                importados.update(apelido.name.split('.')[0] for apelido in no.names)

            elif isinstance(no, ast.ImportFrom) and no.module and not no.level:
                importados.add(no.module.split('.')[0])


    do_nucleo = {nome for nome in importados if (RAIZ / f'{nome}.py').is_file()}

    assert do_nucleo, 'a varredura achou algum modulo do nucleo'

    assert do_nucleo <= listados, f'faltam em py-modules: {sorted(do_nucleo - listados)}'

    assert all((RAIZ / f'{modulo}.py').is_file() for modulo in listados), 'py-modules nao lista modulo que nao existe'




def teste_toda_biblioteca_que_o_codigo_importa_esta_nas_dependencias():

    #! o original recebia biopython, pyfamsa e dendropy por tabela, pelo pacote easycontig-br-core; com o nucleo
    #! dentro deste repositorio elas tem de estar aqui, senao a imagem sobe e morre na primeira leitura de .ab1

    pyproject = tomllib.loads((RAIZ / 'pyproject.toml').read_text())

    declaradas = {re.split(r'[\[<>=!~ ]', dependencia)[0].lower() for dependencia in pyproject['project']['dependencies']}

    distribuicao = {'bio': 'biopython', 'starlette': 'fastapi', 'multipart': 'python-multipart'}  #* import que nao tem o nome da distribuicao (o starlette vem junto do fastapi)

    arquivos = list((RAIZ / 'easycontig_web').rglob('*.py')) + [RAIZ / f'{modulo}.py' for modulo in pyproject['tool']['setuptools']['py-modules']]

    importados = set()

    for arquivo in arquivos:

        for no in ast.walk(ast.parse(arquivo.read_text(), str(arquivo))):

            if isinstance(no, ast.Import):
                importados.update(apelido.name.split('.')[0] for apelido in no.names)

            elif isinstance(no, ast.ImportFrom) and no.module and not no.level:
                importados.add(no.module.split('.')[0])


    de_fora = {nome for nome in importados if nome not in sys.stdlib_module_names and nome != 'easycontig_web' and not (RAIZ / f'{nome}.py').is_file()}

    assert {'Bio', 'pyfamsa', 'dendropy', 'fastapi'} <= de_fora, 'a varredura acha as bibliotecas de terceiros'

    faltam = {nome for nome in de_fora if distribuicao.get(nome.lower(), nome.lower()) not in declaradas}

    assert not faltam, f'importadas pelo codigo e ausentes de [project] dependencies: {sorted(faltam)}'




def teste_todo_subpacote_e_todo_template_entram_no_pacote():

    pyproject = tomllib.loads((RAIZ / 'pyproject.toml').read_text())

    setuptools = pyproject['tool']['setuptools']

    pacotes = {str(arquivo.parent.relative_to(RAIZ)).replace('/', '.') for arquivo in (RAIZ / 'easycontig_web').rglob('__init__.py')}

    assert pacotes == set(setuptools['packages']), 'subpacote fora de packages nao vai para a imagem'


    padroes = setuptools['package-data']['easycontig_web']

    for arquivo in list((RAIZ / 'easycontig_web' / 'templates').iterdir()) + list((RAIZ / 'easycontig_web' / 'estatico').iterdir()):

        relativo = str(arquivo.relative_to(RAIZ / 'easycontig_web'))

        assert any(fnmatch.fnmatch(relativo, padrao) for padrao in padroes), f'{relativo} nao casa com package-data: ficaria fora da imagem'




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
