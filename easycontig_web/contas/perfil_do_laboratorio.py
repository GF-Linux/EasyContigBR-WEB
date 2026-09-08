"""
perfil_do_laboratorio.py — o perfil de cada conta: identidade (nome, laboratorio, foto, links) e o portfolio que o laboratorio DECLARA trabalhar.

Duas coisas moram aqui, e a diferenca entre elas e a razao do modulo:
IDENTIDADE (nome, laboratorio, instituicao, sobre, foto, enderecos de rede),
editavel — o e-mail nao, porque vem do login; e PORTFOLIO (especies e
marcadores), que e texto do dono, nao estatistica. O portfolio NAO e contagem
do que o BLAST devolveu: a primeira versao mostrava ranking das especies
encontradas, e isso convida a ler prevalencia onde nao ha (contava amostras,
nao animais). E esse portfolio declarado que torna possivel o pedido de
amostra entre laboratorios (pedidos_entre_labs.py): um laboratorio acha o
outro no diretorio /labs e pede pela CHAVE publica, nunca pelo e-mail.


COMO FUNCIONA — a vida de um perfil
===================================

    entrar no site --> garantir_registro(email, nome_do_google)      (rotas de entrada, a cada login)
                             |  INSERT; se ja existe, preenche o nome SO se o guardado esta vazio
                             |  e sorteia a chave publica se ainda nao tem (garantir_chave)
                             v
                       linha em `perfis` --> aparece no /labs (listar) com chave para receber pedido
                             |
    formulario /perfil --> salvar(email, nome=..., especies='Babesia vogeli, Hepatozoon canis', links='github.com/lab', ...)
                             |  transacao(): le o nome e a foto guardados, limpa cada campo, grava tudo de uma vez
                             |     nome em branco herda o que a pessoa DIGITOU um dia (nunca o local-part do e-mail)
                             |     foto=None mantem a que esta la
                             |     especies/marcadores: limpar_itens -> lista de ate 24 nomes de ate 90 caracteres
                             |     links: limpar_links -> so http/https, `https://` para quem digitou so o host, ate 8
                             v
    pagina --> pegar(email) -> Perfil (perfil.nome, perfil.especies, perfil.links[0].icone ...)
               sem linha? Perfil com os padroes (nome = local-part do e-mail): perfil vazio e estado normal, nao erro

    /labs/{chave}/pedido --> email_da_chave(chave)   (chave vazia nao casa com ninguem)
    /labs/foto/{arquivo} --> foto_registrada(arquivo) (so sai o que esta na coluna foto de alguem)


COMO USAR
=========

    from easycontig_web.dados import banco_sqlite
    from easycontig_web.contas import perfil_do_laboratorio

    perfis = perfil_do_laboratorio.Perfis(banco_sqlite.BancoSqlite(cfg.caminho_do_sqlite))

    perfis.garantir_registro('ana@ufu.br', 'Ana Souza')
    perfil = perfis.salvar('ana@ufu.br', laboratorio='LabPar', especies='Babesia vogeli; Hepatozoon canis', links='github.com/labpar')
    perfil.especies                                    #* ['Babesia vogeli', 'Hepatozoon canis']
    perfil.links[0].url, perfil.links[0].nome          #* 'https://github.com/labpar', 'GitHub'

    for p in perfis.listar(eu='ana@ufu.br'): p.eu, p.nome, p.chave      #* o diretorio; e-mail dos outros nao sai
    perfis.email_da_chave('v2-UNsPdKxQ')               #* 'ana@ufu.br' ou ''

    python -m easycontig_web.contas.perfil_do_laboratorio   #* quem esta cadastrado e o que declarou


O QUE MUDOU DO perfil_do_laboratorio.py ORIGINAL (456 linhas)
============================================================

 1. criar_esquema e _ESQUEMA sairam: a tabela `perfis` nasce no ESQUEMA unico
    do BancoSqlite (dados/banco_sqlite.py). Toda funcao que recebia
    `sqlite_path` virou metodo de Perfis, que recebe o BancoSqlite.

 2. Perfil e um objeto com um atributo por coluna (perfil.nome), com as listas
    (especies, marcadores, links) ja decodificadas ao montar o registro — o
    original devolvia dict e decodificava em pegar e em listar_perfis, cada um
    do seu jeito. Link e o endereco de rede com nome e icone calculados na hora
    (uma rede nova em REDES vale para quem ja tinha salvado, como antes).

 3. salvar le o guardado, grava e sorteia a chave numa transacao so. O original
    abria uma conexao para ler nome/foto e outra para gravar: duas gravacoes da
    mesma conta no mesmo instante podiam perder a foto de uma delas.
    garantir_registro tambem: INSERT e chave na mesma transacao.

 4. Os `.get` que protegiam a pagina de um banco sem as colunas `links` e
    `chave` (migracoes 2 e 5) sairam: o BancoSqlite.preparar() do arranque
    garante a versao 6 antes de qualquer pagina abrir.

 5. Nomes: _lista -> lista; _limpar_itens -> limpar_itens; _limpar_links ->
    limpar_links; _links -> links_gravados; _rede_de -> rede_de;
    _garantir_chave -> garantir_chave; listar_perfis -> Perfis.listar;
    MAX_TEXTO -> TAMANHO_DO_TEXTO; os cortes de 600 (sobre) e 300 (url) viraram
    TAMANHO_DO_SOBRE e TAMANHO_DA_URL; o "juntar espacos e cortar em 90" que
    aparecia cinco vezes virou uma_linha().

 6. resumo_das_corridas recebe os Lote da FilaDeLotes (l.status), nao dicts.

 7. chave_de(sqlite_path, email) NAO veio: nenhum chamador na web (so a suite).
    pegar(email).chave responde a mesma pergunta depois de garantir_registro.
    Ver NAO-PORTADO.md.

 8. Fora isso, nada foi cortado: as regras de limpeza (so http/https, esquema
    decidido ANTES de completar com https://, corte em 300 ANTES do urlparse,
    separar links por espaco e itens por virgula/ponto-e-virgula/linha,
    deduplicar sem diferenciar maiusculas), a heranca de nome/foto, a chave
    que nunca gira e o e-mail dos outros que nao sai do diretorio sao as mesmas.


POR QUE NASCE CLASSE
====================

    Perfis e dona da tabela `perfis` dentro do BancoSqlite: e o recurso. Perfil
    e Link sao registros como objeto: nao falam com o banco, so carregam os
    campos com nome. lista, limpar_itens, limpar_links, links_gravados, rede_de,
    uma_linha e resumo_das_corridas sao texto -> lista/dict: funcoes.
"""

