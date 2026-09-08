"""
carga.py — quanta gente e quantas corridas esta maquina aguenta.

POR QUE EXISTE: em 2026-08-06 mediu-se carga num arnes ad hoc, dentro de uma
worktree que depois sumiu. Os numeros (inicial 508 -> 112 ms) nao podem ser
repetidos na maquina nova nem comparados com ela, e e exatamente a comparacao
que escolhe a VPS. Este arquivo e aquele teste, commitado.

SEM DEPENDENCIA FORA DA BIBLIOTECA PADRAO, de proposito: ele precisa rodar numa
VPS recem-instalada, antes de existir venv, e dentro do conteiner se preciso.

O QUE MEDE, nesta ordem:

  1. LEITURA: rampa de leitores simultaneos nas quatro paginas quentes. Responde
     "quantas pessoas ao mesmo tempo antes de a tela ficar lenta".
  2. ENVIO: corridas reais (80 .ab1, 26 MB) de contas distintas ao mesmo tempo,
     do POST ate o lote ficar `pronto`. Responde "quantas corridas por hora esta
     maquina monta", que e o numero que escolhe o numero de vCPU.

RECUSA 429/413 NAO E FALHA. A cota e o teto de taxa existem, estao certos e
foram medidos. Conta-los como erro faria o arnes reprovar uma maquina por ela
estar se defendendo. Saem em coluna propria, e o relatorio diz quantas foram.

POR PADRAO O ALVO DEVE SUBIR COM OS TETOS DESLIGADOS (EASYCONTIG_LIM_LEITURA=0,
EASYCONTIG_LIM_ENVIO=0): e o que o carga.sh faz. Com eles ligados, uma rampa de
50 leitores mede o limitador, nao a maquina: sao 300 leituras por minuto POR
CONTA, e 50 leitores a 100 ms estouram isso em meio minuto. O relatorio avisa
quando detecta teto ligado, porque um numero medido contra o limitador parece
apenas "a maquina e ruim".

NUNCA APONTE PARA PRODUCAO. O arnes entra por EASYCONTIG_AUTH=dev, cria contas
e escreve corridas. Para caracterizar a maquina de producao, suba uma instancia
isolada NELA (e o que o carga.sh faz): o que se quer medir e o hardware, nao o
banco de dados de quem usa.


COMO FUNCIONA
=============

    main()
      |-- identificar_maquina()                 cpu, nucleos, RAM, sistema: sem isto dois relatorios nao se comparam
      |-- Sessao(alvo, 'piloto@dominio')        /saude de pe? entra por EASYCONTIG_AUTH=dev?  (senao: sai com 2)
      |-- avisos_de_teto()                      EASYCONTIG_LIM_LEITURA != 0 neste shell -> aviso
      |-- pasta de .ab1                         --amostras, ou a maior dados/lotes/*/entrada
      |
      |-- semente                               --semente corridas na conta leitor@dominio, espera a ultima ficar pronta
      |       o acervo da conta e o que faz a pagina inicial custar caro (N+1 de disco de 06/08)
      |       le a chave da primeira amostra no relatorio.json para medir a rota do traco
      |
      |-- fase_leitura(...)                     para cada nivel da rampa: n Sessoes na MESMA conta,
      |       ThreadPoolExecutor(n) bate --voltas vezes em cada pagina quente
      |       Resumo.linha(): n, ok, p50, p95, max, recusas, falhas
      |       para quando o p95 passa de TETO_P95_MS (o joelho)
      |
      '-- fase_envio(...)                       montar_multipart() UMA vez; --corridas contas envio000..;
              POST /lotes simultaneos; espera cada lote ficar `pronto` (ou `falhou`) ate --paciencia
              -> corridas por hora e segundos de parede por amostra

    Sessao      uma conta: cookie proprio, opener que segue redirecionamento (entrar) e outro que NAO segue (medir)
    Medida      ms, codigo HTTP, bytes, erro; ok e recusa_legitima (429/413)
    Resumo      as medidas de um rotulo e a linha de estatistica delas (percentil interpolado)


COMO USAR
=========

    tools/carga.sh                                                 # sobe instancia isolada e mede (o caminho normal)
    python3 tools/carga.py --alvo http://127.0.0.1:8123 --amostras <pasta>

O relatorio sai em texto na tela e em JSON (--json arquivo), para comparar duas
maquinas depois.


O QUE MUDOU DO ORIGINAL
=======================

 1. Estilo da casa: sem type hints, sem dataclass, sem docstring de funcao; as
    classes com __init__ explicito. Os textos na tela, as chaves do JSON e os
    codigos de saida sao os mesmos.
 2. A chave da amostra e lida em `amostras[].chave` do relatorio.json (era
    `samples[].key` antes da reescrita do nucleo); se nao achar, reclama em vez
    de pular o traco em silencio.
 3. conferir_tetos(sessao) recebia a sessao e nao a usava: virou avisos_de_teto().
 4. O import de `statistics` nao era usado e saiu.


POR QUE AQUI NASCE CLASSE
=========================

Sessao carrega o cookie de uma conta entre entrar(), pegar() e enviar(): e
estado que atravessa chamadas, e cada leitor simultaneo e uma. Medida e Resumo
sao registros com uma regra em cima (o que e ok, o que e recusa legitima, como
resumir). As fases sao funcoes: rodam uma vez e devolvem o que mediram.
"""

