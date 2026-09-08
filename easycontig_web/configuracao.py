"""
configuracao.py — o que muda entre maquinas, lido do ambiente numa Configuracao so.

Tudo que e diferente entre a maquina do autor, a VPS e o conteiner entra por
variavel de ambiente e sai daqui como atributo. Nenhum caminho absoluto no
codigo: o mesmo pacote roda em qualquer lugar trocando so o .env.

Tambem e daqui que sai ONDE cada lote mora no disco (PastasDoLote): quem
grava, quem le e quem apaga a pasta de um lote pergunta a mesma coisa ao
mesmo lugar, em vez de cada um repetir "lotes/<id>".


COMO FUNCIONA — do .env a Configuracao
======================================

    variaveis de ambiente
      EASYCONTIG_DATA_DIR        -> pasta_de_dados     (padrao ./dados)
      EASYCONTIG_DB_18S / _16S   -> banco_18s / banco_16s   (prefixo BLAST, sem extensao)
      EASYCONTIG_TRIM            -> trim               (padrao 4: medido em 11 pares contra o CLC, t=3-4 e o minimo de erro/kb)
      EASYCONTIG_MAX_ARQUIVOS    -> maximo_de_arquivos (padrao 400, por lote)
      EASYCONTIG_MAX_BYTES       -> maximo_de_bytes    (padrao 300 MiB, por lote)
      EASYCONTIG_DOMINIO         -> dominio_permitido  (vazio = qualquer e-mail)
      EASYCONTIG_RETENCAO_DIAS   -> retencao_dias      (padrao 0 = nunca apagar)
           |
           v
    carregar()  ---------------------------->  Configuracao
                cria pasta_de_dados e pasta_de_lotes    |
                                                        |-- .pasta_de_lotes      pasta_de_dados/lotes
                                                        |-- .pasta_de_bancos     pasta_de_dados/bancos      (os bancos que o usuario monta)
                                                        |-- .pasta_de_fotos      pasta_de_dados/fotos       (as fotos de perfil)
                                                        |-- .caminho_do_sqlite   pasta_de_dados/fila.sqlite3
                                                        |
                                                        '-- .pastas_do_lote(lote_id)  -> PastasDoLote
                                                                  |
                                                                  |-- nome_simples(lote_id, papel)   recusa '', '.', '..', barra, nulo; papel = como o erro chama o valor
                                                                  |
                                                                  '-- .raiz             lotes/<id>
                                                                      .entrada          lotes/<id>/entrada        os .ab1 enviados
                                                                      .trabalho         lotes/<id>/trabalho       o que o tracy grava
                                                                      .arvore           lotes/<id>/arvore         a filogenia, por marcador
                                                                      .relatorio_html   lotes/<id>/relatorio.html
                                                                      .relatorio_json   lotes/<id>/relatorio.json
                                                                      .resultado_csv    lotes/<id>/resultado.csv

    conferir_producao()  -> lista de frases: o que NAO pode ficar como esta num servidor
    diagnostico(cfg)     -> [(item, deu_certo, detalhe)] para tracy, blastn, banco 18S, banco 16S


COMO USAR
=========

    import configuracao          # (from easycontig_web import configuracao)

    cfg = configuracao.carregar()                  #* na hora do uso, nunca no topo do modulo
    pastas = cfg.pastas_do_lote('v2-UNsPd')
    pastas.entrada.mkdir(parents=True, exist_ok=True)

    for item, deu_certo, detalhe in configuracao.diagnostico(cfg):
        print(item, 'OK' if deu_certo else 'FALTA', detalhe)


O QUE MUDOU DO configuracao.py ORIGINAL
=======================================

 1. Config (dataclass congelada) virou Configuracao, classe simples com os
    atributos em portugues:

        data_dir -> pasta_de_dados        db_18s / db_16s -> banco_18s / banco_16s
        max_arquivos -> maximo_de_arquivos   max_bytes -> maximo_de_bytes
        lotes_dir -> pasta_de_lotes       sqlite_path -> caminho_do_sqlite
        (novo) pasta_de_fotos: as tres rotas de perfil escreviam cfg.data_dir / "fotos" a mao

 2. tracy_bin e blast_bin SAIRAM da configuracao. Quem acha os programas e o
    ferramentas.py do nucleo, pela mesma escada (variavel de ambiente, pasta
    do pacote, PATH) que o executor usa na hora de montar. Guardar o caminho
    aqui era uma segunda verdade: o diagnostico podia dizer "tracy OK" olhando
    um lugar e a montagem falhar procurando em outro. As variaveis
    EASYCONTIG_TRACY_BIN e EASYCONTIG_BLAST_BIN continuam valendo — o
    ferramentas.py e quem as le.

 3. pastas_do_lote saiu do executor_de_lote.py e virou PastasDoLote, aqui.
    Motivo: o expurgo_por_retencao.py repetia a regra "raiz = lotes/<id>" para
    nao importar o executor (que puxava o nucleo inteiro). Agora a regra e uma
    so, mora onde mora a pasta de dados, e ninguem repete.

 4. nome_simples() e a validacao de lote_id que estava duplicada em
    executor_de_lote.pastas_do_lote, expurgo_por_retencao._nome_simples e
    executor_de_arvore.caminho_de_saida — uma funcao, tres chamadores.
    O erro agora ORIENTA: diz o que o nome pode ter.

 5. dias_de_retencao() veio do expurgo_por_retencao.dias_configurados: ler
    variavel de ambiente e configuracao, e o unico chamador era carregar().
    O comportamento e o mesmo: ausente, ilegivel ou negativo -> 0 (nao apagar),
    com aviso no log nos dois ultimos casos.

 6. Textos de conferir_producao e diagnostico: os mesmos do original, palavra
    por palavra, menos o detalhe de programa que FALTA. Antes, tracy ausente
    com a variavel definida mostrava so o caminho, e sem a variavel dizia
    "defina EASYCONTIG_TRACY_BIN" — mesmo quando o problema era outro. Agora
    (onde_faltou) o detalhe diz qual dos dois casos e: a variavel aponta para
    um lugar sem o programa (mostra o caminho e culpa a variavel), ou nao
    esta definida e o programa nao esta no PATH. E a escada segue ate o PATH
    antes de reclamar, igual ao executor.


POR QUE NASCE CLASSE
====================

    Configuracao e PastasDoLote nao sao donas de recurso nenhum: sao objetos
    de valor, um punhado de caminhos e numeros com nome. Viram classe porque
    o codigo inteiro fala cfg.trim, cfg.pasta_de_lotes, pastas.entrada — e
    um dicionario com chave em texto ('entrada') nao avisa quando o nome sai
    errado; o atributo avisa na hora, com AttributeError.
"""

