"""
teste_executor.py — a ponte para o nucleo: o executor do lote, a leitura do relatorio para a tela e a orientacao pelo DNA.

    entrada/*.ab1 --> executor_de_lote.executar(cfg, lote_id) --> lote.montar_lote (tracy + blastn) --> RelatorioDoLote
                                                                                                          |
                                                          relatorio.json <-- para_json()   documento_html()   para_csv()
                                                                |
    leitura_de_amostras.carregar(relatorio.json) --> RelatorioDoLote --> resumo(lote) / listar(lote) / formatar_amostra(fatos)
    leitura_de_amostras.contar_amostras(relatorio.json) --> so o inteiro, guardado em CONTAGENS por mtime + tamanho

    orientacao.analisar([F.ab1, R.ab1]) --> carregar --> achar_pares --> ([Par], sobras)

O QUE SE PROTEGE
  1. ausencia e ausencia: identidade, cobertura e organismo que nao foram medidos saem VAZIOS na tela e no CSV,
     nunca 0 — zero e um numero e seria lido como medida.
  2. nao achar != nao conseguir procurar != nao montou: `sem_acerto` e neutro (19 das 40 amostras da corrida real
     sao groEL/dsb/sodB e nao estao num banco de rRNA), `blast_indisponivel`/`sem_banco` falam da instalacao e
     `nao_montou` e a unica falha vermelha; a amostra que nao montou NAO entra na conta das sem acerto.
  3. a tela e o CSV mostram os mesmos numeros com a mesma formatacao; duas telas do mesmo sistema nao discordam.
  4. contar_amostras da sempre o mesmo numero que ler o relatorio inteiro: o cache e validado por mtime + tamanho,
     relatorio regravado e recontado, apagado some, e a segunda leitura nao toca no conteudo.
  5. o executor PARA em lote vazio e em envio incompleto: um relatorio de 33 de 40 amostras nao se distingue de
     uma corrida de 33, e um banco escolhido que nao existe falha ANTES de montar qualquer coisa.
  6. a orientacao sai do DNA: o par sintetico (amplicon + ele ao contrario) e reconhecido, amostras diferentes nao
     viram par, o par sobrevive ao nome trocado, e a folga entre par e nao-par e larga. Sem referencia, o detector
     diz que sao OPOSTAS e nao inventa quem e o forward.
  7. a tela DIZ quando uma leitura nao formou par e explica por que (o nome, nao o DNA): numa corrida real, 81 de
     81 leituras cairam aqui, o app estava certo e o silencio pareceu defeito.

O QUE MUDOU (em relacao aos testes originais)
  - o relatorio.json e escrito em portugues (chave, leituras, consenso_pb, situacao...); o JSON antigo em ingles e
    "relatorio de outra versao" e carregar() devolve None em vez de uma tela vazia.
  - `situacao_id` sumiu da tela: `situacao` e o codigo (o mesmo que o CSV escreve em situacao_id quando a amostra
    montou); `nao_montou` vem gravado pelo proprio nucleo (amostra_que_falhou), e a guarda da tela so cobre o
    consenso de 0 pb sem erro. `contig_invertido` e bool na tela e 'sim'/'nao' no CSV.
  - o lote real de 40 amostras (um relatorio.json de outra maquina, em formato antigo) nao entra: os casos que
    dependiam dele viraram relatorios fabricados com a mesma forma.
  - orientacao.analyse -> analisar; Pair.score -> Par.pontuacao; same_sense -> mesmo_sentido; relative -> so_relativo.

O tracy, o blastn e os bancos curados sao pedidos so pelo teste que roda o pipeline de verdade (exigir_ferramenta,
exigir_bancos_curados, pasta_de_ab1_reais): sem eles, o teste e pulado dizendo o que falta, nao falha.
"""

import csv
import io
import json
import os
import re
import shutil
import time
from pathlib import Path

import pytest

import identificacao
import orientacao
from relatorio import FatosDaAmostra, FatosDaLeitura, RelatorioDoLote, Ressalva, amostra_que_falhou
from amostras_sinteticas import PASTA, amostras as amostras_sinteticas
from conftest import RAIZ, exigir_bancos_curados, exigir_ferramenta, pasta_de_ab1_reais, relatorio_fabricado
from easycontig_web import configuracao
from easycontig_web.dados import leitura_de_amostras
from easycontig_web.dados.leitura_de_amostras import CONTAGENS, SITUACOES, carregar, contar_amostras, formatar_amostra, formatar_leitura, listar, resumo
from easycontig_web.processamento import executor_de_lote
from easycontig_web.processamento.executor_de_lote import COLUNAS_DO_CSV, bancos_do_lote, busca_de_referencia, documento_html, executar, para_csv


PAR_REAL = ['amostra28_F_BTF2.ab1', 'amostra28_R_BTR2.ab1']  #* o par de Babesia vogeli que o desktop identifica com 99 < identidade < 100

ADIANTE, REVERSO, OUTRA_AMOSTRA = PASTA / 'amostra12_F_BTF2.ab1', PASTA / 'amostra12_R_BTR2.ab1', PASTA / 'amostra28_F_BTF2.ab1'