from pathlib import Path

import argparse

import json

import os

import platform

import sys

import time

import urllib.error

import urllib.parse

import urllib.request

from concurrent.futures import ThreadPoolExecutor

from http.cookiejar import CookieJar




EXTENSOES_AB1 = {'.ab1', '.abi', '.scf'}

RAMPA_PADRAO = [1, 2, 5, 10, 20, 50]  #* sobe ate achar o joelho; para sozinha quando a maquina recusa ou passa do teto de paciencia

TETO_P95_MS = 3000.0  #! acima disto a pagina deixou de ser instantanea. nao e limite do software: e o criterio de parada da rampa

BONS_DE_LEITURA = (200, 201, 204)

BONS_DE_ENVIO = (303,)  #! o caminho feliz do POST /lotes e 303, nao 200. sem isto o aceite do upload saia como "0 ok"

RECUSAS_LEGITIMAS = (429, 413)  #* teto de taxa e cota: e o software funcionando, nao falha

CAMPO_DE_STATUS = 'status'  #! o campo do /api/lotes/<id> e `status`, nao `estado`: o erro custou 300 s de espera por um lote pronto em 11,7 s




class NaoSegue(urllib.request.HTTPRedirectHandler):
    #! nao seguir redirecionamento e requisito de medicao: uma sessao caida faz GET / responder 303 -> /entrar, e o arnes cronometraria a pagina de login achando que e a inicial

    def redirect_request(self, req, fp, code, msg, headers, newurl):

        return None




class Medida:
    #* uma requisicao medida: quanto levou, que codigo voltou, quantos bytes; codigo 0 e "nao houve resposta"

    def __init__(self, ms, codigo, bytes=0, erro='', bons=BONS_DE_LEITURA):

        self.ms = ms
        self.codigo = codigo
        self.bytes = bytes
        self.erro = erro
        self.bons = bons  #* quais codigos contam como sucesso para ESTA requisicao


    def __repr__(self):

        return f'Medida({self.ms:.0f} ms, {self.codigo})'


    @property
    def ok(self):

        return self.codigo in self.bons


    @property
    def recusa_legitima(self):

        return self.codigo in RECUSAS_LEGITIMAS




