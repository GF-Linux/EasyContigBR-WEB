"""
executor_de_arvore.py — roda UMA arvore filogenetica sobre um lote ja montado.

Como no executor_de_lote: nada de ciencia aqui. Quem alinha, apara, avalia e
infere e o filogenia.py do nucleo. Este arquivo junta os consensos que o
tracy deixou em trabalho/, descobre o marcador de cada um pelo NOME, chama o
nucleo uma vez por marcador e desenha o resultado.

O desenho FICA deste lado de proposito: o filogenia.py nao importa matplotlib
pelo mesmo motivo que o nucleo nao importa Qt — quem instala o nucleo num
servidor nao deve baixar uma pilha de graficos.


COMO FUNCIONA — de trabalho/ ate arvore/<marcador>/
===================================================

    executar(cfg, lote_id, referencia, geracoes, etapa)
           |
           |-- consensos_do_lote(cfg, lote_id, referencia)
           |         |
           |         |-- cfg.pastas_do_lote(lote_id).trabalho     os *.fa do tracy
           |         |       <amostra>.fa        consenso do PAR (o uso normal)
           |         |       <amostra>.cons.fa   consenso da leitura avulsa (assemble)
           |         |       <amostra>.align.fa  NAO e consenso: e o alinhamento das duas leituras -> pulado
           |         |
           |         |-- primeira_sequencia(arquivo)              maiuscula, sem '-'; vazio nao vira taxon
           |         |
           |         '-- marcador_da_amostra(amostra, reserva)    o gene escrito no NOME ganha; reserva_do_lote() e o
           |                                                      marcador do banco do lote; sem nada: 'desconhecido'
           |
           |   nenhum consenso? -> ValueError (rode o lote primeiro)
           |
           |-- filogenia.agrupar_por_marcador(consensos)          um grupo por marcador; < 4 amostras = recusado
           |-- mrbayes_da_instalacao()                            so se algum grupo e montavel; sem mb -> FerramentaAusente
           |
           '-- para cada grupo:
                   etapa('16S (12 amostras)')
                   recusado  -> {'marcador', 'n', 'recusado'}
                   montavel  -> filogenia.montar_arvore(grupo, arvore/<marcador>/, mrbayes, geracoes)
                                arvore.newick  <- resultado.newick (ja com os nomes do laboratorio)
                                arvore.png     <- desenhar(resultado)   figura e conforto: falhou, avisa e segue
                                resumo_do_marcador(...)  -> o dicionario que vai para o banco e para o arvore.html

    caminho_de_saida(cfg, lote_id, pasta, arquivo)  -> lotes/<id>/arvore/<pasta>/<arquivo>, os tres nomes conferidos


COMO USAR
=========

    from easycontig_web import configuracao
    from easycontig_web.processamento import executor_de_arvore

    cfg = configuracao.carregar()
    resumo = executor_de_arvore.executar(cfg, 'v2-UNsPd', referencia='apicomplexa_18s', etapa=print)

    for r in resumo:
        print(r['marcador'], r.get('recusado') or r['veredito'])


O QUE MUDOU DO executor_de_arvore.py ORIGINAL
=============================================

 1. Os nomes do nucleo: filo.Consensus -> filogenia.Consenso, TreeResult ->
    ResultadoDaArvore, assessment -> boletim, build_tree -> montar_arvore,
    read_consensus_tree -> ler_arvore_consenso, safe_name -> nome_seguro.

 2. O Newick e o mapa nome-seguro -> nome-do-laboratorio vem DENTRO do
    ResultadoDaArvore (montar_arvore ja le o .con.tre). O original reconstruia
    o mapa aqui e lia o .con.tre duas vezes (uma para o arquivo, outra para a
    figura). desenhar(resultado, destino) perdeu o parametro mapa.

 3. O mb vem da escada do ferramentas.py (EASYCONTIG_MRBAYES_BIN -> PATH),
    como o tracy e o blastn: MrBayes(caminho) recebe o caminho pronto. Sem mb
    e com algum grupo montavel, FerramentaAusente ANTES de criar pasta.

 4. reserva_do_lote: o marcador de reserva e o do banco que o lote consultou,
    pela mesma regra do executor_de_lote (gene da ficha, ou apelido do banco
    proprio). O original so conhecia o catalogo: banco proprio caia em
    'desconhecido'.

 5. Figura que nao saiu vira AVISO no resumo (e uma linha no log com a causa),
    em vez de sumir em silencio: o usuario ve que o Newick continua valendo.

 6. A arvore que sai e a do filogenia.py novo: consensos na mesma polaridade
    e semente fixa (aprovado no passo 9). A figura e o Newick mudam por isso,
    nao por este arquivo.

 7. caminho_de_saida confere pasta e arquivo com configuracao.nome_simples,
    o mesmo crivo do lote_id (o original repetia a regra pela terceira vez).

 8. marcadores_conhecidos usa Ficha.gene, que ja e '16S' de '16S rRNA', e
    sai em ordem FIXA (tamanho, depois alfabetica). O original ordenava um
    set so por tamanho: a ordem entre 16S/18S/28S/COI/dsb mudava a cada
    reinicio do servidor, e um nome com dois genes de tres letras caia ora
    num grupo, ora noutro.

 9. Dois bugs do original corrigidos de passagem: reserva so de espacos
    estourava IndexError (reserva.split()[0]); agora e 'desconhecido'. O
    erro de "nenhum consenso" ORIENTA. Nada foi cortado.


POR QUE NAO NASCE CLASSE
========================

    O executor nao e dono de nada: o mb e do MrBayes, os caminhos sao da
    PastasDoLote, os grupos e os resultados sao do filogenia.py. Ele le a
    pasta, chama na ordem e traduz o resultado para o dicionario da tela.
"""

