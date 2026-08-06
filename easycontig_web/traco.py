"""
traco.py — o cromatograma de uma amostra, pronto para o navegador.

Por que existe: a página da amostra mostrava só números. Editar base olhando o
pico é o que faz a ferramenta servir para bancada — *"algumas bases podem estar
ruins, principalmente nas pontas"* (ADR 0052).

**Nada de ciência mora aqui.** Quem alinha o traço às colunas do consenso é
`app.core.assembly.chromatogram_data_columns`, que já devolve o traço em
coordenada de COLUNA — e é por isso que F e R saem acoplados de graça: as duas
leituras passam a compartilhar o mesmo eixo x. Este módulo só remonta o objeto e
serializa.

⚠️ **Remontar custa ~0,3 s** (medido no Deck) porque o `tracy consensus` roda de
novo. É feito sob demanda, ao abrir a amostra, e não guardado: o `.bc.json` de um
par são ~760 KB em disco contra 108 KB no fio, e o que o lote guarda já é o
suficiente para o relatório. Tempo é mais barato que volume aqui.
"""
from __future__ import annotations

import math
from pathlib import Path

from app.core.assembly import (build_pair_assembly, chromatogram_data_columns,
                               parse_assembly)
from app.core.tracy_engine import TracyEngine

from .config import Config


def _motor(cfg: Config) -> TracyEngine:
    return TracyEngine(cfg.tracy_bin) if cfg.tracy_bin else TracyEngine()


def amostra_do_relatorio(rep: dict, key: str) -> dict | None:
    for s in rep.get("samples") or []:
        if isinstance(s, dict) and s.get("key") == key:
            return s
    return None


def montar(cfg: Config, lote_dir: Path, amostra: dict):
    """Remonta o Assembly da amostra a partir dos `.ab1` que ainda estão na pasta.

    `None` quando não dá — arquivo apagado pela retenção, amostra que não montou,
    ou grupo que não é um par F+R. Quem chama transforma isso em "sem
    cromatograma para mostrar", nunca em erro: a página de números continua
    valendo.
    """
    leituras = [r for r in (amostra.get("reads") or []) if isinstance(r, dict)]
    if len(leituras) < 2:
        return None

    trabalho = lote_dir / "trabalho"

    # Amostra com MAIS de duas leituras (a pasta do Hepatozoon tem 4 por amostra,
    # dois pares de primers): o lote a montou com `engine.assemble`, que grava um
    # `.json` com os traços já alinhados. Reaproveitar esse arquivo é melhor que
    # remontar — sai de graça e é exatamente o que o relatório usou.
    if len(leituras) > 2:
        j = trabalho / f"{amostra['key']}.json"
        if not j.exists():
            return None
        try:
            return parse_assembly(j)
        except Exception:                   # noqa: BLE001
            return None

    def caminho(r: dict) -> Path | None:
        p = Path(r.get("file") or "")
        if p.is_absolute() and p.exists():
            return p
        # o relatório guarda caminho relativo à raiz do servidor; se o lote foi
        # movido de volume, o nome do arquivo dentro do próprio lote ainda vale
        alt = lote_dir / "entrada" / p.name
        return alt if alt.exists() else None

    f = next((r for r in leituras if (r.get("orient_content") or
                                      r.get("orient_name")) == "F"), None)
    r_ = next((r for r in leituras if r is not f), None)
    if not f or not r_:
        return None
    cf, cr = caminho(f), caminho(r_)
    if not cf or not cr:
        return None

    trabalho.mkdir(parents=True, exist_ok=True)
    try:
        return build_pair_assembly(_motor(cfg), cf, cr,
                                   trabalho / amostra["key"], trim=cfg.trim)
    except Exception:                       # noqa: BLE001
        # Traço é acessório: se o tracy falhar aqui, a página de números segue.
        return None