import json

import re

import secrets

from urllib.parse import urlparse

from easycontig_web.dados.banco_sqlite import agora

from easycontig_web.processamento.fila import PRONTO



#* as redes que ganham icone proprio. a chave e procurada no HOST do endereco; quem nao casa com nenhuma entra como "Site",
#* com icone de globo — ninguem fica de fora por nao estar na lista, e o '' no fim e justamente esse curinga.
#! icone e SVG inline e MONOCROMATICO (currentColor): a tela toda e monocromatica por decisao de paleta (ADR 0052)
#! e um logo colorido de terceiro roubaria o unico acento que existe, que e o violeta
REDES = {
    'github.com': ('GitHub',
        'M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37'
        '-1.34-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.61.07-.61 1 .07 1.53 1.03 1.53'
        '1.03.9 1.53 2.36 1.09 2.94.83.09-.65.35-1.09.63-1.34-2.22-.25-4.56-1.11-4.56'
        '-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02a9.5'
        '9.5 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59'
        '1.03 2.68 0 3.84-2.34 4.69-4.57 4.94.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48'
        'A10 10 0 0 0 12 2z'),
    'orcid.org': ('ORCID',
        'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM8.2 7.1a.9.9 0 1 1 0 1.8.9.9 0 0 1 0'
        '-1.8zm.9 2.9v7H8v-7h1.1zm2.1 0h2.9c2.1 0 3.4 1.5 3.4 3.5s-1.4 3.5-3.4 3.5h-2.9'
        'v-7zm1.1 1v5h1.7c1.6 0 2.3-1.1 2.3-2.5s-.8-2.5-2.3-2.5h-1.7z'),
    'lattes.cnpq.br': ('Lattes',
        'M4 4h16v2H4V4zm0 5h16v2H4V9zm0 5h10v2H4v-2zm0 5h10v2H4v-2zM17 14l4 4-4 4v-3h-2'
        'v-2h2v-3z'),
    'linkedin.com': ('LinkedIn',
        'M4.98 3.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5zM3 9h4v12H3V9zm6 0h3.8v1.7h.05c'
        '.53-.95 1.83-1.95 3.75-1.95 4 0 4.4 2.5 4.4 5.9V21h-4v-5.5c0-1.3-.02-3-1.9-3'
        '-1.9 0-2.2 1.4-2.2 2.9V21H9V9z'),
    'researchgate.net': ('ResearchGate',
        'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-2.2 5.3h2.4c1.6 0 2.6.9 2.6 2.3 0 1.1'
        '-.6 1.9-1.6 2.2l2.1 3.4h-1.6l-1.9-3.2h-.9v3.2H9.8V7.3zm1.1 1v2.7h1.2c.9 0 1.5-.5'
        '1.5-1.4 0-.8-.5-1.3-1.5-1.3h-1.2z'),
    'instagram.com': ('Instagram',
        'M7.5 2h9A5.5 5.5 0 0 1 22 7.5v9A5.5 5.5 0 0 1 16.5 22h-9A5.5 5.5 0 0 1 2 16.5v-9'
        'A5.5 5.5 0 0 1 7.5 2zm0 2A3.5 3.5 0 0 0 4 7.5v9A3.5 3.5 0 0 0 7.5 20h9a3.5 3.5 0'
        '0 0 3.5-3.5v-9A3.5 3.5 0 0 0 16.5 4h-9zM12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10zm0 2a3'
        '3 0 1 0 0 6 3 3 0 0 0 0-6zm5.8-3.4a1.1 1.1 0 1 1 0 2.2 1.1 1.1 0 0 1 0-2.2z'),
    'twitter.com': ('X',
        'M3 3h5.4l4.3 5.9L17.9 3H21l-6.7 7.7L21.4 21H16l-4.6-6.3L5.9 21H3l7.1-8.2z'),
    'x.com': ('X',
        'M3 3h5.4l4.3 5.9L17.9 3H21l-6.7 7.7L21.4 21H16l-4.6-6.3L5.9 21H3l7.1-8.2z'),
    'youtube.com': ('YouTube',
        'M21.6 7.2a2.5 2.5 0 0 0-1.8-1.8C18.2 5 12 5 12 5s-6.2 0-7.8.4A2.5 2.5 0 0 0 2.4'
        '7.2C2 8.8 2 12 2 12s0 3.2.4 4.8a2.5 2.5 0 0 0 1.8 1.8C5.8 19 12 19 12 19s6.2 0'
        '7.8-.4a2.5 2.5 0 0 0 1.8-1.8C22 15.2 22 12 22 12s0-3.2-.4-4.8zM10 15V9l5.2 3L10 15z'),
    '': ('Site',
        'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm6.9 6h-2.9a15.7 15.7 0 0 0-1.3-3.6A8 8 0'
        '0 1 18.9 8zM12 4c.7 1 1.5 2.4 1.9 4h-3.8c.4-1.6 1.2-3 1.9-4zM4.3 14A8 8 0 0 1 4 12'
        'c0-.7.1-1.4.3-2h3.3a17.5 17.5 0 0 0 0 4H4.3zm.8 2H8a15.7 15.7 0 0 0 1.3 3.6A8 8 0 0'
        '1 5.1 16zM8 8H5.1a8 8 0 0 1 4.2-3.6A15.7 15.7 0 0 0 8 8zm4 12c-.7-1-1.5-2.4-1.9-4h3.8'
        'c-.4 1.6-1.2 3-1.9 4zm2.3-6H9.7a15.4 15.4 0 0 1 0-4h4.6a15.4 15.4 0 0 1 0 4zm.4 5.6'
        'a15.7 15.7 0 0 0 1.3-3.6h2.9a8 8 0 0 1-4.2 3.6zm2.7-5.6a17.5 17.5 0 0 0 0-4h3.3c.2.6'
        '.3 1.3.3 2s-.1 1.4-.3 2h-3.3z'),
}

