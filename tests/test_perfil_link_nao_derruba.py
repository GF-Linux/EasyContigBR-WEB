"""
Um link de perfil malformado não pode derrubar toda página da conta.

Achado F2 da varredura de 2026-08-20. `_limpar_links` validava a string INTEIRA
com `urlparse` e depois gravava `bruto[:300]` — o corte, não o validado. Um
endereço cujo corte cai no meio de um `[` de IPv6 (`https://...@[`) passa na
validação (o valor inteiro é válido) mas é gravado quebrado. Na leitura, `_links`
chamava `urlparse` sem proteção, e `urlsplit` levanta `ValueError("Invalid IPv6
URL")`. Como `_casca` lê o perfil em CADA página renderizada, a conta passava a
receber 500 em tudo — inclusive no editor de perfil que desfaria — e o fluxo de
pedidos de outros laboratórios para aquele lab quebrava junto.

DoS persistente que o próprio usuário se aplica, sem saída dentro do app.
"""
from __future__ import annotations

import json

from easycontig_web.contas import perfil_do_laboratorio as perfil


# o valor que estourava: 300 chars terminando dentro do colchete de IPv6
VENENO = "https://" + "a" * 290 + "@[::1]/"


def test_o_escritor_nao_grava_link_que_a_leitura_nao_aguenta():
    """⚠️ Truncar ANTES de validar: o que é gravado tem de ser o que foi
    validado. Um corte que estraga o endereço é rejeitado, não guardado."""
    limpos = perfil._limpar_links(VENENO)
    # nenhum link gravado pode, ao ser relido, fazer urlparse levantar
    for item in limpos:
        # não deve levantar
        from urllib.parse import urlparse
        urlparse(item["url"])
    # e o valor venenoso especificamente não entra
    assert all(not u["url"].endswith("[") for u in limpos)


def test_a_leitura_cura_uma_linha_ruim_ja_gravada():
    """A defesa de trás: um banco que JÁ tem a linha venenosa (gravada antes do
    conserto do escritor) não pode estourar na leitura. Vira 'sem link'."""
    ruim = json.dumps([{"url": "https://" + "a" * 290 + "@["}])
    # antes do fix isto levantava ValueError; agora devolve lista vazia
    assert perfil._links(ruim) == []


def test_link_valido_continua_passando():
    """A blindagem não pode recusar endereço bom."""
    bom = perfil._limpar_links("github.com/fulano https://orcid.org/0000-0002-1825-0097")
    urls = [x["url"] for x in bom]
    assert any("github.com/fulano" in u for u in urls)
    assert any("orcid.org" in u for u in urls)
    # e a leitura os aceita de volta
    relido = perfil._links(json.dumps([{"url": u} for u in urls]))
    assert len(relido) == len(urls)


def test_javascript_continua_barrado():
    """Regressão de vizinhança: o conserto do IPv6 não pode reabrir a porta do
    esquema perigoso (o XSS armazenado que a função já barrava)."""
    assert perfil._limpar_links("javascript:alert(1)") == []
    assert perfil._limpar_links("data:text/html;base64,PHNjcmlwdD4=") == []
