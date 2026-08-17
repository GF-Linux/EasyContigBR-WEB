"""
Testes da camada web: quem vê o quê, o que é recusado e o que NÃO acontece
dentro da requisição.

Não montam nada — o núcleo científico já tem os 151 testes dele no repo do
EasyContig. Aqui o que se protege é a fronteira.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

#! Os .ab1 são FABRICADOS, não lidos de um caminho da máquina do autor.
#!   Ver `tests/amostras_sinteticas.py` — o caminho antigo derrubava 23 testes
#!   fora daquele computador.
from amostras_sinteticas import PASTA as AB1


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    from easycontig_web import servidor_web as main
    importlib.reload(main)                 # o módulo lê a config na importação
    return TestClient(main.app)


def _entrar(c, email="gustavo@ufrrj.br"):
    c.post("/entrar", data={"email": email}, follow_redirects=False)
    return c


# Toda corrida declara contra o que identifica (trava de 2026-08-06). "curado"
# é o banco do RAIC, que é o padrão e o que validou os resultados até aqui.
REF = {"referencia": "curado"}


def _envio(nomes):
    return [("arquivos", (n, (AB1 / n).read_bytes(), "application/octet-stream"))
            for n in nomes]


# --------------------------------------------------------------------- sessão
def test_sem_login_a_raiz_manda_para_entrar(cliente):
    r = cliente.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/entrar"


def test_sem_login_nao_se_cria_lote(cliente):
    r = cliente.post("/lotes", files=_envio(["amostra12_F_BTF2.ab1"]), data=REF)
    assert r.status_code == 401


def test_dominio_restrito_recusa_conta_de_fora(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "ufrrj.br")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    c = TestClient(main.app)
    r = c.post("/entrar", data={"email": "alguem@gmail.com"}, follow_redirects=False)
    # O motivo da recusa saiu da URL em 2026-08-11 e vai na sessão assinada;
    # o que importa é que ela recusou e que a página diz por quê.
    assert r.headers["location"] == "/entrar"
    assert "fora do domínio" in c.get("/entrar").text
    assert c.get("/", follow_redirects=False).headers["location"] == "/entrar"


def test_login_dev_recusado_quando_desligado(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "google")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    r = TestClient(main.app).post("/entrar", data={"email": "x@ufrrj.br"})
    assert r.status_code == 403


# ---------------------------------------------------------------------- lotes
def test_lote_criado_fica_recebendo_e_depois_na_fila(cliente):
    from easycontig_web.processamento import fila_de_lotes as fila
    from easycontig_web import servidor_web as main
    _entrar(cliente)
    r = cliente.post("/lotes", files=_envio(["amostra12_F_BTF2.ab1",
                                             "amostra12_R_BTR2.ab1"]),
                     data={"nome": "corrida", **REF}, follow_redirects=False)
    assert r.status_code == 303
    lote_id = r.headers["location"].rsplit("/", 1)[-1]
    lote = fila.pegar(main.cfg.sqlite_path, lote_id)
    # a requisição termina com o lote PRONTO PARA a fila, não processado:
    # nada de pesado pode acontecer dentro do ciclo HTTP (ADR 0050)
    assert lote["status"] == fila.NA_FILA
    assert lote["n_arquivos"] == 2


def test_arquivo_que_nao_e_trace_e_recusado(cliente):
    _entrar(cliente)
    r = cliente.post("/lotes", files=[("arquivos", ("notas.txt", b"nada", "text/plain"))],
                          data=REF)
    assert r.status_code == 400


def test_extensao_estranha_e_ignorada_mas_o_ab1_passa(cliente):
    from easycontig_web.processamento import fila_de_lotes as fila
    from easycontig_web import servidor_web as main
    _entrar(cliente)
    envio = _envio(["amostra12_F_BTF2.ab1", "amostra12_R_BTR2.ab1"])
    envio.append(("arquivos", ("leia-me.txt", b"nada", "text/plain")))
    r = cliente.post("/lotes", files=envio, data=REF, follow_redirects=False)
    lote_id = r.headers["location"].rsplit("/", 1)[-1]
    assert fila.pegar(main.cfg.sqlite_path, lote_id)["n_arquivos"] == 2


def test_teto_de_arquivos(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_MAX_ARQUIVOS", "1")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    c = _entrar(TestClient(main.app))
    r = c.post("/lotes", files=_envio(["amostra12_F_BTF2.ab1", "amostra12_R_BTR2.ab1"]), data=REF)
    assert r.status_code == 413


def test_nome_com_travessia_de_caminho_vira_so_o_nome(cliente):
    """O navegador manda 'pasta/sub/arquivo.ab1' quando se escolhe uma pasta."""
    from easycontig_web.processamento import executor_de_lote as executor
    from easycontig_web import servidor_web as main
    _entrar(cliente)
    dados = (AB1 / "amostra12_F_BTF2.ab1").read_bytes()
    envio = [("arquivos", ("../../../fora.ab1", dados, "application/octet-stream")),
             ("arquivos", ("sub/dir/amostra12_R_BTR2.ab1", dados, "application/octet-stream"))]
    r = cliente.post("/lotes", files=envio, data=REF, follow_redirects=False)
    lote_id = r.headers["location"].rsplit("/", 1)[-1]
    entrada = executor.pastas_do_lote(main.cfg, lote_id)["entrada"]
    nomes = sorted(p.name for p in entrada.iterdir())
    assert nomes == ["amostra12_R_BTR2.ab1", "fora.ab1"]
    assert not (entrada.parent.parent.parent / "fora.ab1").exists()


# ------------------------------------------------------------------ isolamento
def test_um_usuario_nao_enxerga_o_lote_do_outro(cliente):
    """.ab1 não publicado do laboratório não pode vazar por link colado."""
    _entrar(cliente, "gustavo@ufrrj.br")
    r = cliente.post("/lotes", files=_envio(["amostra12_F_BTF2.ab1"]), data=REF,
                     follow_redirects=False)
    lote_id = r.headers["location"].rsplit("/", 1)[-1]

    cliente.get("/sair")
    _entrar(cliente, "outra@ufrrj.br")
    # 404 e não 403: responder "proibido" já confirmaria que o lote existe
    assert cliente.get(f"/lotes/{lote_id}").status_code == 404
    assert cliente.get(f"/api/lotes/{lote_id}").status_code == 404
    assert cliente.get(f"/lotes/{lote_id}/resultado.csv").status_code == 404


def test_resultado_so_sai_com_o_lote_pronto(cliente):
    _entrar(cliente)
    r = cliente.post("/lotes", files=_envio(["amostra12_F_BTF2.ab1"]), data=REF,
                     follow_redirects=False)
    lote_id = r.headers["location"].rsplit("/", 1)[-1]
    assert cliente.get(f"/lotes/{lote_id}/relatorio").status_code == 409


def test_saude_lista_as_dependencias(cliente):
    """⚠️ Passou a exigir sessão em 2026-08-06. Sem ela, `/saude` entregava a
    qualquer um os caminhos absolutos do contêiner, quais ferramentas estão
    instaladas e onde, e a profundidade da fila **sem filtro de dono** — quem
    repetisse a chamada acompanhava quando o laboratório trabalha. O
    `healthcheck` do compose segue atendido: ele chama sem cookie e só olha o
    código (ver `tests/test_achados_verificados.py`)."""
    _entrar(cliente)
    d = cliente.get("/saude").json()
    assert {x["item"] for x in d["dependencias"]} == {
        "tracy", "blastn", "banco 18S", "banco 16S"}


# --- workspace da amostra: o traço --------------------------------------------
def test_rota_do_traco_exige_sessao(cliente):
    """O traço carrega o conteúdo do .ab1 — não pode sair sem dono."""
    r = cliente.get("/api/lotes/qualquer/amostras/x/traco")
    assert r.status_code == 401


def test_traco_de_lote_alheio_da_404(cliente):
    """O traço carrega o conteúdo do .ab1: a checagem de dono vale aqui também."""
    _entrar(cliente, "gustavo@ufrrj.br")
    r = cliente.post("/lotes", files=_envio(["amostra12_F_BTF2.ab1"]), data=REF,
                     follow_redirects=False)
    lote_id = r.headers["location"].rsplit("/", 1)[-1]

    cliente.get("/sair")
    _entrar(cliente, "outra@ufrrj.br")
    assert cliente.get(
        f"/api/lotes/{lote_id}/amostras/amostra12/traco").status_code == 404


# --- a trava da referência ----------------------------------------------------
def test_sem_referencia_nao_processa(cliente):
    """Montar consenso sem ter contra o que comparar produz um "não achei" que
    não significa nada. Escolher a referência é parte do envio."""
    _entrar(cliente)
    r = cliente.post("/lotes", files=_envio(["amostra12_F_BTF2.ab1"]))
    assert r.status_code == 400
    assert "referência" in r.json()["detail"]


def test_referencia_inventada_e_recusada(cliente):
    _entrar(cliente)
    r = cliente.post("/lotes", files=_envio(["amostra12_F_BTF2.ab1"]),
                     data={"referencia": "../etc/passwd"})
    assert r.status_code == 400


def test_referencia_nao_montada_e_recusada(cliente):
    """Existe no catálogo mas ninguém baixou: oferecer seria oferecer um erro."""
    _entrar(cliente)
    r = cliente.post("/lotes", files=_envio(["amostra12_F_BTF2.ab1"]),
                     data={"referencia": "cestoda_coi"})
    assert r.status_code == 400
    assert "montada" in r.json()["detail"]


def test_a_referencia_escolhida_fica_gravada_no_lote(cliente):
    """Sem isto o relatório não sabe dizer contra o que comparou."""
    from easycontig_web.processamento import fila_de_lotes as fila
    from easycontig_web import servidor_web as main
    _entrar(cliente)
    r = cliente.post("/lotes", files=_envio(["amostra12_F_BTF2.ab1"]),
                     data=REF, follow_redirects=False)
    lote_id = r.headers["location"].rsplit("/", 1)[-1]
    assert fila.pegar(main.cfg.sqlite_path, lote_id)["referencia"] == "curado"


# --- fluxo indevido -----------------------------------------------------------
def test_pagina_com_sessao_nao_vai_para_o_cache(cliente):
    """Sem isto, apagar uma corrida e apertar VOLTAR trazia a página dela de
    volta do cache — com o botão de apagar e tudo — e qualquer clique dava 404.
    A corrida "sumia" sem explicação."""
    _entrar(cliente)
    r = cliente.get("/")
    assert "no-store" in r.headers.get("cache-control", "")


def test_estatico_continua_cacheavel(cliente):
    """O JS do workspace se beneficia do cache; a regra é para página, não asset."""
    r = cliente.get("/estatico/oficina.js")
    assert r.status_code == 200
    assert "no-store" not in r.headers.get("cache-control", "")


def test_perfil_ignora_e_mail_na_url(cliente):
    """Não existe caminho para o perfil de outra pessoa: o perfil é sempre o da
    sessão, e passar `?email=` de alguém não muda isso."""
    _entrar(cliente, "gustavo@ufrrj.br")
    r = cliente.get("/perfil?email=outra@ufrrj.br")
    assert r.status_code == 200
    assert "gustavo@ufrrj.br" in r.text
    assert "outra@ufrrj.br" not in r.text
    assert cliente.get("/perfil/outra@ufrrj.br").status_code == 404
