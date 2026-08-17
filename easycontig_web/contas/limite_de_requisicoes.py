#? LIMITE DE REQUISIÇÕES — Decisão sobre o balde por conta 06/08/2026
#!
#! 1. Quantas requisições uma conta (ou IP) pode fazer por janela de tempo.
#! 2. Medido em 06/08: 200 requisições seguidas passaram em 1,4 s sem resistência.
#! 3. Para o volume real de um laboratório isso nunca seria problema — mas uma
#!    pessoa distraída, ou um robô que acha a URL, derruba.
#! 4. Em memória e sem dependência nova, de propósito: são algumas dezenas de
#!    pessoas, não milhares.

#* Autor: Gustavo Gonçalves Freitas - LHV
#* Copyright (c) 2026 Gustavo Gonçalves Freitas. Todos os direitos reservados.


from __future__ import annotations

import os 
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException, Request





@dataclass(frozen=True)
class Regra:
    nome: str
    quantas: int
    janela: float  # em segundos
    def __str__(self) -> str:
        return f"{self.quantas} por {int(self.janela)}s"
    
    

def _int(nome: str, padrao: int) -> int:
    try:
        v = int(os.environ.get(nome, "") or padrao)
        return v if v >=0 else padrao 
    except ValueError:
        return padrao
    
    
def regras() -> dict[str, Regra]:
    #? Os tetos, 0 desluga a regra para o desenvolvimento e para o dia que alguém precisar processar um lote grande.
    return{
        
        "envio": Regra('envio de corrida', _int('EASYCONTIG_LIM_ENVIO', 20), 3600),
        'leitura': Regra('leitura', _int('EASYCONTIG_LIM_LEITURA', 300), 60),
        'banco': Regra('montagem de banco', _int('EASYCONTIG_LIM_BANCO', 10), 3600),
        'login': Regra('tentativa de entrada', _int('EASYCONTIG_LIM_LOGIN', 20), 600),
        'pedido': Regra('pedido de amostra', _int('EASYCONTIG_LIM_PEDIDO', 30), 3600),
        }
        
        
_registro: dict[str, deque] = defaultdict(deque)
        
        
def proxies_confiaveis() -> set[str]:

    ''' Quem tem permissão para dizer, de que IP veio a requisição. 
    Vazio por padrão, é o padrão seguro.'''



    bruto = os.environ.get('EASYCONTIG_PROXIES_CONFIAVEIS', '')
    if bruto.strip().lower() == 'nenhum':
        return set()
    return {p.strip() for p in bruto.split(',') if p.strip()}


def ip_de_origem(request: Request) -> str:

    '''De que IP Veio ? 
        X- Forwarded-For é o cabeçalho que diz de que IP veio a requisição.
        O padrão é não confiar em ninguem. Agora se caminha da direita para esquerda
        pulando os proxies que declaramos e para-se no primeiro endereço que nao é nosso.'''

    conexão = request.client.host if request.client else '?'
    confiaveis = proxies_confiaveis()

    if conexão in confiaveis:
        cadeia = [p.strip() for p in request.headers.get('x-forwarded-for', '').split(',') if p.strip()]

        for endereco in reversed(cadeia):
            if endereco not in confiaveis:
                return endereco
    return conexão



def chave(request: Request, quem: str | None = None) -> str:

    if quem:
        return 'u:' + quem 
    return 'ip:' + ip_de_origem(request)


def conferir(nome: str, request: Request, quem: str | None = None, agora: float | None = None) -> None:

    ''' Levanta 429 se estourou. Silencioso quando cabe.'''
    
    r = regras().get(nome)
    if not r or r.quantas == 0:
        return
    agora = agora if agora is not None else time.monotonic()

    k = nome + '|' + chave(request, quem)

    d = _registro[k]

    while d and agora - d[0] > r.janela:
        d.popleft()

    if len(d) >= r.quantas:
        espera = int(r.janela - (agora - d[0])) + 1
        raise HTTPException(
            status_code=429,
            detail=f'limite de {r} atingido; tente de novo em {espera}s',
            headers={'Retry-After': str(espera)})

    d.append(agora)


def limpar() -> None:
    ''' Só para os testes: o registro é global e vaza entre casos.'''
    _registro.clear()