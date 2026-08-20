#? EXECUTOR DE ÁRVORE — Decisão sobre a fronteira com a ciência 19/08/2026
#!
#! 1. Roda UMA árvore filogenética sobre um lote já montado.
#! 2. Como o `executor_de_lote`: nada de ciência aqui. Quem alinha, apara,
#!    avalia e infere é `app/core/phylogeny.py` — este arquivo junta os
#!    consensos, descobre o marcador de cada um e desenha o resultado.
#! 3. O desenho FICA deste lado de propósito. `phylogeny.py` não importa
#!    matplotlib pelo mesmo motivo que `app/core/` não importa Qt: quem instala
#!    o núcleo num servidor não deve baixar uma pilha de gráficos, e o desktop
#!    desenha do jeito dele.
from __future__ import annotations

import re
from pathlib import Path

from app.core import phylogeny as filo

from ..configuracao import Config
from ..dados import bancos_de_referencia as bancos
from .executor_de_lote import pastas_do_lote

# Os marcadores que o catálogo conhece, como aparecem no NOME do arquivo. A
# lista sai de `bancos.CATALOGO` para não haver duas verdades sobre o que o
# EasyContig sabe identificar — acrescentar um banco novo passa a ensinar o
# reconhecedor de nomes junto.
def marcadores_conhecidos() -> list[str]:
    fichas = {c.marcador for c in bancos.CATALOGO}
    # "16S rRNA" → "16S": no nome do arquivo o laboratório escreve o gene, não a
    # descrição do banco.
    return sorted({f.split()[0] for f in fichas}, key=len, reverse=True)


def marcador_da_amostra(nome: str, reserva: str = "") -> str:
    """De que marcador é esta amostra.

    ⚠️ **O nome do arquivo vem primeiro, e o banco do lote é só a reserva.**
    Parece o contrário do óbvio — o lote inteiro foi identificado contra UM
    banco — mas a corrida real F13719 desmente o óbvio: as 40 amostras subiram
    numa pasta só e eram de QUATRO marcadores (16S, groEL, dsb, sodB), com o
    gene escrito no nome (`F13719_am24_CAP08_groEL_E08-F08`). Confiar no banco
    do lote juntaria os quatro numa árvore só, que é exatamente o erro que este
    fluxo inteiro existe para impedir.

    Sem nada reconhecível no nome e sem reserva, devolve `desconhecido` — e
    nesse caso todas caem no mesmo grupo, que é honesto: ou o laboratório
    nomeia, ou o programa não tem como saber.
    """
    for m in marcadores_conhecidos():
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(m)}(?![A-Za-z0-9])",
                     nome, re.IGNORECASE):
            return m
    return (reserva.split()[0] if reserva else "") or "desconhecido"


def _ler_fasta(caminho: Path) -> list[tuple[str, str]]:
    nome, buf, saida = None, [], []
    for linha in caminho.read_text(errors="replace").splitlines():
        linha = linha.strip()
        if linha.startswith(">"):
            if nome:
                saida.append((nome, "".join(buf)))
            nome, buf = linha[1:].split()[0], []
        elif linha:
            buf.append(linha)
    if nome:
        saida.append((nome, "".join(buf)))
    return saida


def consensos_do_lote(cfg: Config, lote_id: str, referencia: str = ""
                      ) -> list[filo.Consensus]:
    """Junta os consensos que o lote já montou, cada um com seu marcador.

    Lê os `<amostra>.cons.fa` que o tracy deixou em `trabalho/` — os mesmos
    arquivos que o laboratório hoje copia à mão para o MEGA. A amostra dá o
    nome ao táxon (e não o cabeçalho de dentro do FASTA, que é `>consenso`
    para todas).
    """
    p = pastas_do_lote(cfg, lote_id)
    rotulo = ""
    ficha = bancos.POR_ID.get(referencia)
    if ficha:
        rotulo = ficha.marcador

    saida: list[filo.Consensus] = []
    for arquivo in sorted(p["trabalho"].glob("*.cons.fa")):
        amostra = arquivo.name[:-len(".cons.fa")]
        registros = _ler_fasta(arquivo)
        if not registros:
            continue
        sequencia = registros[0][1].upper().replace("-", "")
        if not sequencia:
            continue
        saida.append(filo.Consensus(name=amostra, sequence=sequencia,
                                    marker=marcador_da_amostra(amostra, rotulo)))
    return saida