EQUIVALENTES = {'n_leituras': 'n_leituras', 'consenso_pb': 'consenso_pb', 'cobertura_media': 'cobertura_media', 'pct_cobertura_1': 'pct_cobertura_1x',
                'organismo': 'organismo', 'acesso': 'accession', 'identidade': 'identidade_%', 'cobertura_da_consulta': 'cobertura_query_%',
                'valor_e': 'e_value', 'marcador': 'marcador', 'situacao': 'situacao_id'}  #* chave da tela -> coluna do CSV

AUSENTES = ('identidade', 'cobertura_da_consulta', 'valor_e', 'organismo', 'acesso', 'marcador')  #* o que fica vazio sem acerto




def amostra(chave='am01', **campos):

    #* uma FatosDaAmostra montada (300 pb, 2 leituras) e sem acerto, com os campos pedidos por cima

    fatos = FatosDaAmostra(chave)

    fatos.leituras = [FatosDaLeitura(f'{chave}_F', sentido_pelo_nome='F', sentido_medido='F', primer='BTF2', bases=300, q_medio=40, q_rotulo='alta', coluna_inicial=10, coluna_final=290),
                      FatosDaLeitura(f'{chave}_R', sentido_pelo_nome='R', sentido_medido='R', primer='BTR2', bases=300, q_medio=39, q_rotulo='alta', coluna_inicial=12, coluna_final=300)]

    fatos.consenso_pb, fatos.largura, fatos.cobertura_media, fatos.n_cobertura_1, fatos.pct_cobertura_1 = 300, 300, 1.5, 10, 3.3

    for campo, valor in campos.items():
        setattr(fatos, campo, valor)


    return fatos




def avulsa(chave):

    #* uma amostra de UMA leitura: montou sozinha, sem a complementar

    fatos = amostra(chave)

    fatos.leituras = fatos.leituras[:1]

    return fatos




def lote(*amostras, **campos):

    return RelatorioDoLote(amostras=list(amostras), **campos)




def gravar(pasta, dados):

    #* um relatorio.json com o que se mandar: texto cru, dict/lista (JSON) ou RelatorioDoLote

    caminho = Path(pasta) / 'relatorio.json'

    if isinstance(dados, RelatorioDoLote):
        dados = dados.para_json()

    elif not isinstance(dados, str):
        dados = json.dumps(dados, ensure_ascii=False)


    caminho.write_text(dados, encoding='utf-8')

    return caminho




def relatorio_com(pasta, quantas):

    #* um relatorio.json de `quantas` amostras identificadas

    return gravar(pasta, lote(*[amostra(f'am{i}', organismo='Babesia vogeli', situacao='ok') for i in range(quantas)], pasta='x', n_arquivos=quantas * 2, trim=4))




def linhas_do_csv(relatorio):

    #* (cabecalho, [linha por amostra]) do CSV, lido pelo modulo csv

    linhas = list(csv.reader(io.StringIO(para_csv(relatorio))))

    return linhas[0], linhas[1:]




def semear(cfg, lote_id, caminhos):

    #* copia os traces para a pasta de entrada do lote e devolve as PastasDoLote

    pastas = cfg.pastas_do_lote(lote_id)

    pastas.entrada.mkdir(parents=True, exist_ok=True)

    for caminho in caminhos:
        shutil.copy(caminho, pastas.entrada / Path(caminho).name)


    return pastas




def fingir_banco(cfg, id):

    #* um banco BLAST "montado" na estante da instalacao: so o .nsq, que e o que existe() confere

    pasta = cfg.pasta_de_bancos / id

    pasta.mkdir(parents=True)

    (pasta / f'{id}.nsq').write_bytes(b'\0' * 64)

    return pasta




@pytest.fixture
def memoria():

    #* a memoria de contar_amostras, limpa antes e depois: o cache de um teste nao pode responder por outro

    CONTAGENS.clear()

    yield CONTAGENS

    CONTAGENS.clear()




@pytest.fixture
def cfg(tmp_path, ambiente):

    #* a Configuracao de uma pasta de dados temporaria, sem banco curado nenhum

    ambiente.setenv('EASYCONTIG_DATA_DIR', str(tmp_path / 'dados'))

    return configuracao.carregar()




@pytest.fixture
def sinteticos():

    #* garante os .ab1 sinteticos em PASTA e devolve {nome: bytes}

    return amostras_sinteticas()




def teste_relatorio_ausente_devolve_none(tmp_path):

    assert carregar(tmp_path / 'nao_existe.json') is None




def teste_json_corrompido_devolve_none_e_avisa_no_log(tmp_path, caplog):

    #* relatorio ilegivel e assunto da pagina ("relatorio indisponivel"), nao motivo para derrubar a requisicao

    caminho = gravar(tmp_path, '{"amostras": [ isto nao é json')

    with caplog.at_level('WARNING', logger='easycontig.leitura_de_amostras'):
        assert carregar(caminho) is None

    assert 'relatorio ilegivel' in caplog.text and str(caminho) in caplog.text




@pytest.mark.parametrize('dados', [[1, 2, 3], {'pasta': 'x'}, {'amostras': 'nao e lista'}, {'folder': 'x', 'samples': [{'key': 'am01'}]}, {'amostras': [{'sem_chave': 1}]}])
def teste_json_valido_que_nao_e_relatorio_devolve_none(tmp_path, dados):

    #* uma lista, um dict sem amostras, o relatorio antigo em ingles e uma amostra sem chave: aceita-los daria uma tela vazia se passando por lote sem amostras

    assert carregar(gravar(tmp_path, dados)) is None




