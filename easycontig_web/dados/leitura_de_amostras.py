"""
leitura_de_amostras.py — le o relatorio.json ja gravado no lote e formata cada amostra para a tela.

A corrida real tem 40 amostras, e a unica forma de olhar UMA era abrir o
relatorio inteiro e procurar com Ctrl+F. Aqui nao se monta nem se identifica
nada: o relatorio ja esta em disco, escrito pelo relatorio.py do nucleo. Este
arquivo so le, conta e formata. Nenhuma decisao cientifica mora aqui, e os
limiares nao sao reescritos: cada ressalva carrega o seu em `regra`, e e esse
texto que a tela imprime.

Duas regras da tela que este arquivo garante:

  - ausencia continua ausencia: sem medida vira string VAZIA, nunca 0 — zero
    e um numero e seria lido como "medi e deu zero";
  - "nao achou" e "nao conseguiu procurar" sao coisas diferentes e saem com
    textos diferentes (SITUACOES); e "nao montou" ganha de tudo, porque ali
    nada chegou a ser consultado.


COMO FUNCIONA — do relatorio.json aos dicts que os templates leem
=================================================================

    carregar(relatorio_json)  ->  RelatorioDoLote (nucleo)  |  None se nao existe, nao e JSON ou nao tem forma de relatorio
           |                                                    (a causa vai para o log; a pagina diz "relatorio indisponivel")
           |
           |-- contar_amostras(relatorio_json) -> int | None      so o numero, com memoria por (mtime, tamanho) — o perfil pergunta 500x
           |
           |-- resumo(lote)          -> dict do cabecalho: total, situacoes que OCORRERAM (texto, classe, nota, n), avulsas, pareadas, ...
           |
           '-- listar(lote)          -> [formatar_amostra(fatos)]  na ordem do relatorio (a tabela)
                      |
                      '-- formatar_amostra(fatos)  -> numeros com casas fixas (numero), situacao_na_tela + rotulo_da_situacao,
                                                      ressalvas [{codigo, texto, regra}], leituras [formatar_leitura(...)]
                                                      a pagina da amostra chama a MESMA funcao: linha e pagina nunca divergem


COMO USAR
=========

    from easycontig_web import configuracao
    from easycontig_web.dados import leitura_de_amostras

    cfg = configuracao.carregar()
    lote = leitura_de_amostras.carregar(cfg.pastas_do_lote('v2-UNsPd').relatorio_json)
    if lote is None:                                     #* 404 "relatorio indisponivel"
        ...
    cabecalho = leitura_de_amostras.resumo(lote)         #* cabecalho['situacoes'], ['avulsas'], ['pareadas'], ['banco'], ['trim']
    linhas = leitura_de_amostras.listar(lote)            #* uma por amostra, para a tabela
    uma = leitura_de_amostras.formatar_amostra(lote.amostra('amostra12'))  #* a MESMA formatacao, para a pagina da amostra (lote.amostra da None se nao ha)

    n = leitura_de_amostras.contar_amostras(caminho)     #* o perfil: so o inteiro, sem decodificar de novo a cada visita


O QUE MUDOU DO dados/leitura_de_amostras.py ORIGINAL
====================================================

 1. carregar devolve o RelatorioDoLote do nucleo (FatosDaAmostra dentro), nao
    o dict cru do JSON. Um relatorio.json ANTIGO (chaves em ingles: samples,
    consensus_len...) nao tem mais forma de relatorio: None, com a causa no
    log — a pagina diz "relatorio indisponivel". Na VPS isso alcanca so os
    lotes com menos de 30 dias no dia da troca (a retencao apaga o resto).

 2. Os dicts formatados falam portugues como o nucleo: key -> chave,
    accession -> acesso, cobertura_query -> cobertura_da_consulta,
    e_value -> valor_e, pct_cobertura_1x -> pct_cobertura_1,
    sentido_nome -> sentido_pelo_nome. Sairam `situacao_id` (o nucleo ja
    grava 'nao_montou' quando a montagem falha — nao ha mais dois codigos
    que divergem) e `invertido`/`contig_invertido: 'sim'|'nao'` (fica um so,
    `contig_invertido`, booleano). Entraram os fatos novos do nucleo:
    par_pelo_dna, sentido_absoluto, evidencia. Os templates serao reescritos
    na fase do front com estes nomes.

 3. _amostras/_situacao/_rotulo_situacao/_num/_formatar/_leitura viraram
    situacao_na_tela/rotulo_da_situacao/numero/formatar_amostra/
    formatar_leitura, sem sublinhado: sao o que a fase do front vai chamar
    ao montar os templates novos. _amostras sumiu: lote.amostras ja e a lista.

 4. contar_amostras continua com a memoria por (mtime_ns, tamanho) — o
    perfil pergunta isso para ate 500 lotes por visita e a resposta tem que
    ser a mesma de len(carregar(...).amostras). Sugestao para depois, a
    decidir: gravar n_amostras na tabela `lotes` ao concluir e aposentar a
    memoria.

 5. Os textos de SITUACOES e a ORDEM_SITUACOES sao os mesmos, letra por letra.
    A regra "erro ou consenso de 0 pb = nao_montou" fica como guarda mesmo
    com o nucleo ja gravando nao_montou: a tela nunca pode dizer "sem acerto"
    de uma amostra que nao teve o que consultar.

 6. valor_e: so None vira ''. O original fazia `e_value or ''`, que apagaria
    um 0,0 numerico — o melhor e-value possivel. Na pratica nao mudava nada
    (o blastn entrega o texto '0.0', que e verdadeiro), mas a regra da tela
    e "ausencia vira vazio", nao "zero vira vazio".

 7. Nada foi cortado: carregar/listar/resumo/amostra sao chamados em
    rotas_de_consulta_de_lote; contar_amostras em rotas_de_perfil.


POR QUE NAO NASCE CLASSE
========================

    Tudo aqui e objeto -> dict e dict -> dict. Quem tem estado e o
    RelatorioDoLote, que ja e classe no nucleo. A memoria de contagem e um
    dict de modulo com validacao por stat: nao e dona de recurso nenhum.
"""