class Sessao:
    #* uma conta: cookie proprio, como o navegador de uma pessoa

    def __init__(self, alvo, email, tempo_limite=120.0):

        self.alvo = alvo.rstrip('/')
        self.email = email
        self.tempo_limite = tempo_limite
        self.jarra = CookieJar()

        biscoitos = urllib.request.HTTPCookieProcessor(self.jarra)

        self.segue = urllib.request.build_opener(biscoitos)  #* so para entrar: o POST /entrar redireciona para /

        self.direto = urllib.request.build_opener(biscoitos, NaoSegue())  #* para medir: um 3xx vira erro visivel


    def __repr__(self):

        return f'Sessao({self.email} em {self.alvo})'


    def entrar(self):

        corpo = urllib.parse.urlencode({'email': self.email, 'proximo': '/'}).encode()

        pedido = urllib.request.Request(self.alvo + '/entrar', data=corpo, headers={'Content-Type': 'application/x-www-form-urlencoded'})

        try:
            with self.segue.open(pedido, timeout=self.tempo_limite) as resposta:
                resposta.read()

        except urllib.error.HTTPError as erro:
            erro.read()

            return False

        return any(biscoito.name == 'session' for biscoito in self.jarra)


    def pegar(self, caminho):

        inicio = time.perf_counter()

        try:
            with self.direto.open(self.alvo + caminho, timeout=self.tempo_limite) as resposta:

                dados = resposta.read()

                return Medida((time.perf_counter() - inicio) * 1000, resposta.status, len(dados))

        except urllib.error.HTTPError as erro:
            descartar(erro)

            return Medida((time.perf_counter() - inicio) * 1000, erro.code)

        except Exception as erro:
            return Medida((time.perf_counter() - inicio) * 1000, 0, erro=repr(erro))


    def enviar(self, corpo, tipo):

        #* POST /lotes. devolve a medida e o id do lote (vazio se nao entrou)

        pedido = urllib.request.Request(self.alvo + '/lotes', data=corpo, headers={'Content-Type': tipo})

        inicio = time.perf_counter()

        try:
            with self.direto.open(pedido, timeout=self.tempo_limite) as resposta:

                resposta.read()

                return Medida((time.perf_counter() - inicio) * 1000, resposta.status, bons=BONS_DE_ENVIO), ''  #* 200 aqui seria estranho: o caminho feliz e 303

        except urllib.error.HTTPError as erro:
            descartar(erro)

            ms = (time.perf_counter() - inicio) * 1000

            if erro.code == 303:
                destino = erro.headers.get('Location', '')

                return Medida(ms, 303, bons=BONS_DE_ENVIO), destino.rsplit('/', 1)[-1]

            return Medida(ms, erro.code, bons=BONS_DE_ENVIO), ''

        except Exception as erro:
            return Medida((time.perf_counter() - inicio) * 1000, 0, erro=repr(erro), bons=BONS_DE_ENVIO), ''


    def estado_do_lote(self, lote_id):

        #* UMA requisicao, nao duas: e o laco de espera da fase 2, e dobra-lo poe carga do arnes em cima da carga medida

        try:
            with self.direto.open(self.alvo + f'/api/lotes/{lote_id}', timeout=self.tempo_limite) as resposta:

                dados = json.loads(resposta.read())

                if CAMPO_DE_STATUS not in dados:  #! um campo ausente nunca e silencioso: devolve as chaves que vieram, para o relatorio mostrar
                    return '?sem-campo-status:' + ','.join(sorted(dados))[:120]

                return dados[CAMPO_DE_STATUS]

        except urllib.error.HTTPError as erro:
            descartar(erro)

            return f'?{erro.code}'

        except Exception:
            return '?'




def descartar(erro):

    #* le e joga fora o corpo de um HTTPError, para a conexao voltar ao pool

    try:
        erro.read()

    except Exception:
        pass




def montar_multipart(arquivos, nome, referencia):

    #! o corpo e montado UMA VEZ e reusado por todas as contas: sao 26 MB, e remontar por conta faria o arnes medir a si mesmo

    limite = '----carga' + os.urandom(8).hex()

    partes = []

    for campo, valor in (('nome', nome), ('referencia', referencia)):
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{campo}"\r\n\r\n{valor}\r\n'.encode())

    for caminho in arquivos:

        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="arquivos"; filename="{caminho.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())

        partes.append(caminho.read_bytes())

        partes.append(b'\r\n')

    partes.append(f'--{limite}--\r\n'.encode())

    return b''.join(partes), f'multipart/form-data; boundary={limite}'




def percentil(valores, p):

    if not valores:
        return float('nan')

    ordenados = sorted(valores)

    k = (len(ordenados) - 1) * p

    baixo, alto = int(k), min(int(k) + 1, len(ordenados) - 1)

    return ordenados[baixo] + (ordenados[alto] - ordenados[baixo]) * (k - baixo)




def formatar(valor):

    return '—' if valor is None else f'{valor:.0f}'




