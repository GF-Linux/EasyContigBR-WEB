"""
limite_de_requisicoes.py — quantas vezes uma conta (ou um IP) pode bater em cada porta do site por janela de tempo.

Medido em 06/08/2026: 200 requisicoes seguidas passaram em 1,4 s sem
resistencia nenhuma. Para o volume real de um laboratorio isso nunca seria
problema — mas uma pessoa distraida com o dedo no F5, ou um robo que acha a
URL, derruba o servidor para todo mundo. O teto aqui e convivencia, nao
autenticacao: e em memoria e sem dependencia nova, de proposito — sao
algumas dezenas de pessoas, nao milhares, e um reinicio do servidor zerar os
baldes nao faz mal.


COMO FUNCIONA — um balde por (regra, identidade)
================================================

    limitador = Limitador()          (uma vez, ao subir o servidor: le os tetos e os proxies do ambiente)
         |
         |  regras do ambiente (0 desliga a regra, para o desenvolvimento e para o dia de um lote grande):
         |     envio    envio de corrida       EASYCONTIG_LIM_ENVIO    20 por 1 h
         |     leitura  leitura                EASYCONTIG_LIM_LEITURA  300 por 1 min   (toda pagina GET/HEAD, pelo middleware)
         |     banco    montagem de banco      EASYCONTIG_LIM_BANCO    10 por 1 h
         |     login    tentativa de entrada   EASYCONTIG_LIM_LOGIN    20 por 10 min   (anonimo: conta por IP)
         |     pedido   pedido de amostra      EASYCONTIG_LIM_PEDIDO   30 por 1 h
         v
    limitador.conferir('envio', request, u.email)      (cada rota que custa, antes de fazer o trabalho)
         |
         |  identidade: 'u:' + e-mail quando ha sessao; senao 'ip:' + ip_de_origem(request)
         |     o IP vem da conexao; atras de um proxy DECLARADO em EASYCONTIG_PROXIES_CONFIAVEIS, do X-Forwarded-For,
         |     lido da direita para a esquerda pulando os proxies nossos — o primeiro endereco que nao e nosso e a origem
         |
         |  sob a tranca:  varre os baldes vencidos (no maximo a cada 60 s, senao a memoria cresce sem teto)
         |                 tira do balde os instantes fora da janela
         |                 cabe?  sim -> anota o instante e volta em silencio
         |                        nao -> HTTPException 429 "limite de envio de corrida (20 por 1 h) atingido; tente de novo em 60 min"
         |                               com o cabecalho Retry-After em segundos (o servidor repassa os dois)
         v
    limitador.limpar()               (so nos testes: o registro vive enquanto o processo vive)


COMO USAR
=========

    from easycontig_web.contas import limite_de_requisicoes

    limitador = limite_de_requisicoes.Limitador()                  #* tetos e proxies do ambiente
    limitador.conferir('envio', request, u.email)                  #* passa, ou levanta HTTPException(429)
    limitador.conferir('login', request)                           #* sem conta: conta pelo IP

    limite_de_requisicoes.Limitador({'login': limite_de_requisicoes.Regra('tentativa de entrada', 3, 60)}, proxies=set())   #* tetos a mao (testes)

    python -m easycontig_web.contas.limite_de_requisicoes           #* mostra os tetos em vigor e demonstra o 429


O QUE MUDOU DO limite_de_requisicoes.py ORIGINAL (168 linhas)
============================================================

 1. O registro, a tranca e o relogio da varredura eram variaveis globais do
    modulo; viraram o objeto Limitador, que o servidor cria uma vez e as rotas
    usam. Os tetos e os proxies sao lidos do ambiente UMA vez, ao criar o
    objeto (o original relia as 5 variaveis a cada requisicao).

 2. O balde e indexado pelo par (regra, identidade), nao por um texto
    'regra|identidade' que a varredura tinha de partir no '|'.

 3. O 429 diz QUAL limite estourou — o original dizia "limite de 20 por
    3600s atingido" e o nome da regra ('envio de corrida'), guardado em cada
    Regra, nao aparecia em lugar nenhum. As duracoes saem legiveis (1 h, 10
    min) no texto; o cabecalho Retry-After continua em segundos inteiros.

 4. Regra e uma classe simples (era dataclass congelada). `_int` virou
    configuracao.inteiro_do_ambiente (a cota_de_espaco tinha uma copia; agora
    e uma so); `chave` virou Limitador.identidade;
    `_talvez_varrer` virou Limitador.varrer; `ip_de_origem` recebe o
    conjunto de proxies em vez de ler o ambiente por dentro.

 5. Nada saiu: todos os chamadores da web (conferir em 8 rotas + o
    middleware de leitura, limpar nos testes, ip_de_origem e
    proxies_confiaveis nos testes) tem o seu lugar.


POR QUE NASCE CLASSE
====================

Limitador e dono de um recurso com vida propria: o registro em memoria (que
cresce, e varrido e e zerado), a tranca que serializa quem mexe nele e o
instante da ultima varredura. Regra e valor pequeno com nome (str e repr).
O resto — ler o ambiente, descobrir o IP de origem — e texto em, texto fora,
e fica funcao.
"""

