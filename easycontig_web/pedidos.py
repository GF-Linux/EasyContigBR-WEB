"""
pedidos.py — um laboratório pede uma amostra a outro, e o outro aceita ou recusa.

É o que a ADR 0052 já apontava quando decidiu que o portfólio do perfil é
**declaração** e não estatística: ele existe para *"outro laboratório achar quem
trabalha com o quê e pedir uma sequência"*. O `/labs` mostrou o quê; isto é o
pedir.

⚠️ **NÃO É CONVERSA, e a diferença é de desenho, não de acabamento.** Um pedido
tem exatamente um ciclo:

    pendente ──aceito──▶ fim      (o dono da amostra justifica)
       │  └──recusado──▶ fim      (idem)
       └──cancelado───▶ fim       (só quem pediu, e só enquanto pendente)

Estado final é final. Quem quiser tentar de novo manda **outro pedido**, com
histórico próprio — decisão do autor em 2026-08-10. Sem responder-a-resposta,
sem linha do tempo, sem caixa de texto que reabre: cada uma dessas coisas
transforma isto numa caixa de mensagens, que é justamente o que não se quer
manter (nem moderar) num serviço de laboratório.

## A regra de privacidade, que é a parte delicada

O `/labs` **não entrega e-mail de terceiro** — achado L1 de 2026-08-08: com o
domínio institucional anunciado na tela de entrada, qualquer pedaço do endereço
alheio remonta o endereço inteiro. Um pedido de amostra, porém, só termina fora
do site: alguém precisa mandar o tubo pelo correio.

A saída decidida com o autor: **o aceite é o consentimento**. Enquanto o pedido
está pendente — ou se for recusado, ou cancelado — nenhum dos dois lados vê o
e-mail do outro; vê o nome e o laboratório, que já estão no diretório. No
instante em que alguém **aceita**, os dois e-mails passam a aparecer *naquele
pedido*, para os dois lados. Aceitar é um ato deliberado de quem foi procurado,
e é o único ponto do sistema em que um endereço atravessa a fronteira entre
contas.

⚠️ Por isso `_visao()` é o único lugar que decide mostrar `contato`, e decide
pelo ESTADO. Nenhuma consulta deste módulo devolve e-mail de terceiro por outro
caminho; se um dia alguém precisar de um, que passe por aqui.

## Por que o alvo é uma chave sorteada, e não o e-mail (nem o hash dele)

O formulário precisa dizer *para quem* é o pedido, e esse identificador vai para
o HTML de uma página que todo mundo abre. Não pode ser o e-mail, pelo L1. E
**também não pode ser `sha256(e-mail)`**, que era o caminho óbvio (a ADR 0054 já
usa isso para o espaço de disco da conta): e-mail institucional é adivinhável —
`nome.sobrenome@ufrrj.br` —, então quem tem a lista de hashes do diretório
confirma, offline e sem bater no servidor, quais endereços têm conta aqui. Hash
de valor de baixa entropia não esconde o valor; só o embrulha.

A chave é `secrets.token_urlsafe`, sorteada por conta e guardada em `perfis`.
Não deriva de nada e não confirma palpite nenhum.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .fila import conectar

PENDENTE = "pendente"
ACEITO = "aceito"
RECUSADO = "recusado"
CANCELADO = "cancelado"

# Teto do que se escreve. `motivo` é a justificativa de quem pede, `resposta` a
# de quem responde — as duas são obrigatórias por decisão do autor: "recusar sem
# dizer por quê" é o comportamento que faria o recurso morrer de desuso.
MAX_TEXTO = 600
MAX_ITENS = 12

# ⚠️ Quantos pedidos PENDENTES a mesma pessoa pode ter com o mesmo laboratório.
# O teto de taxa do `limites.py` já cobre a rajada, mas ele conta por hora e
# esquece; este conta o que está VIVO na caixa de quem recebe. Sem ele, 20
# pedidos por hora, hora após hora, entopem a caixa de um lab só — e quem recebe
# não tem como fazer o barulho parar a não ser respondendo um por um. Com o
# teto, quem quer insistir precisa que o outro responda antes.
MAX_PENDENTES_POR_PAR = 3

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS pedidos (
    id          TEXT PRIMARY KEY,
    de_email    TEXT NOT NULL,
    para_email  TEXT NOT NULL,
    itens       TEXT NOT NULL DEFAULT '[]',  -- do portfólio declarado do alvo
    outro       TEXT NOT NULL DEFAULT '',    -- o que não estava na lista
    motivo      TEXT NOT NULL DEFAULT '',
    estado      TEXT NOT NULL DEFAULT 'pendente',
    resposta    TEXT NOT NULL DEFAULT '',
    criado      TEXT NOT NULL DEFAULT '',
    respondido  TEXT NOT NULL DEFAULT ''
);
-- A caixa de quem recebe é a consulta quente: ela roda em TODA página, porque o
-- contador de pendentes vive na lateral.
CREATE INDEX IF NOT EXISTS idx_pedidos_para ON pedidos(para_email, estado);
CREATE INDEX IF NOT EXISTS idx_pedidos_de   ON pedidos(de_email, estado);
"""


