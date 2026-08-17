#? COTA DE ESPAÇO — Decisão sobre limite por conta 05/08/2026
#!
#! 1. Mede quanto UMA CONTA ocupa, não quanto cabe em um lote.
#! 2. Os tetos por lote (400 arquivos, 300 MB) seguram o envio único absurdo e
#!    mais nada: 50 lotes legítimos enchem o disco sem cruzar teto nenhum.
#! 3. Disco cheio no meio de um upload é o defeito de sempre — metade dos
#!    arquivos gravados e relatório com menos amostras do que a corrida tem.
#! 4. Este módulo NÃO levanta exceção e NÃO decide status HTTP. Responde "como
#!    está esta conta agora" e deixa o servidor traduzir em 413.
#! 5. A MESMA função serve para barrar o envio e para dizer na tela quanto
#!    sobra. Se fossem duas, uma envelheceria e a tela mentiria.
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..processamento import fila_de_lotes as fila

_ATIVOS = fila.ATIVOS          # definido em fila.py: a transação do INSERT usa o mesmo

# 10, e não 3, por causa da ADR 0051: o uso real é PAR A PAR, e cada par enviado
# é um lote próprio. Com 3, quem manda cinco pares seguidos — que é o fluxo
# normal, não abuso — bate na parede. Medido no Deck: um par fica pronto em
# 582 ms, então 10 ativos são ~6 s de trabalho; ainda segura uma disparada, sem
# atrapalhar quem está trabalhando.
_MAX_LOTES_ATIVOS_PADRAO = 10
# 2 GiB ≈ 3 200 pares de 650 KB, ou ~80 corridas inteiras de 26 MB (tamanhos
# medidos nos lotes reais). Como o `.ab1` não é apagado depois do processamento
# (o laboratório volta neles) e a retenção vem desligada, isto é o acumulado da
# conta ao longo do tempo, não o envio da vez.
_MAX_BYTES_CONTA_PADRAO = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class Cota:
    lotes_ativos: int         # em recebendo/na_fila/rodando
    max_lotes_ativos: int     # 0 = sem limite
    bytes_usados: int         # o que esta conta ocupa em disco (lotes + bancos)
    max_bytes_conta: int      # 0 = sem limite
    pode_enviar: bool
    motivo: str               # vazio quando pode; frase pronta quando não
    bancos: int = 0           # quantos bancos próprios a conta mantém
    max_bancos: int = 0       # 0 = sem limite


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


def teto_bancos_conta() -> int:
    """Quantos bancos próprios a conta pode manter (achado M2 de 2026-08-08).

    Não havia teto de QUANTIDADE — só o balde `banco`, de 10 por hora. E
    `_NOME_OK` aceita 40 caracteres alfanuméricos, ou seja o espaço de apelidos
    é praticamente infinito: 10 FASTAs de 20 MB por hora, cada um com apelido
    diferente, são ~200 MB/h **permanentes**. Permanentes porque banco de
    usuário não expira (ver `bytes_dos_bancos`).

    12 é folgado para o uso real — o catálogo inteiro do projeto tem menos que
    isso — e continua sendo um número, que é o que faltava.
    """
    return _inteiro_do_ambiente("EASYCONTIG_MAX_BANCOS_CONTA", 12)


def bytes_dos_bancos(data_dir: Path, email: str) -> int:
    """O disco que os bancos próprios desta conta ocupam.

    ⚠️ Isto NÃO entrava na cota (achado M2): `situacao` somava apenas
    `lotes_dir/<lote_id>`. O banco de usuário fica noutra árvore, então crescia
    invisível — e a `retencao.faxina` também só apaga lote, nunca banco.

    E continua não expirando **de propósito**: um lote é material de trabalho
    que a pessoa baixa e descarta, um banco é referência curada que ela montou
    para usar de novo. Apagar sozinho o segundo seria destruir trabalho
    deliberado por causa de um relógio. O conserto é a cota vê-lo e o teto de
    quantidade limitá-lo — não o expurgo levá-lo.
    """
    from ..dados import bancos_de_referencia as mod_bancos
    total = 0
    for b in mod_bancos.meus_bancos(data_dir, email):
        total += bytes_da_pasta(mod_bancos.pasta(data_dir) / b["id"])
    return total


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