ESQUEMAS_DE_LINK = ('http', 'https')  #* os unicos esquemas que viram href: `javascript:alert(1)` num link da propria pagina e XSS armazenado

ESQUEMA_NO_INICIO = re.compile(r'^([a-zA-Z][a-zA-Z0-9+.\-]*):')  #* `esquema:` no comeco, como a RFC 3986 define: distingue "digitou esquema" de "digitou so o host"

MAX_LINKS = 8  #* enderecos de rede por perfil

MAX_ITENS = 24  #* especies ou marcadores declarados: 200 especies deixa de ser portfolio e vira ruido para quem procura parceiro

TAMANHO_DO_TEXTO = 90  #* nome, laboratorio, instituicao e cada item do portfolio

TAMANHO_DO_SOBRE = 600  #* o texto livre "sobre o laboratorio"

TAMANHO_DA_URL = 300  #* cada endereco de rede, cortado ANTES de validar




def uma_linha(texto):

    #* junta os espacos e corta em TAMANHO_DO_TEXTO: o que todo campo de uma linha so recebe

    return ' '.join((texto or '').split())[:TAMANHO_DO_TEXTO]




def lista(bruto):

    #* a coluna especies/marcadores (JSON de lista) como lista de textos; JSON quebrado ou que nao e lista -> []

    try:
        dados = json.loads(bruto or '[]')

    except (ValueError, TypeError):
        return []

    if not isinstance(dados, list):
        return []

    return [str(item)[:TAMANHO_DO_TEXTO] for item in dados if isinstance(item, (str, int, float))][:MAX_ITENS]




