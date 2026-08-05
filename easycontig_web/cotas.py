"""
cotas.py — quanto UMA CONTA pode ocupar, não quanto cabe em um lote.

Os tetos do `config.py` (400 arquivos, 300 MB) valem por lote: seguram o envio
único absurdo e não seguram mais nada. Cinquenta lotes legítimos, um atrás do
outro, enchem o disco da VPS da universidade sem nunca cruzar um teto de lote —
e disco cheio no meio de um upload é justamente o defeito de sempre: metade dos
arquivos gravados, relatório com menos amostras do que a corrida tem.

Este módulo NÃO levanta exceção e NÃO decide status HTTP. Ele responde "como
está esta conta agora" e deixa `main.py` traduzir isso em 413. A mesma função
serve para (a) barrar o envio e (b) dizer na tela quanto ainda sobra — se
fossem duas, uma envelheceria em relação à outra e a tela mentiria.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import fila

_ATIVOS = (fila.RECEBENDO, fila.NA_FILA, fila.RODANDO)

_MAX_LOTES_ATIVOS_PADRAO = 3
# 2 GiB ≈ 80 corridas de 26 MB guardadas. O .ab1 nunca é apagado depois do
# processamento (o laboratório volta neles), então o que a conta acumula é o
# histórico inteiro, não o lote da vez.
_MAX_BYTES_CONTA_PADRAO = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class Cota:
    lotes_ativos: int         # em recebendo/na_fila/rodando
    max_lotes_ativos: int     # 0 = sem limite
    bytes_usados: int         # o que as pastas desta conta ocupam em disco
    max_bytes_conta: int      # 0 = sem limite
    pode_enviar: bool
    motivo: str               # vazio quando pode; frase pronta quando não


def _inteiro_do_ambiente(nome: str, padrao: int) -> int:
    """Lê um teto do ambiente sem nunca deixar a instalação sem teto por engano.

    Valor ilegível ou negativo cai no padrão em vez de virar 0: `0` é um pedido
    explícito de "sem limite", e um `.env` com erro de digitação não pode
    equivaler a esse pedido.
    """
    bruto = (os.environ.get(nome) or "").strip()
    if not bruto:
        return padrao
    try:
        valor = int(bruto)
    except ValueError:
        return padrao
    return valor if valor >= 0 else padrao


def teto_lotes_ativos() -> int:
    """Quantas corridas a conta pode ter em processamento ao mesmo tempo."""
    return _inteiro_do_ambiente("EASYCONTIG_MAX_LOTES_ATIVOS",
                                _MAX_LOTES_ATIVOS_PADRAO)


def teto_bytes_conta() -> int:
    """Quanto disco a conta pode ocupar somando TODAS as suas corridas."""
    return _inteiro_do_ambiente("EASYCONTIG_MAX_BYTES_CONTA",
                                _MAX_BYTES_CONTA_PADRAO)


def _tamanho(n: int) -> str:
    """Bytes em texto de gente, com vírgula decimal."""
    mb = n / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB".replace(".", ",")
    if mb >= 10 or n == 0:
        return f"{mb:.0f} MB"
    return f"{mb:.1f} MB".replace(".", ",")


def _corridas(n: int) -> str:
    return "1 corrida" if n == 1 else f"{n} corridas"


def bytes_da_pasta(raiz: Path) -> int:
    """Soma o que a pasta ocupa. Pasta ausente é 0, não é erro.

    O banco não sabe o tamanho do lote — ninguém grava isso em lugar nenhum — e
    inventar um campo criaria uma segunda verdade que sai do lugar assim que um
    lote é apagado à mão no servidor. O disco é a única fonte que não mente.
    """
    total = 0
    for pasta, _subpastas, arquivos in os.walk(raiz, onerror=lambda _e: None):
        for nome in arquivos:
            try:
                total += os.stat(os.path.join(pasta, nome)).st_size
            except OSError:
                # arquivo removido entre listar e medir: some da conta, não
                # derruba o envio de quem está esperando na tela.
                continue
    return total


def _lotes_da_conta(sqlite_path: Path, email: str) -> list[tuple[str, str]] | None:
    """(id, status) dos lotes do dono — None quando o banco não respondeu."""
    try:
        with fila.conectar(sqlite_path) as con:
            return [(r["id"], r["status"]) for r in con.execute(
                "SELECT id, status FROM lotes WHERE dono=?", (email,))]
    except Exception:
        return None


def situacao(sqlite_path: Path, lotes_dir: Path, email: str) -> Cota:
    """Como está a conta agora: o que já usa, de quanto, e se pode enviar."""
    max_lotes = teto_lotes_ativos()
    max_bytes = teto_bytes_conta()

    linhas = _lotes_da_conta(sqlite_path, email)
    if linhas is None:
        # Banco ilegível não é motivo para recusar o lote de quem está na
        # bancada: a cota é um limite de convivência, não uma trava de
        # segurança, e a próxima linha de `main.py` (novo_lote) vai falhar de
        # forma visível se o banco estiver mesmo quebrado.
        return Cota(0, max_lotes, 0, max_bytes, True, "")

    ativos = sum(1 for _id, status in linhas if status in _ATIVOS)
    # Conta o disco de TODOS os lotes, inclusive `pronto` e `falhou`: eles
    # continuam ocupando espaço, e é espaço que o usuário pediu para guardar.
    usados = sum(bytes_da_pasta(lotes_dir / lote_id) for lote_id, _s in linhas)

    if max_lotes and ativos >= max_lotes:
        return Cota(ativos, max_lotes, usados, max_bytes, False,
                    f"Você tem {_corridas(ativos)} ainda em processamento "
                    f"(o limite é {max_lotes}); espere alguma terminar para "
                    f"enviar outra.")

    if max_bytes and usados >= max_bytes:
        return Cota(ativos, max_lotes, usados, max_bytes, False,
                    f"Suas corridas já ocupam {_tamanho(usados)} dos "
                    f"{_tamanho(max_bytes)} reservados para esta conta. Peça a "
                    f"quem administra o servidor para remover corridas antigas "
                    f"antes de enviar esta.")

    return Cota(ativos, max_lotes, usados, max_bytes, True, "")