def teste_carregar_le_o_relatorio(tmp_path):

    relatorio = carregar(gravar(tmp_path, lote(amostra(organismo='Babesia vogeli', situacao='ok', ressalvas=[Ressalva('cobertura_1', '10 bases', 'cobertura < 2')]), pasta='corrida', trim=4)))

    assert isinstance(relatorio, RelatorioDoLote) and relatorio.pasta == 'corrida' and relatorio.trim == 4

    fatos = relatorio.amostra('am01')

    assert isinstance(fatos, FatosDaAmostra) and fatos.organismo == 'Babesia vogeli'

    assert all(isinstance(leitura, FatosDaLeitura) for leitura in fatos.leituras) and len(fatos.leituras) == 2

    assert isinstance(fatos.ressalvas[0], Ressalva) and fatos.ressalvas[0].regra == 'cobertura < 2'




def teste_carregar_e_o_inverso_exato_de_para_json(tmp_path):

    #* o que o trabalhador grava e o que a tela le: nenhum campo se perde no caminho

    original = relatorio_fabricado(chaves=('amostra12', 'amostra28'))

    relido = carregar(gravar(tmp_path, original))

    assert relido.para_json() == original.para_json()




def teste_amostra_inexistente_devolve_none():

    assert relatorio_fabricado().amostra('nao_existe') is None

    assert lote().amostra('amostra12') is None




def teste_contar_da_o_mesmo_numero_que_ler_tudo(tmp_path, memoria):

    caminho = relatorio_com(tmp_path, 40)

    assert contar_amostras(caminho) == len(carregar(caminho).amostras) == 40

    assert memoria[str(caminho)][2] == 40




def teste_sem_relatorio_e_none_e_nao_zero(tmp_path, memoria):

    #* zero seria lido como "corrida sem amostras"; ausencia tem que continuar ausencia

    assert contar_amostras(tmp_path / 'nao_existe.json') is None




def teste_relatorio_ilegivel_e_none(tmp_path, memoria):

    assert contar_amostras(gravar(tmp_path, '{isto não é json')) is None

    assert memoria == {}, 'um relatorio ilegivel nao pode ficar guardado como contado'




def teste_relatorio_regravado_e_recontado(tmp_path, memoria):

    #* o caso que a validacao por mtime + tamanho existe para pegar

    caminho = relatorio_com(tmp_path, 8)

    assert contar_amostras(caminho) == 8

    relatorio_com(tmp_path, 40)

    estado = caminho.stat()

    os.utime(caminho, ns=(estado.st_atime_ns, estado.st_mtime_ns + 1_000_000))  #* o mtime tem resolucao finita: forca a marca a diferir como numa regravacao separada por qualquer intervalo perceptivel

    assert contar_amostras(caminho) == 40, 'leu do cache um relatorio que mudou'




def teste_relatorio_apagado_some_do_cache(tmp_path, memoria):

    caminho = relatorio_com(tmp_path, 5)

    assert contar_amostras(caminho) == 5 and str(caminho) in memoria

    caminho.unlink()

    assert contar_amostras(caminho) is None and str(caminho) not in memoria




def teste_a_segunda_leitura_nao_toca_no_conteudo(tmp_path, memoria, monkeypatch):

    #* prova que o cache esta mesmo sendo usado, e nao que o teste e rapido

    caminho = relatorio_com(tmp_path, 12)

    assert contar_amostras(caminho) == 12

    def explode(*argumentos, **nomeados):

        raise AssertionError('releu o relatorio inteiro em vez de usar o cache')


    monkeypatch.setattr(leitura_de_amostras, 'carregar', explode)

    assert contar_amostras(caminho) == 12




def teste_resumo_so_lista_as_situacoes_que_ocorreram():

    #* um "0 sem acerto" impresso sugeriria que alguem esperava o contrario

    codigos = [situacao['codigo'] for situacao in resumo(lote(amostra('a', situacao='ok', organismo='X'), amostra('b')))['situacoes']]

    assert codigos == ['ok', 'sem_acerto']




def teste_resumo_ordena_pela_ordem_fixa_e_poe_as_desconhecidas_no_fim():

    #* o cabecalho nao muda de forma entre um lote e outro; situacao de relatorio mais novo sai por ultimo, com o codigo cru

    relatorio = lote(amostra('a', situacao='zz_nova'), amostra_que_falhou('b', 'estourou'), amostra('c', situacao='ok', organismo='X'), amostra('d', situacao='ok', organismo='X'))

    situacoes = resumo(relatorio)['situacoes']

    assert [situacao['codigo'] for situacao in situacoes] == ['ok', 'nao_montou', 'zz_nova']

    assert situacoes[0]['n'] == 2 and situacoes[0]['classe'] == 'pronto'

    assert situacoes[2] == {'codigo': 'zz_nova', 'texto': 'zz_nova', 'classe': 'sub', 'nota': '', 'n': 1}




def teste_resumo_de_lote_sem_amostras_nao_quebra():

    contagens = resumo(lote())

    assert contagens['total'] == 0 and contagens['situacoes'] == [] and contagens['avulsas'] == 0

    assert listar(lote()) == []




