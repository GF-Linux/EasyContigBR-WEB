"""
executor.py — roda UM lote: a única ponte entre a web e o núcleo científico.

Toda a ciência mora em `app.core` (a biblioteca da ADR 0050). Este arquivo não
decide nada sobre montagem, identidade ou limiar — ele copia parâmetro, chama
`run_batch`, e escreve os três artefatos que o usuário baixa. Se um dia alguém
precisar mudar o que o relatório afirma, o lugar é `app/core/report.py`, nunca
aqui: é o que mantém web e desktop dizendo a mesma coisa sobre a mesma amostra.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from app.core import batch
from app.core.report import BatchReport, render_html
from app.core.tracy_engine import TracyEngine

from .config import Config


def pastas_do_lote(cfg: Config, lote_id: str) -> dict[str, Path]:
    raiz = cfg.lotes_dir / lote_id
    return {
        "raiz": raiz,
        "entrada": raiz / "entrada",     # os .ab1 como o usuário mandou
        "trabalho": raiz / "trabalho",   # saídas do tracy (intermediário)
        "relatorio_html": raiz / "relatorio.html",
        "relatorio_json": raiz / "relatorio.json",
        "resultado_csv": raiz / "resultado.csv",
    }


def _motor(cfg: Config) -> TracyEngine:
    return TracyEngine(cfg.tracy_bin) if cfg.tracy_bin else TracyEngine()


def executar(cfg: Config, lote_id: str, *, nome: str = "", n_esperado: int = 0,
             progresso=None) -> BatchReport:
    """Monta e identifica o lote já gravado em disco; grava HTML+JSON+CSV."""
    p = pastas_do_lote(cfg, lote_id)
    traces = batch.find_traces(p["entrada"])
    if not traces:
        raise ValueError("nenhum arquivo .ab1/.abi/.scf no lote")

    # Cinto e suspensório sobre a corrida do upload: se o que está em disco não
    # bate com o que o lote declarou, o certo é PARAR. Um relatório de 33 de 40
    # amostras não se distingue de um relatório de uma corrida com 33 — não há
    # como o usuário perceber a falta, então processar seria pior que falhar.
    if n_esperado and len(traces) != n_esperado:
        raise ValueError(
            f"o lote declarou {n_esperado} arquivo(s) e há {len(traces)} em disco; "
            f"envio incompleto — nada foi processado")

    p["trabalho"].mkdir(parents=True, exist_ok=True)

    # O blastn é localizado por `app.core.identify.resolve_blast_bin`, que lê
    # esta variável antes de procurar no PATH. Ajustar aqui em vez de exigir que
    # o operador exporte no shell certo mantém a configuração num lugar só.
    if cfg.blast_bin:
        import os
        os.environ["EASYCONTIG_BLAST_BIN"] = str(cfg.blast_bin)

    rep = batch.run_batch(
        traces,
        engine=_motor(cfg),
        out_dir=p["trabalho"],
        trim=cfg.trim,
        db_18s=cfg.db_18s,
        db_16s=cfg.db_16s,
        folder=nome or lote_id,
        progress=progresso,
    )

    p["relatorio_json"].write_text(rep.to_json(), encoding="utf-8")
    p["relatorio_html"].write_text(render_html(rep), encoding="utf-8")
    p["resultado_csv"].write_text(para_csv(rep), encoding="utf-8")
    return rep


def para_csv(rep: BatchReport) -> str:
    """Uma linha por amostra — o formato que o laboratório já recebeu na mão.

    `identidade_%` fica VAZIO quando não houve acerto, em vez de 0: zero é um
    número e seria lido como medida. A coluna `situacao_id` é que separa "achou",
    "procurou e não achou" e "não conseguiu procurar" (ADR 0047).
    """
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "amostra", "n_leituras", "consenso_pb", "cobertura_media",
        "pct_cobertura_1x", "organismo", "accession", "identidade_%",
        "cobertura_query_%", "e_value", "marcador", "situacao_id",
        "contig_invertido", "erro", "ressalvas",
    ])
    for s in rep.samples:
        w.writerow([
            s.key,
            len(s.reads),
            s.consensus_len,
            f"{s.mean_coverage:.2f}" if s.mean_coverage else "",
            f"{s.pct_cov1:.1f}" if s.consensus_len else "",
            s.organism or "",
            s.accession or "",
            f"{s.pct_identity:.3f}" if s.pct_identity is not None else "",
            f"{s.query_cover:.1f}" if s.query_cover is not None else "",
            s.e_value or "",
            s.id_source or "",
            s.id_status,
            "sim" if s.contig_flipped else "nao",
            s.error or "",
            " | ".join(c.text for c in s.caveats),
        ])
    return buf.getvalue()
