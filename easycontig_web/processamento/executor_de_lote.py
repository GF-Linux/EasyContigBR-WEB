"""
executor_de_lote.py — roda UM lote da web no nucleo e grava os tres arquivos.

Nada de ciencia aqui. Quem pareia, monta, identifica e mede e o nucleo
(lote.py, montagem.py, identificacao.py, relatorio.py). Este arquivo pega o
lote que a web ja gravou em disco, acha as ferramentas, escolhe os bancos,
chama o nucleo e deixa na pasta do lote o que a tela serve: relatorio.html,
relatorio.json e resultado.csv.


COMO FUNCIONA — da pasta do lote aos tres arquivos
==================================================

    executar(cfg, lote_id, nome, n_esperado, referencia, progresso)
           |
           |-- cfg.pastas_do_lote(lote_id)                 -> entrada/, trabalho/, relatorio.*, resultado.csv
           |
           |-- lote.achar_traces(pastas.entrada)           nenhum .ab1/.abi/.scf?      -> ValueError
           |                                               n != n_esperado?            -> ValueError (envio incompleto: NADA e processado)
           |
           |-- motor_do_tracy()                            ferramentas.exigir_programa: sem tracy -> FerramentaAusente
           |-- busca_de_referencia(cfg, referencia)        ferramentas.procurar_programa: sem blastn -> None (o lote monta; as amostras ficam blast_indisponivel)
           |         |
           |         '-- bancos_do_lote(cfg, referencia)   ''/'curado' -> [banco_18s, banco_16s] da configuracao
           |                                               outro id    -> estante_de_bancos(cfg).banco(id), se existe
           |                                                              (a UNICA EstanteDeBancos da web: pasta_de_bancos + makeblastdb da escada)
           |
           |-- lote.montar_lote(traces, MontadorDeAmostra(motor, trabalho, trim=cfg.trim), busca, nome, progresso)
           |                                               -> RelatorioDoLote (uma amostra ruim nao derruba o lote)
           |
           |-- relatorio.json  <- relatorio.para_json()
           |-- relatorio.html  <- documento_html(relatorio)   = <html><head><title> em volta de relatorio.renderizar_html
           '-- resultado.csv   <- para_csv(relatorio)         = uma linha por amostra, as 15 colunas de sempre
           |
           v
    RelatorioDoLote  (o trabalhador da fila so conta as amostras e registra o tempo)


COMO USAR
=========

    from easycontig_web import configuracao
    from easycontig_web.processamento import executor_de_lote

    cfg = configuracao.carregar()
    relatorio = executor_de_lote.executar(cfg, 'v2-UNsPd', nome='corrida 13719', n_esperado=80)

    for fatos in relatorio.amostras:
        print(fatos.chave, fatos.organismo, fatos.situacao)


O QUE MUDOU DO executor_de_lote.py ORIGINAL
===========================================

 1. pastas_do_lote saiu daqui: e configuracao.PastasDoLote (cfg.pastas_do_lote),
    porque tres arquivos precisavam da mesma regra e um deles a repetia para
    nao importar este.

 2. As ferramentas vem do ferramentas.py do nucleo, pela escada dele. O
    original gravava EASYCONTIG_BLAST_BIN no os.environ para o nucleo achar o
    blastn; agora ninguem escreve no ambiente — o nucleo le a variavel sozinho.
    estante_de_bancos(cfg) e o unico lugar da web que constroi a EstanteDeBancos
    (rotas de bancos, envio, consulta, cota, comum e arvore usam a mesma): o
    dados/bancos_de_referencia.py da web virou o do nucleo (veja NAO-PORTADO.md).

 3. Sem blastn o lote MONTA e cada amostra sai blast_indisponivel (busca=None
    em lote.montar_lote). E o que o original fazia por dentro do batch.py, uma
    excecao por amostra; e e a regra da casa: montar e avisar em vez de recusar.

 4. bancos_do_lote devolve a lista de BancoDeMarcador, e so. O original
    devolvia tambem um rotulo que executar() recebia como _rotulo e nunca usava.
    O banco escolhido pelo usuario entra com o marcador DELE (o gene da ficha,
    ou o apelido do banco proprio), e nao mais disfarcado de "18S" na coluna
    marcador — o relatorio passa a dizer contra o que a amostra foi comparada.

 5. O erro do banco nao montado ORIENTA: diz onde montar.

 6. Nomes: rep -> relatorio, s -> fatos, BatchReport -> RelatorioDoLote,
    SampleFacts -> FatosDaAmostra; as colunas do CSV sao as mesmas, byte a byte.

 7. Nada foi cortado: executar, documento_html e para_csv tem os mesmos
    chamadores de antes (trabalhador_da_fila, rotas e testes).


POR QUE NAO NASCE CLASSE
========================

    O executor nao e dono de nada: o tracy e do MotorTracy, a pasta e o trim
    sao do MontadorDeAmostra, o blastn e os bancos sao da BuscaDeReferencia,
    os caminhos sao da PastasDoLote. Ele recebe a configuracao, monta os donos
    e chama na ordem. Ficam funcoes.
"""

