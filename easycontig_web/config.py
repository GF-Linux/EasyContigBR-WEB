"""
config.py — tudo que muda entre a máquina do autor, a VPS da universidade e o
contêiner vem daqui, por variável de ambiente.

Nada de caminho absoluto no código: o mesmo pacote roda no Deck (bancos e tracy
dentro do repo da demo) e na VPS (bancos num volume montado) só trocando o `.env`.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import retencao


def _path(nome: str, padrao: str | None = None) -> Path | None:
    v = os.environ.get(nome) or padrao
    return Path(v).expanduser() if v else None


@dataclass(frozen=True)
class Config:
    data_dir: Path            # onde ficam os lotes enviados e os resultados
    db_18s: Path | None       # prefixo do banco BLAST de eucariotos
    db_16s: Path | None       # prefixo do banco BLAST de bactérias
    tracy_bin: Path | None    # binário do Tracy (None = procura no PATH)
    blast_bin: Path | None    # PASTA que contém o blastn
    trim: int                 # trim padrão — 4 por decisão medida (ADR 0042)
    max_arquivos: int         # teto de arquivos por lote
    max_bytes: int            # teto de tamanho por lote
    dominio_permitido: str    # restrição de e-mail no login (vazio = qualquer)
    retencao_dias: int        # prazo do expurgo; 0 = guardar para sempre

    @property
    def lotes_dir(self) -> Path:
        return self.data_dir / "lotes"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "fila.sqlite3"


def conferir_producao() -> list[str]:
    """O que não pode ficar como está fora do desenvolvimento.

    Aviso em arquivo de exemplo não é trava: quem sobe o servidor lê o comando,
    não o `.env.example`. Estes três itens viram erro no arranque porque cada um
    é um jeito silencioso de o serviço nascer aberto.
    """
    problemas = []
    if os.environ.get("EASYCONTIG_AUTH", "dev").lower() == "dev":
        problemas.append(
            "EASYCONTIG_AUTH=dev aceita QUALQUER e-mail sem senha — nunca em servidor")
    if not os.environ.get("EASYCONTIG_SECRET_KEY"):
        problemas.append(
            "EASYCONTIG_SECRET_KEY não definida: a chave é regerada a cada arranque "
            "e todas as sessões caem junto")
    if os.environ.get("EASYCONTIG_HTTPS_ONLY", "0") != "1":
        problemas.append(
            "EASYCONTIG_HTTPS_ONLY=1 não está ligado: o cookie de sessão pode "
            "trafegar em claro")
    return problemas


def carregar() -> Config:
    cfg = Config(
        data_dir=_path("EASYCONTIG_DATA_DIR", "./dados"),
        db_18s=_path("EASYCONTIG_DB_18S"),
        db_16s=_path("EASYCONTIG_DB_16S"),
        tracy_bin=_path("EASYCONTIG_TRACY_BIN"),
        blast_bin=_path("EASYCONTIG_BLAST_BIN"),
        # 4 não é gosto: foi medido em 11 pares curados contra os contigs do CLC
        # e t=3–4 é o mínimo de erro/kb (ADR 0042). Não mexer sem premissa nova.
        trim=int(os.environ.get("EASYCONTIG_TRIM", "4")),
        max_arquivos=int(os.environ.get("EASYCONTIG_MAX_ARQUIVOS", "400")),
        # 400 arquivos × ~330 KB ≈ 130 MB. O .ab1 carrega o traço inteiro, não é
        # texto: um lote de 40 já são ~26 MB (ADR 0050).
        max_bytes=int(os.environ.get("EASYCONTIG_MAX_BYTES", str(300 * 1024 * 1024))),
        dominio_permitido=os.environ.get("EASYCONTIG_DOMINIO", ""),
        # O prazo em si é decisão do laboratório, não do código: `retencao.py`
        # trata valor ausente, ilegível e negativo, e nunca deduz "tudo vencido".
        retencao_dias=retencao.dias_configurados(),
    )
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.lotes_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def diagnostico(cfg: Config) -> list[tuple[str, bool, str]]:
    """(item, ok, detalhe) — o que está pronto e o que falta nesta instalação.

    Existe porque as três dependências externas (tracy, blastn, bancos) falham
    de formas parecidas e silenciosas. A ADR 0047 já registrou a armadilha:
    `blastn` ausente NÃO é "nenhum acerto" — e um servidor que confunde as duas
    coisas transforma problema de instalação em afirmação sobre a amostra.
    """
    itens = []

    tracy = cfg.tracy_bin or (shutil.which("tracy") and Path(shutil.which("tracy")))
    itens.append(("tracy", bool(tracy and Path(tracy).exists()),
                  str(tracy) if tracy else "não encontrado (defina EASYCONTIG_TRACY_BIN)"))

    if cfg.blast_bin:
        bn = cfg.blast_bin / "blastn"
        ok = bn.exists()
        det = str(bn) if ok else f"não existe: {bn}"
    else:
        w = shutil.which("blastn")
        ok, det = bool(w), (w or "não encontrado (defina EASYCONTIG_BLAST_BIN)")
    itens.append(("blastn", ok, det))

    for rotulo, db in (("banco 18S", cfg.db_18s), ("banco 16S", cfg.db_16s)):
        if not db:
            itens.append((rotulo, False, "não configurado"))
        else:
            ok = Path(str(db) + ".nsq").exists()
            itens.append((rotulo, ok, str(db) if ok else f"sem índice .nsq: {db}"))

    return itens