from pathlib import Path

import logging

import os

import ferramentas

import identificacao



REGISTRO = logging.getLogger('easycontig.configuracao')

TRIM_PADRAO = 4                          #! medido em 11 pares curados contra os contigs do CLC: t=3-4 e o minimo de erro/kb. nao mexer sem premissa nova

MAXIMO_DE_ARQUIVOS_PADRAO = 400          #* por lote

MAXIMO_DE_BYTES_PADRAO = 300 * 1024 * 1024  #* por lote. 400 arquivos x ~330 KB = 130 MB; o .ab1 carrega o traco inteiro, nao e texto

DIAS_DE_RETENCAO_PADRAO = 0              #! 0 = NAO APAGAR. ja esteve em 90 e era defeito: instalacao que nao decidiu nada apagava corrida de todo mundo. apagar nao tem desfazer

DIAS_DE_RETENCAO_SUGERIDO = 30           #* o prazo decidido pelo autor em 06/08/2026; e o que vai no .env.example




class PastasDoLote:

    #* onde UM lote mora no disco. o lote_id e conferido aqui, porque estes caminhos sao lidos, gravados e apagados.

    def __init__(self, pasta_de_lotes, lote_id):

        self.lote_id = nome_simples(lote_id)

        self.raiz = Path(pasta_de_lotes) / self.lote_id
        self.entrada = self.raiz / 'entrada'                   #* os .ab1 que o laboratorio enviou
        self.trabalho = self.raiz / 'trabalho'                 #* o que o tracy grava (json, fa, align.fa)
        self.arvore = self.raiz / 'arvore'                     #* a filogenia, uma subpasta por marcador
        self.relatorio_html = self.raiz / 'relatorio.html'
        self.relatorio_json = self.raiz / 'relatorio.json'
        self.resultado_csv = self.raiz / 'resultado.csv'


    def __repr__(self):

        return f'PastasDoLote({self.raiz})'