import csv

import html

import io

import bancos_de_referencia

import ferramentas

import identificacao

import lote as modulo_lote

import montagem

import motor_tracy

import relatorio as modulo_relatorio



CURADO = 'curado'  #* o valor de `referencia` que significa "os bancos 18S + 16S da instalacao", igual a ''

COLUNAS_DO_CSV = ['amostra', 'n_leituras', 'consenso_pb', 'cobertura_media', 'pct_cobertura_1x', 'organismo', 'accession',
                  'identidade_%', 'cobertura_query_%', 'e_value', 'marcador', 'situacao_id', 'contig_invertido', 'erro', 'ressalvas']  #! a ordem e o contrato com a planilha e o R do laboratorio




def motor_do_tracy():

    #* o MotorTracy com o tracy que a escada do ferramentas.py achar. sem tracy nao ha montagem: FerramentaAusente.

    return motor_tracy.MotorTracy(ferramentas.exigir_programa('tracy', 'EASYCONTIG_TRACY_BIN', 'bin', None))




def estante_de_bancos(cfg):

    #* a EstanteDeBancos desta instalacao: a pasta dos bancos da configuracao + o makeblastdb da escada do ferramentas.py
    #! makeblastdb ausente NAO derruba: a estante lista, consulta e remove; so a montagem sobe ErroDoBanco, na hora, com a orientacao

    return bancos_de_referencia.EstanteDeBancos(cfg.pasta_de_bancos, ferramentas.procurar_programa('makeblastdb', 'EASYCONTIG_BLAST_BIN', 'blast', None))




def bancos_do_lote(cfg, referencia=''):

    #* os BancoDeMarcador que o lote consulta, na ordem. vazio ou 'curado' = os dois da instalacao (18S depois 16S).
    #! um banco escolhido pelo usuario entra SOZINHO: a escolha e dele, nao uma cascata 18S -> 16S

    if not referencia or referencia == CURADO:
        return [identificacao.BancoDeMarcador(prefixo, marcador) for prefixo, marcador in ((cfg.banco_18s, '18S'), (cfg.banco_16s, '16S')) if prefixo]


    estante = estante_de_bancos(cfg)

    if not estante.existe(referencia):
        raise ValueError(f"o banco '{referencia}' não está montado nesta instalação — monte-o na página de bancos "
                         f"(ou escolha o banco curado) e envie o lote de novo")

    return [estante.banco(referencia)]




def busca_de_referencia(cfg, referencia=''):

    #* a BuscaDeReferencia pronta, ou None se nao ha blastn: o lote monta mesmo assim e o relatorio diz que a busca nao rodou

    blastn = ferramentas.procurar_programa('blastn', 'EASYCONTIG_BLAST_BIN', 'blast', None)

    if blastn is None:
        return None

    return identificacao.BuscaDeReferencia(blastn, bancos_do_lote(cfg, referencia))




