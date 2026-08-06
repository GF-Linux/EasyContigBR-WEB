"""
limites.py — quantas requisições uma conta (ou um IP) pode fazer por janela.

Por que existe: medido em 2026-08-06, **200 requisições seguidas passaram em
1,4 s sem nenhuma resistência**. Para o volume real do laboratório isso nunca
seria problema — mas uma pessoa distraída com um laço, ou um robô que acha a
URL, derruba o serviço, e o serviço é de todo mundo.

Sem dependência nova, e em memória de propósito: o volume é de algumas dezenas
de pessoas, e outro serviço para a universidade manter (Redis) custa mais do que
resolve. ⚠️ **O limite é por PROCESSO.** Com vários trabalhadores web atrás de um
balanceador, cada um conta o seu — o teto efetivo vira N× o configurado. Está
certo para uma VPS com um processo; deixar de dizer isso é que seria errado.

Janela deslizante simples: guarda os instantes das requisições recentes e conta
quantos cabem na janela. Para o volume aqui, uma lista por chave é barato.
"""
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
    janela: float          # segundos
    def __str__(self) -> str:          # noqa: E301
        return f"{self.quantas} por {int(self.janela)}s"


def _int(nome: str, padrao: int) -> int:
    try:
        v = int(os.environ.get(nome, "") or padrao)
        return v if v >= 0 else padrao
    except ValueError:
        return padrao


def regras() -> dict[str, Regra]:
    """Os tetos. `0` desliga a regra — para desenvolvimento, e para o dia em que
    alguém precisar processar um lote grande sem esbarrar no próprio limite."""
    return {
        # Envio é o caminho caro (grava disco, enfileira). 20/hora cobre com
        # folga o uso real medido — uma corrida por vez, algumas por dia.
        "envio": Regra("envio de corrida", _int("EASYCONTIG_LIM_ENVIO", 20), 3600),
        # Leitura é barata (5 ms medidos), mas é por onde um laço martela.
        "leitura": Regra("leitura", _int("EASYCONTIG_LIM_LEITURA", 300), 60),
        # Montar banco baixa do NCBI: o teto aqui protege o NCBI, não a gente.
        "banco": Regra("montagem de banco", _int("EASYCONTIG_LIM_BANCO", 10), 3600),
        # Login errado repetido é tentativa de adivinhação.
        "login": Regra("tentativa de entrada", _int("EASYCONTIG_LIM_LOGIN", 20), 600),
    }


_registro: dict[str, deque] = defaultdict(deque)


def proxies_confiaveis() -> set[str]:
    """Quem tem permissão para dizer, por cabeçalho, de que IP veio a requisição.

    Vazio por padrão — e é o padrão SEGURO. Ver `chave()`.
    """
    bruto = os.environ.get("EASYCONTIG_PROXIES_CONFIAVEIS", "")
    return {p.strip() for p in bruto.split(",") if p.strip()}


def ip_de_origem(request: Request) -> str:
    """De que IP veio, de verdade.

    ⚠️ ISTO JÁ FOI UM BURACO. `X-Forwarded-For` é um cabeçalho que QUALQUER
    cliente pode escrever, e o código lia direto o que chegasse. Medido em
    2026-08-06: com teto de 5 tentativas de login por 600 s, **30 tentativas
    seguidas passaram sem uma única recusa**, bastando trocar o cabeçalho a cada
    uma. Todo teto por IP — login inclusive, e no modo `dev` cada tentativa que
    acerta o formato ENTRA — era contornável trocando um texto.

    O cabeçalho só vale quando quem o entregou é um proxy que nós declaramos
    confiável. Fora disso vale o endereço da conexão, que o cliente não escolhe.

    O padrão é NÃO CONFIAR EM NINGUÉM. Numa instalação atrás de proxy sem
    configurar `EASYCONTIG_PROXIES_CONFIAVEIS`, todo mundo aparece com o IP do
    proxy e divide o teto anônimo — chato, mas é o erro reversível. Confiar por
    padrão seria o erro irreversível: o teto simplesmente não existiria e
    ninguém perceberia, porque nada quebra.
    """
    conexao = request.client.host if request.client else "?"
    if conexao in proxies_confiaveis():
        declarado = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if declarado:
            return declarado
    return conexao


def chave(request: Request, quem: str | None) -> str:
    """Conta quando há sessão; IP quando não há.

    Pelo IP é imperfeito — uma universidade inteira sai por um NAT — e por isso
    o teto de quem NÃO entrou é o de login, que é o único caminho anônimo que
    escreve alguma coisa. Quem entrou é contado pela CONTA, o que tirou o
    laboratório inteiro de dentro do mesmo balde.
    """
    if quem:
        return "u:" + quem
    return "ip:" + ip_de_origem(request)


def conferir(nome: str, request: Request, quem: str | None = None,
             agora: float | None = None) -> None:
    """Levanta 429 se estourou. Silencioso quando cabe."""
    r = regras().get(nome)
    if not r or r.quantas == 0:
        return
    agora = agora if agora is not None else time.monotonic()
    k = nome + "|" + chave(request, quem)
    d = _registro[k]
    while d and agora - d[0] > r.janela:
        d.popleft()
    if len(d) >= r.quantas:
        espera = int(r.janela - (agora - d[0])) + 1
        raise HTTPException(
            status_code=429,
            detail=f"limite de {r} atingido; tente de novo em {espera}s",
            headers={"Retry-After": str(espera)})
    d.append(agora)


def limpar() -> None:
    """Só para os testes: o registro é global e vaza entre casos."""
    _registro.clear()