import json

import logging

from pathlib import Path

import relatorio as modulo_relatorio



REGISTRO = logging.getLogger('easycontig.leitura_de_amostras')

#* como cada `situacao` do relatorio.py aparece na tela: texto, classe de cor (as do base.html) e a frase que diz de QUEM a situacao fala.
#! `sem_acerto` e NEUTRO de proposito: numa corrida real de 40 amostras, 19 vem de marcadores groEL/dsb/sodB — nao sao rRNA,
#! nao estao num banco 18S/16S, e nao terem acerto e o resultado CERTO. Pintar essas 19 de vermelho afirmaria uma falha que nao houve.
#! `blast_indisponivel` e `sem_banco` usam o ambar de "pendente" porque falam da instalacao, nao da amostra.
#! `nao_montou` e vermelho porque desta vez a falha e real — e NAO e 'sem acerto': nada chegou a ser consultado.
SITUACOES = {
    'ok': ('acerto no banco local', 'pronto',
           'Melhor acerto do BLAST dentro do banco local consultado — uma semelhança com o que existe nesse banco, não uma identificação de espécie.'),
    'sem_acerto': ('sem acerto no banco local', 'sub',
                   'O banco local foi consultado e nenhuma referência foi alcançada. Isso descreve o banco consultado, não a amostra.'),
    'blast_indisponivel': ('busca não executada — blastn indisponível', 'rodando',
                           'A busca no banco local não chegou a rodar porque o blastn não pôde ser chamado. Fala da instalação, não do conteúdo da amostra.'),
    'sem_banco': ('busca não executada — sem banco local', 'rodando',
                  'Nenhum banco de referência local estava disponível para consulta. Fala da instalação, não do conteúdo da amostra.'),
    'nao_montou': ('não montou — nada foi consultado', 'falhou',
                   "A montagem do consenso falhou, então não houve sequência para procurar no banco. Isto NÃO é 'sem acerto': nada chegou a ser consultado."),
}