def executar(cfg, lote_id, nome='', n_esperado=0, referencia='', progresso=None):

    #* monta e identifica o lote ja gravado em disco; grava HTML + JSON + CSV na pasta do lote e devolve o RelatorioDoLote

    pastas = cfg.pastas_do_lote(lote_id)

    traces = modulo_lote.achar_traces(pastas.entrada)

    if not traces:
        raise ValueError('nenhum arquivo .ab1/.abi/.scf no lote — a pasta de entrada esta vazia ou so tem arquivos de outro tipo')


    #! se o que esta em disco nao bate com o que o lote declarou, o certo e PARAR: um relatorio de 33 de 40 amostras
    #! nao se distingue de um relatorio de uma corrida com 33, e o usuario nao teria como perceber a falta

    if n_esperado and len(traces) != n_esperado:
        raise ValueError(f'o lote declarou {n_esperado} arquivo(s) e há {len(traces)} em disco; envio incompleto — nada foi processado')


    pastas.trabalho.mkdir(parents=True, exist_ok=True)

    busca = busca_de_referencia(cfg, referencia)  #* antes do tracy: um banco inexistente falha aqui, sem ter montado nada

    montador = montagem.MontadorDeAmostra(motor_do_tracy(), pastas.trabalho, trim=cfg.trim)

    relatorio = modulo_lote.montar_lote(traces, montador, busca, nome=nome or lote_id, progresso=progresso)


    pastas.relatorio_json.write_text(relatorio.para_json(), encoding='utf-8')
    pastas.relatorio_html.write_text(documento_html(relatorio), encoding='utf-8')
    pastas.resultado_csv.write_text(para_csv(relatorio), encoding='utf-8')

    return relatorio




def documento_html(relatorio):

    #* o fragmento do nucleo dentro de um documento HTML inteiro: sem <title>, o "salvar como PDF" do navegador nomeia o arquivo com a URL

    titulo = f'EasyContig BR — {relatorio.pasta}' if relatorio.pasta else 'EasyContig BR — lote'

    return ("<!doctype html>\n<html lang='pt-BR'>\n<head>\n"
            "<meta charset='utf-8'>\n"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
            f'<title>{html.escape(titulo)}</title>\n'
            '</head>\n<body>\n' + modulo_relatorio.renderizar_html(relatorio) + '\n</body>\n</html>\n')




def para_csv(relatorio):

    #* uma linha por amostra, o formato que o laboratorio ja recebia na mao.
    #! identidade fica VAZIA quando nao houve acerto, e nao 0: zero e um numero e seria lido como medida.
    #! quem separa "achou", "procurou e nao achou" e "nao conseguiu procurar" e a coluna situacao_id

    saida = io.StringIO()

    escritor = csv.writer(saida)

    escritor.writerow(COLUNAS_DO_CSV)

    for fatos in relatorio.amostras:

        escritor.writerow([fatos.chave,
                           len(fatos.leituras),
                           fatos.consenso_pb,
                           f'{fatos.cobertura_media:.2f}' if fatos.cobertura_media else '',
                           f'{fatos.pct_cobertura_1:.1f}' if fatos.consenso_pb else '',
                           fatos.organismo or '',
                           fatos.acesso or '',
                           f'{fatos.identidade:.1f}' if fatos.identidade is not None else '',
                           f'{fatos.cobertura_da_consulta:.1f}' if fatos.cobertura_da_consulta is not None else '',
                           fatos.valor_e or '',
                           fatos.marcador or '',
                           fatos.situacao,
                           'sim' if fatos.contig_invertido else 'nao',
                           fatos.erro or '',
                           ' | '.join(ressalva.texto for ressalva in fatos.ressalvas)])

    return saida.getvalue()




def main():

    from easycontig_web import configuracao

    lote_id = input('Id do lote ja gravado em lotes/<id>/entrada (ou enter para sair): ').strip()

    if not lote_id:
        raise SystemExit(0)

    referencia = input("Banco (enter = curado 18S + 16S; ou o id de um banco montado, ex. apicomplexa_18s): ").strip()

    cfg = configuracao.carregar()


    def mostrar(feito, total, chave):
        if chave:
            print(f'  {feito + 1} de {total}: {chave}')


    relatorio = executar(cfg, lote_id, referencia=referencia, progresso=mostrar)

    pastas = cfg.pastas_do_lote(lote_id)

    print(f'\n{relatorio!r}  banco: {relatorio.banco}')

    for fatos in relatorio.amostras:
        print(f'  {fatos!r}')

    print(f'\nGravados: {pastas.relatorio_html.name}, {pastas.relatorio_json.name}, {pastas.resultado_csv.name} em {pastas.raiz}')




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