def desenhar(resultado: filo.TreeResult, destino: Path,
             mapa: dict[str, str] | None = None) -> Path | None:
    """A figura da árvore, com a probabilidade posterior nos ramos.

    Só desenha o que o MrBayes sustentou: rótulo de ramo apenas para pp ≥ 0,90.
    Encher a figura com 0,51 daria ao olho a impressão de estrutura onde o
    próprio método diz que não há — o inverso do que este fluxo promete.

    Devolve None se não der para desenhar. Figura é conforto; o `.newick` e o
    `.con.tre` são o resultado, e uma falha de desenho não pode derrubar um
    pedido que a ciência já respondeu.
    """
    if not resultado.consensus_tree or not resultado.consensus_tree.exists():
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from io import StringIO
        from Bio import Phylo

        newick = filo.read_consensus_tree(resultado.consensus_tree, mapa)
        arvore = Phylo.read(StringIO(newick), "newick")
        for no in arvore.get_nonterminals():
            if no.name:
                try:
                    no.confidence = float(no.name)
                except ValueError:
                    pass
                no.name = None

        a = resultado.assessment
        fig, eixo = plt.subplots(
            figsize=(9, max(3.5, 0.42 * len(arvore.get_terminals()))))
        Phylo.draw(arvore, axes=eixo, do_show=False,
                   branch_labels=lambda c: (
                       f"{c.confidence:.2f}"
                       if (not c.is_terminal() and c.confidence
                           and c.confidence >= 0.90) else ""))
        eixo.set_title(
            f"{resultado.marker} — {a.n_taxa} amostras · {a.columns_after} pb"
            f" · identidade mediana {a.identity_median:.1f}%\n{resultado.verdict}",
            fontsize=9)
        eixo.set_xlabel("substituições por sítio")
        for lado in ("top", "right"):
            eixo.spines[lado].set_visible(False)
        fig.tight_layout()
        fig.savefig(destino, dpi=140)
        plt.close(fig)
        return destino
    except Exception:                           # noqa: BLE001
        return None


def executar(cfg: Config, lote_id: str, *, referencia: str = "",
             ngen: int = 5_000_000, etapa=None) -> list[dict]:
    """Monta uma árvore por marcador e devolve o resumo que a tela mostra.

    O resumo é uma lista de dicionários simples (e não os objetos do núcleo)
    porque ele é serializado para o banco e lido pelo template — atravessa
    processo, e o que atravessa processo tem de ser dado, não objeto.
    """
    p = pastas_do_lote(cfg, lote_id)
    consensos = consensos_do_lote(cfg, lote_id, referencia)
    if not consensos:
        raise ValueError(
            "nenhum consenso encontrado neste lote; a árvore precisa das "
            "amostras já montadas")

    saida_dir = p["raiz"] / "arvore"
    saida_dir.mkdir(parents=True, exist_ok=True)

    resumo: list[dict] = []
    for grupo in filo.group_by_marker(consensos):
        if etapa:
            etapa(f"{grupo.marker} ({len(grupo.members)} amostras)")
        if not grupo.buildable:
            resumo.append({"marcador": grupo.marker, "n": len(grupo.members),
                           "recusado": grupo.skipped})
            continue

        pasta = saida_dir / filo.safe_name(grupo.marker)
        r = filo.build_tree(grupo, pasta, ngen=ngen)
        a = r.assessment

        # O mapa nome-seguro → nome-do-laboratório é reconstruído aqui porque
        # `build_tree` o consome por dentro; sem ele a figura sairia com o nome
        # mutilado que só o MrBayes entende.
        mapa = {filo.safe_name(c.name): c.name for c in grupo.members}
        newick = ""
        if r.consensus_tree:
            newick = filo.read_consensus_tree(r.consensus_tree, mapa)
            (pasta / "arvore.newick").write_text(newick, encoding="utf-8")
        figura = desenhar(r, pasta / "arvore.png", mapa)

        resumo.append({
            "marcador": grupo.marker,
            "n": a.n_taxa,
            "colunas": a.columns_after,
            "colunas_antes": a.columns_before,
            "identidade": round(a.identity_median, 1),
            "asdsf": r.asdsf,
            "convergiu": r.converged,
            "veredito": r.verdict,
            "avisos": a.warnings,
            "figura": figura.name if figura else "",
            "newick": "arvore.newick" if newick else "",
            "pasta": filo.safe_name(grupo.marker),
        })
    return resumo


def caminho_de_saida(cfg: Config, lote_id: str, pasta: str, arquivo: str) -> Path:
    """Um arquivo da árvore, com o nome conferido em vez de confiado.

    Mesma regra do `pastas_do_lote`: quem decide onde se lê não pode depender
    de o chamador ter validado antes. `pasta` e `arquivo` vêm da URL.
    """
    for parte in (pasta, arquivo):
        if not parte or parte in (".", "..") or "/" in parte or "\\" in parte \
                or "\0" in parte or parte != Path(parte).name:
            raise ValueError(f"caminho inválido: {parte!r}")
    return pastas_do_lote(cfg, lote_id)["raiz"] / "arvore" / pasta / arquivo
