"""
A memória curta da cota não pode virar uma segunda verdade.

Medido em 2026-08-06: com 40 corridas guardadas de 80 arquivos, `situacao()`
gastava 23,03 ms percorrendo pastas, ~68% do tempo da página inicial. O cache
existe por isso — e estes testes existem para que ele nunca responda um número
que o disco desmente.

O que o torna seguro é uma propriedade do domínio, não esperteza: pasta de lote
`pronto`/`falhou` não muda mais. Tudo aqui testa as bordas dessa afirmação.
"""
from __future__ import annotations

import pytest

from easycontig_web.contas import cota_de_espaco as cotas
from easycontig_web.processamento import fila_de_lotes as fila


@pytest.fixture(autouse=True)
def _limpar():
    cotas._medidos.clear()
    yield
    cotas._medidos.clear()


def _lote(tmp_path, lote_id: str, *, status: str, bytes_: int, dono="a@b.br"):
    """Cria a linha na fila e a pasta em disco, no estado pedido."""
    sqlite = tmp_path / "fila.sqlite3"
    fila.criar_esquema(sqlite)
    real = fila.novo_lote(sqlite, dono=dono, nome="x", n_arquivos=1)
    with fila.conectar(sqlite) as con:
        con.execute("UPDATE lotes SET id=?, status=? WHERE id=?",
                    (lote_id, status, real))
    pasta = tmp_path / "lotes" / lote_id / "entrada"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "leitura.ab1").write_bytes(b"x" * bytes_)
    return sqlite, tmp_path / "lotes"


def test_lote_apagado_some_da_conta(tmp_path):
    """O modo de falha que um campo no banco teria: alguém apaga a pasta no
    servidor e a cota continua cobrando por ela."""
    import shutil
    sqlite, lotes_dir = _lote(tmp_path, "L1", status=fila.PRONTO, bytes_=5000)

    antes = cotas.situacao(sqlite, lotes_dir, "a@b.br")
    assert antes.bytes_usados >= 5000
    assert "L1" in cotas._medidos, "devia ter entrado na memória curta"

    shutil.rmtree(lotes_dir / "L1")          # apagado por fora, sem avisar

    depois = cotas.situacao(sqlite, lotes_dir, "a@b.br")
    assert depois.bytes_usados == 0, (
        "a cota cobrou por um lote que não existe mais no disco")
    assert "L1" not in cotas._medidos


def test_lote_em_andamento_e_remedido_toda_vez(tmp_path):
    """`recebendo`/`rodando` estão sendo escritos agora — congelar o tamanho
    deles é o defeito que faria a cota deixar passar um upload que estoura."""
    sqlite, lotes_dir = _lote(tmp_path, "L2", status=fila.RECEBENDO, bytes_=1000)

    primeiro = cotas.situacao(sqlite, lotes_dir, "a@b.br").bytes_usados
    assert "L2" not in cotas._medidos, "lote ativo não pode entrar na memória"

    # o upload continua e a pasta cresce
    (lotes_dir / "L2" / "entrada" / "outra.ab1").write_bytes(b"y" * 9000)

    segundo = cotas.situacao(sqlite, lotes_dir, "a@b.br").bytes_usados
    assert segundo > primeiro, "a cota não viu o lote crescer durante o upload"


def test_o_numero_com_cache_e_o_mesmo_sem_cache(tmp_path):
    """A otimização não pode mudar a resposta."""
    sqlite, lotes_dir = _lote(tmp_path, "L3", status=fila.PRONTO, bytes_=7777)

    com_cache = cotas.situacao(sqlite, lotes_dir, "a@b.br").bytes_usados
    cotas._medidos.clear()
    sem_cache = cotas.situacao(sqlite, lotes_dir, "a@b.br").bytes_usados
    direto = cotas.bytes_da_pasta(lotes_dir / "L3")

    assert com_cache == sem_cache == direto


def test_lote_que_termina_passa_a_ser_lembrado(tmp_path):
    """A transição é o momento delicado: enquanto rodava não podia ser lembrado,
    e depois de pronto tem que passar a ser."""
    sqlite, lotes_dir = _lote(tmp_path, "L4", status=fila.RODANDO, bytes_=2000)

    cotas.situacao(sqlite, lotes_dir, "a@b.br")
    assert "L4" not in cotas._medidos

    fila.concluir(sqlite, "L4")
    cotas.situacao(sqlite, lotes_dir, "a@b.br")
    assert "L4" in cotas._medidos