class Configuracao:

    #* o que muda entre maquinas, ja lido e convertido. veja carregar().

    def __init__(self, pasta_de_dados, banco_18s=None, banco_16s=None, trim=TRIM_PADRAO, maximo_de_arquivos=MAXIMO_DE_ARQUIVOS_PADRAO,
                 maximo_de_bytes=MAXIMO_DE_BYTES_PADRAO, dominio_permitido='', retencao_dias=DIAS_DE_RETENCAO_PADRAO):

        self.pasta_de_dados = Path(pasta_de_dados)               #* lotes enviados, resultados, fila e bancos do usuario
        self.banco_18s = Path(banco_18s) if banco_18s else None  #* prefixo do banco BLAST curado de eucariotos (sem extensao)
        self.banco_16s = Path(banco_16s) if banco_16s else None  #* prefixo do banco BLAST curado de bacterias
        self.trim = trim
        self.maximo_de_arquivos = maximo_de_arquivos             #* teto de arquivos por lote
        self.maximo_de_bytes = maximo_de_bytes                   #* teto de tamanho por lote
        self.dominio_permitido = dominio_permitido               #* restricao de e-mail no login (vazio = qualquer)
        self.retencao_dias = retencao_dias                       #* prazo do expurgo; 0 = guardar para sempre


    @property
    def pasta_de_lotes(self):

        return self.pasta_de_dados / 'lotes'


    @property
    def pasta_de_bancos(self):

        return self.pasta_de_dados / 'bancos'  #* os bancos que o usuario monta pela tela (bancos_de_referencia.EstanteDeBancos)


    @property
    def pasta_de_fotos(self):

        return self.pasta_de_dados / 'fotos'  #* as fotos de perfil (contas.perfil_do_laboratorio); o nome do arquivo fica na tabela perfis


    @property
    def caminho_do_sqlite(self):

        return self.pasta_de_dados / 'fila.sqlite3'


    def pastas_do_lote(self, lote_id):

        return PastasDoLote(self.pasta_de_lotes, lote_id)


    def __repr__(self):

        return f'Configuracao({self.pasta_de_dados}, trim={self.trim}, retencao={self.retencao_dias} dias)'




def nome_simples(nome, papel='lote_id'):

    #* aceita so um nome de pasta ou arquivo solto. tudo que sai daqui vira caminho lido, gravado ou apagado: '..' passando seria escrever fora do lote.
    #* papel e como o erro chama o valor (lote_id, pasta da arvore, arquivo da arvore): o mesmo crivo serve para os tres, e o erro diz qual falhou

    if not isinstance(nome, str) or not nome.strip():
        raise ValueError(f'{papel} vazio: e so o nome de uma pasta ou arquivo dentro do lote e nao pode ficar em branco')

    if nome in ('.', '..') or '/' in nome or '\\' in nome or '\0' in nome or nome != Path(nome).name:
        raise ValueError(f'{papel} inválido: {nome!r} — e so um nome, sem barra, sem "..", sem caractere nulo')

    return nome




def caminho_do_ambiente(variavel, padrao=None):

    #* Path do que estiver na variavel (ou no padrao), com ~ expandido; None se nao ha nada

    valor = os.environ.get(variavel) or padrao

    return Path(valor).expanduser() if valor else None




def inteiro_do_ambiente(variavel, padrao):

    #* um teto inteiro >= 0 do ambiente; em branco, nao-numero ou negativo -> o padrao
    #! nunca deixa a instalacao sem teto por engano: 0 e um pedido explicito de "sem limite", e um .env com erro de digitacao nao pode valer o mesmo

    try:
        valor = int(os.environ.get(variavel, '') or padrao)

    except ValueError:
        return padrao

    return valor if valor >= 0 else padrao