import os

import threading

import time

from collections import defaultdict, deque

from fastapi import HTTPException

from easycontig_web.configuracao import inteiro_do_ambiente



INTERVALO_DA_VARREDURA = 60.0  #* segundos entre duas varreduras dos baldes vencidos

SEM_PROXY = 'nenhum'  #* o valor que diz por escrito "nao ha proxy na frente" (conferir_producao exige a variavel preenchida)




class Regra:

    #* o teto de UMA porta: `quantas` vezes por `janela` segundos; quantas=0 desliga

    def __init__(self, nome, quantas, janela):

        self.nome = nome  #* como a pessoa ve a porta no 429: 'envio de corrida'

        self.quantas = quantas

        self.janela = janela  #* em segundos


    def __str__(self):

        return f'{self.quantas} por {duracao(self.janela)}'


    def __repr__(self):

        return f'Regra({self.nome!r}, {self.quantas!r}, {self.janela!r})'




def duracao(segundos):

    #* segundos -> texto curto para a pessoa: 3600 -> '1 h', 600 -> '10 min', 3599 -> '60 min' (arredonda para cima), 5 -> '5 s'

    segundos = int(segundos)

    if segundos >= 3600 and segundos % 3600 == 0:
        return f'{segundos // 3600} h'

    if segundos >= 60:
        return f'{-(-segundos // 60)} min'

    return f'{segundos} s'




def regras_do_ambiente():

    #* os tetos em vigor, por nome de regra; 0 desliga a regra

    return {'envio': Regra('envio de corrida', inteiro_do_ambiente('EASYCONTIG_LIM_ENVIO', 20), 3600),
            'leitura': Regra('leitura', inteiro_do_ambiente('EASYCONTIG_LIM_LEITURA', 300), 60),
            'banco': Regra('montagem de banco', inteiro_do_ambiente('EASYCONTIG_LIM_BANCO', 10), 3600),
            'login': Regra('tentativa de entrada', inteiro_do_ambiente('EASYCONTIG_LIM_LOGIN', 20), 600),
            'pedido': Regra('pedido de amostra', inteiro_do_ambiente('EASYCONTIG_LIM_PEDIDO', 30), 3600)}




def proxies_confiaveis():

    #* quem tem permissao para dizer de que IP veio a requisicao (EASYCONTIG_PROXIES_CONFIAVEIS, separado por virgula)
    #* vazio e 'nenhum' -> ninguem: e o padrao seguro, o X-Forwarded-For de um desconhecido nao vale nada

    bruto = os.environ.get('EASYCONTIG_PROXIES_CONFIAVEIS', '')

    if bruto.strip().lower() == SEM_PROXY:
        return set()

    return {item.strip() for item in bruto.split(',') if item.strip()}




def ip_de_origem(request, confiaveis):

    #* de que IP veio? o da conexao — a menos que a conexao seja de um proxy nosso: ai o X-Forwarded-For diz, lido da direita
    #* para a esquerda pulando os proxies declarados; para no primeiro endereco que nao e nosso. Um cliente que forja o
    #* cabecalho so consegue enganar quem confia nele, e sem proxy declarado ninguem confia

    conexao = request.client.host if request.client else '?'

    if conexao in confiaveis:

        cadeia = [item.strip() for item in request.headers.get('x-forwarded-for', '').split(',') if item.strip()]

        for endereco in reversed(cadeia):

            if endereco not in confiaveis:
                return endereco

    return conexao