# --------------------------------------------------------------- memória curta
#
# Medido em 2026-08-06: com 40 corridas de 80 arquivos guardadas — o acúmulo
# normal de uma conta, não um caso extremo — `situacao()` gastava **23,03 ms**
# só percorrendo pastas, e isso era ~68% do tempo da PÁGINA INICIAL, que a
# chama a cada visita. O `os.walk` cresce com o histórico, para sempre.
#
# O que torna o cache exato, e não uma aproximação: **pasta de lote terminado
# não muda mais**. Depois de `pronto` ou `falhou` ninguém escreve ali — só a
# faxina, que apaga a pasta inteira. Então o tamanho de um lote terminado é uma
# constante, e recalculá-lo a cada visita é trabalho jogado fora. Lote ativo
# (`recebendo`/`rodando`) está sendo escrito neste instante e continua sendo
# medido no disco, toda vez.
#
# A promessa da função acima segue de pé: o disco continua sendo a única fonte.
# A entrada só é usada se a pasta AINDA EXISTE — um `os.stat`, não um walk —,
# então um lote apagado à mão some da conta na visita seguinte, que era
# exatamente o modo de falha que um campo no banco teria.
_TERMINADOS = (fila.PRONTO, fila.FALHOU)
_medidos: dict[str, int] = {}


def esquecer(lote_id: str) -> None:
    """Tira um lote da memória curta. Existe para os testes e para quem quiser
    forçar a remedição; o caminho normal de invalidação é a pasta sumir."""
    _medidos.pop(lote_id, None)


def _bytes_do_lote(lotes_dir: Path, lote_id: str, status: str) -> int:
    if status not in _TERMINADOS:
        return bytes_da_pasta(lotes_dir / lote_id)
    raiz = lotes_dir / lote_id
    if not raiz.exists():
        _medidos.pop(lote_id, None)
        return 0
    if lote_id not in _medidos:
        _medidos[lote_id] = bytes_da_pasta(raiz)
    return _medidos[lote_id]


def _lotes_da_conta(sqlite_path: Path, email: str) -> list[tuple[str, str]] | None:
    """(id, status) dos lotes do dono — None quando o banco não respondeu."""
    try:
        with fila.conectar(sqlite_path) as con:
            return [(r["id"], r["status"]) for r in con.execute(
                "SELECT id, status FROM lotes WHERE dono=?", (email,))]
    except Exception:
        return None


def situacao(sqlite_path: Path, lotes_dir: Path, email: str,
             data_dir: Path | None = None) -> Cota:
    """Como está a conta agora: o que já usa, de quanto, e se pode enviar.

    `data_dir` liga os BANCOS próprios à conta (achado M2). É opcional para
    não quebrar chamador antigo, mas em `main.py` é sempre passado: sem ele a
    conta enxerga só metade do que ocupa, que era o buraco.
    """
    max_lotes = teto_lotes_ativos()
    max_bytes = teto_bytes_conta()
    max_bancos = teto_bancos_conta()
    n_bancos = 0
    bytes_bancos = 0
    if data_dir is not None:
        from ..dados import bancos_de_referencia as mod_bancos
        n_bancos = len(mod_bancos.meus_bancos(data_dir, email))
        bytes_bancos = bytes_dos_bancos(data_dir, email)

    linhas = _lotes_da_conta(sqlite_path, email)
    if linhas is None:
        # Banco ilegível não é motivo para recusar o lote de quem está na
        # bancada: a cota é um limite de convivência, não uma trava de
        # segurança, e a próxima linha de `main.py` (novo_lote) vai falhar de
        # forma visível se o banco estiver mesmo quebrado.
        return Cota(0, max_lotes, bytes_bancos, max_bytes, True, "",
                    n_bancos, max_bancos)

    ativos = sum(1 for _id, status in linhas if status in _ATIVOS)
    # Conta o disco de TODOS os lotes, inclusive `pronto` e `falhou`: eles
    # continuam ocupando espaço, e é espaço que o usuário pediu para guardar.
    usados = sum(_bytes_do_lote(lotes_dir, lote_id, status)
                 for lote_id, status in linhas) + bytes_bancos

    if max_lotes and ativos >= max_lotes:
        return Cota(ativos, max_lotes, usados, max_bytes, False,
                    f"Você tem {_corridas(ativos)} ainda em processamento "
                    f"(o limite é {max_lotes}); espere alguma terminar para "
                    f"enviar outra.", n_bancos, max_bancos)

    if max_bytes and usados >= max_bytes:
        return Cota(ativos, max_lotes, usados, max_bytes, False,
                    f"Suas corridas e bancos já ocupam {_tamanho(usados)} dos "
                    f"{_tamanho(max_bytes)} reservados para esta conta. Apague "
                    f"uma corrida antiga ou um banco próprio antes de enviar "
                    f"esta.", n_bancos, max_bancos)

    return Cota(ativos, max_lotes, usados, max_bytes, True, "",
                n_bancos, max_bancos)