def teste_resumo_nao_emite_juizo_sobre_o_lote():

    #* o relatorio relata, nao conclui: nada de nota, score ou aprovacao

    assert not {'nota', 'score', 'qualidade', 'aprovadas', 'boas', 'ruins'} & set(resumo(relatorio_fabricado()))




def teste_resumo_copia_o_contexto_do_lote_e_descarta_nota_vazia():

    contagens = resumo(lote(amostra(), pasta='corrida', gerado_em='05/09/2026 10:00', n_arquivos=2, trim=4, banco='18S (banco local curado)', notas=['', 'a placa veio com 4 marcadores', None]))

    assert (contagens['pasta'], contagens['gerado_em'], contagens['n_arquivos'], contagens['trim'], contagens['banco']) == ('corrida', '05/09/2026 10:00', 2, 4, '18S (banco local curado)')

    assert contagens['notas'] == ['a placa veio com 4 marcadores'] and 'Tracy' in contagens['motor']




def teste_resumo_conta_avulsas_e_pareadas():

    contagens = resumo(lote(*[amostra(f'par{i}') for i in range(40)], *[avulsa(f'avulsa{i}') for i in range(81)]))

    assert contagens['avulsas'] == 81 and contagens['pareadas'] == 40 and contagens['total'] == 121




def teste_corrida_inteira_sem_par_nao_passa_despercebida():

    #* o caso real: nenhuma leitura pareou. `avulsas` tem de ser diferente de zero, senao a tela nao tem como avisar

    contagens = resumo(lote(*[avulsa(f'avulsa{i}') for i in range(81)]))

    assert contagens['avulsas'] == 81 and contagens['pareadas'] == 0




def teste_lote_normal_nao_dispara_aviso():

    assert resumo(lote(*[amostra(f'par{i}') for i in range(40)]))['avulsas'] == 0




def teste_resumo_conta_ressalvas_erros_e_contigs_invertidos():

    relatorio = lote(amostra('a', contig_invertido=True, ressalvas=[Ressalva('x', 'y', 'z')]), amostra('b'), amostra_que_falhou('c', 'estourou'))

    contagens = resumo(relatorio)

    assert (contagens['com_contig_invertido'], contagens['com_ressalva'], contagens['com_erro']) == (1, 2, 1)  #* a que falhou traz a ressalva da falha




def teste_amostra_sem_acerto_mantem_a_identidade_vazia_e_nunca_zero():

    linha = formatar_amostra(amostra())

    for campo in AUSENTES:

        assert linha[campo] == '', f'{campo} deveria ficar vazio'

        assert linha[campo] != 0 and linha[campo] != '0'


    assert linha['identificado'] is False

    assert linha['consenso_pb'] == 300 and linha['cobertura_media'] == '1.50', 'a amostra continua montada, com consenso medido'




def teste_cobertura_ausente_fica_vazia():

    linha = formatar_amostra(amostra(cobertura_media=None, consenso_pb=0, pct_cobertura_1=0.0))

    assert linha['cobertura_media'] == '' and linha['pct_cobertura_1'] == ''




def teste_identidade_sai_com_uma_casa_e_valor_e_como_o_nucleo_gravou():

    #* o nucleo grava round(identidade, 1); imprimir 99.400 onde o blastn mediu 99,44 afirmaria uma precisao que o numero nao tem.
    #* o valor_e e texto ('0.0', '1e-150') e passa como esta: 0.0 e uma medida, nao ausencia

    linha = formatar_amostra(amostra(organismo='Babesia vogeli', acesso='KY290979.1', identidade=99.4, cobertura_da_consulta=100.0, valor_e='0.0', marcador='18S', situacao='ok'))

    assert (linha['identidade'], linha['cobertura_da_consulta'], linha['valor_e'], linha['marcador']) == ('99.4', '100.0', '0.0', '18S')

    assert linha['identificado'] is True and linha['situacao_classe'] == 'pronto'




def teste_numero_nao_inventa_medida():

    assert leitura_de_amostras.numero(None, 1) == '' and leitura_de_amostras.numero('abc', 1) == ''

    assert leitura_de_amostras.numero(0, 1) == '0.0' and leitura_de_amostras.numero('99.44', 1) == '99.4'




def teste_falta_de_ferramenta_aparece_diferente_de_falta_de_acerto():

    #* blastn ausente NAO e "nenhum acerto": um fala da instalacao, o outro do banco consultado

    a, b, c = listar(lote(amostra('a'), amostra('b', situacao='blast_indisponivel'), amostra('c', situacao='sem_banco')))

    assert len({a['situacao_texto'], b['situacao_texto'], c['situacao_texto']}) == 3

    assert a['situacao_classe'] != b['situacao_classe'] and b['situacao_classe'] == c['situacao_classe']

    assert 'instalação' in b['situacao_nota'] and 'instalação' in c['situacao_nota']

    assert 'não a amostra' in a['situacao_nota']




def teste_sem_acerto_nunca_e_pintado_como_falha():

    #* o vermelho e da amostra que nao montou, nao da que nao tem acerto

    for linha in listar(lote(amostra('a'), amostra('b', situacao='blast_indisponivel'), amostra('c', situacao='sem_banco'))):

        assert linha['situacao_classe'] not in ('falhou', 'erro') and linha['erro'] == ''




