"""
`contar_amostras` tem que dar sempre o mesmo número que ler o relatório inteiro.

É uma otimização de leitura na página do perfil (25,89 ms para 40 corridas, e o
teto ali é 500). Otimização que muda a resposta não é otimização.
"""
from __future__ import annotations

import json

import pytest

from easycontig_web.dados import leitura_de_amostras as amostras


@pytest.fixture(autouse=True)
def _limpar():
    amostras._contagens.clear()
    yield
    amostras._contagens.clear()


def _relatorio(caminho, n):
    caminho.write_text(json.dumps({
        "folder": "x", "n_files": n * 2, "trim": 4,
        "samples": [{"key": f"am{i}", "organism": "Babesia vogeli"} for i in range(n)],
    }))
    return caminho


def test_da_o_mesmo_numero_que_ler_tudo(tmp_path):
    p = _relatorio(tmp_path / "relatorio.json", 40)
    assert amostras.contar_amostras(p) == len(amostras.carregar(p)["samples"]) == 40


def test_sem_relatorio_e_None_e_nao_zero(tmp_path):
    """Zero é um número e seria lido como 'corrida sem amostras'. Ausência tem
    que continuar ausência — é a regra que o módulo inteiro segue."""
    assert amostras.contar_amostras(tmp_path / "nao_existe.json") is None


def test_relatorio_ilegivel_e_None(tmp_path):
    p = tmp_path / "relatorio.json"
    p.write_text("{isto não é json")
    assert amostras.contar_amostras(p) is None


def test_relatorio_regravado_e_recontado(tmp_path):
    """O caso que a validação por mtime+tamanho existe para pegar."""
    import os
    p = _relatorio(tmp_path / "relatorio.json", 8)
    assert amostras.contar_amostras(p) == 8

    _relatorio(p, 40)
    # mtime tem resolução finita; força a marca a diferir como diferiria numa
    # regravação real separada por qualquer intervalo perceptível
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    assert amostras.contar_amostras(p) == 40, "leu do cache um relatório que mudou"


def test_relatorio_apagado_some_do_cache(tmp_path):
    p = _relatorio(tmp_path / "relatorio.json", 5)
    assert amostras.contar_amostras(p) == 5
    assert str(p) in amostras._contagens

    p.unlink()

    assert amostras.contar_amostras(p) is None
    assert str(p) not in amostras._contagens


def test_a_segunda_leitura_nao_toca_no_conteudo(tmp_path, monkeypatch):
    """Prova que o cache está mesmo sendo usado, e não que o teste é rápido."""
    p = _relatorio(tmp_path / "relatorio.json", 12)
    assert amostras.contar_amostras(p) == 12

    def _explode(*_a, **_k):
        raise AssertionError("releu o relatório inteiro em vez de usar o cache")

    monkeypatch.setattr(amostras, "carregar", _explode)
    assert amostras.contar_amostras(p) == 12