def _linha(pos: list[float], picos: list[float], alt: int = 100,
           topo: float = 1.0) -> str:
    """Uma polilinha SVG em coordenada de coluna. `NaN` vira quebra de traço.

    A quebra existe porque `chromatogram_data_columns` marca com NaN a coluna em
    que a leitura tem gap: desenhar por cima ligaria dois picos que não são
    vizinhos no dado, e o traço passaria a afirmar uma continuidade inexistente.
    """
    partes, atual = [], []
    for x, y in zip(pos, picos):
        if y is None or (isinstance(y, float) and math.isnan(y)):
            if len(atual) > 1:
                partes.append(" ".join(atual))
            atual = []
            continue
        atual.append(f"{x:.2f},{alt - min(y / topo, 1.0) * alt * 0.94:.1f}")
    if len(atual) > 1:
        partes.append(" ".join(atual))
    return "|".join(partes)          # o navegador quebra em "|" e faz N polilinhas


def para_navegador(asm, amostra: dict, passo: int = 2) -> dict | None:
    """Alinhamento + traço das duas leituras, no mesmo eixo x (coluna).

    `passo` reduz a resolução do traço: 1 ponto em 2 é indistinguível na tela e
    corta o peso pela metade. O traço inteiro de um par são ~108 KB comprimidos
    (medido); com passo 2, metade disso.
    """
    if asm is None or len(asm.reads) < 2:
        return None

    leituras, brutos, topos = [], {}, {}
    for r in asm.reads:
        d = chromatogram_data_columns(asm, r.name)
        if not d:
            return None
        brutos[r.name] = d
        # Amplitude POR LEITURA e pelo PERCENTIL 95, não pelo máximo.
        #
        # Duas correções, ambas medidas nesta amostra:
        # (a) por leitura, porque F e R saem de corridas diferentes e a altura do
        #     pico é do aparelho, não da amostra — normalizar as duas pelo mesmo
        #     topo achatava a mais fraca;
        # (b) pelo p95, porque um único pico saturado domina a escala: a leitura
        #     R tem máximo 1490 e p95 de 110 — 13× —, e com o máximo como topo
        #     **92,7% do traço ficava abaixo de 5% da altura**, ou seja, uma
        #     linha reta. A tela afirmava "não há sinal" onde havia sinal.
        # O que passa do topo é ceifado no desenho (`min(y/topo, 1)`); perder a
        # ponta de um pico saturado custa menos que perder o traço inteiro.
        vals = sorted(v for k in ("peakA", "peakC", "peakG", "peakT")
                      for v in d[k] if v is not None and not math.isnan(v))
        topos[r.name] = vals[int(len(vals) * 0.95)] if vals else 1.0

    for r in asm.reads:
        d = brutos[r.name]
        pos = d["pos"][::passo]
        canais = {c: _linha(pos, d["peak" + c][::passo], topo=topos[r.name])
                  for c in "ACGT"}
        info = next((x for x in (amostra.get("reads") or [])
                     if isinstance(x, dict) and x.get("name") == r.name), {})
        leituras.append({
            "nome": r.name,
            "sentido": "F" if r.forward else "R",
            "primer": info.get("primer") or "",
            "q_medio": info.get("mean_q"),
            "q_rotulo": info.get("q_label") or "",
            "canais": canais,
            # uma entrada por base: coluna + letra + qualidade Phred
            "bases": [[float(c), s, q] for c, s, q in
                      zip(d["basecallPos"], d["primarySeq"], d["basecallQual"])],
            "alinhada": r.aligned,
        })

    largura = len(asm.consensus)
    mism = [c for c in range(largura)
            if len({r.aligned[c].upper() for r in asm.reads
                    if c < len(r.aligned) and r.aligned[c] not in "-. N"}) > 1]
    return {
        "largura": largura,
        "consenso": asm.consensus,
        "consenso_pb": len(asm.consensus_nogap),
        "cobertura": asm.coverage,
        "mismatches": mism,
        "leituras": leituras,
    }