def teste_situacao_desconhecida_sai_com_o_codigo_cru():

    #* relatorio mais novo que esta tela: melhor ilegivel do que traduzido errado

    linha = formatar_amostra(amostra(situacao='algo_que_ainda_nao_existe'))

    assert linha['situacao_texto'] == 'algo_que_ainda_nao_existe' and linha['situacao_classe'] == 'sub' and linha['situacao_nota'] == ''




def teste_amostra_que_nao_montou_nao_se_confunde_com_sem_acerto():

    #* um .ab1 corrompido produzia uma pilula IDENTICA a das 19 cujo "sem acerto" e o resultado certo: falha exibida como resultado

    ruim = formatar_amostra(amostra_que_falhou('RUIM', 'TracyError: consensus falhou', leituras=['RUIM_F_BTF2.ab1']))

    boa = formatar_amostra(amostra('OK'))

    assert ruim['situacao'] == 'nao_montou' and boa['situacao'] == 'sem_acerto'

    assert ruim['situacao_texto'] != boa['situacao_texto'] and ruim['situacao_classe'] == 'falhou' != boa['situacao_classe']

    assert 'nada chegou a ser consultado' in ruim['situacao_nota'], 'a frase nao pode afirmar que o banco foi consultado'

    assert ruim['erro'] == 'TracyError: consensus falhou' and ruim['consenso_pb'] == 0

    assert ruim['ressalvas'][0]['codigo'] == 'falha_montagem' and 'não completou' in ruim['ressalvas'][0]['texto']

    assert ruim['leituras'][0]['sentido_pelo_nome'] == 'F' and ruim['leituras'][0]['primer'] == 'BTF2', 'o que o nome do arquivo diz continua na tela'




def teste_consenso_de_zero_bases_sem_erro_tambem_e_nao_montou():

    #* a guarda da tela: 'sem_acerto' afirmaria que o banco foi consultado, e nao havia consenso para consultar.
    #* o CSV nao passa pela guarda: grava o codigo que o nucleo escreveu. a paridade CSV x tela vale para a amostra
    #* que montou; aqui os dois divergem de proposito e o teste prende os dois lados

    vazia = amostra(consenso_pb=0)

    assert formatar_amostra(vazia)['situacao'] == 'nao_montou'

    cabecalho, (linha,) = linhas_do_csv(lote(vazia))

    assert linha[cabecalho.index('situacao_id')] == 'sem_acerto' and linha[cabecalho.index('consenso_pb')] == '0'




def teste_quem_nao_montou_sai_da_contagem_de_sem_acerto():

    contagens = resumo(lote(amostra_que_falhou('RUIM', 'estourou'), amostra('A'), amostra('B', situacao='ok', organismo='X')))

    assert contagens['por_situacao'] == {'ok': 1, 'sem_acerto': 1, 'nao_montou': 1}  #* a falha NAO entra em sem_acerto

    assert contagens['com_erro'] == 1




def teste_ressalvas_chegam_na_saida_com_o_limiar_junto():

    linha = formatar_amostra(amostra(ressalvas=[Ressalva('cobertura_1', '189 base(s) sustentadas por uma leitura.', 'cobertura < 2')]))

    assert linha['n_ressalvas'] == 1

    assert linha['ressalvas'][0] == {'codigo': 'cobertura_1', 'texto': '189 base(s) sustentadas por uma leitura.', 'regra': 'cobertura < 2'}




def teste_contig_invertido_e_informado_como_fato():

    #* a polaridade do contig e arbitraria; informar nao e acusar

    a, b = listar(lote(amostra('a', contig_invertido=True), amostra('b', contig_invertido=False)))

    assert a['contig_invertido'] is True and b['contig_invertido'] is False




def teste_as_leituras_saem_com_janela_e_qualidade():

    fatos = relatorio_fabricado().amostra('amostra12')

    fatos.leituras[0].coluna_inicial, fatos.leituras[0].coluna_final = 95, 418

    forward, reverse = formatar_amostra(fatos)['leituras']

    assert forward['janela'] == '95–418' and forward['q_medio'] == 'Q41.2' and forward['q_rotulo'] == 'alta' and forward['bases'] == 410

    assert reverse['janela'] == '', 'sem coluna final nao ha janela para mostrar'

    assert forward['sentido_diverge'] is False and reverse['primer'] == 'BTR2'




def teste_a_leitura_aponta_quando_o_nome_e_o_dna_discordam():

    #* o relatorio ja aponta a divergencia entre o sentido do nome e o medido; a tela so repete, e nao inventa quando falta um dos dois

    assert formatar_leitura(FatosDaLeitura('x', sentido_pelo_nome='F', sentido_medido='R'))['sentido_diverge'] is True

    assert formatar_leitura(FatosDaLeitura('x', sentido_pelo_nome='F', sentido_medido=None))['sentido_diverge'] is False

    assert formatar_leitura(FatosDaLeitura('x'))['q_medio'] == '' and formatar_leitura(FatosDaLeitura('x'))['janela'] == ''




