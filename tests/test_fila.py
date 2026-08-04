"""
Testes da fila. O foco é a corrida que produziu o defeito real: um lote entrar
no trabalhador antes de o upload terminar.
"""
from __future__ import annotations

import pytest

from easycontig_web import fila


@pytest.fixture()
def banco(tmp_path):
    p = tmp_path / "fila.sqlite3"
    fila.criar_esquema(p)
    return p


def test_lote_novo_nasce_recebendo_e_e_invisivel_para_o_trabalhador(banco):
    """O defeito de 33 de 40 amostras: enfileirar antes do último byte."""
    fila.novo_lote(banco, dono="a@x", nome="corrida", n_arquivos=80)
    assert fila.reivindicar(banco) is None, \
        "um lote ainda recebendo arquivos NUNCA pode ser pego pelo trabalhador"


def test_so_apos_liberar_o_lote_entra_na_fila(banco):
    lote_id = fila.novo_lote(banco, dono="a@x", nome="corrida", n_arquivos=80)
    fila.liberar_para_fila(banco, lote_id, 80)
    pego = fila.reivindicar(banco)
    assert pego is not None and pego["id"] == lote_id
    assert fila.pegar(banco, lote_id)["status"] == fila.RODANDO


def test_liberar_grava_o_numero_realmente_recebido(banco):
    """O executor confere este número contra o disco; tem que ser o real, e não
    o que o navegador prometeu ao iniciar o envio."""
    lote_id = fila.novo_lote(banco, dono="a@x", nome="c", n_arquivos=80)
    fila.liberar_para_fila(banco, lote_id, 66)
    assert fila.pegar(banco, lote_id)["n_arquivos"] == 66


def test_dois_trabalhadores_nao_pegam_o_mesmo_lote(banco):
    """É o UPDATE condicional que permite escalar por processos."""
    lote_id = fila.novo_lote(banco, dono="a@x", nome="c", n_arquivos=2)
    fila.liberar_para_fila(banco, lote_id, 2)
    primeiro = fila.reivindicar(banco)
    segundo = fila.reivindicar(banco)
    assert primeiro["id"] == lote_id
    assert segundo is None


def test_fila_e_atendida_na_ordem_de_chegada(banco):
    ids = []
    for i in range(3):
        lid = fila.novo_lote(banco, dono="a@x", nome=f"c{i}", n_arquivos=2)
        fila.liberar_para_fila(banco, lid, 2)
        ids.append(lid)
    assert [fila.reivindicar(banco)["id"] for _ in ids] == ids


def test_orfaos_de_rodando_voltam_para_a_fila(banco):
    """Queda de energia no meio do lote não pode deixar o usuário girando."""
    lote_id = fila.novo_lote(banco, dono="a@x", nome="c", n_arquivos=2)
    fila.liberar_para_fila(banco, lote_id, 2)
    fila.reivindicar(banco)
    assert fila.reenfileirar_orfaos(banco) == 1
    assert fila.pegar(banco, lote_id)["status"] == fila.NA_FILA


def test_upload_interrompido_falha_em_vez_de_ser_processado_pela_metade(banco):
    """RECEBENDO órfão vira FALHOU — reenfileirar reproduziria o defeito."""
    lote_id = fila.novo_lote(banco, dono="a@x", nome="c", n_arquivos=80)
    fila.reenfileirar_orfaos(banco)
    lote = fila.pegar(banco, lote_id)
    assert lote["status"] == fila.FALHOU
    assert "interrompido" in lote["erro"]


def test_progresso_e_conclusao(banco):
    lote_id = fila.novo_lote(banco, dono="a@x", nome="c", n_arquivos=4)
    fila.liberar_para_fila(banco, lote_id, 4)
    fila.reivindicar(banco)
    fila.progresso(banco, lote_id, 1, 2, "amostra12")
    l = fila.pegar(banco, lote_id)
    assert (l["feito"], l["total"], l["etapa"]) == (1, 2, "amostra12")
    fila.concluir(banco, lote_id)
    assert fila.pegar(banco, lote_id)["status"] == fila.PRONTO


def test_listar_separa_por_dono(banco):
    fila.liberar_para_fila(banco, fila.novo_lote(banco, dono="a@x", nome="a", n_arquivos=1), 1)
    fila.liberar_para_fila(banco, fila.novo_lote(banco, dono="b@x", nome="b", n_arquivos=1), 1)
    assert [x["nome"] for x in fila.listar(banco, dono="a@x")] == ["a"]
    assert len(fila.listar(banco)) == 2
