"""
Testes da retenção. O foco é o que NÃO pode ser apagado: um lote com upload em
curso, um lote que o trabalhador está montando, e tudo, quando o prazo é 0.

Dado apagado por engano não tem desfazer — então quase todo teste aqui afirma
uma ausência de estrago, e não um estrago bem-sucedido.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from easycontig_web import fila, retencao

AGORA = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


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


def semear(banco, lotes_dir, *, dias_atras: int, status: str = fila.PRONTO,
           bytes_ab1: int = 1024) -> str:
    """Cria um lote com pasta em disco e idade forjada em `criado_em`."""
    lote_id = fila.novo_lote(banco, dono="a@x", nome="corrida", n_arquivos=2)
    criado = (AGORA - timedelta(days=dias_atras)).isoformat(timespec="seconds")
    with fila.conectar(banco) as con:
        con.execute("UPDATE lotes SET status=?, criado_em=? WHERE id=?",
                    (status, criado, lote_id))
    entrada = lotes_dir / lote_id / "entrada"
    entrada.mkdir(parents=True)
    (entrada / "amostra_F.ab1").write_bytes(b"\0" * bytes_ab1)
    return lote_id


def test_lote_vencido_perde_pasta_e_linha_juntas(banco, lotes_dir):
    """Meia remoção é defeito dos dois lados: linha sem pasta promete um
    relatório que não existe, pasta sem linha é lixo que ninguém enxerga."""
    lote_id = semear(banco, lotes_dir, dias_atras=200)
    r = retencao.faxina(banco, lotes_dir, dias=90, agora=AGORA)

    assert r["apagados"] == 1 and r["ids"] == [lote_id]
    assert not (lotes_dir / lote_id).exists()
    assert fila.pegar(banco, lote_id) is None


def test_lote_dentro_do_prazo_fica(banco, lotes_dir):
    lote_id = semear(banco, lotes_dir, dias_atras=89)
    r = retencao.faxina(banco, lotes_dir, dias=90, agora=AGORA)

    assert r["apagados"] == 0
    assert (lotes_dir / lote_id / "entrada" / "amostra_F.ab1").exists()
    assert fila.pegar(banco, lote_id) is not None


def test_lote_velho_rodando_nao_e_apagado(banco, lotes_dir):
    """O trabalhador está escrevendo NAQUELA pasta neste instante — puxar o
    tapete no meio da montagem some com o dado de origem, não só com a saída."""
    lote_id = semear(banco, lotes_dir, dias_atras=500, status=fila.RODANDO)
    r = retencao.faxina(banco, lotes_dir, dias=90, agora=AGORA)

    assert r["apagados"] == 0
    assert (lotes_dir / lote_id).exists()
    assert fila.pegar(banco, lote_id)["status"] == fila.RODANDO


def test_lote_velho_recebendo_nao_e_apagado(banco, lotes_dir):
    """`recebendo` é upload em curso: os bytes ainda estão chegando."""
    lote_id = semear(banco, lotes_dir, dias_atras=500, status=fila.RECEBENDO)
    r = retencao.faxina(banco, lotes_dir, dias=90, agora=AGORA)

    assert r["apagados"] == 0
    assert (lotes_dir / lote_id).exists()
    assert fila.pegar(banco, lote_id) is not None


def test_falhou_e_na_fila_vencidos_tambem_saem(banco, lotes_dir):
    """Só os dois estados 'em uso' são intocáveis; velho é velho."""
    ids = {semear(banco, lotes_dir, dias_atras=200, status=s)
           for s in (fila.FALHOU, fila.NA_FILA)}
    r = retencao.faxina(banco, lotes_dir, dias=90, agora=AGORA)
    assert set(r["ids"]) == ids


def test_dias_zero_nao_apaga_nada(banco, lotes_dir):
    """Servidor que apaga dado porque faltou variável de ambiente é pior que
    servidor que acumula: acúmulo aparece no disco, apagado não volta."""
    lote_id = semear(banco, lotes_dir, dias_atras=9999)
    r = retencao.faxina(banco, lotes_dir, dias=0, agora=AGORA)

    assert r == {"apagados": 0, "ids": [], "bytes": 0}
    assert (lotes_dir / lote_id).exists()
    assert fila.pegar(banco, lote_id) is not None
    assert retencao.lotes_expirados(banco, dias=0, agora=AGORA) == []


def test_apagar_lote_e_idempotente(banco, lotes_dir):
    """A faxina, uma rota 'apagar agora' e um retry podem chegar na mesma pasta."""
    lote_id = semear(banco, lotes_dir, dias_atras=1)
    assert retencao.apagar_lote(banco, lotes_dir, lote_id) is True
    assert retencao.apagar_lote(banco, lotes_dir, lote_id) is False


def test_lote_inexistente_devolve_falso(banco, lotes_dir):
    assert retencao.apagar_lote(banco, lotes_dir, "nunca-existiu") is False


def test_pasta_ausente_ainda_assim_apaga_a_linha(banco, lotes_dir):
    """Linha órfã faz a página do lote oferecer um relatório que não existe."""
    lote_id = semear(banco, lotes_dir, dias_atras=200)
    import shutil
    shutil.rmtree(lotes_dir / lote_id)

    assert retencao.apagar_lote(banco, lotes_dir, lote_id) is True
    assert fila.pegar(banco, lote_id) is None


def test_pasta_orfa_sem_linha_tambem_e_removida(banco, lotes_dir):
    """O outro lado: pasta sem linha é lixo invisível que enche o volume."""
    orfa = lotes_dir / "sobrou-de-um-crash"
    (orfa / "entrada").mkdir(parents=True)

    assert retencao.apagar_lote(banco, lotes_dir, "sobrou-de-um-crash") is True
    assert not orfa.exists()


@pytest.mark.parametrize("ruim", ["..", "../..", "a/b", "/etc", "", "   ", ".",
                                  "lote/../..", "c\\d"])
def test_id_que_sobe_de_pasta_e_recusado(banco, lotes_dir, ruim):
    """Esta função apaga árvore recursivamente: um id com `..` viraria rm -rf
    na pasta de cima. O chamador valida, mas a trava mora aqui também."""
    vizinho = lotes_dir.parent / "nao_me_toque"
    vizinho.mkdir()

    with pytest.raises(ValueError):
        retencao.apagar_lote(banco, lotes_dir, ruim)
    assert vizinho.exists() and lotes_dir.exists()


def test_faxina_conta_apagados_e_bytes(banco, lotes_dir):
    velhos = [semear(banco, lotes_dir, dias_atras=100 + i, bytes_ab1=2048)
              for i in range(3)]
    novo = semear(banco, lotes_dir, dias_atras=10)

    r = retencao.faxina(banco, lotes_dir, dias=90, agora=AGORA)

    assert r["apagados"] == 3
    assert sorted(r["ids"]) == sorted(velhos)
    assert r["bytes"] == 3 * 2048
    assert fila.pegar(banco, novo) is not None


def test_faxina_segue_mesmo_com_um_lote_problematico(banco, lotes_dir, monkeypatch):
    """Uma pasta travada não pode fazer o volume voltar a crescer em silêncio."""
    bom = semear(banco, lotes_dir, dias_atras=200)
    ruim = semear(banco, lotes_dir, dias_atras=300)

    de_verdade = retencao.apagar_lote

    def explode(sqlite_path, lotes_dir_, lote_id):
        if lote_id == ruim:
            raise OSError("permissão negada no volume")
        return de_verdade(sqlite_path, lotes_dir_, lote_id)

    monkeypatch.setattr(retencao, "apagar_lote", explode)
    r = retencao.faxina(banco, lotes_dir, dias=90, agora=AGORA)

    assert r["ids"] == [bom]
    assert fila.pegar(banco, ruim) is not None


def test_lotes_expirados_vem_do_mais_antigo_para_o_mais_novo(banco, lotes_dir):
    velho = semear(banco, lotes_dir, dias_atras=400)
    meio = semear(banco, lotes_dir, dias_atras=200)
    semear(banco, lotes_dir, dias_atras=1)

    achados = retencao.lotes_expirados(banco, dias=90, agora=AGORA)
    assert [l["id"] for l in achados] == [velho, meio]


def test_criado_em_ilegivel_nao_conta_como_velho(banco, lotes_dir):
    """Data estranha no banco não pode virar 'vencido há muito tempo'."""
    lote_id = semear(banco, lotes_dir, dias_atras=1)
    with fila.conectar(banco) as con:
        con.execute("UPDATE lotes SET criado_em=? WHERE id=?", ("ontem", lote_id))

    assert retencao.lotes_expirados(banco, dias=90, agora=AGORA) == []
    assert retencao.expira_em(fila.pegar(banco, lote_id), 90) is None


def test_expira_em_diz_a_data_para_a_tela_avisar(banco, lotes_dir):
    lote_id = semear(banco, lotes_dir, dias_atras=10)
    lote = fila.pegar(banco, lote_id)

    assert retencao.expira_em(lote, 90) == AGORA + timedelta(days=80)
    assert retencao.expira_em(lote, 0) is None, "retenção desligada não tem data"


def test_dias_configurados_le_o_ambiente(monkeypatch):
    monkeypatch.delenv("EASYCONTIG_RETENCAO_DIAS", raising=False)
    assert retencao.dias_configurados() == retencao.DIAS_PADRAO

    monkeypatch.setenv("EASYCONTIG_RETENCAO_DIAS", "30")
    assert retencao.dias_configurados() == 30

    monkeypatch.setenv("EASYCONTIG_RETENCAO_DIAS", "0")
    assert retencao.dias_configurados() == 0, "desligar tem que ser possível"


@pytest.mark.parametrize("valor", ["-1", "muitos", "90 dias", " "])
def test_valor_invalido_cai_no_padrao_em_vez_de_esvaziar_o_volume(monkeypatch, valor):
    """`-1` significaria 'tudo já venceu': um erro de digitação no .env não pode
    apagar o volume inteiro."""
    monkeypatch.setenv("EASYCONTIG_RETENCAO_DIAS", valor)
    assert retencao.dias_configurados() == retencao.DIAS_PADRAO