def teste_a_tabela_e_a_pagina_mostram_os_mesmos_numeros():

    #* duas telas do mesmo sistema nao podem discordar sobre a mesma medida

    relatorio = lote(*relatorio_fabricado(chaves=('amostra12', 'amostra28')).amostras, amostra('c', situacao='blast_indisponivel'), amostra_que_falhou('d', 'estourou'))

    for linha in listar(relatorio):
        assert formatar_amostra(relatorio.amostra(linha['chave'])) == linha




def teste_os_valores_batem_com_as_colunas_do_csv():

    #* o CSV e o formato que o laboratorio ja recebe; a tela nao pode exibir outro numero nem chamar a coluna de outro nome

    relatorio = lote(*relatorio_fabricado(chaves=('amostra12', 'amostra28')).amostras, amostra('c', situacao='blast_indisponivel', contig_invertido=True), amostra('d'))

    cabecalho, linhas = linhas_do_csv(relatorio)

    assert cabecalho == COLUNAS_DO_CSV

    for linha_csv, linha in zip(linhas, listar(relatorio)):

        assert linha_csv[cabecalho.index('amostra')] == linha['chave']

        assert linha_csv[cabecalho.index('contig_invertido')] == ('sim' if linha['contig_invertido'] else 'nao')

        for nosso, coluna in EQUIVALENTES.items():
            assert str(linha[nosso]) == linha_csv[cabecalho.index(coluna)], nosso




def teste_csv_deixa_identidade_vazia_quando_nao_houve_acerto():

    cabecalho, (linha,) = linhas_do_csv(lote(amostra('x')))

    assert linha[cabecalho.index('identidade_%')] == '' and linha[cabecalho.index('e_value')] == ''

    assert linha[cabecalho.index('situacao_id')] == 'sem_acerto' and linha[cabecalho.index('consenso_pb')] == '300'




def teste_csv_separa_falta_de_ferramenta_de_falta_de_acerto():

    cabecalho, linhas = linhas_do_csv(lote(amostra('a'), amostra('b', situacao='blast_indisponivel'), amostra_que_falhou('c', 'estourou')))

    assert [linha[cabecalho.index('situacao_id')] for linha in linhas] == ['sem_acerto', 'blast_indisponivel', 'nao_montou']

    assert linhas[2][cabecalho.index('erro')] == 'estourou' and 'não completou' in linhas[2][cabecalho.index('ressalvas')]




def teste_csv_escreve_as_ressalvas_por_extenso_numa_celula_so():

    cabecalho, (linha,) = linhas_do_csv(lote(amostra('a', ressalvas=[Ressalva('x', 'uma, com vírgula', 'r1'), Ressalva('y', 'outra', 'r2')])))

    assert linha[cabecalho.index('ressalvas')] == 'uma, com vírgula | outra', 'a virgula do texto nao pode quebrar a coluna'




def teste_o_html_do_lote_e_um_documento_inteiro_com_titulo():

    #* sem <title>, o "salvar como PDF" do navegador nomeia o arquivo com a URL

    documento = documento_html(relatorio_fabricado(nome='corrida'))

    assert documento.startswith('<!doctype html>') and '<title>EasyContig BR — corrida</title>' in documento

    assert 'Tracy' in documento and 'Babesia vogeli' in documento and documento.rstrip().endswith('</html>')

    assert '<title>EasyContig BR — lote</title>' in documento_html(lote(amostra()))




def teste_o_titulo_do_html_escapa_o_nome_da_corrida():

    documento = documento_html(relatorio_fabricado(nome='<script>x</script>'))

    assert '<title>EasyContig BR — &lt;script&gt;x&lt;/script&gt;</title>' in documento




def teste_o_lote_curado_consulta_os_dois_bancos_da_instalacao_na_ordem(tmp_path, ambiente):

    ambiente.setenv('EASYCONTIG_DATA_DIR', str(tmp_path / 'dados'))
    ambiente.setenv('EASYCONTIG_DB_18S', str(tmp_path / 'b18'))
    ambiente.setenv('EASYCONTIG_DB_16S', str(tmp_path / 'b16'))

    cfg = configuracao.carregar()

    for referencia in ('', 'curado'):

        bancos = bancos_do_lote(cfg, referencia)

        assert [banco.marcador for banco in bancos] == ['18S', '16S']

        assert [Path(banco.prefixo) for banco in bancos] == [tmp_path / 'b18', tmp_path / 'b16']




def teste_sem_banco_curado_declarado_o_lote_nao_consulta_nada(cfg):

    assert bancos_do_lote(cfg) == []




def teste_o_banco_escolhido_entra_sozinho(cfg):

    #* a escolha e do usuario, nao uma cascata 18S -> 16S

    fingir_banco(cfg, 'meu_abc_refs')

    bancos = bancos_do_lote(cfg, 'meu_abc_refs')

    assert len(bancos) == 1 and bancos[0].marcador == 'refs' and bancos[0].instalado




def teste_o_banco_escolhido_que_nao_esta_montado_orienta(cfg):

    with pytest.raises(ValueError) as erro:
        bancos_do_lote(cfg, 'meu_abc_sumiu')

    assert 'não está montado' in str(erro.value) and 'página de bancos' in str(erro.value)




def teste_sem_blastn_nao_ha_busca_e_o_lote_monta_mesmo_assim(cfg, monkeypatch):

    monkeypatch.setattr(executor_de_lote.ferramentas, 'procurar_programa', lambda *argumentos, **nomeados: None)

    assert busca_de_referencia(cfg) is None




