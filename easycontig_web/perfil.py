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
import re
from pathlib import Path
from urllib.parse import urlparse

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
    atualizado   TEXT NOT NULL DEFAULT '',
    links        TEXT NOT NULL DEFAULT '[]'    -- endereços de rede (ver REDES)
);
"""

# As redes que ganham ícone próprio. A chave é procurada no HOST do endereço;
# quem não casa com nenhuma entra como "site", com ícone de globo — ninguém fica
# de fora por não estar nesta lista, e o `""` no fim é justamente esse curinga.
#
# Ícone é SVG inline e MONOCROMÁTICO (`currentColor`): a tela toda é
# monocromática por decisão de paleta (ADR 0052) e um logo colorido de terceiro
# roubaria o único acento que existe, que é o violeta.
REDES: dict[str, tuple[str, str]] = {
    "github.com": ("GitHub",
        "M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37"
        "-1.34-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.61.07-.61 1 .07 1.53 1.03 1.53"
        "1.03.9 1.53 2.36 1.09 2.94.83.09-.65.35-1.09.63-1.34-2.22-.25-4.56-1.11-4.56"
        "-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02a9.5"
        "9.5 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59"
        "1.03 2.68 0 3.84-2.34 4.69-4.57 4.94.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48"
        "A10 10 0 0 0 12 2z"),
    "orcid.org": ("ORCID",
        "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM8.2 7.1a.9.9 0 1 1 0 1.8.9.9 0 0 1 0"
        "-1.8zm.9 2.9v7H8v-7h1.1zm2.1 0h2.9c2.1 0 3.4 1.5 3.4 3.5s-1.4 3.5-3.4 3.5h-2.9"
        "v-7zm1.1 1v5h1.7c1.6 0 2.3-1.1 2.3-2.5s-.8-2.5-2.3-2.5h-1.7z"),
    "lattes.cnpq.br": ("Lattes",
        "M4 4h16v2H4V4zm0 5h16v2H4V9zm0 5h10v2H4v-2zm0 5h10v2H4v-2zM17 14l4 4-4 4v-3h-2"
        "v-2h2v-3z"),
    "linkedin.com": ("LinkedIn",
        "M4.98 3.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5zM3 9h4v12H3V9zm6 0h3.8v1.7h.05c"
        ".53-.95 1.83-1.95 3.75-1.95 4 0 4.4 2.5 4.4 5.9V21h-4v-5.5c0-1.3-.02-3-1.9-3"
        "-1.9 0-2.2 1.4-2.2 2.9V21H9V9z"),
    "researchgate.net": ("ResearchGate",
        "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-2.2 5.3h2.4c1.6 0 2.6.9 2.6 2.3 0 1.1"
        "-.6 1.9-1.6 2.2l2.1 3.4h-1.6l-1.9-3.2h-.9v3.2H9.8V7.3zm1.1 1v2.7h1.2c.9 0 1.5-.5"
        "1.5-1.4 0-.8-.5-1.3-1.5-1.3h-1.2z"),
    "instagram.com": ("Instagram",
        "M7.5 2h9A5.5 5.5 0 0 1 22 7.5v9A5.5 5.5 0 0 1 16.5 22h-9A5.5 5.5 0 0 1 2 16.5v-9"
        "A5.5 5.5 0 0 1 7.5 2zm0 2A3.5 3.5 0 0 0 4 7.5v9A3.5 3.5 0 0 0 7.5 20h9a3.5 3.5 0"
        "0 0 3.5-3.5v-9A3.5 3.5 0 0 0 16.5 4h-9zM12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10zm0 2a3"
        "3 0 1 0 0 6 3 3 0 0 0 0-6zm5.8-3.4a1.1 1.1 0 1 1 0 2.2 1.1 1.1 0 0 1 0-2.2z"),
    "twitter.com": ("X",
        "M3 3h5.4l4.3 5.9L17.9 3H21l-6.7 7.7L21.4 21H16l-4.6-6.3L5.9 21H3l7.1-8.2z"),
    "x.com": ("X",
        "M3 3h5.4l4.3 5.9L17.9 3H21l-6.7 7.7L21.4 21H16l-4.6-6.3L5.9 21H3l7.1-8.2z"),
    "youtube.com": ("YouTube",
        "M21.6 7.2a2.5 2.5 0 0 0-1.8-1.8C18.2 5 12 5 12 5s-6.2 0-7.8.4A2.5 2.5 0 0 0 2.4"
        "7.2C2 8.8 2 12 2 12s0 3.2.4 4.8a2.5 2.5 0 0 0 1.8 1.8C5.8 19 12 19 12 19s6.2 0"
        "7.8-.4a2.5 2.5 0 0 0 1.8-1.8C22 15.2 22 12 22 12s0-3.2-.4-4.8zM10 15V9l5.2 3L10 15z"),
    "": ("Site",
        "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm6.9 6h-2.9a15.7 15.7 0 0 0-1.3-3.6A8 8 0"
        "0 1 18.9 8zM12 4c.7 1 1.5 2.4 1.9 4h-3.8c.4-1.6 1.2-3 1.9-4zM4.3 14A8 8 0 0 1 4 12"
        "c0-.7.1-1.4.3-2h3.3a17.5 17.5 0 0 0 0 4H4.3zm.8 2H8a15.7 15.7 0 0 0 1.3 3.6A8 8 0 0"
        "1 5.1 16zM8 8H5.1a8 8 0 0 1 4.2-3.6A15.7 15.7 0 0 0 8 8zm4 12c-.7-1-1.5-2.4-1.9-4h3.8"
        "c-.4 1.6-1.2 3-1.9 4zm2.3-6H9.7a15.4 15.4 0 0 1 0-4h4.6a15.4 15.4 0 0 1 0 4zm.4 5.6"
        "a15.7 15.7 0 0 0 1.3-3.6h2.9a8 8 0 0 1-4.2 3.6zm2.7-5.6a17.5 17.5 0 0 0 0-4h3.3c.2.6"
        ".3 1.3.3 2s-.1 1.4-.3 2h-3.3z"),
}

MAX_LINKS = 8

# `esquema:` no começo da linha, na forma que a RFC 3986 define. Serve para
# distinguir "a pessoa digitou um esquema" de "a pessoa digitou só o host" —
# ver o comentário em `_limpar_links`.
_ESQUEMA_NO_INICIO = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")

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
                "foto": "", "atualizado": "", "links": []}
    d = dict(r)
    d["especies"] = _lista(d["especies"])
    d["marcadores"] = _lista(d["marcadores"])
    # `.get`: um banco que ainda não passou pela migração 2 não tem a coluna, e
    # a página não pode quebrar por causa disso.
    d["links"] = _links(d.get("links") or "[]")
    return d


def _rede_de(url: str) -> tuple[str, str]:
    """(nome, caminho do ícone) para um endereço. Cai no globo quando não casa."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        host = ""
    host = host.lower().removeprefix("www.")
    for chave, par in REDES.items():
        if chave and (host == chave or host.endswith("." + chave)):
            return par
    return REDES[""]


