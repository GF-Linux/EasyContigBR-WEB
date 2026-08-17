"""A tela precisa DIZER quando uma leitura não formou par.

Em 2026-08-07 uma corrida real (`Sequenciamento_F13719_#80649-27072026_A09.ab1`)
subiu para o servidor e as **81 leituras viraram 81 amostras de uma leitura só**.
O app estava certo: o sentido F/R ele descobre pelo DNA, mas quem diz que duas
leituras são da mesma amostra é o nome — e ali o único token variável é o poço
da placa, com `SMPL1` idêntico nos 80 (ADR 0034/0045).

O defeito era a tela **calar**. Quem enviou viu 81 linhas sem explicação, e a
leitura natural disso é "o programa falhou". Mesmo padrão da ADR 0039: recusa
informada é resultado; silêncio parece defeito.
"""
from easycontig_web.dados import leitura_de_amostras as amostras


def _rep(n_pares, n_avulsas):
    s = []
    for i in range(n_pares):
        s.append({"key": f"par{i}", "reads": [{"file": "a.ab1"}, {"file": "b.ab1"}],
                  "consensus_len": 600})
    for i in range(n_avulsas):
        s.append({"key": f"avulsa{i}", "reads": [{"file": "x.ab1"}],
                  "consensus_len": 0})
    return {"samples": s, "n_files": n_pares * 2 + n_avulsas}


def test_conta_avulsas_e_pareadas():
    r = amostras.resumo(_rep(40, 81))
    assert r["avulsas"] == 81
    assert r["pareadas"] == 40
    assert r["total"] == 121


def test_corrida_inteira_sem_par_nao_passa_despercebida():
    """O caso real: nenhuma leitura pareou. `avulsas` tem de ser diferente de
    zero, senão a tela não tem como avisar e o silêncio volta."""
    r = amostras.resumo(_rep(0, 81))
    assert r["avulsas"] == 81
    assert r["pareadas"] == 0


def test_lote_normal_nao_dispara_aviso():
    """Um lote em que tudo pareou não pode exibir aviso nenhum — '0 avulsas'
    impresso sugeriria que alguém esperava o contrário."""
    r = amostras.resumo(_rep(40, 0))
    assert r["avulsas"] == 0


def test_a_pagina_do_lote_explica_o_motivo():
    """Não basta contar: a página tem de dizer POR QUE não pareou, senão o
    número sozinho continua parecendo falha do arquivo."""
    import re
    from pathlib import Path
    # Espaço normalizado: uma quebra de linha no HTML não pode derrubar uma
    # verificação de CONTEÚDO. A primeira versão deste teste falhou porque a
    # frase estava partida em "poço da\n placa" — e afrouxar a asserção seria
    # o conserto errado.
    html = re.sub(r"\s+", " ", Path("easycontig_web/templates/lote.html").read_text())
    assert "não formou par" in html
    assert "mesma amostra" in html      # a explicação, não só a contagem
    assert "poço da placa" in html      # o caso concreto que produziu isto
