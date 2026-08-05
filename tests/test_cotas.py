"""
Testes da cota por conta. O que se protege aqui é o disco da VPS: os tetos do
`config.py` são por lote e não impedem ninguém de mandar cinquenta lotes
seguidos. Ver `cotas.py`.
"""
from __future__ import annotations

import pytest

from easycontig_web import cotas, fila


@pytest.fixture()
def banco(tmp_path):
    p = tmp_path / "fila.sqlite3"
    fila.criar_esquema(p)
    return p


@pytest.fixture()
def lotes_dir(tmp_path):
    d = tmp_path / "lotes"
    d.mkdir()
    return d


def _lote(banco, lotes_dir, *, dono="a@x", status=fila.NA_FILA, bytes_=0):
    """Cria um lote no banco e, opcionalmente, a pasta com `bytes_` em disco."""
    lote_id = fila.novo_lote(banco, dono=dono, nome="c", n_arquivos=1)
    if status != fila.RECEBENDO:
        fila.liberar_para_fila(banco, lote_id, 1)
    if status == fila.RODANDO:
        # `reivindicar` pega o mais antigo da fila, que num teste com vários
        # lotes pode não ser este; aqui interessa só o status desta linha.
        with fila.conectar(banco) as con:
            con.execute("UPDATE lotes SET status=? WHERE id=?",
                        (fila.RODANDO, lote_id))
    elif status == fila.PRONTO:
        fila.concluir(banco, lote_id)
    elif status == fila.FALHOU:
        fila.falhar(banco, lote_id, "erro qualquer")
    if bytes_:
        entrada = lotes_dir / lote_id / "entrada"
        entrada.mkdir(parents=True)
        (entrada / "amostra.ab1").write_bytes(b"\0" * bytes_)
    return lote_id


def test_conta_sem_lotes_pode_enviar(banco, lotes_dir):
    c = cotas.situacao(banco, lotes_dir, "a@x")
    assert (c.lotes_ativos, c.bytes_usados) == (0, 0)
    assert c.pode_enviar and c.motivo == ""


def test_teto_de_lotes_ativos_barra_o_envio(banco, lotes_dir, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_MAX_LOTES_ATIVOS", "2")
    _lote(banco, lotes_dir, status=fila.NA_FILA)
    _lote(banco, lotes_dir, status=fila.RODANDO)
    c = cotas.situacao(banco, lotes_dir, "a@x")
    assert c.lotes_ativos == 2 and not c.pode_enviar
    # a frase é o que o usuário lê: precisa dizer o que fazer, não um código
    assert "espere" in c.motivo.lower()


def test_recebendo_conta_como_ativo(banco, lotes_dir, monkeypatch):
    """Upload em curso ocupa disco e vaga na fila tanto quanto os outros."""
    monkeypatch.setenv("EASYCONTIG_MAX_LOTES_ATIVOS", "1")
    _lote(banco, lotes_dir, status=fila.RECEBENDO)
    assert not cotas.situacao(banco, lotes_dir, "a@x").pode_enviar


def test_lote_pronto_ou_falhou_nao_ocupa_vaga(banco, lotes_dir, monkeypatch):
    """Senão a conta travaria para sempre no primeiro dia de uso."""
    monkeypatch.setenv("EASYCONTIG_MAX_LOTES_ATIVOS", "2")
    _lote(banco, lotes_dir, status=fila.PRONTO)
    _lote(banco, lotes_dir, status=fila.FALHOU)
    c = cotas.situacao(banco, lotes_dir, "a@x")
    assert c.lotes_ativos == 0 and c.pode_enviar


def test_teto_de_bytes_barra_o_envio(banco, lotes_dir, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_MAX_BYTES_CONTA", "1000")
    _lote(banco, lotes_dir, status=fila.PRONTO, bytes_=600)
    _lote(banco, lotes_dir, status=fila.PRONTO, bytes_=600)
    c = cotas.situacao(banco, lotes_dir, "a@x")
    assert c.bytes_usados == 1200 and not c.pode_enviar
    assert "ocupam" in c.motivo


def test_bytes_abaixo_do_teto_passam(banco, lotes_dir, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_MAX_BYTES_CONTA", "1000")
    _lote(banco, lotes_dir, status=fila.PRONTO, bytes_=600)
    c = cotas.situacao(banco, lotes_dir, "a@x")
    assert c.bytes_usados == 600 and c.pode_enviar and c.motivo == ""


def test_zero_desliga_o_teto_de_lotes_ativos(banco, lotes_dir, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_MAX_LOTES_ATIVOS", "0")
    for _ in range(5):
        _lote(banco, lotes_dir, status=fila.NA_FILA)
    c = cotas.situacao(banco, lotes_dir, "a@x")
    assert c.lotes_ativos == 5 and c.max_lotes_ativos == 0 and c.pode_enviar


def test_zero_desliga_o_teto_de_bytes(banco, lotes_dir, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_MAX_BYTES_CONTA", "0")
    _lote(banco, lotes_dir, status=fila.PRONTO, bytes_=5000)
    c = cotas.situacao(banco, lotes_dir, "a@x")
    assert c.bytes_usados == 5000 and c.max_bytes_conta == 0 and c.pode_enviar


def test_pasta_ausente_conta_zero_e_nao_quebra(banco, lotes_dir):
    """Lote apagado à mão no servidor é caso comum de manutenção."""
    _lote(banco, lotes_dir, status=fila.PRONTO, bytes_=0)   # sem pasta em disco
    _lote(banco, lotes_dir, status=fila.PRONTO, bytes_=800)
    assert cotas.situacao(banco, lotes_dir, "a@x").bytes_usados == 800


def test_lotes_de_outro_dono_nao_entram_nesta_cota(banco, lotes_dir, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_MAX_LOTES_ATIVOS", "1")
    monkeypatch.setenv("EASYCONTIG_MAX_BYTES_CONTA", "1000")
    _lote(banco, lotes_dir, dono="b@x", status=fila.NA_FILA, bytes_=5000)
    c = cotas.situacao(banco, lotes_dir, "a@x")
    assert (c.lotes_ativos, c.bytes_usados) == (0, 0)
    assert c.pode_enviar


def test_tetos_vem_do_ambiente_e_caem_no_padrao_quando_ilegiveis(monkeypatch):
    monkeypatch.delenv("EASYCONTIG_MAX_LOTES_ATIVOS", raising=False)
    monkeypatch.delenv("EASYCONTIG_MAX_BYTES_CONTA", raising=False)
    assert cotas.teto_lotes_ativos() == 3
    assert cotas.teto_bytes_conta() == 2 * 1024 * 1024 * 1024
    # `.env` com erro de digitação não pode virar "sem limite" por acidente
    monkeypatch.setenv("EASYCONTIG_MAX_LOTES_ATIVOS", "três")
    monkeypatch.setenv("EASYCONTIG_MAX_BYTES_CONTA", "-1")
    assert cotas.teto_lotes_ativos() == 3
    assert cotas.teto_bytes_conta() == 2 * 1024 * 1024 * 1024