import logging

import re

import bancos_de_referencia

import ferramentas

import filogenia

from easycontig_web import configuracao

from easycontig_web.processamento import executor_de_lote



REGISTRO = logging.getLogger('easycontig.arvore')

SUFIXOS_DE_CONSENSO = ('.cons.fa', '.fa')  #! a ordem importa: '.cons.fa' primeiro, senao '.fa' engole os dois e o nome da amostra sai com '.cons'

SUFIXO_DE_ALINHAMENTO = '.align.fa'  #! dois registros com gaps: entrando como taxon, a arvore seria das leituras, nao das amostras

DESCONHECIDO = 'desconhecido'  #* o marcador de quem nao tem gene no nome nem banco no lote: todos caem no mesmo grupo, que e honesto

PROBABILIDADE_MINIMA = 0.90  #! so rotula o ramo que o MrBayes sustentou: encher a figura de 0,51 daria ao olho estrutura onde o metodo diz que nao ha

FRONTEIRA = r'(?<![A-Za-z0-9]){}(?![A-Za-z0-9])'  #! '_16S_' e marcador; 'CAP16Sul' nao e. sem a fronteira, um nome de poco viraria grupo fantasma




def marcadores_conhecidos():

    #* os genes que o catalogo conhece, como aparecem no NOME do arquivo, do mais longo para o mais curto.
    #! sai do CATALOGO para nao haver duas verdades: um banco novo ensina o reconhecedor de nomes junto
    #! desempate alfabetico: o original ordenava um set so por tamanho, e a ordem entre 16S/18S/COI/dsb mudava a cada
    #! reinicio do servidor (hash randomization) — um nome com dois genes de 3 letras cairia ora num grupo, ora noutro

    return sorted({ficha.gene for ficha in bancos_de_referencia.CATALOGO}, key=lambda gene: (-len(gene), gene))




def marcador_da_amostra(nome, reserva=''):

    #* de que marcador e esta amostra. O NOME DO ARQUIVO VEM PRIMEIRO; o banco do lote e so a reserva.
    #! parece o contrario do obvio, mas a corrida real F13719 subiu 40 amostras de QUATRO marcadores numa pasta so,
    #! com o gene no nome (F13719_am24_CAP08_groEL_E08-F08). confiar no banco do lote juntaria os quatro numa arvore

    for marcador in marcadores_conhecidos():

        if re.search(FRONTEIRA.format(re.escape(marcador)), nome, re.IGNORECASE):
            return marcador

    palavras = reserva.split()  #* '18S rRNA' -> '18S'; '' ou so espacos -> nada (o original estourava com IndexError no so-espacos)

    return palavras[0] if palavras else DESCONHECIDO




