"""
fila.py — a fila de lotes, em SQLite.

Por que existe (ADR 0050): o processamento em si é barato — 0,65 s de CPU por
amostra, medido. O que derruba um servidor não é o BLAST, é manter a requisição
HTTP ABERTA os ~26 s que um lote de 40 leva: vinte pessoas e acabaram as
conexões, com a CPU quase parada. Então a requisição só ENFILEIRA e devolve um
protocolo; quem trabalha é um processo à parte.

SQLite e não Redis de propósito: uma dependência a menos para a universidade
manter, e o volume real (algumas corridas por mês) não chega perto de justificar
outro serviço. A troca por Redis/RQ é local — só este arquivo muda.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# RECEBENDO existe por causa de uma corrida real: o lote era inserido como
# `na_fila` ANTES de os arquivos terminarem de subir, o trabalhador (que varre a
# fila a cada 0,25 s) pegava no meio do upload de 26 MB e montava só o que já
# tinha caído no disco. Resultado observado: relatório com 33 das 40 amostras e
# NADA avisando da falta — o pior tipo de defeito, porque parece um resultado.
# Agora o lote só entra na fila depois do último byte gravado.
RECEBENDO, NA_FILA, RODANDO, PRONTO, FALHOU = (
    "recebendo", "na_fila", "rodando", "pronto", "falhou")

# Os estados que ocupam vaga na cota da conta: o lote existe e ainda vai dar
# trabalho. Mora aqui, e não em `cotas.py`, porque quem precisa contá-los
# dentro da transação do INSERT é este módulo — e `cotas` importa `fila`, não
# o contrário.
ATIVOS = (RECEBENDO, NA_FILA, RODANDO)

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS lotes (
    id            TEXT PRIMARY KEY,
    dono          TEXT NOT NULL DEFAULT '',
    nome          TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    n_arquivos    INTEGER NOT NULL DEFAULT 0,
    feito         INTEGER NOT NULL DEFAULT 0,
    total         INTEGER NOT NULL DEFAULT 0,
    etapa         TEXT NOT NULL DEFAULT '',
    erro          TEXT NOT NULL DEFAULT '',
    referencia    TEXT NOT NULL DEFAULT '',   -- banco escolhido para identificar
    trabalhador   TEXT NOT NULL DEFAULT '',   -- quem o reivindicou (migração 3)
    criado_em     TEXT NOT NULL,
    iniciado_em   TEXT,
    terminado_em  TEXT
);
CREATE INDEX IF NOT EXISTS ix_lotes_status ON lotes(status, criado_em);
CREATE INDEX IF NOT EXISTS ix_lotes_dono   ON lotes(dono, criado_em);

-- Sinal de vida do trabalhador. Nasceu em 2026-08-06, quando o autor mandou um
-- par e o lote ficou em `na_fila` PARA SEMPRE: nenhum trabalhador estava
-- atendendo esta fila. A tela dizia "Na fila. Esta página se atualiza sozinha",
-- que é uma promessa — alguém vai pegar — e não havia ninguém. Mesmo gênero da
-- mensagem de envio interrompido de 05/08: a tela afirmando um fato que não
-- verificou.
CREATE TABLE IF NOT EXISTS pulso (
    trabalhador TEXT PRIMARY KEY,
    visto_em    TEXT NOT NULL
);
"""