ORDEM_SITUACOES = ('ok', 'sem_acerto', 'blast_indisponivel', 'sem_banco', 'nao_montou')  #* fixa: o cabecalho nao muda de forma entre um lote e outro

CONTAGENS = {}  #* caminho -> (mtime_ns, tamanho, n): a memoria de contar_amostras




def carregar(relatorio_json):

    #* o RelatorioDoLote gravado pelo trabalhador, ou None se nao existe, nao e JSON ou nao tem forma de relatorio.
    #! nao levanta: relatorio ilegivel e assunto da pagina ("relatorio indisponivel"), nao motivo para derrubar a requisicao.
    #! e um JSON valido SEM a lista de amostras tambem e None: aceita-lo daria uma tela vazia se passando por lote sem amostras

    caminho = Path(relatorio_json)

    try:
        dicionario = json.loads(caminho.read_text(encoding='utf-8'))

        if not isinstance(dicionario, dict) or not isinstance(dicionario.get('amostras'), list):
            raise ValueError('o JSON nao tem a lista "amostras" (relatorio de outra versao, ou nem e um relatorio)')

        return modulo_relatorio.RelatorioDoLote.de_dicionario(dicionario)

    except FileNotFoundError:
        return None  #* lote sem relatorio ainda (ou expurgado): nem vale uma linha de log

    except (OSError, ValueError, TypeError, KeyError, AttributeError) as erro:  #* ValueError cobre o JSONDecodeError; os outros, a forma errada por dentro
        REGISTRO.warning('relatorio ilegivel em %s: %s: %s', caminho, type(erro).__name__, erro)
        return None




def contar_amostras(relatorio_json):

    #* quantas amostras o relatorio tem; None quando nao ha relatorio valido. Mesma resposta que len(carregar(...).amostras).
    #! medido no original: o perfil decodificava o relatorio.json de CADA corrida da conta — 25,89 ms para 40, e o teto e 500 —
    #! para tirar um inteiro de cada um. Guarda-se so o inteiro, validado por mtime+tamanho (um stat custa microssegundos e
    #! nao depende de ninguem ter razao sobre o ciclo de vida do arquivo): relatorio regravado e recontado

    caminho = Path(relatorio_json)

    chave = str(caminho)

    try:
        estado = caminho.stat()

    except OSError:
        CONTAGENS.pop(chave, None)
        return None


    marca = (estado.st_mtime_ns, estado.st_size)

    guardado = CONTAGENS.get(chave)

    if guardado and guardado[:2] == marca:
        return guardado[2]


    lote = carregar(caminho)

    if lote is None:
        CONTAGENS.pop(chave, None)
        return None


    CONTAGENS[chave] = (*marca, len(lote.amostras))

    return len(lote.amostras)