def reserva_do_lote(cfg, referencia=''):

    #* o marcador do banco que o lote consultou, para as amostras sem gene no nome. curado/vazio: sem reserva.
    #* a mesma regra do executor_de_lote: gene da ficha do catalogo, ou apelido do banco proprio

    if not referencia or referencia == executor_de_lote.CURADO:
        return ''

    return executor_de_lote.estante_de_bancos(cfg).banco(referencia).marcador




def primeira_sequencia(arquivo):

    #* a sequencia do primeiro registro do FASTA, maiuscula e sem gaps. '' se o arquivo nao tem registro.
    #! o cabecalho de dentro e '>Consensus' para todas: quem da nome ao taxon e o arquivo, nao o FASTA

    dentro, pedacos = False, []

    for linha in arquivo.read_text(errors='replace').splitlines():

        linha = linha.strip()

        if linha.startswith('>'):

            if dentro:
                break  #* segundo registro: o primeiro acabou

            dentro = True

        elif linha and dentro:
            pedacos.append(linha)

    return ''.join(pedacos).upper().replace('-', '')




def consensos_do_lote(cfg, lote_id, referencia=''):

    #* junta os consensos que o lote ja montou, cada um com seu marcador: os mesmos arquivos que o laboratorio copiava a mao para o MEGA

    trabalho = cfg.pastas_do_lote(lote_id).trabalho

    reserva = reserva_do_lote(cfg, referencia)


    por_amostra = {}

    for arquivo in sorted(trabalho.glob('*.fa')):

        if arquivo.name.endswith(SUFIXO_DE_ALINHAMENTO):
            continue

        for sufixo in SUFIXOS_DE_CONSENSO:

            if arquivo.name.endswith(sufixo):
                por_amostra.setdefault(arquivo.name[:-len(sufixo)], arquivo)  #! setdefault: se um dia .cons.fa e .fa existirem para a mesma amostra, ganha o que se chama de consenso
                break


    consensos = []

    for amostra, arquivo in sorted(por_amostra.items()):

        sequencia = primeira_sequencia(arquivo)

        if sequencia:
            consensos.append(filogenia.Consenso(amostra, sequencia, marcador_da_amostra(amostra, reserva)))

    return consensos




def rotulo_do_ramo(ramo):

    #* a probabilidade posterior no ramo interno, so quando o MrBayes a sustentou

    if ramo.is_terminal() or not ramo.confidence or ramo.confidence < PROBABILIDADE_MINIMA:
        return ''

    return f'{ramo.confidence:.2f}'




def desenhar(resultado, destino):

    #* a figura da arvore, com a probabilidade posterior nos ramos. None se nao deu para desenhar: a figura e conforto,
    #* o .newick e o .con.tre sao o resultado, e uma falha de desenho nao derruba um pedido que a ciencia ja respondeu

    if not resultado.newick:
        return None

    try:

        import matplotlib  #! importados aqui de proposito: o modulo carrega (e o Newick sai) mesmo numa maquina sem matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from io import StringIO
        from Bio import Phylo

        arvore = Phylo.read(StringIO(resultado.newick), 'newick')

        for no in arvore.get_nonterminals():  #* o filogenia.py escreve a probabilidade como NOME do no interno; o Bio.Phylo desenha 'confidence'

            if no.name:

                try:
                    no.confidence = float(no.name)
                except ValueError:
                    pass

                no.name = None


        boletim = resultado.boletim

        figura, eixo = plt.subplots(figsize=(9, max(3.5, 0.42 * len(arvore.get_terminals()))))

        Phylo.draw(arvore, axes=eixo, do_show=False, branch_labels=rotulo_do_ramo)

        eixo.set_title(f'{resultado.marcador} — {boletim.n_taxons} amostras · {boletim.colunas_depois} pb'
                       f' · identidade mediana {boletim.identidade_mediana:.1f}%\n{resultado.veredito}', fontsize=9)

        eixo.set_xlabel('substituições por sítio')

        for lado in ('top', 'right'):
            eixo.spines[lado].set_visible(False)

        figura.tight_layout()

        figura.savefig(destino, dpi=140)

        plt.close(figura)

        return destino

    except Exception as erro:  #! qualquer falha de desenho: registra a causa e devolve None; quem chamou avisa o usuario

        REGISTRO.warning('figura de %s nao desenhada: %s', resultado.marcador, erro)

        return None