def limpar_itens(texto):

    #* o portfolio digitado: um por linha OU separados por virgula/ponto-e-virgula — aceita os tres sem exigir sintaxe.
    #* nome de especie tem espaco no meio ("Babesia vogeli"): por isso NAO separa por espaco. repetidos (sem caixa) saem

    bruto = (texto or '').replace(';', '\n').replace(',', '\n')

    vistos, saida = set(), []

    for linha in bruto.splitlines():

        item = uma_linha(linha)

        if item and item.lower() not in vistos:
            vistos.add(item.lower())
            saida.append(item)

    return saida[:MAX_ITENS]




def rede_de(url):

    #* (nome, caminho do icone) de um endereco: casa o host (sem www.) com REDES; nao casou -> o globo

    try:
        host = urlparse(url).hostname or ''

    except ValueError:
        host = ''

    host = host.lower().removeprefix('www.')

    for chave, par in REDES.items():

        if chave and (host == chave or host.endswith('.' + chave)):
            return par

    return REDES['']




def limpar_links(texto):

    #* os enderecos digitados (separados por ESPACO EM BRANCO: endereco pode ter virgula — `data:text/html;base64,...`
    #* partido na virgula deixava o esquema perigoso numa metade e o base64 da outra virava um "site") -> lista de Link
    #! so http e https passam: o Jinja escapa o TEXTO, nao o ESQUEMA de uma URL. endereco sem esquema ganha https://
    #! (quem digita github.com/fulano deu um endereco), mas o esquema e decidido ANTES de completar: completar primeiro
    #! transformava `javascript:alert(1)` em `https://javascript:alert(1)`, que o urlparse le como host `javascript` e passa

    saida, vistos = [], set()

    for bruto in (texto or '').split():

        esquema = ESQUEMA_NO_INICIO.match(bruto)

        if esquema:

            if esquema.group(1).lower() not in ESQUEMAS_DE_LINK:
                continue

        else:
            bruto = 'https://' + bruto

        bruto = bruto[:TAMANHO_DA_URL]  #! cortar ANTES de validar: senao valida-se um endereco e grava-se OUTRO (o corte pode cair no `[` de um IPv6)

        try:
            partes = urlparse(bruto)

        except ValueError:
            continue

        if partes.scheme not in ESQUEMAS_DE_LINK or not partes.hostname:
            continue

        if bruto.lower() in vistos:
            continue

        vistos.add(bruto.lower())

        saida.append(Link(bruto))

        if len(saida) >= MAX_LINKS:
            break

    return saida




