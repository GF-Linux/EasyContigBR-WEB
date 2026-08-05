"""
amostras.py — a leitura por amostra do `relatorio.json` já gravado no lote.

Existe porque a corrida real tem 40 amostras e a única forma de olhar UMA era
abrir o relatório inteiro e procurar com Ctrl+F. Aqui não se monta nem se
identifica nada: o arquivo já está em disco, escrito por `app/core/report.py`,
e este módulo só o formata para a tela.

Nenhuma decisão científica mora aqui. Em particular:

* os limiares não são reescritos — cada ressalva carrega o seu em `rule`, e é
  esse texto que a tela imprime ao lado do fato (o limiar não pode ser folclore,
  e repeti-lo neste arquivo criaria uma segunda fonte para o mesmo número);
* ausência continua ausência: `pct_identity`, `query_cover`, `e_value` e
  `mean_coverage` viram string vazia quando não há medida, nunca `0` — zero é um
  número e seria lido como medida (a mesma regra de `executor.para_csv`);
* "não achou" e "não conseguiu procurar" são coisas diferentes e saem com textos
  e cores diferentes (ADR 0047); nenhuma das duas usa o vermelho de falha, que
  fica reservado para a amostra que não montou.

Os nomes dos campos acompanham as colunas de `executor.para_csv` — duas telas do
mesmo sistema não podem chamar a mesma coisa por nomes diferentes.
"""
from __future__ import annotations

import json
from pathlib import Path

# Como cada `id_status` de `app/core/report.py` aparece na tela: texto, classe de
# cor (as de `base.html`) e a frase que diz de QUEM a situação fala.
#
# `sem_acerto` é NEUTRO de propósito. Numa corrida real de 40 amostras, 19 vêm
# de marcadores groEL/dsb/sodB — não são rRNA, então não estão num banco 18S/16S
# e não terem acerto é o resultado CERTO. Pintar essas 19 de vermelho seria
# afirmar uma falha que não houve.
#
# `blast_indisponivel` e `sem_banco` usam o âmbar de "pendente" porque falam da
# instalação, não da amostra: há o que consertar, e não é o material do usuário.
SITUACOES: dict[str, tuple[str, str, str]] = {
    "ok": (
        "acerto no banco local", "pronto",
        "Melhor acerto do BLAST dentro do banco local consultado — uma semelhança "
        "com o que existe nesse banco, não uma identificação de espécie.",
    ),
    "sem_acerto": (
        "sem acerto no banco local", "sub",
        "O banco local foi consultado e nenhuma referência foi alcançada. Isso "
        "descreve o banco consultado, não a amostra.",
    ),
    "blast_indisponivel": (
        "busca não executada — blastn indisponível", "rodando",
        "A busca no banco local não chegou a rodar porque o blastn não pôde ser "
        "chamado. Fala da instalação, não do conteúdo da amostra.",
    ),
    "sem_banco": (
        "busca não executada — sem banco local", "rodando",
        "Nenhum banco de referência local estava disponível para consulta. Fala "
        "da instalação, não do conteúdo da amostra.",
    ),
    # Achado testando em 2026-08-05: uma amostra que não montou (Tracy falhou,
    # consenso de 0 pb) recebia a pílula de `sem_acerto` — mesma palavra e mesma
    # cor das 19 cujo "sem acerto" é o resultado CERTO —, e ainda entrava naquela
    # contagem. É a afirmação falsa mais cara possível aqui: dizer que o banco
    # foi consultado quando não houve o que consultar. Situação própria, e
    # vermelha, porque desta vez a falha é real.
    "nao_montou": (
        "não montou — nada foi consultado", "falhou",
        "A montagem do consenso falhou, então não houve sequência para procurar "
        "no banco. Isto NÃO é 'sem acerto': nada chegou a ser consultado.",
    ),
}

# A ordem em que as situações aparecem no cabeçalho. Fixa, para o cabeçalho não
# mudar de forma entre um lote e outro.
ORDEM_SITUACOES = ("ok", "sem_acerto", "blast_indisponivel", "sem_banco",
                   "nao_montou")