def mrbayes_da_instalacao():

    #* o MrBayes com o mb que a escada do ferramentas.py achar. sem mb nao ha arvore: FerramentaAusente.

    return filogenia.MrBayes(ferramentas.exigir_programa('mb', 'EASYCONTIG_MRBAYES_BIN', 'bin', None))




def resumo_do_marcador(grupo, resultado, figura):

    #* o dicionario que a tela mostra e o banco guarda: atravessa processo, entao e dado, nao objeto

    boletim = resultado.boletim

    avisos = list(boletim.avisos)

    if not figura:
        avisos.append('a figura não foi desenhada (veja o log do servidor); o Newick continua disponível para o FigTree, iTOL ou MEGA')

    return {'marcador': grupo.marcador,
            'n': boletim.n_taxons,
            'colunas': boletim.colunas_depois,
            'colunas_antes': boletim.colunas_antes,
            'identidade': round(boletim.identidade_mediana, 1),
            'asdsf': resultado.asdsf,
            'convergiu': resultado.convergiu,
            'veredito': resultado.veredito,
            'avisos': avisos,
            'figura': figura.name if figura else '',
            'newick': 'arvore.newick' if resultado.newick else '',
            'pasta': filogenia.nome_seguro(grupo.marcador)}




def executar(cfg, lote_id, referencia='', geracoes=filogenia.GERACOES, etapa=None):

    #* monta uma arvore por marcador e devolve o resumo que a tela mostra (lista de dicionarios)

    pastas = cfg.pastas_do_lote(lote_id)

    consensos = consensos_do_lote(cfg, lote_id, referencia)

    if not consensos:
        raise ValueError('nenhum consenso encontrado neste lote; a árvore precisa das amostras já montadas — rode o lote primeiro '
                         'e confira se trabalho/ tem os .fa do tracy')


    grupos = filogenia.agrupar_por_marcador(consensos)

    mrbayes = mrbayes_da_instalacao() if any(grupo.montavel for grupo in grupos) else None  #* so exige o mb se vai usa-lo; e antes de tocar o disco

    pastas.arvore.mkdir(parents=True, exist_ok=True)


    resumo = []

    for grupo in grupos:

        if etapa:
            etapa(f'{grupo.marcador} ({len(grupo.membros)} amostras)')

        if not grupo.montavel:
            resumo.append({'marcador': grupo.marcador, 'n': len(grupo.membros), 'recusado': grupo.recusado})
            continue


        pasta = pastas.arvore / filogenia.nome_seguro(grupo.marcador)

        resultado = filogenia.montar_arvore(grupo, pasta, mrbayes, geracoes)

        if resultado.newick:
            (pasta / 'arvore.newick').write_text(resultado.newick, encoding='utf-8')

        figura = desenhar(resultado, pasta / 'arvore.png')

        resumo.append(resumo_do_marcador(grupo, resultado, figura))

    return resumo




def caminho_de_saida(cfg, lote_id, pasta, arquivo):

    #* um arquivo da arvore, com os tres nomes conferidos em vez de confiados: pasta e arquivo vem da URL

    return (cfg.pastas_do_lote(lote_id).arvore
            / configuracao.nome_simples(pasta, 'pasta da árvore')
            / configuracao.nome_simples(arquivo, 'arquivo da árvore'))




def main():

    lote_id = input('Id do lote ja montado (trabalho/ com os .fa do tracy; ou enter para sair): ').strip()

    if not lote_id:
        raise SystemExit(0)

    referencia = input('Banco que o lote consultou (enter = nenhum; ex. apicomplexa_18s): ').strip()

    cfg = configuracao.carregar()

    resumo = executar(cfg, lote_id, referencia=referencia, etapa=lambda texto: print(f'  {texto}'))

    for r in resumo:

        if r.get('recusado'):
            print(f"\n{r['marcador']}: recusado — {r['recusado']}")
            continue

        print(f"\n{r['marcador']}: {r['n']} amostras, {r['colunas']} pb (de {r['colunas_antes']}), identidade mediana {r['identidade']}%")
        print(f"  {r['veredito']}  (ASDSF {r['asdsf']})")

        for aviso in r['avisos']:
            print(f'  aviso: {aviso}')

        print(f"  arquivos em {cfg.pastas_do_lote(lote_id).arvore / r['pasta']}: {r['newick'] or '(sem newick)'}, {r['figura'] or '(sem figura)'}")




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