class Resumo:
    #* as medidas de um rotulo (uma pagina num nivel da rampa, o aceite do POST) e a estatistica delas

    def __init__(self, rotulo):

        self.rotulo = rotulo
        self.medidas = []


    def __repr__(self):

        return f'Resumo({self.rotulo}, {len(self.medidas)} medidas)'


    def linha(self):

        bons = [medida.ms for medida in self.medidas if medida.ok]

        recusas = sum(1 for medida in self.medidas if medida.recusa_legitima)

        falhas = [medida for medida in self.medidas if not medida.ok and not medida.recusa_legitima]

        return {
            'rotulo': self.rotulo,
            'n': len(self.medidas),
            'ok': len(bons),
            'p50': round(percentil(bons, 0.50), 1) if bons else None,
            'p95': round(percentil(bons, 0.95), 1) if bons else None,
            'max': round(max(bons), 1) if bons else None,
            'recusas': recusas,
            'falhas': len(falhas),
            'codigos_de_falha': sorted({medida.codigo for medida in falhas}),
            'erros': sorted({medida.erro for medida in falhas if medida.erro})[:3],
        }




def identificar_maquina():

    #* sem isto os numeros nao sao comparaveis entre maquinas

    info = {'sistema': platform.platform(), 'python': platform.python_version(), 'nucleos': os.cpu_count()}

    try:
        for linha in Path('/proc/cpuinfo').read_text().splitlines():

            if linha.startswith('model name'):
                info['cpu'] = linha.split(':', 1)[1].strip()

                break

    except Exception:
        pass

    try:
        for linha in Path('/proc/meminfo').read_text().splitlines():

            if linha.startswith('MemTotal'):
                info['ram_gb'] = round(int(linha.split()[1]) / 1024 / 1024, 1)

                break

    except Exception:
        pass

    return info




def avisos_de_teto():

    #* nao da para ler a configuracao do servidor de fora; o que da e olhar o shell de quem chama

    avisos = []

    if os.environ.get('EASYCONTIG_LIM_LEITURA', '') not in ('0',):
        avisos.append('EASYCONTIG_LIM_LEITURA não está em 0 NESTE shell — se o alvo subiu com o mesmo ambiente, a rampa acima de ~5 leitores vai medir o limitador de taxa e não a máquina (300 leituras/min por conta).')

    return avisos




def fase_leitura(alvo, email, caminhos, rampa, voltas, saida):

    #! todos os leitores entram na MESMA conta, de proposito: /lotes/<id> recusa lote de outra conta (404), e o custo da inicial cresce com o acervo da conta

    print('\n=== FASE 1 — LEITURA (rampa de leitores simultâneos) ===\n')

    print(f'{"simultâneos":>12} {"página":<28} {"p50":>8} {"p95":>8} {"max":>8} {"recusas":>8} {"falhas":>7}')

    print('-' * 84)

    saida['leitura'] = []

    for n in rampa:

        sessoes = []

        for _ in range(n):

            sessao = Sessao(alvo, email)

            if not sessao.entrar():
                print(f'  ⚠️  {email} não entrou — o alvo está em EASYCONTIG_AUTH=dev e o domínio bate?')

                return

            sessoes.append(sessao)

        pior_p95 = 0.0

        for rotulo, caminho in caminhos:

            resumo = Resumo(f'{rotulo} @{n}')


            def bater(sessao, caminho=caminho):

                return [sessao.pegar(caminho) for _ in range(voltas)]


            with ThreadPoolExecutor(max_workers=n) as piscina:
                for medidas in piscina.map(bater, sessoes):
                    resumo.medidas.extend(medidas)

            linha = resumo.linha()

            saida['leitura'].append({'simultaneos': n, **linha})

            print(f'{n:>12} {rotulo:<28} {formatar(linha["p50"]):>8} {formatar(linha["p95"]):>8} {formatar(linha["max"]):>8} {linha["recusas"]:>8} {linha["falhas"]:>7}'
                  + (f'   {linha["codigos_de_falha"]}' if linha['falhas'] else ''))

            if linha['p95']:
                pior_p95 = max(pior_p95, linha['p95'])

        if pior_p95 > TETO_P95_MS:
            print(f'\n  ⏹  parando a rampa: p95 passou de {TETO_P95_MS:.0f} ms com {n} simultâneos. O joelho está aqui.')

            saida['joelho_leitura'] = n

            return

    saida['joelho_leitura'] = None




