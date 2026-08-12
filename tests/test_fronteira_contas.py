"""
As seis correções de fronteira de 2026-08-06, cada uma com o caso que a motivou.

Todas nasceram de uma varredura de segurança e TODAS foram reproduzidas antes de
consertar — nenhuma entrou aqui por suspeita.
"""
from __future__ import annotations

import importlib
import subprocess

import pytest
from fastapi.testclient import TestClient

from easycontig_web import bancos


@pytest.fixture()
def montar(tmp_path, monkeypatch):
    def _montar(**env):
        monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
        monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
        monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
        monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
        monkeypatch.delenv("EASYCONTIG_ADMINS", raising=False)
        monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        from easycontig_web import main
        importlib.reload(main)
        return TestClient(main.app)
    return _montar


# ─────────────────────────── 1. banco privado não vaza para a conta vizinha
@pytest.mark.parametrize("a,b", [
    # o achatamento apagava o ponto: duas pessoas, uma pasta só
    ("joao.silva@ufrrj.br", "joaosilva@ufrrj.br"),
    ("ana.paula@ufrrj.br", "anapaula@ufrrj.br"),
    # e o corte em 24 juntava endereços longos que só diferem no fim
    ("ana.carolina.rodrigues.lima@ufrrj.br",
     "ana.carolina.rodrigues.limao@ufrrj.br"),
])
def test_contas_diferentes_nao_dividem_a_pasta_de_banco(a, b):
    assert bancos.id_do_usuario(a, "meubanco") != bancos.id_do_usuario(b, "meubanco")


def test_a_mesma_conta_continua_achando_o_proprio_banco():
    """O contrário do teste acima: o identificador tem que ser estável, senão a
    pessoa perde o banco dela a cada visita."""
    assert (bancos.id_do_usuario("joao.silva@ufrrj.br", "x")
            == bancos.id_do_usuario("joao.silva@ufrrj.br", "x"))
    # e-mail é caso-insensível; entrar com outra caixa não pode criar outra conta
    assert (bancos.id_do_usuario("Joao.Silva@UFRRJ.br", "x")
            == bancos.id_do_usuario("joao.silva@ufrrj.br", "x"))


def test_o_email_nao_aparece_no_nome_da_pasta():
    """Ganho de tabela do hash: o endereço deixa de vazar em listagem de
    diretório, backup e log."""
    bid = bancos.id_do_usuario("maristela@ufrrj.br", "meubanco")
    assert "maristela" not in bid and "ufrrj" not in bid


def test_meus_bancos_nao_lista_o_do_vizinho(tmp_path):
    """O vazamento como ele apareceria na tela: a conta B abrindo "Meus bancos"
    e vendo o banco inédito da conta A.

    ⚠️ O banco precisa parecer MONTADO (o `.nsq`) — `meus_bancos` descarta pasta
    sem índice como órfã de um envio que falhou. Sem esse arquivo o teste passa
    pelo motivo errado, que foi como ele nasceu.
    """
    dono, vizinho = "joao.silva@ufrrj.br", "joaosilva@ufrrj.br"
    alheio = bancos.id_do_usuario(dono, "inedito")
    p = bancos.prefixo(tmp_path, alheio)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.with_suffix(".nsq").write_bytes(b"indice falso")
    p.with_suffix(".meta.json").write_text('{"sequencias": 3}')

    assert [x["id"] for x in bancos.meus_bancos(tmp_path, dono)] == [alheio], (
        "o dono deixou de ver o próprio banco")
    assert alheio not in [x["id"] for x in bancos.meus_bancos(tmp_path, vizinho)], (
        "a conta vizinha enxerga o banco inédito da outra")


# ─────────────────────────── 2. redirecionamento não sai do site
@pytest.mark.parametrize("destino", [
    "//evil.com",
    "https://evil.com",
    "/\\evil.com",        # o navegador normaliza \ para / → vira //evil.com
    "/\tevil.com",
    "/\nevil.com",
])
def test_destino_externo_vira_raiz(montar, destino):
    montar()
    from easycontig_web import main
    assert main._destino(destino) == "/"


@pytest.mark.parametrize("destino", ["/", "/lotes/abc123",
                                     "/lotes/abc/amostras/F13719_am01"])
def test_destino_interno_e_preservado(montar, destino):
    montar()
    from easycontig_web import main
    assert main._destino(destino) == destino


