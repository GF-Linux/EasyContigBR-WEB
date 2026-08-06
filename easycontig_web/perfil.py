"""
perfil.py — quem é a conta, e o que o laboratório declara trabalhar.

Duas coisas moram aqui, e a diferença entre elas é a razão do módulo existir:

* **Identidade** — nome de exibição, laboratório, foto. Editável pela pessoa; o
  e-mail não, porque vem do login.
* **Portfólio de espécies e marcadores** — o que o laboratório **declara** que
  trabalha. É texto do dono, não estatística.

⚠️ **O portfólio NÃO é contagem do que o BLAST devolveu.** A primeira versão
desta tela mostrava um ranking das espécies encontradas nas corridas, e isso
convida a ler prevalência onde não há: aquilo contava amostras (não animais) e
só enxergava o que estava no banco local. O portfólio é declaração — serve para
outro laboratório **achar quem trabalha com o quê** e pedir uma sequência, que é
o rumo de compartilhamento por pedido da ADR 0052.

Guardado no mesmo SQLite da fila: é um registro por conta, lido em toda página.
Um segundo banco só para isto seria mais uma coisa para a universidade manter.
"""
from __future__ import annotations

import json
from pathlib import Path

from .fila import conectar

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS perfis (
    email        TEXT PRIMARY KEY,
    nome         TEXT NOT NULL DEFAULT '',
    laboratorio  TEXT NOT NULL DEFAULT '',
    instituicao  TEXT NOT NULL DEFAULT '',
    sobre        TEXT NOT NULL DEFAULT '',
    especies     TEXT NOT NULL DEFAULT '[]',   -- portfólio declarado
    marcadores   TEXT NOT NULL DEFAULT '[]',   -- idem
    foto         TEXT NOT NULL DEFAULT '',     -- nome do arquivo no volume
    atualizado   TEXT NOT NULL DEFAULT ''
);
"""

# Teto do que é declarado. Não é limitação técnica: uma lista de 200 espécies
# deixa de ser portfólio e vira ruído para quem procura um parceiro.
MAX_ITENS = 24
MAX_TEXTO = 90


def criar_esquema(sqlite_path: Path) -> None:
    with conectar(sqlite_path) as con:
        con.executescript(_ESQUEMA)


def _lista(bruto: str) -> list[str]:
    try:
        v = json.loads(bruto or "[]")
    except ValueError:
        return []
    return [str(x)[:MAX_TEXTO] for x in v if isinstance(x, (str, int, float))][:MAX_ITENS]


def pegar(sqlite_path: Path, email: str) -> dict:
    """O perfil da conta. Devolve os padrões quando ainda não há registro —
    perfil vazio é estado normal de quem acabou de entrar, não erro."""
    with conectar(sqlite_path) as con:
        r = con.execute("SELECT * FROM perfis WHERE email=?", (email,)).fetchone()
    if not r:
        return {"email": email, "nome": email.split("@")[0], "laboratorio": "",
                "instituicao": "", "sobre": "", "especies": [], "marcadores": [],
                "foto": "", "atualizado": ""}
    d = dict(r)
    d["especies"] = _lista(d["especies"])
    d["marcadores"] = _lista(d["marcadores"])
    return d


def _limpar_itens(texto: str) -> list[str]:
    """Uma por linha ou separadas por vírgula — aceita as duas, sem exigir sintaxe.

    Quem digita um portfólio está listando nomes de espécie, e nome de espécie
    tem espaço no meio: separar só por espaço quebraria "Babesia vogeli".
    """
    bruto = (texto or "").replace(";", "\n").replace(",", "\n")
    vistos, saida = set(), []
    for linha in bruto.splitlines():
        s = " ".join(linha.split())[:MAX_TEXTO]
        if s and s.lower() not in vistos:
            vistos.add(s.lower())
            saida.append(s)
    return saida[:MAX_ITENS]


def salvar(sqlite_path: Path, email: str, *, nome: str = "", laboratorio: str = "",
           instituicao: str = "", sobre: str = "", especies: str = "",
           marcadores: str = "", foto: str | None = None) -> dict:
    """Grava o perfil. `foto=None` mantém a que já está lá."""
    from datetime import datetime, timezone
    atual = pegar(sqlite_path, email)
    dados = {
        "nome": " ".join((nome or "").split())[:MAX_TEXTO] or atual["nome"],
        "laboratorio": " ".join((laboratorio or "").split())[:MAX_TEXTO],
        "instituicao": " ".join((instituicao or "").split())[:MAX_TEXTO],
        "sobre": (sobre or "").strip()[:600],
        "especies": json.dumps(_limpar_itens(especies), ensure_ascii=False),
        "marcadores": json.dumps(_limpar_itens(marcadores), ensure_ascii=False),
        "foto": atual["foto"] if foto is None else foto,
        "atualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with conectar(sqlite_path) as con:
        con.execute(
            "INSERT INTO perfis (email,nome,laboratorio,instituicao,sobre,especies,"
            "marcadores,foto,atualizado) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET nome=excluded.nome,"
            "laboratorio=excluded.laboratorio,instituicao=excluded.instituicao,"
            "sobre=excluded.sobre,especies=excluded.especies,"
            "marcadores=excluded.marcadores,foto=excluded.foto,"
            "atualizado=excluded.atualizado",
            (email, dados["nome"], dados["laboratorio"], dados["instituicao"],
             dados["sobre"], dados["especies"], dados["marcadores"],
             dados["foto"], dados["atualizado"]))
    return pegar(sqlite_path, email)


def resumo_das_corridas(lotes: list[dict], relatorios: list[dict]) -> dict:
    """Contagens do que a conta tem no servidor. Só volume — nada de espécie.

    O que apareceu nas corridas fica FORA daqui de propósito: contar acertos do
    BLAST e exibir como perfil transformaria "o banco local achou isto" em "o
    laboratório trabalha com isto", que são afirmações diferentes.
    """
    prontos = [l for l in lotes if l.get("status") == "pronto"]
    amostras = sum(len(r.get("samples") or []) for r in relatorios)
    return {"corridas": len(lotes), "corridas_prontas": len(prontos),
            "amostras": amostras}