def criar_esquema(sqlite_path: Path) -> None:
    with conectar(sqlite_path) as con:
        con.executescript(_ESQUEMA)


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _texto(bruto: str) -> str:
    return (bruto or "").strip()[:MAX_TEXTO]


class NaoPode(Exception):
    """O pedido não pode existir, e a mensagem é para a pessoa ler na tela."""


def criar(sqlite_path: Path, *, de_email: str, para_email: str,
          itens: list[str], outro: str = "", motivo: str = "") -> str:
    """Registra o pedido e devolve o id. Levanta `NaoPode` com texto de tela.

    ⚠️ `itens` é FILTRADO contra o portfólio declarado do alvo, e não gravado
    como veio. Os valores chegam de caixas de seleção que o próprio servidor
    desenhou a partir daquele portfólio — mas o formulário é do navegador, e um
    POST feito à mão manda o que quiser. Sem o filtro, `itens` viraria um campo
    de texto livre extra, sem rótulo e sem teto declarado, atravessando de uma
    conta para outra. O texto livre existe e tem nome: é o `outro`.
    """
    if de_email == para_email:
        raise NaoPode("um pedido é para outro laboratório")
    motivo = _texto(motivo)
    if not motivo:
        raise NaoPode("diga para que serve a amostra — quem recebe decide com isso")

    from . import perfil as mod_perfil
    alvo = mod_perfil.pegar(sqlite_path, para_email)
    declarado = {*(alvo.get("especies") or []), *(alvo.get("marcadores") or [])}
    escolhidos, vistos = [], set()
    for item in itens or []:
        s = " ".join(str(item).split())
        if s in declarado and s not in vistos:
            vistos.add(s)
            escolhidos.append(s)
        if len(escolhidos) >= MAX_ITENS:
            break

    outro = _texto(outro)
    if not escolhidos and not outro:
        raise NaoPode("marque ao menos um item do portfólio, ou escreva o que precisa")

    with conectar(sqlite_path) as con:
        # `BEGIN IMMEDIATE` porque contar-e-inserir é uma decisão só: sem a
        # trava, dois envios simultâneos leem o mesmo total e os dois passam —
        # o mesmo gênero do furo de cota do achado L3 (ADR de 2026-08-08).
        con.execute("BEGIN IMMEDIATE")
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM pedidos WHERE de_email=? AND para_email=? "
                "AND estado=?", (de_email, para_email, PENDENTE)).fetchone()[0]
            if n >= MAX_PENDENTES_POR_PAR:
                raise NaoPode(
                    f"você já tem {n} pedidos esperando resposta deste "
                    "laboratório; aguarde a resposta antes de mandar outro")
            pid = secrets.token_urlsafe(9)
            con.execute(
                "INSERT INTO pedidos (id,de_email,para_email,itens,outro,motivo,"
                "estado,criado) VALUES (?,?,?,?,?,?,?,?)",
                (pid, de_email, para_email,
                 json.dumps(escolhidos, ensure_ascii=False), outro, motivo,
                 PENDENTE, _agora()))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return pid


def responder(sqlite_path: Path, pedido_id: str, quem: str, *,
              aceitar: bool, resposta: str) -> dict | None:
    """Aceita ou recusa. Só quem RECEBEU responde, e só o que está pendente.

    Devolve o pedido atualizado, ou `None` se não havia o que responder — o id
    não existe, é de outra pessoa, ou já foi respondido. Os três casos dão a
    mesma resposta de propósito: distinguir "não é seu" de "não existe" conta a
    quem tentou que aquele id é de alguém.
    """
    resposta = _texto(resposta)
    if not resposta:
        raise NaoPode("diga o motivo — quem pediu precisa saber com o que contar")
    estado = ACEITO if aceitar else RECUSADO
    with conectar(sqlite_path) as con:
        # A condição `estado=PENDENTE` está no UPDATE, e não num SELECT antes:
        # é o que impede dois cliques (ou duas abas) de responderem duas vezes.
        cur = con.execute(
            "UPDATE pedidos SET estado=?, resposta=?, respondido=? "
            "WHERE id=? AND para_email=? AND estado=?",
            (estado, resposta, _agora(), pedido_id, quem, PENDENTE))
        if cur.rowcount == 0:
            return None
    return pegar(sqlite_path, pedido_id, quem)