def teste_blastn_pendurado_e_derrubado_e_conta_como_busca_que_nao_rodou(tmp_path, cfg):

    #* um blastn que nunca termina seguraria a amostra (e o trabalhador) para sempre. o teto derruba, e o lote diz
    #* "a busca nao rodou" (blast_indisponivel), nunca "sem acerto": ausencia de resposta nao e resposta

    pendurado = tmp_path / 'blastn'

    pendurado.write_text('#!/bin/sh\nexec sleep 30\n')

    pendurado.chmod(0o755)

    banco = identificacao.BancoDeMarcador(fingir_banco(cfg, 'apicomplexa_18s') / 'apicomplexa_18s', '18S')

    busca = identificacao.BuscaDeReferencia(pendurado, [banco], tempo_limite=0.3)

    comeco = time.monotonic()

    with pytest.raises(identificacao.ErroDoBlast, match='passou de 0.3 s no banco 18S'):
        busca.acertos('ACGT' * 20, banco)

    assert time.monotonic() - comeco < 5

    assert busca.identificar('ACGT' * 20).status == 'blast_indisponivel'




def teste_com_blastn_a_busca_leva_os_bancos_do_lote(cfg):

    exigir_ferramenta('blastn')

    fingir_banco(cfg, 'meu_abc_refs')

    busca = busca_de_referencia(cfg, 'meu_abc_refs')

    assert busca is not None and [banco.marcador for banco in busca.bancos] == ['refs']




def teste_lote_vazio_falha_em_vez_de_gerar_relatorio_em_branco(cfg):

    pastas = semear(cfg, 'vazio', [])

    (pastas.entrada / 'leia-me.txt').write_text('nao sou um trace')

    with pytest.raises(ValueError, match='nenhum arquivo'):
        executar(cfg, 'vazio')

    assert not pastas.relatorio_json.exists()




def teste_envio_incompleto_para_tudo(cfg, sinteticos):

    #* a trava do defeito de 33 de 40: relatorio a menos e indistinguivel de uma corrida menor, entao o certo e falhar e nao processar

    pastas = semear(cfg, 'curto', [PASTA / nome for nome in ('amostra12_F_BTF2.ab1', 'amostra12_R_BTR2.ab1')])

    with pytest.raises(ValueError, match='envio incompleto'):
        executar(cfg, 'curto', n_esperado=80)

    assert not pastas.relatorio_json.exists() and not pastas.trabalho.exists(), 'nada pode ter sido processado'




def teste_banco_escolhido_inexistente_falha_antes_de_montar(cfg, sinteticos):

    exigir_ferramenta('blastn')

    pastas = semear(cfg, 'ruim', [PASTA / nome for nome in ('amostra12_F_BTF2.ab1', 'amostra12_R_BTR2.ab1')])

    with pytest.raises(ValueError, match='não está montado'):
        executar(cfg, 'ruim', n_esperado=2, referencia='meu_abc_sumiu')

    assert not pastas.relatorio_json.exists() and not list(pastas.trabalho.glob('*')), 'o tracy nao pode ter rodado'




def teste_par_real_monta_identifica_e_grava_os_tres_artefatos(tmp_path, ambiente):

    #! aqui, e SO aqui, o .ab1 precisa ser REAL: o teste confere a ESPECIE identificada, coisa que DNA sorteado nunca vai dar

    exigir_ferramenta('tracy')
    exigir_ferramenta('blastn')

    banco_18s, banco_16s = exigir_bancos_curados()

    reais = pasta_de_ab1_reais()

    ambiente.setenv('EASYCONTIG_DATA_DIR', str(tmp_path / 'dados'))
    ambiente.setenv('EASYCONTIG_DB_18S', str(banco_18s))
    ambiente.setenv('EASYCONTIG_DB_16S', str(banco_16s))

    cfg = configuracao.carregar()

    pastas = semear(cfg, 'par', [reais / nome for nome in PAR_REAL])

    relatorio = executar(cfg, 'par', nome='babesia', n_esperado=2)

    assert len(relatorio.amostras) == 1 and relatorio.pasta == 'babesia'

    fatos = relatorio.amostras[0]

    assert fatos.erro is None and fatos.organismo == 'Babesia vogeli'

    assert 99.0 < fatos.identidade < 100.0, 'o mesmo numero que o desktop entrega para esta amostra'

    assert fatos.marcador == '18S' and fatos.situacao == 'ok' and len(fatos.leituras) == 2

    for caminho in (pastas.relatorio_html, pastas.relatorio_json, pastas.resultado_csv):
        assert caminho.exists() and caminho.stat().st_size > 0


    assert 'Tracy' in pastas.relatorio_html.read_text(encoding='utf-8')

    assert carregar(pastas.relatorio_json).amostra('amostra28').organismo == 'Babesia vogeli', 'o que a tela vai ler e o que o executor mediu'

    cabecalho, (linha,) = linhas_do_csv(relatorio)

    assert linha[cabecalho.index('organismo')] == 'Babesia vogeli' and linha[cabecalho.index('situacao_id')] == 'ok'