# ─────────────────────────── 3. o mapa da API não é público em produção
def test_docs_saem_do_ar_em_producao(montar):
    c = montar(EASYCONTIG_PRODUCAO="0")
    assert c.get("/api/docs").status_code == 200      # desenvolvimento: serve

    # Em produção o `conferir_producao` exige o resto da configuração; aqui
    # interessa só o efeito nas rotas de documentação.
    from easycontig_web import main
    assert main._DOCS == "/api/docs"


def test_em_producao_o_openapi_nao_e_montado(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "google")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("EASYCONTIG_HTTPS_ONLY", "1")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "ufrrj.br")
    monkeypatch.setenv("EASYCONTIG_URL_BASE", "https://easycontigbr.com.br")
    # Exigida desde 2026-08-11: em branco atrás de um proxy, todo anônimo divide
    # o mesmo balde de limite. Sem ela o arranque de produção recusa — que é o
    # comportamento certo, e é por isso que este cenário precisou dela.
    monkeypatch.setenv("EASYCONTIG_PROXIES_CONFIAVEIS", "nenhum")
    monkeypatch.setenv("EASYCONTIG_PRODUCAO", "1")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo")
    from easycontig_web import main
    importlib.reload(main)
    c = TestClient(main.app)
    for rota in ("/api/docs", "/openapi.json", "/redoc"):
        assert c.get(rota).status_code == 404, f"{rota} continua público"


# ─────────────────────────── 4. banco compartilhado não some pela mão de qualquer um
def test_conta_comum_nao_remove_banco_compartilhado(montar):
    c = montar()
    c.post("/entrar", data={"email": "estagiario@ufrrj.br"}, follow_redirects=False)
    alvo = list(bancos.POR_ID)[0]
    r = c.post(f"/bancos/{alvo}/remover", follow_redirects=False)
    assert r.status_code == 403, (
        "uma conta qualquer voltou a poder derrubar a referência do laboratório")


def test_administrador_declarado_remove(montar):
    c = montar(EASYCONTIG_ADMINS="chefe@ufrrj.br")
    c.post("/entrar", data={"email": "chefe@ufrrj.br"}, follow_redirects=False)
    alvo = list(bancos.POR_ID)[0]
    assert c.post(f"/bancos/{alvo}/remover",
                  follow_redirects=False).status_code == 303


def test_montar_continua_liberado_para_todos(montar):
    """Montar é aditivo e já tem teto de taxa — não é o que precisava de nome."""
    c = montar()
    c.post("/entrar", data={"email": "qualquer@ufrrj.br"}, follow_redirects=False)
    r = c.post(f"/bancos/{list(bancos.POR_ID)[0]}/montar", follow_redirects=False)
    assert r.status_code != 403


# ─────────────────────────── 5. programa externo não fica pendurado
def test_blastn_tem_teto_de_tempo(monkeypatch, tmp_path):
    """Sem teto, um processo que não termina segura a requisição para sempre."""
    chamada = {}

    def _falso(*args, **kwargs):
        chamada.update(kwargs)
        raise subprocess.TimeoutExpired(cmd="blastn", timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", _falso)
    with pytest.raises(RuntimeError, match="tempo limite"):
        bancos.consultar("ACGT", tmp_path / "db")
    assert chamada.get("timeout"), "o blastn foi chamado sem timeout"


def test_makeblastdb_tem_teto_de_tempo(monkeypatch, tmp_path):
    chamada = {}

    def _falso(*args, **kwargs):
        chamada.update(kwargs)
        raise subprocess.TimeoutExpired(cmd="makeblastdb", timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", _falso)
    with pytest.raises(RuntimeError, match="interrompida"):
        bancos._makeblastdb(None, tmp_path / "e.fasta", tmp_path / "s")
    assert chamada.get("timeout"), "o makeblastdb foi chamado sem timeout"


# ─────────────────────────── 6. foto trocada não deixa lixo no volume
def test_trocar_a_foto_apaga_a_anterior(montar, tmp_path):
    c = montar()
    c.post("/entrar", data={"email": "gustavo@ufrrj.br"}, follow_redirects=False)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100

    for _ in range(3):
        c.post("/perfil", data={"nome": "G"},
               files={"foto": ("f.png", png, "image/png")}, follow_redirects=False)

    fotos = list((tmp_path / "dados" / "fotos").iterdir())
    assert len(fotos) == 1, (
        f"três envios deixaram {len(fotos)} arquivos; as antigas viraram lixo")