def _limpar_links(texto: str) -> list[dict]:
    """Um endereço por linha → lista de {url, nome, icone}.

    ⚠️ **Só `http` e `https` passam.** Sem esta checagem, `javascript:alert(1)`
    digitado aqui viraria um `href` clicável na própria página de perfil —
    XSS armazenado, que é exatamente o defeito corrigido no commit `bb72803`
    voltando por uma porta nova. O Jinja escapa o TEXTO, não o ESQUEMA de uma
    URL, então escapar não bastaria.

    Endereço sem esquema ganha `https://`: quem digita `github.com/fulano` está
    dando um endereço, e recusar por falta de prefixo seria exigir sintaxe.
    """
    # ⚠️ Separa por ESPAÇO EM BRANCO, e não por vírgula como os outros campos
    # desta tela: **endereço pode conter vírgula** (`.../a,b`), e quebrar por
    # ela parte um endereço legítimo em dois pedaços inválidos. Foi o teste do
    # `data:text/html;base64,...` que mostrou: a vírgula partia o token, o
    # esquema perigoso ficava numa metade e o base64 da outra virava um "site".
    # Endereço não tem espaço no meio, então esta é a separação certa aqui.
    saida, vistos = [], set()
    for linha in (texto or "").split():
        bruto = linha.strip()
        if not bruto:
            continue
        # ⚠️ O ESQUEMA É DECIDIDO ANTES DE COMPLETAR O ENDEREÇO. Completar
        # primeiro era um buraco meu, achado pelo teste: `javascript:alert(1)`
        # não tem `://`, virava `https://javascript:alert(1)` — que o urlparse
        # lê como esquema `https` e host `javascript` — e passava direto para o
        # `href`. Não executa script, mas põe `javascript:` dentro de um link da
        # nossa página, que é o oposto do que esta função existe para garantir.
        esquema = _ESQUEMA_NO_INICIO.match(bruto)
        if esquema:
            if esquema.group(1).lower() not in ("http", "https"):
                continue
        else:
            bruto = "https://" + bruto
        try:
            partes = urlparse(bruto)
        except ValueError:
            continue
        if partes.scheme not in ("http", "https") or not partes.hostname:
            continue
        url = bruto[:300]
        if url.lower() in vistos:
            continue
        vistos.add(url.lower())
        nome, icone = _rede_de(url)
        saida.append({"url": url, "nome": nome, "icone": icone})
        if len(saida) >= MAX_LINKS:
            break
    return saida