def dias_de_retencao():

    #* EASYCONTIG_RETENCAO_DIAS como inteiro. ausente, ilegivel ou negativo -> DIAS_DE_RETENCAO_PADRAO (0 = nao apagar).
    #! negativo e o caso perigoso: dias=-1 significaria "tudo ja venceu", e um erro de digitacao no .env esvaziaria o volume. toda bifurcacao erra para o lado de guardar

    bruto = os.environ.get('EASYCONTIG_RETENCAO_DIAS', '').strip()

    if not bruto:
        return DIAS_DE_RETENCAO_PADRAO

    try:
        dias = int(bruto)

    except ValueError:
        REGISTRO.warning('EASYCONTIG_RETENCAO_DIAS=%r não é um número; usando %d dias', bruto, DIAS_DE_RETENCAO_PADRAO)
        return DIAS_DE_RETENCAO_PADRAO

    if dias < 0:
        REGISTRO.warning('EASYCONTIG_RETENCAO_DIAS=%d é negativo; usando %d dias', dias, DIAS_DE_RETENCAO_PADRAO)
        return DIAS_DE_RETENCAO_PADRAO

    return dias




def carregar():

    #! chame na hora do uso, nunca no topo do modulo: no topo o valor congela na primeira importacao, e o teste que troca EASYCONTIG_DATA_DIR nao troca nada

    cfg = Configuracao(caminho_do_ambiente('EASYCONTIG_DATA_DIR', './dados'),
                       banco_18s=caminho_do_ambiente('EASYCONTIG_DB_18S'),
                       banco_16s=caminho_do_ambiente('EASYCONTIG_DB_16S'),
                       trim=int(os.environ.get('EASYCONTIG_TRIM', str(TRIM_PADRAO))),
                       maximo_de_arquivos=int(os.environ.get('EASYCONTIG_MAX_ARQUIVOS', str(MAXIMO_DE_ARQUIVOS_PADRAO))),
                       maximo_de_bytes=int(os.environ.get('EASYCONTIG_MAX_BYTES', str(MAXIMO_DE_BYTES_PADRAO))),
                       dominio_permitido=os.environ.get('EASYCONTIG_DOMINIO', ''),
                       retencao_dias=dias_de_retencao())

    cfg.pasta_de_dados.mkdir(parents=True, exist_ok=True)
    cfg.pasta_de_lotes.mkdir(parents=True, exist_ok=True)

    return cfg




def conferir_producao():

    #* o que NAO pode ficar como esta fora do desenvolvimento: cada item e um jeito silencioso de o servico nascer aberto.
    #* devolve as frases; quem sobe o servidor decide se derruba.

    problemas = []

    if os.environ.get('EASYCONTIG_AUTH', 'dev').lower() == 'dev':
        problemas.append('EASYCONTIG_AUTH=dev aceita QUALQUER e-mail sem senha — nunca em servidor')

    if not os.environ.get('EASYCONTIG_SECRET_KEY'):
        problemas.append('EASYCONTIG_SECRET_KEY não definida: a chave é regerada a cada arranque e todas as sessões caem junto')

    if os.environ.get('EASYCONTIG_HTTPS_ONLY', '0') != '1':
        problemas.append('EASYCONTIG_HTTPS_ONLY=1 não está ligado: o cookie de sessão pode trafegar em claro')


    #! medido em 06/08/2026: com a tela de consentimento do Google em "Testando" e UM e-mail na lista de teste, TRES contas
    #! diferentes entraram. a lista de usuarios de teste so vale para escopos sensiveis, e openid/email/profile nao sao.
    #! o Google prova a identidade e NAO restringe o acesso; quem restringe e esta variavel, e so ela.

    if not os.environ.get('EASYCONTIG_DOMINIO', '').strip():
        problemas.append('EASYCONTIG_DOMINIO está em branco: quem pode entrar não foi decidido, e a lista de usuários de teste '
                         'do Google NÃO restringe nada (medido). Ponha os domínios (ex.: `ufrrj.br`) ou `*` para declarar '
                         'que é aberto de propósito')


    #! medido em 11/08/2026: atras de um proxy, PROXIES_CONFIAVEIS em branco poe todo visitante anonimo no balde do IP do
    #! nginx. o teto de login (20 por 600 s) deixa de ser por pessoa e vira 20 tentativas no mundo. nao ha conserto em
    #! codigo — o servidor nao distingue dois clientes atras de um proxy em que nao confia. exige-se a DECLARACAO.

    if not os.environ.get('EASYCONTIG_PROXIES_CONFIAVEIS', '').strip():
        problemas.append('EASYCONTIG_PROXIES_CONFIAVEIS está em branco, e em branco atrás de um proxy põe TODO visitante '
                         'anônimo no mesmo balde de limite: vinte erros de login trancam a entrada do site inteiro. Ponha '
                         'o endereço de onde o proxy chega (no compose padrão, o gateway 172.18.0.1) ou `nenhum` se '
                         'realmente não há proxy na frente')


    #! achado de 08/08/2026: em branco, o redirect_uri do OAuth e a checagem de origem dos POST saem do cabecalho Host,
    #! que e do cliente. atras do nginx e o host errado, e o login inteiro quebra.

    if not os.environ.get('EASYCONTIG_URL_BASE', '').strip():
        problemas.append('EASYCONTIG_URL_BASE está em branco: o endereço de volta do Google e a checagem de origem dos '
                         'POST passam a sair do cabeçalho Host, que é do cliente. Ponha o endereço público (ex.: '
                         '`https://easycontigbr.com.br`)')

    return problemas




