"""M2 e L3 — os dois buracos da cota, de 2026-08-08.

M2: banco próprio não entrava na cota NEM na retenção. `situacao` somava apenas
    `lotes_dir/<lote_id>`, e `retencao.faxina` só apaga lote. Sem teto de
    QUANTIDADE (só o balde de 10/h) e com apelido de 40 caracteres, eram
    ~200 MB/h permanentes e invisíveis.

L3: `pode_enviar` só recusava com `usados >= max_bytes` — nunca perguntava se o
    envio da vez CABE. E `usados` é medido do disco, que só conta byte já
    chegado: dez envios simultâneos liam o mesmo número antes de qualquer um
    gravar, e os dez passavam.
"""
from __future__ import annotations

import threading

import pytest

from easycontig_web import cotas, fila


@pytest.fixture()
def banco(tmp_path):
    p = tmp_path / "fila.sqlite3"
    fila.criar_esquema(p)
    return p


# ═══════════════════════════════════════════════════════════════════════ M2
def test_a_migracao_4_criou_a_coluna_de_reserva(banco):
    from easycontig_web import migracoes
    assert migracoes.versao(banco) >= 4
    with fila.conectar(banco) as con:
        colunas = {r[1] for r in con.execute("PRAGMA table_info(lotes)")}
    assert "bytes_previstos" in colunas


def test_o_banco_do_usuario_entra_na_cota(banco, tmp_path):
    """Sem `data_dir` a conta enxerga só metade do que ocupa — que era o buraco."""
    from easycontig_web import bancos as mod
    data_dir = tmp_path / "dados"
    # O id tem de ser o que `id_do_usuario` monta, senão `meus_bancos` — que
    # lista por prefixo do espaço da conta — não enxerga a pasta.
    banco_id = mod.id_do_usuario("a@ufrrj.br", "refs")
    pasta = data_dir / "bancos" / banco_id
    pasta.mkdir(parents=True)
    # `.nsq` é o que `bancos.existe()` procura para dizer que está montado.
    (pasta / f"{banco_id}.nsq").write_bytes(b"x" * 5_000_000)

    sem = cotas.situacao(banco, data_dir / "lotes", "a@ufrrj.br")
    com = cotas.situacao(banco, data_dir / "lotes", "a@ufrrj.br", data_dir)

    assert sem.bytes_usados == 0, "a chamada antiga não devia contar banco"
    assert com.bytes_usados >= 5_000_000, "o banco não entrou na cota"
    assert com.bancos == 1


def test_ha_teto_de_quantidade_de_bancos(monkeypatch):
    """Não havia número nenhum: só o balde de taxa, que se renova toda hora."""
    monkeypatch.delenv("EASYCONTIG_MAX_BANCOS_CONTA", raising=False)
    assert cotas.teto_bancos_conta() > 0
    monkeypatch.setenv("EASYCONTIG_MAX_BANCOS_CONTA", "3")
    assert cotas.teto_bancos_conta() == 3


# ═══════════════════════════════════════════════════════════════════════ L3
def test_o_envio_que_nao_cabe_e_recusado(banco):
    """`pode_enviar` só olhava se a conta JÁ estourou. Um envio de 900 MB contra
    100 MB de sobra passava, porque no instante da pergunta ainda cabia."""
    with pytest.raises(fila.CotaDeBytesEstourada):
        fila.novo_lote(banco, dono="a@x", nome="grande", n_arquivos=1,
                       bytes_previstos=900_000_000,
                       teto_bytes=1_000_000_000,
                       usados_em_disco=900_000_000)


def test_o_que_cabe_continua_passando(banco):
    lote = fila.novo_lote(banco, dono="a@x", nome="ok", n_arquivos=1,
                          bytes_previstos=50_000_000,
                          teto_bytes=1_000_000_000,
                          usados_em_disco=100_000_000)
    assert fila.pegar(banco, lote)["bytes_previstos"] == 50_000_000


def test_a_rajada_simultanea_nao_fura_mais_a_cota(banco):
    """O teste que vale: 10 envios ao mesmo tempo, cada um reservando 300 MB,
    contra uma cota de 1 GB. Sem a reserva transacional TODOS entravam, porque
    liam o disco antes de qualquer byte chegar nele.

    `teto_ativos=0` de propósito: o teto de lotes ativos limitava o estrago por
    acidente, e este teste mede a trava de BYTES sozinha.
    """
    entraram, recusados = [], []
    largada = threading.Event()

    def enviar():
        largada.wait(timeout=5)
        try:
            entraram.append(fila.novo_lote(
                banco, dono="a@x", nome="r", n_arquivos=1,
                bytes_previstos=300_000_000,
                teto_bytes=1_000_000_000,
                usados_em_disco=0))
        except fila.CotaDeBytesEstourada:
            recusados.append(1)

    fios = [threading.Thread(target=enviar) for _ in range(10)]
    for f in fios:
        f.start()
    largada.set()
    for f in fios:
        f.join(timeout=10)

    assert len(entraram) == 3, (
        f"{len(entraram)} envios de 300 MB entraram numa cota de 1 GB")
    assert len(recusados) == 7
    with fila.conectar(banco) as con:
        reservado = con.execute(
            "SELECT COALESCE(SUM(bytes_previstos),0) FROM lotes").fetchone()[0]
    assert reservado <= 1_000_000_000


def test_a_reserva_morre_quando_o_lote_entra_na_fila(banco):
    """A reserva vale só durante o upload. Mantida depois, a conta pagaria duas
    vezes pelo mesmo lote — uma pela previsão e outra pelo disco."""
    lote = fila.novo_lote(banco, dono="a@x", nome="x", n_arquivos=2,
                          bytes_previstos=10_000_000, teto_bytes=1_000_000_000)
    assert fila.pegar(banco, lote)["bytes_previstos"] == 10_000_000
    fila.liberar_para_fila(banco, lote, 2)
    assert fila.pegar(banco, lote)["bytes_previstos"] == 0


def test_sem_teto_de_bytes_nada_muda(banco):
    """`teto_bytes=0` é "sem limite" declarado — não pode virar recusa."""
    for _ in range(5):
        fila.novo_lote(banco, dono="a@x", nome="x", n_arquivos=1,
                       bytes_previstos=10**12, teto_bytes=0)
    assert len(fila.listar(banco, dono="a@x")) == 5
