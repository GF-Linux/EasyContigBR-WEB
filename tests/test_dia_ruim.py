"""
O dia ruim: disco cheio, envios simultâneos e o relógio saltando.

Três cenários que nunca haviam sido exercitados, simulados em 2026-08-06 a
pedido do autor. Os três acharam defeito, e nenhum deles aparece num dia normal
— é o gênero de coisa que só se descobre no servidor, no dia, com o dado de
alguém no meio.

O que cada um achou, medido antes de consertar:

1. **Disco cheio** (tmpfs de 1 MB de verdade, 6 `.ab1` reais de ~2 MB): a
   limpeza estava certa — lote marcado `falhou`, pasta removida, nada na fila —
   mas quem enviou recebia `500 Internal Server Error` cru, e o aviso do
   navegador mandava "corrija a seleção e envie de novo". Conselho errado: não
   há nada errado com a seleção, e reenviar não resolve.
2. **Oito envios simultâneos da mesma conta**, teto de 1: **sete entraram**. A
   cota era conferida antes de o lote existir; todos passaram pela contagem
   antes de qualquer um aparecer nela.
3. **Relógio um ano à frente**, retenção de 30 dias: a faxina apagou **as 20
   corridas do dia** numa passada. Apagar não tem desfazer.
"""
from __future__ import annotations

import datetime as dt
import errno
import importlib
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from easycontig_web import fila, retencao


# ══════════════════════════════════════════ 1. disco cheio
@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.setenv("EASYCONTIG_LIM_ENVIO", "0")
    monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
    from easycontig_web import main
    importlib.reload(main)
    c = TestClient(main.app, raise_server_exceptions=False)
    c.post("/entrar", data={"email": "gustavo@ufrrj.br"}, follow_redirects=False)
    c._main = main
    return c


AB1 = [("arquivos", ("a_F.ab1", b"ABIF" + b"\0" * 400, "application/octet-stream")),
       ("arquivos", ("a_R.ab1", b"ABIF" + b"\0" * 400, "application/octet-stream"))]
FORM = {"nome": "par", "modo": "par", "referencia": "curado"}


def test_disco_cheio_diz_que_e_do_servidor_e_nao_da_selecao(cliente, monkeypatch):
    """507 e uma frase que não manda a pessoa mexer no que está certo."""
    real = Path.open

    def sem_espaco(self, *a, **k):
        if self.suffix == ".ab1":
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "open", sem_espaco)
    r = cliente.post("/lotes", files=AB1, data=FORM, follow_redirects=False)

    assert r.status_code == 507, "disco cheio não pode sair como 500 anônimo"
    texto = r.text.lower()
    assert "espaço em disco" in texto
    assert "não é a sua seleção" in texto, "manda a pessoa corrigir o que está certo"


def test_disco_cheio_nao_deixa_lote_meio_gravado_na_fila(cliente, monkeypatch):
    """O que já estava certo antes, e não pode regredir: nada meio-escrito
    chega ao trabalhador, e nenhuma pasta fica ocupando o volume."""
    real = Path.open

    def sem_espaco(self, *a, **k):
        if self.suffix == ".ab1":
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "open", sem_espaco)
    cliente.post("/lotes", files=AB1, data=FORM, follow_redirects=False)

    caminho = cliente._main.cfg.sqlite_path
    with fila.conectar(caminho) as con:
        linhas = [dict(r) for r in con.execute("SELECT status, erro FROM lotes")]
    assert linhas and all(l["status"] == fila.FALHOU for l in linhas)
    assert "disco" in linhas[0]["erro"], "o erro gravado não diz o que houve"
    assert not fila.reivindicar(caminho), "lote quebrado foi para o trabalhador"
    lotes_dir = cliente._main.cfg.lotes_dir
    assert not any(lotes_dir.iterdir()), "sobrou pasta ocupando o volume"


def test_erro_nao_previsto_sai_com_casca_e_sem_vazar_o_traceback(cliente, monkeypatch):
    """Antes: `Internal Server Error` em texto puro, sem caminho de volta."""
    real = Path.open

    def quebrado(self, *a, **k):
        if self.suffix == ".ab1":
            raise RuntimeError("/caminho/secreto/do/servidor explodiu")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "open", quebrado)
    r = cliente.post("/lotes", files=AB1, data=FORM, follow_redirects=False,
                     headers={"accept": "text/html"})
    assert r.status_code == 500
    assert "não foi por causa do que você mandou" in r.text
    assert "/caminho/secreto" not in r.text, "o traceback vazou para a tela"