def resumo(lote):

    #* as contagens do lote para o cabecalho — so contagem, nada de nota ou score.
    #! `situacoes` vem pronta para a tela e SO com as situacoes que ocorreram: um "0 sem acerto" impresso sugeriria que alguem esperava o contrario.
    #! `avulsas` NAO e detalhe: numa corrida cujos nomes so trazem o poco (`..._A09.ab1`), TODAS as leituras caem aqui — medido no
    #! original, 81 de 81 — e o app estava CERTO (a identidade do individuo so o nome resolve). O defeito era a tela CALAR:
    #! recusa informada e resultado, silencio parece defeito

    por_situacao = {}

    for fatos in lote.amostras:
        codigo = situacao_na_tela(fatos)
        por_situacao[codigo] = por_situacao.get(codigo, 0) + 1


    conhecidas = [codigo for codigo in ORDEM_SITUACOES if codigo in por_situacao]

    outras = sorted(codigo for codigo in por_situacao if codigo not in ORDEM_SITUACOES)

    situacoes = []

    for codigo in conhecidas + outras:
        texto, classe, nota = rotulo_da_situacao(codigo)
        situacoes.append({'codigo': codigo, 'texto': texto, 'classe': classe, 'nota': nota, 'n': por_situacao[codigo]})


    return {
        'total': len(lote.amostras),
        'por_situacao': por_situacao,
        'situacoes': situacoes,
        'avulsas': sum(1 for fatos in lote.amostras if len(fatos.leituras) < 2),
        'pareadas': sum(1 for fatos in lote.amostras if len(fatos.leituras) >= 2),
        'com_ressalva': sum(1 for fatos in lote.amostras if fatos.ressalvas),
        'com_erro': sum(1 for fatos in lote.amostras if fatos.erro),
        'com_contig_invertido': sum(1 for fatos in lote.amostras if fatos.contig_invertido),
        'pasta': lote.pasta or '',  #* contexto do lote, copiado do topo do relatorio
        'gerado_em': lote.gerado_em or '',
        'n_arquivos': lote.n_arquivos or 0,
        'motor': lote.motor or '',
        'trim': lote.trim or 0,
        'banco': lote.banco or '',
        'notas': [nota for nota in lote.notas if nota],
    }




def listar(lote):

    #* as amostras ja formatadas para a tabela, na ordem do relatorio

    return [formatar_amostra(fatos) for fatos in lote.amostras]




def situacao_na_tela(fatos):

    #* a situacao da IDENTIFICACAO — mas a falha de montagem ganha de tudo.
    #! o nucleo ja grava 'nao_montou' quando a montagem falha; a guarda fica para o consenso de 0 pb sem erro:
    #! na tela, 'sem_acerto' afirmaria que o banco foi consultado, e nao havia consenso para consultar

    if fatos.erro or not fatos.consenso_pb:
        return 'nao_montou'

    return fatos.situacao or 'sem_acerto'




def rotulo_da_situacao(codigo):

    #* (texto, classe, nota) de uma situacao; situacao desconhecida (relatorio mais novo que esta tela) sai com o codigo cru e sem cor:
    #* melhor exibir algo ilegivel do que traduzi-lo errado

    return SITUACOES.get(codigo, (codigo, 'sub', ''))




def numero(valor, casas):

    #* numero com casas fixas — string VAZIA quando nao ha medida.
    #! o 0 e o inimigo aqui: preencher a ausencia com zero transforma "nao medi" em "medi e deu zero", que ninguem afirmou

    if valor is None:
        return ''

    try:
        return f'{float(valor):.{casas}f}'

    except (TypeError, ValueError):
        return ''