def fase_envio(alvo, dominio, arquivos, quantas, referencia, paciencia, saida):

    print(f'\n=== FASE 2 — ENVIO ({quantas} corridas simultâneas, {len(arquivos)} arquivos cada) ===\n')

    corpo, tipo = montar_multipart(arquivos, 'carga', referencia)

    mb = len(corpo) / 1024 / 1024

    print(f'  corpo montado uma vez: {mb:.1f} MB × {quantas} contas\n')

    sessoes = []

    for i in range(quantas):

        sessao = Sessao(alvo, f'envio{i:03d}@{dominio}')

        if not sessao.entrar():
            print(f'  ⚠️  conta envio{i:03d}@{dominio} não entrou.')

            return

        sessoes.append(sessao)

    inicio = time.perf_counter()


    def subir(sessao):

        medida, lote = sessao.enviar(corpo, tipo)

        return sessao.email, medida, lote


    with ThreadPoolExecutor(max_workers=quantas) as piscina:
        resultados = list(piscina.map(subir, sessoes))

    aceitos = [(email, medida, lote) for email, medida, lote in resultados if lote]

    recusados = [(email, medida) for email, medida, lote in resultados if not lote]

    aceite = Resumo('aceite do POST /lotes')

    aceite.medidas = [medida for _, medida, _ in resultados]

    linha = aceite.linha()

    print(f'  aceite do POST (upload inteiro recebido): p50 {formatar(linha["p50"])} ms · p95 {formatar(linha["p95"])} ms · max {formatar(linha["max"])} ms')

    print(f'  aceitos: {len(aceitos)} · recusados (cota/teto, legítimo): {len(recusados)}')

    for email, medida in recusados:
        print(f'      {email}: HTTP {medida.codigo}' + (f' {medida.erro}' if medida.erro else ''))

    if not aceitos:
        print('\n  ⚠️  nenhum lote entrou — não há o que cronometrar.')

        saida['envio'] = {'aceitos': 0, 'recusados': len(recusados)}

        return

    print(f'\n  esperando os {len(aceitos)} lotes ficarem `pronto` (paciência: {paciencia:.0f} s)')

    por_email = {sessao.email: sessao for sessao in sessoes}

    pendentes = {lote: (por_email[email], email) for email, _, lote in aceitos}  #! pelo e-mail, nao pela ordem: quem consulta o lote tem de ser a sessao DONA dele, e parear por posicao devolveria 404 assim que a ordem de conclusao mudasse

    tempos = {}

    falhados = {}

    while pendentes and (time.perf_counter() - inicio) < paciencia:

        time.sleep(1.0)

        for lote in list(pendentes):

            sessao, email = pendentes[lote]

            estado = sessao.estado_do_lote(lote)

            if estado == 'pronto':
                tempos[lote] = time.perf_counter() - inicio

                del pendentes[lote]

                print(f'      {lote} pronto em {tempos[lote]:6.1f} s   ({email})')

            elif estado == 'falhou':
                falhados[lote] = email

                del pendentes[lote]

                print(f'      {lote} FALHOU   ({email})')

    for lote, (_, email) in pendentes.items():
        print(f'      {lote} ainda não terminou ao fim da paciência   ({email})')

    total_de_amostras = len(tempos) * (len(arquivos) // 2)

    duracao = max(tempos.values()) if tempos else 0.0

    saida['envio'] = {
        'corridas_simultaneas': quantas,
        'arquivos_por_corrida': len(arquivos),
        'mb_por_corrida': round(mb, 1),
        'aceite_p50_ms': linha['p50'],
        'aceite_p95_ms': linha['p95'],
        'aceitos': len(aceitos),
        'recusados_legitimos': len(recusados),
        'prontos': len(tempos),
        'falhados': len(falhados),
        'nao_terminaram': len(pendentes),
        'segundos_ate_o_ultimo': round(duracao, 1),
        'tempos_por_lote': {lote: round(segundos, 1) for lote, segundos in tempos.items()},
    }

    if tempos and duracao > 0:

        por_hora = len(tempos) / duracao * 3600

        segundos_por_amostra = duracao / total_de_amostras if total_de_amostras else float('nan')

        saida['envio']['corridas_por_hora'] = round(por_hora, 1)

        saida['envio']['segundos_de_parede_por_amostra'] = round(segundos_por_amostra, 2)

        print(f'\n  ⏱  {len(tempos)} corridas em {duracao:.1f} s  →  {por_hora:.0f} corridas/hora nesta máquina')

        print(f'     {segundos_por_amostra:.2f} s de parede por amostra (referência: 0,65 s de CPU/amostra medido no Deck em 04/08)')




def achar_amostras(pasta):

    #* a pasta de .ab1: a informada, ou a maior dados/lotes/*/entrada do repositorio

    if pasta is None:

        raiz = Path(__file__).resolve().parent.parent

        candidatas = sorted((raiz / 'dados' / 'lotes').glob('*/entrada'), key=lambda pasta: -len(list(pasta.iterdir())))

        pasta = candidatas[0] if candidatas else None

    if pasta is None or not pasta.is_dir():
        raise SystemExit('\n  ⚠️  não achei uma pasta de .ab1. Passe --amostras <pasta>.')

    arquivos = sorted(arquivo for arquivo in pasta.iterdir() if arquivo.suffix.lower() in EXTENSOES_AB1)

    if not arquivos:
        raise SystemExit(f'\n  ⚠️  {pasta} não tem .ab1.')

    return pasta, arquivos




def semear(alvo, conta, arquivos, quantas, referencia, paciencia):

    #! o acervo da conta e o que faz a pagina inicial custar caro (N+1 de disco de 06/08): medir contra uma conta com uma corrida so daria um numero bonito e falso. --semente 40 e o acervo com que 06/08 mediu

    leitor = Sessao(alvo, conta)

    if not leitor.entrar():
        raise SystemExit(f'\n  ⚠️  {conta} não entrou.')

    corpo, tipo = montar_multipart(arquivos, 'semente', referencia)

    print(f'\n  semeando {quantas} corrida(s) em {conta} — a página inicial custa em função do acervo')

    lote_ref = ''

    for i in range(quantas):

        medida, lote = leitor.enviar(corpo, tipo)

        if not lote:
            raise SystemExit(f'  ⚠️  semente {i + 1} devolveu HTTP {medida.codigo} {medida.erro}')

        lote_ref = lote

    inicio = time.perf_counter()

    while time.perf_counter() - inicio < paciencia:

        estado = leitor.estado_do_lote(lote_ref)

        if estado in ('pronto', 'falhou'):
            print(f'  semente pronta em {time.perf_counter() - inicio:.1f} s (último lote: {estado})')

            break

        time.sleep(1.0)

    else:
        raise SystemExit('  ⚠️  a semente não terminou; há trabalhador rodando? (confira o log do trabalhador — o arnês NÃO é a fonte)')

    return leitor, lote_ref




def caminhos_quentes(alvo, sessao, lote_ref):

    #* as paginas medidas na fase 1; a rota do traco e a mais pesada do app e so entra se a chave da amostra for achada no relatorio

    caminhos = [('/ (inicial)', '/'), ('/perfil', '/perfil')]

    if not lote_ref:
        return caminhos

    caminhos.append(('/lotes/<id>', f'/lotes/{lote_ref}'))

    try:
        relatorio = json.loads(sessao.direto.open(f'{alvo}/lotes/{lote_ref}/relatorio.json', timeout=60).read())

        chave = next((amostra['chave'] for amostra in (relatorio.get('amostras') or []) if isinstance(amostra, dict) and amostra.get('chave')), None)  #! a chave vem do RELATORIO (amostras[].chave), nao do /api/lotes/<id>, que devolve a linha do banco e nao tem amostra nenhuma

    except Exception as erro:
        chave = None

        print(f'  ⚠️  não li o relatório da semente: {erro!r}')

    if chave:
        caminhos.append(('traço (cromatograma)', f'/api/lotes/{lote_ref}/amostras/{chave}/traco'))

    else:
        print('  ⚠️  sem chave de amostra — o traço, que é a rota mais pesada, FICOU DE FORA desta medição.')

    return caminhos




def main():

    parser = argparse.ArgumentParser(description='Arnês de carga do EasyContig BR (biblioteca padrão só).')

    parser.add_argument('--alvo', default='http://127.0.0.1:8123', help='endereço da instância ISOLADA (nunca produção)')

    parser.add_argument('--dominio', default='carga.local', help='domínio das contas de teste; tem que estar em EASYCONTIG_DOMINIO do alvo')

    parser.add_argument('--amostras', type=Path, default=None, help='pasta com os .ab1 de uma corrida (padrão: procura)')

    parser.add_argument('--rampa', default=','.join(str(x) for x in RAMPA_PADRAO), help='níveis de leitores simultâneos, separados por vírgula')

    parser.add_argument('--voltas', type=int, default=6, help='requisições por leitor em cada nível')

    parser.add_argument('--corridas', type=int, default=3, help='corridas simultâneas na fase 2')

    parser.add_argument('--semente', type=int, default=5, help='corridas semeadas na conta de leitura antes da fase 1; use 40 para reproduzir o acervo medido em 06/08')

    parser.add_argument('--referencia', default='curado')

    parser.add_argument('--paciencia', type=float, default=900.0, help='segundos de espera pelos lotes na fase 2')

    parser.add_argument('--so-leitura', action='store_true')

    parser.add_argument('--so-envio', action='store_true')

    parser.add_argument('--json', type=Path, default=None, help='grava o relatório em JSON, para comparar máquinas')

    argumentos = parser.parse_args()


    saida = {'quando': time.strftime('%Y-%m-%dT%H:%M:%S'), 'alvo': argumentos.alvo, 'maquina_do_arnes': identificar_maquina()}

    print('=' * 84)

    print('ARNÊS DE CARGA — EasyContig BR')

    print('=' * 84)

    maquina = saida['maquina_do_arnes']

    print(f'  máquina : {maquina.get("cpu", "?")}')

    print(f'            {maquina.get("nucleos", "?")} núcleos · {maquina.get("ram_gb", "?")} GB RAM · {maquina.get("sistema", "?")}')

    print(f'  alvo    : {argumentos.alvo}')


    piloto = Sessao(argumentos.alvo, f'piloto@{argumentos.dominio}')

    saude = piloto.pegar('/saude')

    if not saude.ok:
        print(f'\n  ⚠️  {argumentos.alvo}/saude respondeu {saude.codigo} {saude.erro}\n      O alvo não está de pé. Suba com tools/carga.sh, ou confira QUEM atende a porta:\n      ss -ltnp | grep {argumentos.alvo.rsplit(":", 1)[-1]}')

        raise SystemExit(2)

    if not piloto.entrar():
        print(f'\n  ⚠️  não consegui entrar como piloto@{argumentos.dominio}.\n      O alvo precisa de EASYCONTIG_AUTH=dev e de EASYCONTIG_DOMINIO contendo \'{argumentos.dominio}\'.')

        raise SystemExit(2)

    for aviso in avisos_de_teto():
        print(f'\n  ⚠️  {aviso}')


    try:
        pasta, arquivos = achar_amostras(argumentos.amostras)

        print(f'  amostras: {len(arquivos)} arquivos de {pasta}')

        conta_de_leitura = f'leitor@{argumentos.dominio}'

        lote_ref = ''

        if not argumentos.so_envio:
            piloto, lote_ref = semear(argumentos.alvo, conta_de_leitura, arquivos, argumentos.semente, argumentos.referencia, argumentos.paciencia)

    except SystemExit as erro:  #* os erros de preparo saem na tela com o texto que orienta, e o codigo de saida e 2
        print(erro)

        raise SystemExit(2)


    caminhos = caminhos_quentes(argumentos.alvo, piloto, lote_ref)

    rampa = [int(x) for x in argumentos.rampa.split(',') if x.strip()]

    if not argumentos.so_envio:
        saida['semente_corridas'] = argumentos.semente

        fase_leitura(argumentos.alvo, conta_de_leitura, caminhos, rampa, argumentos.voltas, saida)

    if not argumentos.so_leitura:
        fase_envio(argumentos.alvo, argumentos.dominio, arquivos, argumentos.corridas, argumentos.referencia, argumentos.paciencia, saida)


    print('\n' + '=' * 84)

    if argumentos.json:
        argumentos.json.write_text(json.dumps(saida, indent=2, ensure_ascii=False))

        print(f'relatório em {argumentos.json}')

        print('Para comparar duas máquinas: rode o mesmo comando nas duas e compare os JSON.')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
