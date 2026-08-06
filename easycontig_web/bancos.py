"""
bancos.py — catálogo de bancos de referência, montados sob demanda do NCBI.

A ideia, dita pelo autor em 2026-08-05: *"trabalhando com protozoários? usa essa
referência. Apicomplexa? essa. Cestódeos? essa."* — um catálogo por grupo, com o
pesquisador escolhendo o que consultar.

⚠️ **A REGRA QUE NÃO PODE CAIR:** nada aqui altera o resultado do motor. O banco
curado do RAIC 2026 continua sendo quem **identifica**, e é dele que saem espécie,
árvore NJ e as vistas 3D — a ADR 0037 registra que mexer nele muda tudo isso.
Estes bancos são **consulta adicional**, exibida à parte e rotulada como tal.
Ampliar a referência sem separar as duas coisas seria trocar um resultado
validado por outro sem ninguém decidir.

⚠️ **Identidade maior não é resultado melhor.** Medido: o mesmo consenso de
*Hepatozoon canis* dá 99,568 % contra o banco curado (16 refs) e 99,856 % contra
o de Adeleorina (311 refs) — mesma espécie, número maior só porque havia uma
referência mais próxima. É exatamente o que a ADR 0044 avisa, e a tela precisa
dizer isso quando o banco crescer.

Por que consulta ao NCBI e não um pacote pronto: GenBank é domínio público, SILVA
e BOLD restringem redistribuição (ADR 0044). Montando localmente a partir de uma
CONSULTA versionada, cada laboratório monta o seu — e o que vai para o git é o
critério, não o FASTA. Critério é revisável; blob de 80 MB não é.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

# O NCBI limita a 3 requisições por segundo sem chave de API (ADR 0052). Aqui há
# duas por banco (esearch + efetch), então o intervalo é folgado de propósito.
PAUSA = 0.4


@dataclass(frozen=True)
class Conjunto:
    id: str
    nome: str
    grupo: str            # como aparece agrupado na tela
    marcador: str
    termo: str            # a consulta Entrez — é ISTO que vai para o git
    aprox: int            # nº de sequências medido em 2026-08-05
    nota: str = ""


# Contagens medidas no NCBI em 2026-08-05. Servem para a tela dizer o tamanho
# ANTES de baixar; o número real do dia é o que vale, e a consulta é a verdade.
CATALOGO: tuple[Conjunto, ...] = (
    Conjunto("apicomplexa_18s", "Apicomplexa", "Protozoários", "18S rRNA",
             "txid5794[Organism:exp] AND 18S ribosomal RNA[Title] AND 1000:2500[SLEN]",
             4902, "cobre piroplasmas, coccídios e Hepatozoon de uma vez"),
    Conjunto("piroplasmida_18s", "Piroplasmida", "Protozoários", "18S rRNA",
             "txid5863[Organism:exp] AND 18S ribosomal RNA[Title] AND 1000:2500[SLEN]",
             1977, "Babesia, Theileria, Cytauxzoon"),
    Conjunto("coccidia_18s", "Coccidia", "Protozoários", "18S rRNA",
             "txid5796[Organism:exp] AND 18S ribosomal RNA[Title] AND 1000:2500[SLEN]",
             2222, "Eimeria, Toxoplasma, Cystoisospora, Sarcocystis"),
    Conjunto("adeleorina_18s", "Adeleorina", "Protozoários", "18S rRNA",
             "txid75740[Organism:exp] AND 18S ribosomal RNA[Title] AND 1000:2500[SLEN]",
             311, "Hepatozoon e afins"),
    Conjunto("trypanosomatidae_18s", "Trypanosomatidae", "Protozoários", "18S rRNA",
             "txid5654[Organism:exp] AND 18S ribosomal RNA[Title] AND 1000:2500[SLEN]",
             689, "Trypanosoma, Leishmania"),
    Conjunto("sar_18s", "Protozoários (SAR) — amplo", "Protozoários", "18S rRNA",
             "txid2698737[Organism:exp] AND 18S ribosomal RNA[Title] AND 1000:2500[SLEN]",
             25517, "o guarda-chuva grande; use quando não souber o grupo"),

    Conjunto("anaplasmataceae_16s", "Anaplasmataceae", "Bactérias", "16S rRNA",
             "txid942[Organism:exp] AND 16S ribosomal RNA[Title] AND 1000:2000[SLEN]",
             2895, "Anaplasma, Ehrlichia"),
    Conjunto("anaplasmataceae_groel", "Anaplasmataceae", "Bactérias", "groEL",
             "txid942[Organism:exp] AND groEL[Title]", 5621,
             "gene codificante — ⚠️ pede corte de identidade diferente do rRNA (ADR 0046)"),
    Conjunto("anaplasmataceae_dsb", "Anaplasmataceae", "Bactérias", "dsb",
             "txid942[Organism:exp] AND dsb[Title]", 256,
             "gene codificante — primer específico de Ehrlichia"),

    Conjunto("cestoda_18s", "Cestoda", "Helmintos", "18S rRNA",
             "txid6199[Organism:exp] AND 18S ribosomal RNA[Title] AND 1000:2500[SLEN]",
             1027, "tênias"),
    Conjunto("cestoda_coi", "Cestoda", "Helmintos", "COI",
             "txid6199[Organism:exp] AND (COI[Title] OR cytochrome c oxidase subunit 1[Title])"
             " AND 300:2000[SLEN]", 5922, "código de barras mitocondrial"),
    Conjunto("nematoda_18s", "Nematoda", "Helmintos", "18S rRNA",
             "txid6231[Organism:exp] AND 18S ribosomal RNA[Title] AND 1000:2500[SLEN]",
             5242),
    Conjunto("trematoda_28s", "Trematoda", "Helmintos", "28S rRNA",
             "txid6178[Organism:exp] AND 28S ribosomal RNA[Title] AND 500:3000[SLEN]",
             4909),

    Conjunto("vertebrados_18s", "Vertebrados (hospedeiro)", "Hospedeiro", "18S rRNA",
             "txid7742[Organism:exp] AND 18S ribosomal RNA[Title] AND 1500:2500[SLEN]",
             43802, "para reconhecer quando a leitura é DNA do animal (ADR 0041)"),
)

POR_ID = {c.id: c for c in CATALOGO}


# ─────────────────────────────────────────────────────────── onde mora em disco
def pasta(data_dir: Path) -> Path:
    return data_dir / "bancos"


def prefixo(data_dir: Path, banco_id: str) -> Path:
    return pasta(data_dir) / banco_id / banco_id


def existe(data_dir: Path, banco_id: str) -> bool:
    return prefixo(data_dir, banco_id).with_suffix(".nsq").exists()


def _meta(data_dir: Path, banco_id: str) -> dict:
    p = prefixo(data_dir, banco_id).with_suffix(".meta.json")
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def estado(data_dir: Path, banco_id: str) -> dict:
    """O que a tela precisa saber: está montado, com quantas, de quando.

    As chaves são SEMPRE as mesmas, montado ou não. Devolver um dicionário curto
    para o caso "não montado" quebrava a página inteira de bancos assim que
    sobrasse uma pasta pela metade — e a página quebrada não voltava sozinha.
    """
    if not existe(data_dir, banco_id):
        return {"montado": False, "sequencias": 0, "baixado_em": "",
                "bytes": 0, "termo": ""}
    m = _meta(data_dir, banco_id)
    raiz = prefixo(data_dir, banco_id).parent
    bytes_ = sum(f.stat().st_size for f in raiz.glob("*") if f.is_file())
    return {"montado": True, "sequencias": m.get("sequencias"),
            "baixado_em": m.get("baixado_em", ""), "bytes": bytes_,
            "termo": m.get("termo", "")}


# ───────────────────────────────────────────────────────────────── construção
def _entrez(cgi: str, **kw) -> bytes:
    url = EUTILS + cgi + "?" + urllib.parse.urlencode(kw)
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


# Teto de tempo dos programas externos. Nenhum tinha (verificado em 2026-08-06),
# e sem teto um processo que não termina fica pendurado PARA SEMPRE segurando o
# lugar dele — no caso do `blastn` de consulta, uma requisição web; no caso do
# `makeblastdb`, o envio de FASTA de alguém. O número é folgado: montar um banco
# de 43 mil sequências leva segundos, e o BLAST de um consenso, milissegundos.
_LIMITE_MAKEBLASTDB = 600.0     # 10 min: os bancos grandes do catálogo cabem
_LIMITE_BLASTN = 120.0          # 2 min: consulta de UM consenso


def _makeblastdb(blast_bin: Path | None, fasta: Path, saida: Path) -> None:
    exe = str(blast_bin / "makeblastdb") if blast_bin else "makeblastdb"
    # `-parse_seqids` sempre: sem ele o accession não sai no resultado, e foi a
    # armadilha registrada na ADR 0048.
    try:
        subprocess.run([exe, "-in", str(fasta), "-dbtype", "nucl", "-parse_seqids",
                        "-out", str(saida)], check=True, capture_output=True,
                       timeout=_LIMITE_MAKEBLASTDB)
    except subprocess.TimeoutExpired as e:
        # Mensagem própria: o texto do TimeoutExpired é críptico e esta é a
        # frase que a pessoa vê na tela de bancos.
        raise RuntimeError(
            f"a montagem do banco passou de {int(_LIMITE_MAKEBLASTDB // 60)} "
            "minutos e foi interrompida") from e


def montar(data_dir: Path, banco_id: str, blast_bin: Path | None = None,
           teto: int = 60000) -> dict:
    """Baixa o conjunto do NCBI e monta o banco BLAST. Devolve o estado.

    `teto` existe porque uma consulta pode crescer entre uma medição e outra: o
    catálogo diz ~43 mil para vertebrados, e se um dia virar 400 mil ninguém quer
    descobrir isso pelo disco cheio.
    """
    c = POR_ID.get(banco_id)
    if not c:
        raise KeyError(banco_id)

    r = json.loads(_entrez("esearch.fcgi", db="nuccore", term=c.termo, retmax=0,
                           usehistory="y", retmode="json"))["esearchresult"]
    n = int(r["count"])
    if n == 0:
        raise RuntimeError("a consulta não devolveu nenhuma sequência")
    if n > teto:
        raise RuntimeError(
            f"a consulta devolve {n} sequências, acima do teto de {teto}; "
            f"estreite o termo antes de baixar")

    time.sleep(PAUSA)
    destino = prefixo(data_dir, banco_id).parent
    destino.mkdir(parents=True, exist_ok=True)
    fasta = destino / f"{banco_id}.fasta"

    # Em lotes: o efetch de 40 mil registros de uma vez é onde a conexão cai, e
    # cair no meio deixaria um FASTA truncado com cara de completo — o mesmo
    # gênero do defeito das "33 de 40 amostras".
    partes, passo = [], 5000
    for ini in range(0, n, passo):
        partes.append(_entrez("efetch.fcgi", db="nuccore", query_key=r["querykey"],
                              WebEnv=r["webenv"], rettype="fasta", retmode="text",
                              retstart=ini, retmax=passo).decode("utf-8", "replace"))
        time.sleep(PAUSA)
    texto = "".join(partes)
    baixadas = sum(1 for l in texto.splitlines() if l.startswith(">"))
    if baixadas < n * 0.9:
        raise RuntimeError(
            f"o NCBI devolveu {baixadas} de {n} sequências; banco não montado")

    fasta.write_text(texto, encoding="utf-8")
    _makeblastdb(blast_bin, fasta, prefixo(data_dir, banco_id))
    from datetime import datetime, timezone
    (prefixo(data_dir, banco_id).with_suffix(".meta.json")).write_text(json.dumps({
        "sequencias": baixadas, "termo": c.termo,
        "baixado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, ensure_ascii=False))
    # O FASTA sai depois de montado: o banco BLAST basta, e guardar os dois
    # dobra o disco sem servir a ninguém.
    fasta.unlink(missing_ok=True)
    return estado(data_dir, banco_id)


def remover(data_dir: Path, banco_id: str) -> bool:
    import shutil
    if banco_id not in POR_ID and not banco_id.startswith("meu_"):
        raise ValueError("banco desconhecido")
    raiz = prefixo(data_dir, banco_id).parent
    if not raiz.exists():
        return False
    shutil.rmtree(raiz, ignore_errors=True)
    return True


# ────────────────────────────────────────────────── o banco do próprio usuário
_NOME_OK = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def espaco_da_conta(email: str) -> str:
    """O pedaço do nome da pasta que diz DE QUEM é o banco.

    ⚠️ ISTO JÁ VAZOU DADO ENTRE CONTAS. Era
    `re.sub(r"[^A-Za-z0-9]", "", email)[:24].lower()`, e colidia de duas formas
    independentes, as duas reproduzidas em 2026-08-06:

      * **o achatamento apaga o ponto** — `joao.silva@ufrrj.br` e
        `joaosilva@ufrrj.br` são duas pessoas diferentes e davam a MESMA pasta.
        Nem precisava de e-mail longo;
      * **o corte em 24** junta endereços que só diferem depois do 24º caractere.

    Colidir aqui não é detalhe de nomenclatura: `meus_bancos()` lista por
    prefixo, então a segunda conta **vê, consulta e pode REMOVER** o banco
    privado da primeira. E banco privado é exatamente onde ficam as sequências
    do grupo **ainda não publicadas** — o ativo que este serviço existe para não
    deixar vazar. Verificado ponta a ponta: a conta B enxergava o banco da conta
    A em "Meus bancos".

    Hash, e não e-mail legível: não colide na prática, não depende de o endereço
    ser curto, e de quebra tira o e-mail de dentro do nome da pasta — ele deixa
    de aparecer em listagem de diretório, backup e log.
    """
    normal = (email or "").strip().lower()
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()[:16]


def id_do_usuario(email: str, apelido: str) -> str:
    """Um banco por conta e apelido. Nada disso vira caminho sem passar pelo
    filtro de `_NOME_OK`."""
    if not _NOME_OK.match(apelido or ""):
        raise ValueError("use letras, números, hífen ou sublinhado (até 40)")
    return f"meu_{espaco_da_conta(email)}_{apelido}"


def montar_do_usuario(data_dir: Path, banco_id: str, fasta_texto: str,
                      blast_bin: Path | None = None) -> dict:
    """Monta o banco a partir de um FASTA que a pessoa enviou.

    É o caso de quem tem sequência do próprio grupo ainda não publicada: ela
    passa a poder comparar contra o que é dela sem que o dado saia da conta.
    """
    # Conta LINHA que começa com ">", não todo ">" do texto: um `>` dentro da
    # descrição (`>REF001 <img …>`) inflava o número mostrado na tela.
    n_seqs = sum(1 for l in fasta_texto.splitlines() if l.startswith(">"))
    if n_seqs == 0:
        raise ValueError("o arquivo não parece um FASTA (nenhuma linha começa com >)")
    destino = prefixo(data_dir, banco_id).parent
    destino.mkdir(parents=True, exist_ok=True)
    fasta = destino / "entrada.fasta"
    fasta.write_text(fasta_texto, encoding="utf-8")
    try:
        _makeblastdb(blast_bin, fasta, prefixo(data_dir, banco_id))
    except Exception:
        # Um envio que falha não pode deixar rastro: a pasta pela metade ficava
        # na listagem e derrubava a página de bancos toda vez que ela abrisse.
        import shutil
        shutil.rmtree(destino, ignore_errors=True)
        raise
    finally:
        fasta.unlink(missing_ok=True)
    from datetime import datetime, timezone
    (prefixo(data_dir, banco_id).with_suffix(".meta.json")).write_text(json.dumps({
        "sequencias": n_seqs, "termo": "enviado pelo usuário",
        "baixado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, ensure_ascii=False))
    return estado(data_dir, banco_id)


def meus_bancos(data_dir: Path, email: str) -> list[dict]:
    conta = espaco_da_conta(email)
    saida = []
    raiz = pasta(data_dir)
    if not raiz.exists():
        return saida
    for d in sorted(raiz.iterdir()):
        if d.is_dir() and d.name.startswith(f"meu_{conta}_"):
            e = estado(data_dir, d.name)
            if not e["montado"]:
                # pasta órfã de um envio que falhou: não é banco, não se lista
                continue
            e["id"] = d.name
            e["apelido"] = d.name.split("_", 2)[2]
            saida.append(e)
    return saida


# ────────────────────────────────────────────────────────────────── consulta
def consultar(consenso: str, prefixo_db: Path, blast_bin: Path | None = None,
              n: int = 5) -> list[dict]:
    """BLAST do consenso contra UM banco. Só devolve os acertos — não conclui.

    Isto NÃO substitui a identificação: o veredito continua saindo do banco
    curado, por `app.core.identify`. Aqui é consulta paralela, e quem chama
    rotula como tal.
    """
    exe = str(blast_bin / "blastn") if blast_bin else "blastn"
    try:
        r = subprocess.run(
            [exe, "-db", str(prefixo_db), "-outfmt",
             "6 sacc stitle pident qcovs evalue bitscore length",
             "-max_target_seqs", str(n), "-num_threads", "1"],
            input=f">consulta\n{consenso}\n", text=True, capture_output=True,
            timeout=_LIMITE_BLASTN)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("a consulta ao banco passou do tempo limite") from e
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "blastn falhou").strip()[:300])
    saida = []
    for linha in r.stdout.splitlines()[:n]:
        p = linha.split("\t")
        if len(p) < 7:
            continue
        saida.append({"accession": p[0], "titulo": p[1][:150],
                      "identidade": round(float(p[2]), 1),
                      "cobertura": round(float(p[3]), 1), "e_value": p[4],
                      "bits": p[5], "alinhado": int(p[6])})
    return saida