def links_gravados(bruto):

    #* a coluna links (JSON de [{'url': ...}]) como lista de Link. nome e icone sao recalculados do url em vez de confiar
    #* no gravado: uma rede nova em REDES passa a valer para quem ja tinha salvado
    #! urlparse LEVANTA em endereco com `[` de IPv6 desbalanceado, e uma linha assim pode estar no banco (gravada antes
    #! do corte-antes-de-validar): linha ruim vira "sem link", nao pagina com 500

    try:
        dados = json.loads(bruto or '[]')

    except (ValueError, TypeError):
        return []

    if not isinstance(dados, list):
        return []

    saida = []

    for item in dados[:MAX_LINKS]:

        url = item.get('url', '') if isinstance(item, dict) else ''

        if not isinstance(url, str):
            continue

        try:
            if urlparse(url).scheme not in ESQUEMAS_DE_LINK:
                continue

        except ValueError:
            continue

        saida.append(Link(url))

    return saida




def garantir_chave(con, email):

    #* sorteia a chave publica da conta se ela ainda nao tem uma, na conexao que o chamador ja abriu.
    #! idempotente pelo WHERE chave='': o segundo login nao troca a chave do primeiro. girar a chave faria falhar,
    #! sem motivo visivel, o formulario de pedido que alguem ja tem aberto numa aba apontando para ela

    con.execute("UPDATE perfis SET chave=? WHERE email=? AND chave=''", (secrets.token_urlsafe(9), email))




def resumo_das_corridas(lotes, n_amostras):

    #* contagens do que a conta tem no servidor: so volume, nada de especie (contar acertos do BLAST e exibir como perfil
    #* transformaria "o banco local achou isto" em "o laboratorio trabalha com isto"). recebe as CONTAGENS de amostras,
    #* nao os relatorios: era a unica coisa tirada deles, e decodificar 64 KB de JSON por corrida para somar inteiros e caro

    prontos = [lote for lote in lotes if lote.status == PRONTO]

    return {'corridas': len(lotes), 'corridas_prontas': len(prontos), 'amostras': sum(n_amostras)}




class Link:

    #* um endereco de rede do perfil: o url gravado e o nome/icone que a tela desenha, calculados de REDES na hora

    def __init__(self, url):

        self.url = url  #* como foi gravado (ja limpo: http/https, ate TAMANHO_DA_URL)

        self.nome, self.icone = rede_de(url)  #* 'GitHub' e o caminho do SVG; 'Site' + globo para quem nao casa


    def __repr__(self):

        return f'Link({self.url!r}, {self.nome!r})'




