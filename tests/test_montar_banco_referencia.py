"""
`montar` consultava o catálogo chamando o dicionário: `POR_ID(banco_id)`.

Toda montagem de banco de referência morria em `'dict' object is not callable`
antes de tocar a rede. A suíte inteira passava porque `montar` só aparece nos
testes trocada por um dublê — a função de verdade nunca rodava, já que ela fala
com o NCBI.

Estes testes exercitam a consulta ao catálogo sem rede: o id desconhecido para
na linha seguinte, e o id válido só precisa que o `Conjunto` volte inteiro.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from easycontig_web.dados import bancos_de_referencia as bancos


def test_id_desconhecido_levanta_keyerror(tmp_path: Path):
    # Antes do conserto isto era TypeError: 'dict' object is not callable.
    with pytest.raises(KeyError):
        bancos.montar(tmp_path, "banco_que_nao_existe")


def test_catalogo_devolve_o_conjunto_e_nao_explode(tmp_path: Path, monkeypatch):
    """O id válido tem de passar da consulta e chegar na chamada ao NCBI."""
    chamou = {}

    def _sem_rede(cgi, **kw):
        chamou["termo"] = kw.get("term")
        raise RuntimeError("parou antes da rede")

    monkeypatch.setattr(bancos, "_entrez", _sem_rede)

    with pytest.raises(RuntimeError, match="parou antes da rede"):
        bancos.montar(tmp_path, "apicomplexa_18s")

    # chegou lá usando o termo do catálogo, não um dicionário chamado
    assert chamou["termo"] == bancos.POR_ID["apicomplexa_18s"].termo


def test_todo_id_do_catalogo_e_consultavel():
    for c in bancos.CATALOGO:
        assert bancos.POR_ID.get(c.id) is c