# O trabalhador bate o pulso a cada volta do laço, mas só grava a cada
# `INTERVALO_PULSO` segundos. Some da conta depois de `PULSO_VELHO`: folga larga
# de propósito, porque um lote grande segura o laço, e anunciar "ninguém está
# atendendo" enquanto alguém trabalha seria trocar uma mentira por outra.
INTERVALO_PULSO = 5.0
PULSO_VELHO = 90.0


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conectar(caminho: Path):
    con = sqlite3.connect(caminho, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    # WAL: o worker escreve progresso enquanto a página web lê o status. Sem
    # isso, o SQLite serializa leitor e escritor e a página trava a cada polling.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
    finally:
        con.close()


def criar_esquema(caminho: Path) -> None:
    """Cria as tabelas se faltarem e leva o banco à versão de esquema atual.

    As mudanças de esquema saíram daqui e foram para `migracoes.py` (2026-08-06):
    o `if` que conferia `PRAGMA table_info` para descobrir se a coluna
    `referencia` já existia funcionou para UMA coluna, mas não dizia em que
    versão o banco estava, não tinha ordem declarada e não deixava por onde
    voltar atrás. Agora o `PRAGMA user_version` responde as três coisas, e há
    backup automático antes da primeira alteração.

    Continua seguro chamar a cada arranque, de qualquer processo: `IF NOT
    EXISTS` para as tabelas e, nas migrações, nada roda duas vezes.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with conectar(caminho) as con:
        con.executescript(_ESQUEMA)
    # Importado aqui, e não no topo, porque `migracoes` importa `conectar` deste
    # módulo — no topo seria import circular.
    from . import migracoes
    migracoes.aplicar(caminho)


class CotaEstourada(Exception):
    """O teto de lotes ativos da conta já estava cheio quando o lote ia nascer."""

    def __init__(self, ativos: int, teto: int):
        self.ativos, self.teto = ativos, teto
        super().__init__(f"{ativos} de {teto} lotes ativos")


def novo_lote(caminho: Path, *, dono: str, nome: str, n_arquivos: int,
              referencia: str = "", teto_ativos: int = 0) -> str:
    """Abre o lote em RECEBENDO — invisível para o trabalhador até liberar.

    `referencia` é o banco escolhido para identificar. Fica GRAVADO no lote, e
    não lido da configuração na hora de rodar: assim o relatório continua
    interpretável mesmo que a instalação mude de banco depois — a pergunta "o
    que este resultado comparou contra o quê" tem resposta no próprio registro.

    ⚠️ `teto_ativos` é conferido **aqui dentro**, na mesma transação do INSERT.
    A conferência da cota morava só na rota, antes do lote existir — e entre
    contar e criar havia uma janela. Medido em 2026-08-06: com teto de 1 e oito
    envios simultâneos da mesma conta, **sete entraram**. Todos passaram pela
    contagem antes de qualquer um aparecer nela.

    `BEGIN IMMEDIATE` porque o SQLite só serializa escritores a partir do
    momento em que a transação vira de escrita; começando em modo de leitura,
    dois processos leem o mesmo número e os dois escrevem.
    """
    lote_id = secrets.token_urlsafe(9)
    with conectar(caminho) as con:
        if teto_ativos:
            con.execute("BEGIN IMMEDIATE")
            marcas = ",".join("?" * len(ATIVOS))
            ativos = con.execute(
                f"SELECT COUNT(*) FROM lotes WHERE dono=? AND status IN ({marcas})",
                (dono, *ATIVOS)).fetchone()[0]
            if ativos >= teto_ativos:
                con.execute("ROLLBACK")
                raise CotaEstourada(ativos, teto_ativos)
        con.execute(
            "INSERT INTO lotes (id, dono, nome, status, n_arquivos, criado_em,"
            " referencia) VALUES (?,?,?,?,?,?,?)",
            (lote_id, dono, nome, RECEBENDO, n_arquivos, _agora(), referencia),
        )
        # `isolation_level=None` deixa tudo em autocommit; a transação explícita
        # acima é a única que precisa ser fechada à mão. Sem este COMMIT o lote
        # nasceria e sumiria — e a contagem seguinte veria a vaga livre de novo.
        if teto_ativos:
            con.execute("COMMIT")
    return lote_id


def liberar_para_fila(caminho: Path, lote_id: str, n_arquivos: int) -> None:
    """Último byte gravado: agora sim o trabalhador pode pegar.

    `n_arquivos` é reescrito aqui com o que REALMENTE foi gravado, e não com o
    que o navegador prometeu no início — é esse número que o executor confere
    contra os arquivos que acha em disco.
    """
    with conectar(caminho) as con:
        con.execute("UPDATE lotes SET status=?, n_arquivos=? WHERE id=? AND status=?",
                    (NA_FILA, n_arquivos, lote_id, RECEBENDO))


def pegar(caminho: Path, lote_id: str) -> dict | None:
    with conectar(caminho) as con:
        r = con.execute("SELECT * FROM lotes WHERE id=?", (lote_id,)).fetchone()
    return dict(r) if r else None


def listar(caminho: Path, *, dono: str | None = None, limite: int = 50) -> list[dict]:
    sql = "SELECT * FROM lotes"
    args: list = []
    if dono is not None:
        sql += " WHERE dono=?"
        args.append(dono)
    sql += " ORDER BY criado_em DESC LIMIT ?"
    args.append(limite)
    with conectar(caminho) as con:
        return [dict(r) for r in con.execute(sql, args)]


def reivindicar(caminho: Path, trabalhador: str = "") -> dict | None:
    """Marca o lote mais antigo da fila como RODANDO e devolve — ou None.

    O UPDATE condicional é o que permite mais de um trabalhador na mesma fila:
    quem perder a corrida atualiza 0 linhas e tenta o próximo. Sem isso, dois
    workers montariam o mesmo lote e gravariam por cima um do outro.

    `trabalhador` fica GRAVADO na linha porque sem ele `reenfileirar_orfaos`
    não consegue distinguir "o dono deste lote morreu" de "o dono está
    trabalhando nele agora" — as duas situações são idênticas na tabela, e a
    segunda foi tratada como a primeira até 2026-08-06.
    """
    with conectar(caminho) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            r = con.execute(
                "SELECT * FROM lotes WHERE status=? ORDER BY criado_em LIMIT 1",
                (NA_FILA,),
            ).fetchone()
            if not r:
                con.execute("COMMIT")
                return None
            n = con.execute(
                "UPDATE lotes SET status=?, iniciado_em=?, trabalhador=? "
                "WHERE id=? AND status=?",
                (RODANDO, _agora(), trabalhador, r["id"], NA_FILA),
            ).rowcount
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return dict(r) if n == 1 else None


# ── só o dono carimba ────────────────────────────────────────────────────────
# ⚠️ Estas três gravavam pelo `id` do lote e mais nada. Importa porque nenhuma
# trava de reenfileiramento é perfeita: se um trabalhador for desapossado do
# lote — por um reenfileiramento indevido, um relógio torto, uma pausa longa —
# ele **ainda assim** carimbava PRONTO por cima do irmão que estava montando de
# verdade, e o relatório publicado seria o da montagem abandonada. Achado pelo
# painel de verificação em 2026-08-06: sem isto, mesmo com o pulso perfeito o
# desfecho continuava podendo ser escrito por quem não estava mais no comando.
#
# `trabalhador=""` grava sem conferir, de propósito: é o caminho do servidor
# web, que marca `falhou` num lote que ninguém chegou a reivindicar.
def _dono_confere(trabalhador: str) -> tuple[str, tuple]:
    return (" AND trabalhador=?", (trabalhador,)) if trabalhador else ("", ())


def progresso(caminho: Path, lote_id: str, feito: int, total: int, etapa: str,
              trabalhador: str = "") -> bool:
    cond, extra = _dono_confere(trabalhador)
    with conectar(caminho) as con:
        return con.execute(
            f"UPDATE lotes SET feito=?, total=?, etapa=? WHERE id=?{cond}",
            (feito, total, etapa[:120], lote_id, *extra)).rowcount == 1


def concluir(caminho: Path, lote_id: str, trabalhador: str = "") -> bool:
    """True se este trabalhador ainda era o dono. False = perdeu o lote."""
    cond, extra = _dono_confere(trabalhador)
    with conectar(caminho) as con:
        return con.execute(
            f"UPDATE lotes SET status=?, terminado_em=?, etapa='' WHERE id=?{cond}",
            (PRONTO, _agora(), lote_id, *extra)).rowcount == 1


def falhar(caminho: Path, lote_id: str, erro: str, trabalhador: str = "") -> bool:
    cond, extra = _dono_confere(trabalhador)
    with conectar(caminho) as con:
        return con.execute(
            f"UPDATE lotes SET status=?, terminado_em=?, erro=?, etapa='' "
            f"WHERE id=?{cond}",
            (FALHOU, _agora(), erro[:2000], lote_id, *extra)).rowcount == 1


def pulsar(caminho: Path, trabalhador: str) -> None:
    """Registra que este trabalhador está vivo e olhando ESTA fila."""
    with conectar(caminho) as con:
        con.execute(
            "INSERT INTO pulso (trabalhador, visto_em) VALUES (?,?) "
            "ON CONFLICT(trabalhador) DO UPDATE SET visto_em=excluded.visto_em",
            (trabalhador, _agora()))


def fila_atendida(caminho: Path, agora: datetime | None = None) -> bool:
    """Há algum trabalhador vivo atendendo esta fila?

    Serve para a tela parar de prometer o que ninguém vai cumprir. É uma
    pergunta sobre a FILA, não sobre um lote: quem responde é o pulso mais
    recente de qualquer trabalhador.

    Falha para o lado de "está atendida" em dois casos, e os dois são decisão,
    não descuido:

    * **registro ou relógio ilegíveis** — anunciar abandono por engano assusta à
      toa, e o lote sendo processado desmente o aviso em segundos;
    * **tabela ausente** — significa banco anterior a este recurso, o que só
      acontece entre subir o código novo e reiniciar o servidor (o
      `criar_esquema` do arranque cria a tabela). Durante uma atualização, todo
      mundo veria "nada está atendendo" ao mesmo tempo, e seria mentira.

    O caso que motivou tudo isto — pulso NENHUM, nunca — não depende de
    interpretar data alguma e é respondido com um `False` seco.
    """
    agora = agora or datetime.now(timezone.utc)
    try:
        with conectar(caminho) as con:
            linha = con.execute("SELECT MAX(visto_em) AS v FROM pulso").fetchone()
    except sqlite3.Error:
        return True
    if not linha or not linha["v"]:
        return False                     # nunca subiu trabalhador nenhum aqui
    try:
        visto = datetime.fromisoformat(linha["v"])
    except ValueError:
        return True
    if visto.tzinfo is None:
        visto = visto.replace(tzinfo=timezone.utc)
    return (agora - visto).total_seconds() <= PULSO_VELHO


def trabalhadores_vivos(caminho: Path, agora: datetime | None = None) -> set[str]:
    """Quem bateu pulso há menos de `PULSO_VELHO`."""
    agora = agora or datetime.now(timezone.utc)
    vivos: set[str] = set()
    try:
        with conectar(caminho) as con:
            linhas = con.execute("SELECT trabalhador, visto_em FROM pulso").fetchall()
    except sqlite3.Error:
        return vivos
    for r in linhas:
        try:
            visto = datetime.fromisoformat(r["visto_em"])
        except (ValueError, TypeError):
            continue
        if visto.tzinfo is None:
            visto = visto.replace(tzinfo=timezone.utc)
        if (agora - visto).total_seconds() <= PULSO_VELHO:
            vivos.add(r["trabalhador"])
    return vivos


def reenfileirar_orfaos(caminho: Path, eu: str = "",
                        agora: datetime | None = None) -> int:
    """Devolve à fila os lotes deixados em RODANDO por um worker que morreu.

    Chamado quando o worker sobe. Sem isso, uma queda de energia (o cenário que
    motivou pôr isto numa VPS com no-break) deixaria o lote preso em "rodando"
    para sempre, e a página do usuário giraria eternamente.

    RECEBENDO NÃO é reenfileirado: um upload interrompido deixou a pasta pela
    metade, e mandá-la para a fila reproduziria exatamente o defeito das 33 de
    40 amostras. Lote sem upload completo é lote perdido, e tem que dizer isso.

    ⚠️ **ANTES, ISTO REENFILEIRAVA O TRABALHO DOS OUTROS.** O UPDATE varria
    *todos* os lotes em RODANDO, sem olhar de quem eram. E o
    `docker-compose.yml` sobe `replicas: ${TRABALHADORES:-2}`: subir o segundo
    trabalhador — ou reiniciar um deles, ou o `restart: unless-stopped` agindo
    depois de um OOM — devolvia à fila o lote que o outro estava montando
    **naquele instante**. Os dois passavam a escrever na mesma pasta.

    Agora só volta para a fila o lote cujo dono **não está pulsando**. Um lote
    sem dono registrado (banco anterior à migração 3) é reenfileirado apenas se
    NENHUM outro trabalhador estiver vivo — na dúvida, não se toca no trabalho
    alheio, porque o preço de esperar é uma página girando e o de errar é o
    relatório de duas montagens misturadas.
    """
    vivos = trabalhadores_vivos(caminho, agora) - {eu}

    def orfao(dono: str) -> bool:
        if dono:
            return dono not in vivos      # o dono existe: só é órfão se morreu
        return not vivos                  # sem dono (banco velho): só se ninguém vive

    with conectar(caminho) as con:
        # `BEGIN IMMEDIATE`: ler e escrever na MESMA transação de escrita. Em
        # autocommit, dois trabalhadores subindo juntos — que é o caso normal do
        # `docker compose up` com duas réplicas — leem a mesma lista de órfãos e
        # os dois reenfileiram.
        con.execute("BEGIN IMMEDIATE")
        try:
            rodando = con.execute(
                "SELECT id, trabalhador FROM lotes WHERE status=?",
                (RODANDO,)).fetchall()
            orfaos = [r["id"] for r in rodando if orfao(r["trabalhador"])]
            n = 0
            if orfaos:
                marcas = ",".join("?" * len(orfaos))
                n = con.execute(
                    f"UPDATE lotes SET status=?, etapa='', trabalhador='' "
                    f"WHERE id IN ({marcas}) AND status=?",
                    (NA_FILA, *orfaos, RODANDO)).rowcount
            con.execute(
                "UPDATE lotes SET status=?, erro=? WHERE status=?",
                (FALHOU, "o envio dos arquivos foi interrompido; envie a corrida de novo",
                 RECEBENDO))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return n
