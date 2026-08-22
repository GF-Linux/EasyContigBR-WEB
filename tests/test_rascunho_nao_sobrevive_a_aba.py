"""F3 — o rascunho não pode sobreviver a quem o escreveu.

Dentro do rascunho vão as leituras ALINHADAS de uma corrida ainda não
publicada, vindas de `/api/lotes/.../traco` — rota atrás de `_exigir` E de dono
do lote. No `localStorage` elas ficavam gravadas no perfil do navegador sem
prazo, e só saíam num `/sair` deliberado: numa estação compartilhada de
laboratório, a próxima pessoa a sentar abria o devtools e lia a sequência de
quem estava antes, sem credencial nenhuma e depois de a sessão do servidor já
ter acabado.

Duas travas, e as duas são conferidas aqui porque o Playwright não está
instalado em toda máquina (os testes de navegador se pulam sozinhos, e uma
regressão nisto não pode depender disso):

1. o rascunho mora no `sessionStorage` — sobrevive ao F5, que é o motivo de ele
   existir, e morre com a aba;
2. tem prazo de validade, conferido na leitura e na varredura da tela de
   entrada.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parent.parent
OFICINA = RAIZ / "easycontig_web" / "estatico" / "oficina.js"


@pytest.fixture(scope="module")
def js() -> str:
    return OFICINA.read_text(encoding="utf-8")


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    return TestClient(main.app)


def test_o_rascunho_nao_mora_mais_no_local_storage(js):
    """`localStorage` é o que sobrevive ao fim da sessão — é o achado inteiro."""
    corpo = js.split("function guardarRascunho")[1].split("function lerRascunho")[0]
    assert "localStorage" not in corpo, (
        "o rascunho voltou a ser gravado no localStorage: ele sobrevive ao "
        "fechamento da aba e à expiração da sessão")


def test_o_rascunho_usa_session_storage(js):
    assert "sessionStorage" in js, "o rascunho deixou de usar sessionStorage"


def test_a_leitura_do_rascunho_confere_prazo(js):
    """Ler sem conferir prazo devolve a edição da véspera numa aba esquecida
    aberta a noite inteira."""
    corpo = js.split("function lerRascunho")[1].split("fetch(")[0]
    assert "VALIDADE_MS" in corpo, "a leitura do rascunho não confere mais o prazo"
    assert "removeItem" in corpo, (
        "o rascunho vencido é ignorado mas continua guardado — tem de sair")


def test_o_prazo_e_curto(js):
    """Um prazo longo demais é o mesmo que não ter prazo."""
    linha = [l for l in js.splitlines() if "VALIDADE_MS" in l and "=" in l][0]
    assert "12 * 60 * 60 * 1000" in linha, (
        f"o prazo do rascunho mudou: {linha.strip()} — confira se ainda é curto "
        "o bastante para uma máquina compartilhada")


def test_a_tela_de_entrada_recolhe_o_local_storage_antigo(cliente):
    """Quem editou ANTES da correção tem a sequência gravada em disco no perfil
    do navegador, e ela não sai sozinha: a varredura da tela de entrada é o que
    recolhe esse resto."""
    html = cliente.get("/entrar").text
    assert "localStorage" in html, (
        "a tela de entrada parou de varrer o localStorage — o rascunho gravado "
        "antes da correção fica lá para sempre")
    assert "sessionStorage" in html, (
        "a tela de entrada não varre o sessionStorage, onde o rascunho mora agora")


def test_a_visita_comum_apaga_o_que_venceu(cliente):
    """Numa visita comum o rascunho VIVO é preservado (perder trabalho em
    silêncio é o defeito que a ADR 0052 manda evitar), mas o vencido sai — é
    isto que fecha a sessão que expirou sozinha numa máquina compartilhada."""
    html = cliente.get("/entrar").text
    assert "12 * 60 * 60 * 1000" in html, (
        "a varredura da tela de entrada não confere prazo: rascunho de sessão "
        "expirada fica para o próximo usuário da máquina")