class Perfil:

    #* uma linha da tabela perfis, com nome em cada campo e as listas ja decodificadas. nao fala com o banco: quem grava e Perfis

    def __init__(self, email, nome='', laboratorio='', instituicao='', sobre='', especies='', marcadores='',
                 foto='', atualizado='', links='', chave='', eu=False):

        self.email = email  #* vem do login; nao se edita. no diretorio (listar) fica '' para os outros: nao sai do banco

        self.nome = nome  #* o que a pessoa digitou (ou o nome que o Google devolveu no primeiro login)

        self.laboratorio = laboratorio  #* texto livre de uma linha

        self.instituicao = instituicao  #* idem

        self.sobre = sobre  #* texto livre de ate TAMANHO_DO_SOBRE

        self.especies = lista(especies)  #* portfolio declarado: lista de nomes

        self.marcadores = lista(marcadores)  #* idem (18S, 16S, ITS...)

        self.foto = foto  #* nome do arquivo na pasta fotos do volume; '' sem foto

        self.atualizado = atualizado  #* ISO UTC do ultimo salvar; '' se nunca salvou

        self.links = links_gravados(links)  #* lista de Link

        self.chave = chave  #* id publico sorteado: e o que vai para o HTML no lugar do e-mail; '' ate o primeiro login

        self.eu = eu  #* no diretorio: este perfil e de quem esta olhando


    def __repr__(self):

        return f'Perfil({self.email!r}, nome={self.nome!r})'