def cancelar(sqlite_path: Path, pedido_id: str, quem: str) -> bool:
    """Quem pediu desiste, enquanto ninguém respondeu. Some da caixa do outro."""
    with conectar(sqlite_path) as con:
        cur = con.execute(
            "UPDATE pedidos SET estado=?, respondido=? "
            "WHERE id=? AND de_email=? AND estado=?",
            (CANCELADO, _agora(), pedido_id, quem, PENDENTE))
        return cur.rowcount > 0


# ------------------------------------------------------------------- consultas
_CAMPOS = """
    p.id, p.de_email, p.para_email, p.itens, p.outro, p.motivo, p.estado,
    p.resposta, p.criado, p.respondido,
    de.nome AS de_nome, de.laboratorio AS de_lab, de.instituicao AS de_inst,
    de.foto AS de_foto,
    pa.nome AS para_nome, pa.laboratorio AS para_lab, pa.instituicao AS para_inst,
    pa.foto AS para_foto
"""
_DE_ONDE = """
    FROM pedidos p
    LEFT JOIN perfis de ON de.email = p.de_email
    LEFT JOIN perfis pa ON pa.email = p.para_email
"""


def _visao(linha, eu: str) -> dict:
    """A linha do banco vista POR UMA DAS DUAS PARTES.

    ⚠️ **É aqui, e só aqui, que um e-mail atravessa a fronteira entre contas.**
    `contato` sai apenas quando o pedido está `aceito`, porque foi o aceite que
    autorizou (ver o cabeçalho do módulo). Nos outros estados a pessoa do outro
    lado aparece só com o que ela já publica no `/labs`: nome, laboratório,
    instituição e foto.

    O `sou_dono` diz de que lado desta conversa a conta está — quem RECEBEU
    responde, quem MANDOU cancela. A tela não decide isso sozinha.
    """
    d = dict(linha)
    recebi = d["para_email"] == eu
    outro_lado = "de" if recebi else "para"
    visao = {
        "id": d["id"],
        "estado": d["estado"],
        "recebi": recebi,
        "itens": _lista(d["itens"]),
        "outro": d["outro"],
        "motivo": d["motivo"],
        "resposta": d["resposta"],
        "criado": d["criado"],
        "respondido": d["respondido"],
        "quem": {
            "nome": d[f"{outro_lado}_nome"] or "",
            "laboratorio": d[f"{outro_lado}_lab"] or "",
            "instituicao": d[f"{outro_lado}_inst"] or "",
            "foto": d[f"{outro_lado}_foto"] or "",
        },
        "contato": "",
    }
    if d["estado"] == ACEITO:
        visao["contato"] = d["de_email"] if recebi else d["para_email"]
    return visao


def _lista(bruto: str) -> list[str]:
    try:
        v = json.loads(bruto or "[]")
    except ValueError:
        return []
    return [str(x) for x in v if isinstance(x, str)][:MAX_ITENS]


def pegar(sqlite_path: Path, pedido_id: str, eu: str) -> dict | None:
    """Um pedido, se a conta for uma das duas partes dele."""
    with conectar(sqlite_path) as con:
        r = con.execute(
            f"SELECT {_CAMPOS} {_DE_ONDE} "
            "WHERE p.id=? AND (p.de_email=? OR p.para_email=?)",
            (pedido_id, eu, eu)).fetchone()
    return _visao(r, eu) if r else None


def _listar(sqlite_path: Path, eu: str, coluna: str) -> list[dict]:
    """Pendentes primeiro (é o que pede ação), depois os mais recentes."""
    with conectar(sqlite_path) as con:
        linhas = con.execute(
            f"SELECT {_CAMPOS} {_DE_ONDE} WHERE p.{coluna}=? "
            "ORDER BY (p.estado='pendente') DESC, p.criado DESC LIMIT 200",
            (eu,)).fetchall()
    return [_visao(r, eu) for r in linhas]


def recebidos(sqlite_path: Path, eu: str) -> list[dict]:
    """O que pediram a esta conta. Cancelado não aparece: quem pediu desistiu
    antes de a pessoa olhar, e mostrar 'fulano desistiu' é ruído sobre um pedido
    que nunca chegou a exigir nada."""
    return [p for p in _listar(sqlite_path, eu, "para_email")
            if p["estado"] != CANCELADO]


def enviados(sqlite_path: Path, eu: str) -> list[dict]:
    return _listar(sqlite_path, eu, "de_email")


def contar_pendentes(sqlite_path: Path, eu: str) -> int:
    """Quantos pedidos esperam resposta DESTA conta.

    Roda em toda página (o contador mora na lateral), e é por isso que existe o
    índice `idx_pedidos_para`: sem ele isto é varredura de tabela em cada
    requisição do app inteiro.
    """
    with conectar(sqlite_path) as con:
        r = con.execute("SELECT COUNT(*) FROM pedidos WHERE para_email=? AND "
                        "estado=?", (eu, PENDENTE)).fetchone()
    return int(r[0]) if r else 0