def onde_faltou(nome, variavel):

    #* o detalhe do diagnostico quando o programa nao foi achado: diz o que o operador tem que conferir.
    #! "defina a variavel" quando ela JA esta definida e mentira que manda o operador para o lugar errado:
    #! se ela aponta para um caminho sem o programa, o detalhe mostra o caminho e culpa a variavel

    apontado = ferramentas.caminho_da_variavel(variavel, ferramentas.nome_do_executavel(nome))

    if apontado is not None:
        return f'não existe: {apontado} ({variavel} aponta para um lugar sem o {nome})'

    return f'não encontrado (defina {variavel}, ou ponha o {nome} no PATH)'




def diagnostico(cfg):

    #* (item, deu_certo, detalhe) para o que a instalacao precisa: tracy, blastn, banco 18S, banco 16S.
    #! blastn ausente NAO e "nenhum acerto": um servidor que confunde os dois transforma problema de instalacao em afirmacao sobre a amostra

    itens = []

    for nome, variavel, subpasta in (('tracy', 'EASYCONTIG_TRACY_BIN', 'bin'), ('blastn', 'EASYCONTIG_BLAST_BIN', 'blast')):

        caminho = ferramentas.procurar_programa(nome, variavel, subpasta, None)  #* a MESMA escada que o executor sobe na hora de montar

        itens.append((nome, caminho is not None, str(caminho) if caminho else onde_faltou(nome, variavel)))


    for rotulo, prefixo in (('banco 18S', cfg.banco_18s), ('banco 16S', cfg.banco_16s)):

        if not prefixo:
            itens.append((rotulo, False, 'não configurado'))
            continue

        instalado = identificacao.BancoDeMarcador(prefixo, rotulo).instalado

        itens.append((rotulo, instalado, str(prefixo) if instalado else f'sem índice .nsq: {prefixo}'))

    return itens




def main():

    cfg = carregar()

    print(f'{cfg!r}')
    print(f'  lotes:   {cfg.pasta_de_lotes}')
    print(f'  bancos:  {cfg.pasta_de_bancos}')
    print(f'  fila:    {cfg.caminho_do_sqlite}')
    print(f'  lote:    {cfg.maximo_de_arquivos} arquivos, {cfg.maximo_de_bytes // (1024 * 1024)} MiB')
    print(f'  dominio: {cfg.dominio_permitido or "(qualquer e-mail)"}')

    print('\nDiagnostico:')

    for item, deu_certo, detalhe in diagnostico(cfg):
        print(f'  {item:<10} {"OK   " if deu_certo else "FALTA"} {detalhe}')


    problemas = conferir_producao()

    print(f'\nProducao: {"pronto" if not problemas else str(len(problemas)) + " pendencia(s)"}')

    for problema in problemas:
        print(f'  - {problema}')




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