def teste_um_par_de_verdade_e_reconhecido_pelo_dna(sinteticos):

    #* duas leituras da mesma regiao em sentidos opostos tem que se reconhecer

    pares, sobras = orientacao.analisar([str(ADIANTE), str(REVERSO)])

    assert len(pares) == 1 and sobras == [], 'o par nao foi reconhecido'

    par = pares[0]

    assert par.pontuacao > 0.9, f'sobreposicao invertida fraca demais: {par.pontuacao}'

    assert par.mesmo_sentido < 0.1, f'as duas se pareceram no MESMO sentido ({par.mesmo_sentido}): nao sao um par F/R, sao duas leituras parecidas'

    assert par.mesma_amostra, 'os nomes concordam que e o mesmo individuo'




def teste_sem_referencia_o_detector_nao_inventa_quem_e_o_forward(sinteticos):

    #* o limite, declarado: sem referencia ele prova que sao OPOSTAS; qual e a forward em termos absolutos, so a comparacao com as referencias 18S diz

    (par,), _ = orientacao.analisar([str(ADIANTE), str(REVERSO)])

    assert par.so_relativo and not par.certo

    assert par.rotulos == ('leitura 1', 'leitura 2 (sentido oposto)')

    assert 'Não deu para dizer qual é o forward' in par.explicar() and 'mesma amostra' in par.explicar()




def teste_amostras_diferentes_nao_viram_par(sinteticos):

    #* nem que o nome sugira: os dois arquivos sao _F_BTF2

    pares, sobras = orientacao.analisar([str(ADIANTE), str(OUTRA_AMOSTRA)])

    assert pares == [] and len(sobras) == 2, 'duas leituras de amostras distintas foram pareadas'




def teste_o_par_sobrevive_a_nome_trocado(tmp_path, sinteticos):

    #* o caso que o leia-me descreve: nomes embaralhados de proposito. quem responde e a sequencia, entao o par continua sendo par

    shutil.copy(ADIANTE, tmp_path / 'amostra12_R_BTR2.ab1')  #* o conteudo ADIANTE recebe nome de reverso, e vice-versa
    shutil.copy(REVERSO, tmp_path / 'amostra12_F_BTF2.ab1')

    pares, _ = orientacao.analisar([str(tmp_path / 'amostra12_F_BTF2.ab1'), str(tmp_path / 'amostra12_R_BTR2.ab1')])

    assert len(pares) == 1, 'com o nome trocado o par se perdeu: sinal de que o nome estava decidindo'

    assert pares[0].pontuacao > 0.9 and pares[0].mesmo_sentido < 0.1




def teste_a_diferenca_entre_par_e_nao_par_e_larga(sinteticos):

    #* a distancia entre os dois casos tem que ser larga, nao apertada: e o que o leia-me afirma ("sem meio-termo")

    (par,), _ = orientacao.analisar([str(ADIANTE), str(REVERSO)])

    assert par.pontuacao - par.mesmo_sentido > 0.5, f'a decisao ficou apertada: {par.pontuacao} invertido contra {par.mesmo_sentido} no mesmo sentido'




def teste_arquivo_ilegivel_fica_de_fora_sem_derrubar_a_analise(tmp_path, sinteticos):

    (tmp_path / 'lixo.ab1').write_bytes(b'isto nao e um cromatograma')

    pares, sobras = orientacao.analisar([str(ADIANTE), str(REVERSO), str(tmp_path / 'lixo.ab1')])

    assert len(pares) == 1 and sobras == []




def teste_a_pagina_do_lote_explica_o_motivo_da_leitura_avulsa():

    #* nao basta contar: a pagina tem de dizer POR QUE nao pareou, senao o numero sozinho continua parecendo falha do arquivo.
    #* espaco normalizado: uma quebra de linha no HTML nao pode derrubar uma verificacao de CONTEUDO

    html = re.sub(r'\s+', ' ', (RAIZ / 'easycontig_web' / 'templates' / 'lote.html').read_text(encoding='utf-8'))

    assert 'não formou par' in html

    assert 'mesma amostra' in html, 'a explicacao, nao so a contagem'

    assert 'poço da placa' in html, 'o caso concreto que produziu isto'




def teste_o_aviso_das_avulsas_aparece_na_rota_com_a_contagem(bancada, ab1):

    cliente = bancada.entrar()

    lote_id = bancada.enviar_lote(cliente, [('amostra12_F_BTF2.ab1', ab1['amostra12_F_BTF2.ab1'])])

    bancada.concluir(lote_id, relatorio=lote(*relatorio_fabricado().amostras, avulsa('sozinha'), avulsa('outra'), pasta='corrida'))

    pagina = re.sub(r'\s+', ' ', cliente.get(f'/lotes/{lote_id}').text)

    assert '2 leituras não formou par' in pagina and '1 amostra parearam normalmente' in pagina




def teste_lote_em_que_tudo_pareou_nao_mostra_o_aviso(bancada, ab1):

    #* um '0 avulsas' impresso sugeriria que alguem esperava o contrario

    cliente = bancada.entrar()

    lote_id = bancada.enviar_lote(cliente, [('amostra12_F_BTF2.ab1', ab1['amostra12_F_BTF2.ab1'])])

    bancada.concluir(lote_id)

    assert 'não formou par' not in cliente.get(f'/lotes/{lote_id}').text




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