def formatar_amostra(fatos):

    #* o dict de UMA amostra para a tabela e para a pagina: os mesmos valores e a mesma formatacao do CSV.
    #! identidade com 1 casa, e nao 3: o nucleo grava round(identidade, 1); imprimir 99.400 onde o blastn mediu 99,44
    #! AFIRMA uma precisao que o numero nao tem (conferido contra o blastn cru em 8 amostras, no original)

    codigo = situacao_na_tela(fatos)

    texto, classe, nota = rotulo_da_situacao(codigo)

    ressalvas = [{'codigo': ressalva.codigo or '', 'texto': ressalva.texto or '', 'regra': ressalva.regra or ''} for ressalva in fatos.ressalvas]

    return {
        'chave': fatos.chave or '',
        'n_leituras': len(fatos.leituras),  #* ---- montagem
        'consenso_pb': fatos.consenso_pb or 0,
        'cobertura_media': numero(fatos.cobertura_media, 2) if fatos.cobertura_media else '',
        'n_cobertura_1': fatos.n_cobertura_1 or 0,
        'pct_cobertura_1': numero(fatos.pct_cobertura_1, 1) if fatos.consenso_pb else '',
        'discordancias': fatos.discordancias or 0,
        'contig_invertido': bool(fatos.contig_invertido),
        'par_pelo_dna': fatos.par_pelo_dna,  #* None (nao conferido) / True / False
        'sentido_absoluto': bool(fatos.sentido_absoluto),
        'evidencia': fatos.evidencia or '',
        'organismo': fatos.organismo or '',  #* ---- identificacao
        'acesso': fatos.acesso or '',
        'identidade': numero(fatos.identidade, 1),
        'cobertura_da_consulta': numero(fatos.cobertura_da_consulta, 1),
        'valor_e': fatos.valor_e if fatos.valor_e is not None else '',
        'marcador': fatos.marcador or '',
        'situacao': codigo,
        'situacao_texto': texto,
        'situacao_classe': classe,
        'situacao_nota': nota,
        'identificado': bool(fatos.organismo),
        'ressalvas': ressalvas,  #* ---- ressalvas e falha
        'n_ressalvas': len(ressalvas),
        'erro': fatos.erro or '',
        'leituras': [formatar_leitura(leitura) for leitura in fatos.leituras],
    }




def formatar_leitura(leitura):

    #* o dict de UMA leitura dentro da amostra; o relatorio ja aponta a divergencia entre o sentido do nome e o medido, a tela so repete

    pelo_nome, medido = leitura.sentido_pelo_nome, leitura.sentido_medido

    return {
        'nome': leitura.nome or '',
        'primer': leitura.primer or '',
        'sentido_pelo_nome': pelo_nome or '',
        'sentido_medido': medido or '',
        'sentido_diverge': bool(pelo_nome and medido and pelo_nome != medido),
        'bases': leitura.bases or 0,
        'q_medio': f'Q{leitura.q_medio}' if leitura.q_medio else '',
        'q_rotulo': leitura.q_rotulo or '',
        'janela': f'{leitura.coluna_inicial or 0}–{leitura.coluna_final}' if leitura.coluna_final else '',
    }




def main():

    from easycontig_web import configuracao

    lote_id = input('Id de um lote ja processado (ou enter para sair): ').strip()

    if not lote_id:
        raise SystemExit(0)

    cfg = configuracao.carregar()

    caminho = cfg.pastas_do_lote(lote_id).relatorio_json

    lote = carregar(caminho)

    if lote is None:
        raise SystemExit(f'relatorio indisponivel em {caminho} (nao existe, nao e JSON ou nao tem a forma de um relatorio; veja o log)')


    print(f'{lote!r} — contar_amostras: {contar_amostras(caminho)}\n')

    cabecalho = resumo(lote)

    print(f"{cabecalho['total']} amostras ({cabecalho['pareadas']} pareadas, {cabecalho['avulsas']} avulsas), {cabecalho['com_ressalva']} com ressalva, {cabecalho['com_erro']} com erro; banco: {cabecalho['banco']!r}, trim {cabecalho['trim']}")

    for situacao in cabecalho['situacoes']:
        print(f"  {situacao['n']:>3}  {situacao['texto']} [{situacao['classe']}]")


    print()

    for linha in listar(lote):
        print(f"  {linha['chave']:<28} {linha['n_leituras']} leit.  {linha['consenso_pb']:>5} pb  cob {linha['cobertura_media'] or '—':>5}  id {linha['identidade'] or '—':>5}  {linha['organismo'] or '—':<30} {linha['situacao_texto']}" + (f"  ({linha['n_ressalvas']} ressalva(s))" if linha['n_ressalvas'] else ''))


    chave = input('\nChave de uma amostra para ver inteira (ou enter): ').strip()

    if chave:
        fatos = lote.amostra(chave)
        print(json.dumps(formatar_amostra(fatos), ensure_ascii=False, indent=1) if fatos else f'nao ha amostra {chave!r} neste lote')




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