def _links(bruto: str) -> list[dict]:
    """Lê a coluna. Recalcula nome e ícone do URL em vez de confiar no gravado —
    assim uma rede nova em `REDES` passa a valer para quem já tinha salvado."""
    try:
        v = json.loads(bruto or "[]")
    except ValueError:
        return []
    saida = []
    for item in v[:MAX_LINKS]:
        url = (item or {}).get("url", "") if isinstance(item, dict) else ""
        if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https"):
            continue
        nome, icone = _rede_de(url)
        saida.append({"url": url, "nome": nome, "icone": icone})
    return saida


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
           marcadores: str = "", foto: str | None = None, links: str = "") -> dict:
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
        # Só o `url` vai para o disco: nome e ícone são derivados na leitura, e
        # gravá-los criaria uma cópia que envelhece quando `REDES` mudar.
        "links": json.dumps([{"url": x["url"]} for x in _limpar_links(links)],
                            ensure_ascii=False),
    }
    with conectar(sqlite_path) as con:
        con.execute(
            "INSERT INTO perfis (email,nome,laboratorio,instituicao,sobre,especies,"
            "marcadores,foto,atualizado,links) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET nome=excluded.nome,"
            "laboratorio=excluded.laboratorio,instituicao=excluded.instituicao,"
            "sobre=excluded.sobre,especies=excluded.especies,"
            "marcadores=excluded.marcadores,foto=excluded.foto,"
            "atualizado=excluded.atualizado,links=excluded.links",
            (email, dados["nome"], dados["laboratorio"], dados["instituicao"],
             dados["sobre"], dados["especies"], dados["marcadores"],
             dados["foto"], dados["atualizado"], dados["links"]))
    return pegar(sqlite_path, email)


def resumo_das_corridas(lotes: list[dict], n_amostras: list[int]) -> dict:
    """Contagens do que a conta tem no servidor. Só volume — nada de espécie.

    O que apareceu nas corridas fica FORA daqui de propósito: contar acertos do
    BLAST e exibir como perfil transformaria "o banco local achou isto" em "o
    laboratório trabalha com isto", que são afirmações diferentes.

    Recebe as CONTAGENS, e não os relatórios (2026-08-06): era a única coisa que
    esta função tirava deles, e exigir o relatório inteiro obrigava a página a
    decodificar 64 KB de JSON por corrida para somar inteiros.
    """
    prontos = [l for l in lotes if l.get("status") == "pronto"]
    return {"corridas": len(lotes), "corridas_prontas": len(prontos),
            "amostras": sum(n_amostras)}
