'''

bancos_re.py é o catálogo de bancos de referência montados sob demanda do NCBI

1. Ideia - O laboratório trabalha com protozoarios ? utiliza essa ref, o objetivo é que a ref
seja montada sob demanda.

2. O motor foi curado para a RAIC -Reunião Anual de Iniciação Científica da UFRRJ - RAIC e RAIDTec 2026

3. Identidade maior não é resultado melhor.

4. O Genbank é dominio público, oq irá para o github é o critério.

#* Autor: Gustavo Gonçalves Freitas - LHV
#* Copyright (c) 2026 Gustavo Gonçalves Freitas. Todos os direitos reservados.

'''

from __future__ import annotations

import email
import email
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

#! NCNBI limita a 3 requisições por segundo sem chave API

PAUSA = 0.4

@dataclass(frozen=True)
class Conjunto:

    id: str
    nome: str
    grupo: str
    marcador: str
    termo: str
    aprox: int
    nota: str = ""



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



def pasta(data_dir: Path) -> Path:
    return data_dir / "bancos_re"

def prefixo(data_dir: Path, banco_id: str) -> Path:

    return pasta(data_dir) / banco_id / banco_id

def existe(data_dir: Path, banco_id: str) -> bool:
    return prefixo(data_dir, banco_id).with_suffix(".nsq").exists()


def _meta(data_dir: Path, banco_id: str) -> dict:
    p = prefixo(data_dir, banco_id).with_suffix(".meta.json")

    try :
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def estado(data_dir: Path, banco_id: str) -> dict:
    '''Devolve um dicionario com o estado do banco de referência.'''

    if not existe(data_dir, banco_id):

        return {"montado": False, "sequencias": 0, "baixado_em": "", "bytes": 0, "termo": ""}
    
    m = _meta(data_dir, banco_id)

    raiz = prefixo(data_dir, banco_id).parent

    bytes_ = sum(f.stat().st_size for f in raiz.glob("*") if f.is_file())

    return {"montado": True, "sequencias": m.get("sequencias"), "baixado_em": m.get("baixado_em", ""), "bytes": bytes_,
            "termo": m.get("termo", "")}


def _entrez(cgi: str, **kw) -> bytes:
    #? aqui é a função que faz a requisição ao NCBI, usando o EUTILS e o cgi apropriado, com os parâmetros passados em kw

    url = EUTILS + cgi + "?" + urllib.parse.urlencode(kw)

    with urllib.request.urlopen(url, timeout = 120) as r:
        return r.read()


_LIMITE_MAKEBLASTDB = 600.0
_LIMITE_BLASTN = 120.0


def _makeblastdb(blast_bin: Path | None, fasta: Path, saida: Path) -> None:
    #? aqui é o ponto de montagem do banco de referência, a partir do arquivo fasta baixado do NCBI

    exe = str(blast_bin / "makeblastdb") if blast_bin else "makeblastdb"

    try:
        subprocess.run([exe, "-in", str(fasta), "-dbtype", "nucl", "-parse_seqids", "-out", str(saida)], check=True, capture_output=True,
        timeout=_LIMITE_MAKEBLASTDB)

    except subprocess.TimeoutExpired as e:

        raise RuntimeError(
            f"a montagem do banco passou de {int(_LIMITE_MAKEBLASTDB // 60)} "
            "minutos e foi interrompida") from e


def montar(data_dir: Path, banco_id: str, blast_bin: Path | None = None, teto: int = 60000) -> dict:
    #? aqui é a função principal que monta o banco de referência, chamando as funções auxiliares para baixar os dados do NCBI, criar o arquivo fasta e montar o banco com makeblastdb


    c = POR_ID(banco_id)

    if not c:
        raise KeyError(banco_id)


    r = json.loads(_entrez("esearch.fcgi", db="nuccore", term=c.termo, retmax=0, usehistory="y", retmode="json"))['esearchresult']

    n = int(r['count'])

    if n == 0:
        raise RuntimeError(f'A consulta não devolveu nenhuma sequência')

    if n > teto:
        raise RuntimeError(f'A consulta devolveu {n} sequências, acima do teto de {teto}')


    time.sleep(PAUSA)
    destino = prefixo(data_dir, banco_id).parent
    destino.mkdir(parents=True, exist_ok=True)
    fasta = destino / f'{banco_id}.fasta'



    partes,passo = [], 5000

    for ini in range(0, n, passo):
        partes.append(_entrez("efetch.fcgi", db="nuccore", query_key=r["querykey"], WebEnv=r["webenv"], rettype="fasta", retmode="text", retstart=ini, retmax=passo).decode("utf-8", "replace"))
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
  
    fasta.unlink(missing_ok=True)
    return estado(data_dir, banco_id)


def remover(data_dir: Path, banco_id: str) -> bool:
    #? aqui é a função que remove o banco de referência montado, apagando os arquivos gerados

    import shutil

    if banco_id not in POR_ID and not _ID_MEU_OK.match(banco_id or ""):
        raise ValueError("banco desconhecido")
    raiz = prefixo(data_dir, banco_id).parent
    if not raiz.exists():
        return False
    shutil.rmtree(raiz, ignore_errors=True)

    return True



_NOME_OK = re.compile(r"^[A-Za-z0-9_-]{1,40}\Z")

_ID_MEU_OK = re.compile(r"^meu_[0-9a-z]{1,24}_[A-Za-z0-9_-]{1,40}\Z")



def espaco_da_conta(email: str) -> str:
    #? aqui é a função que cria um espaço de armazenamento para o usuário, baseado no hash do email

    normal = (email or "").strip().lower()
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()[:16]

def id_do_usuario(email: str, apelido: str) -> str:
    #? aqui é a função que cria um identificador único para o banco de referência do usuário, baseado no hash do email e no apelido fornecido
    
    if not _NOME_OK.match(apelido or ""):
        raise ValueError("use letras, números, hífen ou sublinhado (até 40)")
    return f"meu_{espaco_da_conta(email)}_{apelido}"



def montar_do_usuario(data_dir: Path, banco_id: str, fasta_texto: str, blast_bin: Path | None = None) -> dict:
    #? aqui é a função que monta um banco de referência a partir de um arquivo fasta enviado pelo usuário

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
    }, 
    ensure_ascii=False))

    return estado(data_dir, banco_id)


def meus_bancos(data_dir: Path, email: str) -> list[dict]:
    #? aqui é a função que lista os bancos de referência montados pelo usuário
    
    conta = espaco_da_conta(email)
    saida = []
    raiz = pasta(data_dir)
    if not raiz.exists():
        return saida
    for d in sorted(raiz.iterdir()):
        if d.is_dir() and d.name.startswith(f"meu_{conta}_"):
            e = estado(data_dir, d.name)
            if not e["montado"]:
                continue
            e["id"] = d.name
            e["apelido"] = d.name.split("_", 2)[2]
            saida.append(e)
    return saida


def consultar(consenso: str, prefixo_db: Path, blast_bin: Path | None = None, n: int = 5) -> list[dict]:
    #? aqui é a função que consulta o banco de referência montado, usando o blastn, e devolve os n melhores resultados
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
