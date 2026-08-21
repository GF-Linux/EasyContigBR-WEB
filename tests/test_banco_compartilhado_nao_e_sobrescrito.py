"""
Remontar um banco do catálogo NÃO é o mesmo que montá-lo.

Achado F1 da varredura de 2026-08-20. `remover_banco` exige `EASYCONTIG_ADMINS`
desde 2026-08-06, com a justificativa escrita ao lado: um estagiário recém-
cadastrado não pode derrubar a referência que todo o laboratório usa. Mas o
mesmo comentário liberava montar, "porque é aditivo" — e é, **enquanto o banco
não existe**.

Quando ele já existe, `makeblastdb -out <prefixo>` reescreve os `.nhr/.nin/.nsq`
no lugar: destruição igual à da remoção, entrando pela porta que ficou aberta
por se chamar "montar". Pior, sem trava: duas montagens do mesmo id escrevem os
mesmos arquivos ao mesmo tempo, e o desfecho não é um erro visível — é um banco
corrompido que aparece como "sem acerto" na amostra de alguém, dias depois. É a
ADR 0039 de novo: falha de instalação chegando como resultado da amostra.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.setenv("EASYCONTIG_ADMINS", "chefe@ufrrj.br")
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    return main


def _entrar(c, email):
    c.post("/entrar", data={"email": email}, follow_redirects=False)
    return c


def _fingir_montado(banco_id: str):
    """Cria o `.nsq` que `bancos.existe` procura — sem baixar nada do NCBI."""
    from easycontig_web import configuracao as config
    from easycontig_web.dados import bancos_de_referencia as bancos
    p = bancos.prefixo(config.carregar().data_dir, banco_id).with_suffix(".nsq")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"indice falso")
    return p


def test_conta_comum_nao_remonta_banco_compartilhado_ja_montado(app):
    """⚠️ O achado. Antes, qualquer autenticado sobrescrevia o índice que todo
    o laboratório usa para identificar — sem ser administrador."""
    _fingir_montado("apicomplexa_18s")
    c = _entrar(TestClient(app.app), "estagiario@ufrrj.br")
    r = c.post("/bancos/apicomplexa_18s/montar", follow_redirects=False)
    assert r.status_code == 403
    assert "administra" in r.json()["detail"]


def test_administrador_pode_remontar(app):
    """A operação não desaparece: ela ganha dono, como a remoção tem."""
    _fingir_montado("apicomplexa_18s")
    c = _entrar(TestClient(app.app), "chefe@ufrrj.br")
    r = c.post("/bancos/apicomplexa_18s/montar", follow_redirects=False)
    # passa da autorização; o que vier depois é a montagem de verdade (que aqui
    # falha por não haver rede/NCBI) — mas NÃO é 403.
    assert r.status_code != 403


def test_montar_o_que_ainda_nao_existe_continua_liberado(app):
    """A decisão original fica de pé: criar é aditivo e é o fluxo que a página
    existe para oferecer. Só sobrescrever é que virou ação de administrador."""
    c = _entrar(TestClient(app.app), "estagiario@ufrrj.br")
    r = c.post("/bancos/apicomplexa_18s/montar", follow_redirects=False)
    assert r.status_code != 403


def test_duas_montagens_do_mesmo_banco_nao_correm_juntas(app, monkeypatch):
    """A segunda metade do achado: sem trava, dois `makeblastdb` escrevem o
    mesmo prefixo ao mesmo tempo e corrompem o índice em silêncio."""
    from easycontig_web.web import rotas_de_bancos as rotas
    with rotas._so_uma_montagem("apicomplexa_18s"):
        # já montando: a segunda tentativa é recusada em vez de escrever junto
        c = _entrar(TestClient(app.app), "estagiario@ufrrj.br")
        r = c.post("/bancos/apicomplexa_18s/montar", follow_redirects=False)
        assert r.status_code == 409
        assert "já está sendo montado" in r.json()["detail"]


def test_a_trava_e_liberada_no_fim(app):
    """Trava que não solta transforma um erro momentâneo em banco impossível de
    montar para sempre."""
    from easycontig_web.web import rotas_de_bancos as rotas
    with rotas._so_uma_montagem("apicomplexa_18s"):
        pass
    # e uma segunda vez tem de entrar sem reclamar
    with rotas._so_uma_montagem("apicomplexa_18s"):
        pass


def test_a_trava_solta_mesmo_se_a_montagem_estourar(app):
    """O caminho real de falha: NCBI fora do ar no meio. A trava não pode
    sobreviver ao erro."""
    from easycontig_web.web import rotas_de_bancos as rotas
    with pytest.raises(RuntimeError):
        with rotas._so_uma_montagem("apicomplexa_18s"):
            raise RuntimeError("NCBI caiu")
    with rotas._so_uma_montagem("apicomplexa_18s"):
        pass