def carregar(relatorio_json: Path) -> dict | None:
    """Lê o `relatorio.json` do lote. `None` se não existe ou não serve.

    Não levanta: um relatório ilegível é assunto da página, que diz "relatório
    indisponível", e não motivo para derrubar a requisição inteira. Também
    devolve `None` para um JSON válido que não tenha a forma de um relatório —
    aceitá-lo daria uma tela vazia se passando por lote sem amostras.
    """
    try:
        texto = Path(relatorio_json).read_text(encoding="utf-8")
        rep = json.loads(texto)
    except (OSError, ValueError):
        return None
    if not isinstance(rep, dict) or not isinstance(rep.get("samples"), list):
        return None
    return rep


def resumo(rep: dict) -> dict:
    """Contagens do lote para o cabeçalho. Só contagem — nada de nota ou score.

    `situacoes` já vem pronta para a tela (texto, classe e a frase de contexto),
    e só com as situações que realmente ocorreram: um "0 sem acerto" impresso
    sugeriria que alguém esperava o contrário.
    """
    amostras = _amostras(rep)
    por_situacao: dict[str, int] = {}
    for s in amostras:
        codigo = _situacao(s)
        por_situacao[codigo] = por_situacao.get(codigo, 0) + 1

    conhecidas = [c for c in ORDEM_SITUACOES if c in por_situacao]
    outras = sorted(c for c in por_situacao if c not in ORDEM_SITUACOES)
    situacoes = []
    for codigo in conhecidas + outras:
        texto, classe, nota = _rotulo_situacao(codigo)
        situacoes.append({"codigo": codigo, "texto": texto, "classe": classe,
                          "nota": nota, "n": por_situacao[codigo]})

    return {
        "total": len(amostras),
        "por_situacao": por_situacao,
        "situacoes": situacoes,
        "com_ressalva": sum(1 for s in amostras if s.get("caveats")),
        "com_erro": sum(1 for s in amostras if s.get("error")),
        "com_contig_invertido": sum(1 for s in amostras if s.get("contig_flipped")),
        # contexto do lote, copiado do topo do relatório
        "pasta": rep.get("folder") or "",
        "gerado_em": rep.get("generated_at") or "",
        "n_arquivos": rep.get("n_files") or 0,
        "motor": rep.get("engine") or "",
        "trim": rep.get("trim") or 0,
        "banco": rep.get("db") or "",
        "notas": [n for n in (rep.get("notes") or []) if n],
    }


def listar(rep: dict) -> list[dict]:
    """As amostras já formatadas para a tabela, na ordem do relatório."""
    return [_formatar(s) for s in _amostras(rep)]


def amostra(rep: dict, key: str) -> dict | None:
    """Uma amostra pelo `key`, na mesma formatação da tabela. `None` se não há.

    Mesma formatação de propósito: a linha da tabela e a página da amostra não
    podem exibir números diferentes para a mesma medida.
    """
    for s in _amostras(rep):
        if s.get("key") == key:
            return _formatar(s)
    return None


# ------------------------------------------------------------------ internos
def _amostras(rep: dict) -> list[dict]:
    itens = rep.get("samples") if isinstance(rep, dict) else None
    return [s for s in (itens or []) if isinstance(s, dict)]


def _situacao(s: dict) -> str:
    """A situação da IDENTIFICAÇÃO — mas a falha de montagem ganha de tudo.

    A ordem importa e é o conserto de um defeito real: o `id_status` que
    `report.py` grava numa amostra que nem montou continua sendo `sem_acerto`,
    porque do ponto de vista do BLAST não houve acerto mesmo. Só que na tela
    isso vira a MESMA frase das amostras cujo "sem acerto" é o resultado certo,
    e passa a afirmar que o banco foi consultado. Não foi: não havia consenso.
    """
    if s.get("error") or not s.get("consensus_len"):
        return "nao_montou"
    return s.get("id_status") or "sem_acerto"