# ══════════════════════════════════════════ 2. envios simultâneos
def test_oito_envios_ao_mesmo_tempo_nao_furam_o_teto(tmp_path, monkeypatch):
    """A conferência da cota mora DENTRO da transação que cria o lote."""
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.setenv("EASYCONTIG_MAX_LOTES_ATIVOS", "1")
    monkeypatch.setenv("EASYCONTIG_LIM_ENVIO", "0")
    monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
    from easycontig_web import main
    importlib.reload(main)
    c = TestClient(main.app, raise_server_exceptions=False)
    c.post("/entrar", data={"email": "gustavo@ufrrj.br"}, follow_redirects=False)

    n = 8
    codigos = [None] * n
    largada = threading.Barrier(n)

    def enviar(i):
        largada.wait()                      # todos partem no mesmo instante
        codigos[i] = c.post("/lotes", files=AB1, data=FORM,
                            follow_redirects=False).status_code

    ts = [threading.Thread(target=enviar, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    with fila.conectar(main.cfg.sqlite_path) as con:
        criados = con.execute("SELECT COUNT(*) FROM lotes").fetchone()[0]
    assert criados == 1, f"teto de 1 e {criados} lotes criados: a cota foi furada"
    assert codigos.count(413) == n - 1, f"recusas erradas: {codigos}"


def test_a_recusa_por_cota_diz_quantas_e_quantas_cabem(tmp_path):
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    fila.novo_lote(banco, dono="a@ufrrj.br", nome="1", n_arquivos=2, teto_ativos=2)
    fila.novo_lote(banco, dono="a@ufrrj.br", nome="2", n_arquivos=2, teto_ativos=2)
    with pytest.raises(fila.CotaEstourada) as e:
        fila.novo_lote(banco, dono="a@ufrrj.br", nome="3", n_arquivos=2, teto_ativos=2)
    assert e.value.ativos == 2 and e.value.teto == 2


def test_o_teto_e_por_conta_e_nao_do_servidor(tmp_path):
    """A vizinha não pode gastar a vaga de ninguém."""
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    fila.novo_lote(banco, dono="a@ufrrj.br", nome="1", n_arquivos=2, teto_ativos=1)
    fila.novo_lote(banco, dono="b@ufrrj.br", nome="1", n_arquivos=2, teto_ativos=1)


def test_lote_terminado_devolve_a_vaga(tmp_path):
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    lid = fila.novo_lote(banco, dono="a@ufrrj.br", nome="1", n_arquivos=2, teto_ativos=1)
    fila.liberar_para_fila(banco, lid, 2)
    fila.reivindicar(banco)
    fila.concluir(banco, lid)
    fila.novo_lote(banco, dono="a@ufrrj.br", nome="2", n_arquivos=2, teto_ativos=1)


# ══════════════════════════════════════════ 3. o relógio saltando
def _acervo(tmp_path, n=20):
    banco = tmp_path / "f.sqlite3"
    lotes = tmp_path / "lotes"
    lotes.mkdir()
    fila.criar_esquema(banco)
    for i in range(n):
        lid = fila.novo_lote(banco, dono="a@ufrrj.br", nome=f"c{i}", n_arquivos=2)
        fila.liberar_para_fila(banco, lid, 2)
        (lotes / lid).mkdir()
        with fila.conectar(banco) as con:
            con.execute("UPDATE lotes SET status='pronto' WHERE id=?", (lid,))
    return banco, lotes


def test_relogio_um_ano_a_frente_nao_apaga_o_acervo(tmp_path):
    """O caso medido: 20 corridas do dia, retenção de 30 dias, relógio saltando.
    Antes apagava as vinte."""
    banco, lotes = _acervo(tmp_path)
    daqui_a_um_ano = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365)
    r = retencao.faxina(banco, lotes, dias=30, agora=daqui_a_um_ano)

    assert r["apagados"] == 0, "o acervo inteiro foi apagado de uma vez"
    assert r["travado"], "travou sem dizer por quê"
    assert "relógio" in r["travado"] and "30" in r["travado"], (
        "a mensagem não manda conferir relógio nem prazo")
    assert len(list(lotes.iterdir())) == 20


def test_o_freio_e_destravavel_por_quem_decide(tmp_path, monkeypatch):
    """A primeira faxina de uma instalação antiga vence muita coisa com razão.
    O ponto do freio não é impedir: é fazer alguém decidir, em vez do relógio."""
    banco, lotes = _acervo(tmp_path)
    monkeypatch.setenv("EASYCONTIG_EXPURGO_EM_MASSA", "1")
    daqui_a_um_ano = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365)
    r = retencao.faxina(banco, lotes, dias=30, agora=daqui_a_um_ano)
    assert r["apagados"] == 20 and not r["travado"]


def test_o_dia_normal_nao_e_travado(tmp_path):
    """Vencer alguns de muitos é rotina e tem de passar direto — senão o freio
    vira um desligamento silencioso da retenção."""
    banco, lotes = _acervo(tmp_path, n=40)
    ids = sorted(p.name for p in lotes.iterdir())[:6]
    velho = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)).isoformat()
    with fila.conectar(banco) as con:
        for i in ids:
            con.execute("UPDATE lotes SET criado_em=? WHERE id=?", (velho, i))
    r = retencao.faxina(banco, lotes, dias=30)
    assert r["apagados"] == 6 and not r["travado"]


def test_instalacao_pequena_nao_trava_por_apagar_a_maioria(tmp_path):
    """Com 3 lotes no total, apagar 2 é rotina, não catástrofe."""
    banco, lotes = _acervo(tmp_path, n=3)
    velho = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)).isoformat()
    ids = sorted(p.name for p in lotes.iterdir())[:2]
    with fila.conectar(banco) as con:
        for i in ids:
            con.execute("UPDATE lotes SET criado_em=? WHERE id=?", (velho, i))
    r = retencao.faxina(banco, lotes, dias=30)
    assert r["apagados"] == 2 and not r["travado"]
