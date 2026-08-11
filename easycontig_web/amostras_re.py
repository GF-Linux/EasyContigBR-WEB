''' Amostras_re.py - leitura por amostra do 'relatório.json' já gravado

#! 1. Nenhuma decisã científica mora aqui.
#! 2. Os limiares são reescritos - Cada ressalva carrega o seu 'rule'
#! 3. Ausência continua ausência.
#! 4. 'Não achou' e 'não conseguiu procuar' são coisas diferentes.

#* Autor: Gustavo Gonçalves Freitas - LHV
#* Copyright (c) 2026 Gustavo Gonçalves Freitas. Todos os direitos reservados.


'''


from __future__ import annotations


import json
from pathlib import Path


SITUACOES : dict[str, tuple[str, str, str]] = {
    'ok': ('acerto no banco local', 'pronto', 'Melhor acerto do BLAST dentro do banco local consultado - uma semelhança ' 
           'com o que existe nesse banco local, não uma identificação de espécie.')
,
    'sem_acerto': ('Sem acerto no banco local', 'sub', 'O banco local foi consultado e nenhuma referência foi alcançada.'
                'isso descreve o banco consultado, não a amostra.'),


    'blast_indisponivel': ('Busca não executada, blastn indisponível', 'rodando',
                           'A busca no banco local não chegou a rodar porque o blastn não pôde ser executado'
                           ' Ocorreu um erro de execução do blastn, ou o blastn não está instalado, ou o banco local não foi montado.'),


    'sem_banco': ('Busca não executada, banco local não montado', 'rodando',
                'A busca no banco local não chegou a rodar porque o banco local não foi montado'
                ' Ocorreu um erro de execução do blastn, ou o blastn não está instalado, ou o banco local não foi montado.'),



    'nao_montou': ('Não montou, nada foi consultado', 'falhou', 
                   'A montagem do consenso falho, ou não houve sequência para encontrar'
                   'no banco. Isso não configure acerto, uma vez que nada foi consultado.'), 
}


ORDEM_SITUACOES = ('ok', 'sem_acerto', 'blast_indisponivel', 'sem_banco', 'nao_montou')



def carregar(relatorio_json: Path) -> dict | None:
    ''' Lê o `relatorio.json` do lote. `None` se não existe ou não serve. '''
    try:
        texto = Path(relatorio_json).read_text(encoding="utf-8")
        rep = json.loads(texto)
    except (OSError, ValueError):
        return None
    if not isinstance(rep, dict) or not isinstance(rep.get('samples'), list):
        return None
    return rep


_contagens : dict[str, tuple[int, int]] = {}

def contar_amostras(relatorio_json: Path) -> int | None:
    ''' Conta as amostras do `relatorio.json` do lote. 
      '''
    caminho = Path(relatorio_json)

    try:
        st = caminho.stat()
    except OSError:
        return None 
    
    chave = str(caminho)
    marca = (st.st_mtime, st.st_size)
    guardado = _contagens.get(chave)
    if guardado and guardado[:2] == marca:

        return guardado[2]

    rep = carregar(caminho)
    if rep is None:
        _contagens.pop(chave, None)
        return None

    n = len(rep.get('samples', []))
    _contagens[chave] = (*marca, n)
    return n


def resumo(rep: dict) -> dict:
    #? Contagens do lote para cabeçalho do relatório. 

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
        "avulsas": sum(1 for s in amostras if len(s.get("reads") or []) < 2),
        "pareadas": sum(1 for s in amostras if len(s.get("reads") or []) >= 2),
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
    ''' Lista as amostras do lote para tabela do relatório. '''

    return [_formatar(s) for s in _amostras(rep)] 


def amostra(rep: dict, key: str) -> dict | None:
    #? aqui não é necessário _formatar() porque a função listar() já faz isso. Mas aqui sim, porque é só uma amostra.
    
    for s in _amostras(rep):
        if s.get("key") == key:
            return _formatar(s)
    return None


#! ------------------------------------------------------------------------------------------#


def _amostras(rep: dict) -> list[dict]:
    ''' Lista as amostras do lote para tabela do relatório. '''

    itens = rep.get('samples') if isinstance(rep, dict) else None

    return [s for s in (itens or []) if isinstance(s, dict)]


def _situacao(s: dict) -> str:
    ''' Qual a situação da amostra. '''

    if s.get('error') or not s.get('consensus_len'):

        return 'nao_montou'

    return s.get('id_status') or 'sem_acerto'


def _rotulo_situacao(codigo: str) -> tuple[str, str, str]:
    ''' Rótulo da situação da amostra. '''

    return SITUACOES.get(codigo, (codigo, 'sub', ''))


def _num(valor, casas: int) -> str:
    ''' Número com casas fixas  '''


    if valor is None:
        return ''

    try:
        return f"{float(valor):.{casas}f}"

    except (ValueError, TypeError):
        return ''


def _formatar(s: dict) -> dict:
    ''' Formata a amostra para tabela do relatório. '''
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
        "n_leituras": len(s.get("reads") or []),
        "consenso_pb": consenso,
        "cobertura_media": _num(cobertura, 2) if cobertura else "",
        'n_cobertura_1': s.get('n_coverage_1') or 0,
        'pct_cobertura_1': _num(s.get('pct_coverage_1'), 1) if consenso else '',
        'discordâncias': s.get('n_corrected') or 0,
        'contig_invertido': 'sim' if s.get('contig_flipped') else 'não',
        'invertido': bool(s.get('contig_flipped')),
        'organismo': s.get("organism") or "",
        'accession': s.get("accession") or "",
        'identidade': _num(s.get('pct_identity'), 1),
        'cobertura_query': _num(s.get('pct_query_coverage'), 1),
        'e_value': _num(s.get('e_value'), 1),
        'marcador': s.get("id_source") or "",
        'situacao_id': codigo_bruto,
        'situacao': codigo,
        'situacao_texto': texto,
        'situacao_classe': classe,
        'situacao_nota': nota,
        'identificado': bool(s.get("organism")),
        'ressalvas': ressalvas,
        'n_ressalvas': len(ressalvas),
        'erro': s.get("error") or "",
        'leituras':[_leitura(r) for r in (s.get("reads") or []) if isinstance(r, dict)],

    }        

def _leitura(r: dict) -> dict:
    ''' Formata a leitura para tabela do relatório. '''

    inicio, fim = r.get('col_start') or 0, r.get('col_end') or 0
    nome, medido = r.get('orien_nome') or '', r.get('orient_content') or ''

    return {
        "nome": r.get("name") or "",
        "primer": r.get("primer") or "",
        "sentido_nome": nome or "",
        "sentido_medido": medido or "",
        "sentido_diverge": bool(nome and medido and nome != medido),
        'bases': r.get('n_bases') or 0,
        "q_medio": f"Q{r['mean_q']}" if r.get("mean_q") else "",
        'q_rotulo': r.get('q_label') or '',
         "janela": f"{inicio}–{fim}" if fim else "",

    }