class Limitador:

    #* dono dos baldes: um deque de instantes por (regra, identidade), sob uma tranca

    def __init__(self, regras=None, proxies=None):

        self.regras = regras_do_ambiente() if regras is None else regras

        self.proxies = proxies_confiaveis() if proxies is None else proxies

        self.registro = defaultdict(deque)  #* (nome da regra, identidade) -> instantes (time.monotonic) dentro da janela

        self.tranca = threading.Lock()

        self.ultima_varredura = 0.0


    def identidade(self, request, quem=None):

        #* por conta quando ha sessao; senao pelo IP, que e a unica identidade de quem nao entrou

        if quem:
            return 'u:' + quem

        return 'ip:' + ip_de_origem(request, self.proxies)


    def conferir(self, nome, request, quem=None, agora=None):

        #* levanta HTTPException 429 se estourou; silencioso quando cabe. Regra desconhecida ou desligada (0) passa direto
        #* `agora` so os testes passam, para andar o relogio sem esperar

        regra = self.regras.get(nome)

        if not regra or regra.quantas == 0:
            return

        agora = time.monotonic() if agora is None else agora

        balde = (nome, self.identidade(request, quem))

        with self.tranca:

            self.varrer(agora)

            instantes = self.registro[balde]

            while instantes and agora - instantes[0] > regra.janela:
                instantes.popleft()

            if len(instantes) >= regra.quantas:

                espera = int(regra.janela - (agora - instantes[0])) + 1  #* quando o instante mais velho sai da janela

                raise HTTPException(status_code=429, detail=f'limite de {regra.nome} ({regra}) atingido; tente de novo em {duracao(espera)}',
                                    headers={'Retry-After': str(espera)})

            instantes.append(agora)


    def varrer(self, agora):

        #* chamado SEMPRE sob a tranca. Sem isto o registro guardaria para sempre um balde por identidade que ja bateu UMA
        #* vez, e a memoria do processo cresceria sem teto. Roda no maximo a cada INTERVALO_DA_VARREDURA e so descarta o
        #* balde cujos instantes venceram TODOS — exatamente o que conferir faria na proxima visita, entao nao muda quem e barrado

        if agora - self.ultima_varredura <= INTERVALO_DA_VARREDURA:
            return

        self.ultima_varredura = agora

        for balde in list(self.registro):

            instantes = self.registro[balde]

            regra = self.regras.get(balde[0])

            janela = regra.janela if regra else 0.0

            while instantes and agora - instantes[0] > janela:
                instantes.popleft()

            if not instantes:
                del self.registro[balde]


    def limpar(self):

        #* so para os testes: o registro vive enquanto o processo vive e vaza entre casos

        with self.tranca:

            self.registro.clear()

            self.ultima_varredura = 0.0


    def __repr__(self):

        return f'Limitador({len(self.registro)} baldes, proxies={sorted(self.proxies)!r})'




class RequisicaoDeMentira:

    #* o minimo de um Request que o limitador olha: de onde veio a conexao e o X-Forwarded-For (para o main e os testes)

    def __init__(self, ip, encaminhado=''):

        self.client = type('Cliente', (), {'host': ip})()

        self.headers = {'x-forwarded-for': encaminhado} if encaminhado else {}




def main():

    limitador = Limitador()

    print(f'{limitador!r}')

    print('\nTetos em vigor (0 = desligado):')

    for nome, regra in limitador.regras.items():
        print(f'  {nome:8} {regra.nome:22} {str(regra) if regra.quantas else "desligado":14} EASYCONTIG_LIM_{nome.upper()}')

    print(f'\nProxies confiaveis: {", ".join(sorted(limitador.proxies)) or "nenhum (o IP e o da conexao)"}')

    requisicao = RequisicaoDeMentira('203.0.113.9')

    regra = limitador.regras['login']

    print(f'\nDemonstracao: {regra.quantas + 1} tentativas de entrada seguidas do IP {ip_de_origem(requisicao, limitador.proxies)}')

    for tentativa in range(1, regra.quantas + 2):

        try:
            limitador.conferir('login', requisicao)

        except HTTPException as erro:
            print(f'  tentativa {tentativa}: {erro.status_code} {erro.detail}  (Retry-After: {erro.headers["Retry-After"]})')

            break

    else:
        print('  todas passaram (a regra esta desligada)')

    limitador.limpar()




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