def _rotulo_situacao(codigo: str) -> tuple[str, str, str]:
    # Situação desconhecida (relatório mais novo que esta tela) aparece com o
    # código cru e sem cor: melhor exibir algo ilegível do que traduzi-lo errado.
    return SITUACOES.get(codigo, (codigo, "sub", ""))


def _num(valor, casas: int) -> str:
    """Número com casas fixas — string VAZIA quando não há medida.

    O `0` é o inimigo aqui: preencher a ausência com zero transforma "não medi"
    em "medi e deu zero", que é uma afirmação que ninguém fez.
    """
    if valor is None:
        return ""
    try:
        return f"{float(valor):.{casas}f}"
    except (TypeError, ValueError):
        return ""


def _formatar(s: dict) -> dict:
    # Dois códigos de propósito, e eles PODEM divergir:
    # `situacao_id` é o que `report.py` gravou e o que sai no CSV — a paridade
    # entre as duas saídas é testada e tem que continuar valendo.
    # `situacao` é o que a tela mostra, e numa amostra que não montou ele diz
    # `nao_montou` onde o CSV ainda diz `sem_acerto`. A divergência é o conserto,
    # não um descuido: no CSV existe a coluna `erro` ao lado para desfazer a
    # ambiguidade, e na tela não existia nada.
    codigo_bruto = s.get("id_status") or "sem_acerto"
    codigo = _situacao(s)
    texto, classe, nota = _rotulo_situacao(codigo)
    consenso = s.get("consensus_len") or 0
    cobertura = s.get("mean_coverage")
    ressalvas = [
        {"codigo": c.get("code") or "", "texto": c.get("text") or "",
         "regra": c.get("rule") or ""}
        for c in (s.get("caveats") or []) if isinstance(c, dict)
    ]
    return {
        "key": s.get("key") or "",
        # ---- montagem (mesmos valores e mesma formatação do CSV)
        "n_leituras": len(s.get("reads") or []),
        "consenso_pb": consenso,
        "cobertura_media": _num(cobertura, 2) if cobertura else "",
        "n_cobertura_1": s.get("n_cov1") or 0,
        "pct_cobertura_1x": _num(s.get("pct_cov1"), 1) if consenso else "",
        "discordancias": s.get("n_corrected") or 0,
        "contig_invertido": "sim" if s.get("contig_flipped") else "nao",
        "invertido": bool(s.get("contig_flipped")),
        # ---- identificação
        "organismo": s.get("organism") or "",
        "accession": s.get("accession") or "",
        "identidade": _num(s.get("pct_identity"), 3),
        "cobertura_query": _num(s.get("query_cover"), 1),
        "e_value": s.get("e_value") or "",
        "marcador": s.get("id_source") or "",
        "situacao_id": codigo_bruto,
        "situacao": codigo,
        "situacao_texto": texto,
        "situacao_classe": classe,
        "situacao_nota": nota,
        "identificado": bool(s.get("organism")),
        # ---- ressalvas e falha
        "ressalvas": ressalvas,
        "n_ressalvas": len(ressalvas),
        "erro": s.get("error") or "",
        "leituras": [_leitura(r) for r in (s.get("reads") or [])
                     if isinstance(r, dict)],
    }


def _leitura(r: dict) -> dict:
    inicio, fim = r.get("col_start") or 0, r.get("col_end") or 0
    nome, medido = r.get("orient_name"), r.get("orient_content")
    return {
        "nome": r.get("name") or "",
        "primer": r.get("primer") or "",
        "sentido_nome": nome or "",
        "sentido_medido": medido or "",
        # O relatório aponta a divergência entre o sentido declarado no nome e o
        # medido no conteúdo; a tela só repete o apontamento.
        "sentido_diverge": bool(nome and medido and nome != medido),
        "bases": r.get("n_bases") or 0,
        "q_medio": f"Q{r['mean_q']}" if r.get("mean_q") else "",
        "q_rotulo": r.get("q_label") or "",
        "janela": f"{inicio}–{fim}" if fim else "",
    }