class Perfis:

    #* a tabela perfis: le e grava o perfil de cada conta. recebe o BancoSqlite ja preparado

    def __init__(self, banco):

        self.banco = banco  #* dados.banco_sqlite.BancoSqlite


    def pegar(self, email):

        #* o perfil da conta. sem linha, os padroes (nome = local-part do e-mail): perfil vazio e estado normal, nao erro

        with self.banco.conectar() as con:
            linha = con.execute('SELECT * FROM perfis WHERE email=?', (email,)).fetchone()

        if not linha:
            return Perfil(email, nome=email.split('@')[0])

        return Perfil(**dict(linha))


    def salvar(self, email, nome='', laboratorio='', instituicao='', sobre='', especies='', marcadores='', foto=None, links=''):

        #* grava o perfil e devolve o Perfil como ficou. foto=None mantem a que esta la. tudo numa transacao: ler o guardado,
        #* gravar e sortear a chave sem outro salvar da mesma conta no meio
        #! nome em branco herda o nome GUARDADO, nunca o local-part do e-mail: o /labs mostra o nome de todos e a tela de
        #! entrada anuncia o dominio — gravar o local-part deixava qualquer conta remontar o e-mail institucional dos outros
        #! so o url de cada link vai para o disco: nome e icone sao derivados na leitura (Link), e gravados envelheceriam

        with self.banco.transacao() as con:

            guardado = con.execute('SELECT nome, foto FROM perfis WHERE email=?', (email,)).fetchone()

            nome_guardado = (guardado['nome'] if guardado else '') or ''

            foto_guardada = (guardado['foto'] if guardado else '') or ''

            con.execute(
                'INSERT INTO perfis (email, nome, laboratorio, instituicao, sobre, especies, marcadores, foto, atualizado, links) '
                'VALUES (?,?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(email) DO UPDATE SET nome=excluded.nome, laboratorio=excluded.laboratorio, '
                'instituicao=excluded.instituicao, sobre=excluded.sobre, especies=excluded.especies, '
                'marcadores=excluded.marcadores, foto=excluded.foto, atualizado=excluded.atualizado, links=excluded.links',
                (email,
                 uma_linha(nome) or nome_guardado,
                 uma_linha(laboratorio),
                 uma_linha(instituicao),
                 (sobre or '').strip()[:TAMANHO_DO_SOBRE],
                 json.dumps(limpar_itens(especies), ensure_ascii=False),
                 json.dumps(limpar_itens(marcadores), ensure_ascii=False),
                 foto_guardada if foto is None else foto,
                 agora(),
                 json.dumps([{'url': link.url} for link in limpar_links(links)], ensure_ascii=False)))

            garantir_chave(con, email)  #* quem chega aqui ja passou pelo login, mas salvar tambem e chamado direto (suite, manutencao)

        return self.pegar(email)


    def garantir_registro(self, email, nome_declarado=''):

        #* entrar no site ja cadastra a pessoa no diretorio: um diretorio cujo proposito e um laboratorio achar outro nao
        #* pode depender de um formulario que ninguem pediu para preencher
        #! nome_declarado e o nome que o PROVEDOR devolveu (a conta Google), nunca o local-part do e-mail; sem ele fica
        #! vazio e o /labs mostra "perfil sem nome". o UPDATE do conflito preenche o nome SO quando o guardado esta vazio:
        #! DO NOTHING deixaria "perfil sem nome" para sempre; UPDATE seco pisaria no que a pessoa digitou, a cada login
        #! a chave e instrucao SEPARADA: o WHERE daquele UPDATE vale para a linha inteira, e escrita junto a chave so
        #! nasceria para quem ainda nao tem nome — justamente quem ja preencheu o perfil ficaria sem chave

        with self.banco.transacao() as con:

            con.execute("INSERT INTO perfis (email, nome) VALUES (?,?) "
                        "ON CONFLICT(email) DO UPDATE SET nome=excluded.nome WHERE perfis.nome='' AND excluded.nome<>''",
                        (email, uma_linha(nome_declarado)))

            garantir_chave(con, email)


    def foto_registrada(self, arquivo):

        #* este nome de arquivo e mesmo a foto de algum perfil? pertencimento ao banco, e nao "existe na pasta":
        #* a rota /labs/foto/{arquivo} so entrega o que esta na coluna foto de alguem

        if not arquivo:
            return False

        with self.banco.conectar() as con:
            return con.execute('SELECT 1 FROM perfis WHERE foto=? LIMIT 1', (arquivo,)).fetchone() is not None


    def email_da_chave(self, chave):

        #* de quem e esta chave; '' quando nao e de ninguem
        #! chave vazia nao casa com ninguem: a coluna nasce '' e e preenchida depois, entao linha com chave='' e estado
        #! normal — sem esta guarda um POST com o campo em branco escolheria a primeira dessas linhas como alvo do pedido

        if not chave:
            return ''

        with self.banco.conectar() as con:
            linha = con.execute('SELECT email FROM perfis WHERE chave=?', (chave,)).fetchone()

        return (linha['email'] if linha else '') or ''


    def listar(self, eu=''):

        #* o diretorio: todo mundo que tem linha em perfis (entrou no site ao menos uma vez), por nome e e-mail sem caixa,
        #* para nao depender da ordem de cadastro. so o que e DECLARACAO do dono; nada que veio das corridas
        #! o e-mail de terceiro NAO sai daqui: quem chama passa o proprio em `eu` e recebe perfil.eu por perfil; nos outros
        #! o email fica '' — a tela marca "voce" sem carregar a lista de enderecos de todo mundo

        with self.banco.conectar() as con:
            linhas = con.execute('SELECT * FROM perfis ORDER BY nome COLLATE NOCASE, email COLLATE NOCASE').fetchall()

        saida = []

        for linha in linhas:

            perfil = Perfil(**dict(linha), eu=(linha['email'] == eu))

            if not perfil.eu:
                perfil.email = ''

            saida.append(perfil)

        return saida


    def __repr__(self):

        return f'Perfis({self.banco!r})'




def main():

    from easycontig_web import configuracao
    from easycontig_web.dados import banco_sqlite

    digitado = input('Caminho do fila.sqlite3 (enter = o da configuracao, EASYCONTIG_DATA_DIR): ').strip()

    banco = banco_sqlite.BancoSqlite(digitado) if digitado else banco_sqlite.BancoSqlite(configuracao.carregar().caminho_do_sqlite)

    if not banco.caminho.exists():
        print(f'o arquivo nao existe: {banco.caminho}')
        return

    perfis = Perfis(banco)

    todos = perfis.listar()

    print(f'\n{perfis!r}: {len(todos)} perfil(is) cadastrado(s)\n')

    for perfil in todos:
        print(f'  {perfil.nome or "(sem nome)":30} {perfil.laboratorio[:24]:24} {perfil.instituicao[:20]:20} '
              f'{len(perfil.especies):2} especie(s) {len(perfil.marcadores):2} marcador(es) {len(perfil.links)} link(s) '
              f'chave {perfil.chave or "-":12} foto {"sim" if perfil.foto else "nao"}  atualizado {perfil.atualizado or "nunca"}')




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
