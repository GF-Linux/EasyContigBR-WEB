"""
Migração de esquema: o banco que já está em produção não pode quebrar.

O caso que importa não é o banco novo — é o banco que já existe no volume da
VPS, criado antes de tudo isto, com corridas de verdade dentro. Estes testes
constroem esse banco antigo à mão e passam a migração por cima.
"""
from __future__ import annotations

import sqlite3

import pytest

from easycontig_web.processamento import fila_de_lotes as fila
from easycontig_web.dados import esquema_e_migracoes as migracoes

# O esquema como era ANTES da coluna `referencia` — copiado do que estava em
# produção. Fica escrito aqui de propósito: se o `_ESQUEMA` de hoje mudar, este
# não pode mudar junto, senão o teste deixa de exercitar a atualização real.
_ESQUEMA_ANTIGO = """
CREATE TABLE lotes (
    id            TEXT PRIMARY KEY,
    dono          TEXT NOT NULL DEFAULT '',
    nome          TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    n_arquivos    INTEGER NOT NULL DEFAULT 0,
    feito         INTEGER NOT NULL DEFAULT 0,
    total         INTEGER NOT NULL DEFAULT 0,
    etapa         TEXT NOT NULL DEFAULT '',
    erro          TEXT NOT NULL DEFAULT '',
    criado_em     TEXT NOT NULL,
    iniciado_em   TEXT,
    terminado_em  TEXT
);
"""


def _banco_antigo(tmp_path, com_corrida=True):
    caminho = tmp_path / "fila.sqlite3"
    con = sqlite3.connect(caminho)
    con.executescript(_ESQUEMA_ANTIGO)
    if com_corrida:
        con.execute(
            "INSERT INTO lotes (id, dono, nome, status, n_arquivos, criado_em)"
            " VALUES ('velho', 'maristela@ufrrj.br', 'F13719', 'pronto', 80,"
            " '2026-07-30T10:00:00+00:00')")
    con.commit()
    con.close()
    return caminho


def test_banco_novo_nasce_na_versao_alvo(tmp_path):
    caminho = tmp_path / "fila.sqlite3"
    fila.criar_esquema(caminho)
    assert migracoes.versao(caminho) == migracoes.VERSAO_ALVO


def test_banco_antigo_ganha_a_coluna_sem_perder_corrida(tmp_path):
    """O que não pode acontecer: atualizar o servidor e a corrida da Maristela
    sumir."""
    caminho = _banco_antigo(tmp_path)
    with fila.conectar(caminho) as con:
        assert "referencia" not in {r[1] for r in con.execute("PRAGMA table_info(lotes)")}

    fila.criar_esquema(caminho)

    with fila.conectar(caminho) as con:
        assert "referencia" in {r[1] for r in con.execute("PRAGMA table_info(lotes)")}
    lote = fila.pegar(caminho, "velho")
    assert lote is not None, "a corrida que já estava no banco desapareceu"
    assert lote["nome"] == "F13719"
    assert lote["dono"] == "maristela@ufrrj.br"
    assert lote["referencia"] == "", "coluna nova devia nascer vazia, não nula"
    assert migracoes.versao(caminho) == migracoes.VERSAO_ALVO


def test_migrar_faz_backup_antes_de_alterar(tmp_path):
    """'Poder voltar atrás' só vale se a cópia existir SEM ninguém lembrar."""
    caminho = _banco_antigo(tmp_path)
    fila.criar_esquema(caminho)

    backups = list(tmp_path.glob("fila.sqlite3.antes-de-migrar-*"))
    assert len(backups) == 1, f"esperava um backup, achei {backups}"

    # e o backup é um banco legível, com a corrida lá dentro
    con = sqlite3.connect(backups[0])
    assert con.execute("SELECT nome FROM lotes WHERE id='velho'").fetchone()[0] == "F13719"
    con.close()


def test_banco_ja_migrado_nao_gera_backup_novo(tmp_path):
    """Senão todo arranque do servidor deixaria um arquivo a mais no volume —
    e são vários processos subindo (web + N trabalhadores)."""
    caminho = tmp_path / "fila.sqlite3"
    fila.criar_esquema(caminho)
    antes = len(list(tmp_path.glob("*antes-de-migrar*")))

    for _ in range(5):
        fila.criar_esquema(caminho)

    assert len(list(tmp_path.glob("*antes-de-migrar*"))) == antes


def test_aplicar_e_idempotente(tmp_path):
    caminho = _banco_antigo(tmp_path)
    assert migracoes.aplicar(caminho) >= 1
    assert migracoes.aplicar(caminho) == 0
    assert migracoes.aplicar(caminho) == 0


def test_restaurar_devolve_o_banco_anterior(tmp_path):
    caminho = _banco_antigo(tmp_path)
    fila.criar_esquema(caminho)
    backup = next(tmp_path.glob("fila.sqlite3.antes-de-migrar-*"))

    fila.novo_lote(caminho, dono="a@b.br", nome="depois", n_arquivos=1)
    assert len(fila.listar(caminho)) == 2

    migracoes.restaurar(backup, caminho)

    lotes = fila.listar(caminho)
    assert len(lotes) == 1 and lotes[0]["id"] == "velho"


def test_migracao_que_falha_nao_avanca_a_versao(tmp_path, monkeypatch):
    """Se uma migração levanta no meio, o banco tem que continuar dizendo a
    versão antiga — senão a próxima subida pula a mudança e o esquema fica num
    estado que ninguém declarou."""
    caminho = tmp_path / "fila.sqlite3"
    fila.criar_esquema(caminho)
    v_inicial = migracoes.versao(caminho)

    def _explode(con):
        con.execute("ALTER TABLE lotes ADD COLUMN meia_feita TEXT DEFAULT ''")
        raise RuntimeError("falha no meio da migração")

    monkeypatch.setattr(migracoes, "MIGRACOES",
                        [*migracoes.MIGRACOES, (99, "quebrada", _explode)])
    with pytest.raises(RuntimeError):
        migracoes.aplicar(caminho)

    assert migracoes.versao(caminho) == v_inicial
    with fila.conectar(caminho) as con:
        colunas = {r[1] for r in con.execute("PRAGMA table_info(lotes)")}
    assert "meia_feita" not in colunas, "o ALTER não foi desfeito pelo ROLLBACK"


# ───────────────────────────── v5: pedidos de amostra e a chave pública
_PERFIS_V4 = """
CREATE TABLE perfis (
    email        TEXT PRIMARY KEY,
    nome         TEXT NOT NULL DEFAULT '',
    laboratorio  TEXT NOT NULL DEFAULT '',
    instituicao  TEXT NOT NULL DEFAULT '',
    sobre        TEXT NOT NULL DEFAULT '',
    especies     TEXT NOT NULL DEFAULT '[]',
    marcadores   TEXT NOT NULL DEFAULT '[]',
    foto         TEXT NOT NULL DEFAULT '',
    atualizado   TEXT NOT NULL DEFAULT '',
    links        TEXT NOT NULL DEFAULT '[]'
);
"""


def _banco_na_v4(tmp_path):
    """O banco como está na VPS hoje: perfis sem `chave`, `user_version=4`."""
    caminho = _banco_antigo(tmp_path)
    con = sqlite3.connect(caminho)
    con.executescript(_PERFIS_V4)
    con.execute("INSERT INTO perfis (email, nome) VALUES (?,?)",
                ("g.freitas@ufrrj.br", "Gustavo"))
    con.execute("INSERT INTO perfis (email, nome) VALUES (?,?)",
                ("m.peckle@ufrrj.br", "Maristela"))
    con.execute("PRAGMA user_version = 4")
    con.commit()
    con.close()
    return caminho


def test_v5_sorteia_chave_para_quem_ja_tinha_perfil(tmp_path):
    """⚠️ A parte da migração 5 que NÃO é `IF NOT EXISTS`.

    A chave é sorteada, então nenhum padrão de coluna a produz: sem este laço,
    todo perfil anterior a esta versão apareceria no `/labs` **sem botão de
    pedir amostra** até a pessoa entrar de novo — e o app não manda e-mail para
    avisar que era só isso que faltava.
    """
    caminho = _banco_na_v4(tmp_path)
    migracoes.aplicar(caminho)
    assert migracoes.versao(caminho) == migracoes.VERSAO_ALVO

    with fila.conectar(caminho) as con:
        chaves = {r["email"]: r["chave"]
                  for r in con.execute("SELECT email, chave FROM perfis")}
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pedidos'"
        ).fetchone(), "a tabela de pedidos não foi criada"

    assert all(chaves.values()), f"perfil ficou sem chave: {chaves}"
    assert len(set(chaves.values())) == len(chaves), "duas contas, a mesma chave"
    # a corrida da Maristela continua lá (a migração não é só sobre perfis)
    assert fila.pegar(caminho, "velho")["nome"] == "F13719"


def test_v5_nao_troca_a_chave_de_quem_ja_tem(tmp_path):
    """Girar a chave faria falhar, sem motivo visível, um formulário de pedido
    já aberto numa aba."""
    caminho = _banco_na_v4(tmp_path)
    migracoes.aplicar(caminho)
    with fila.conectar(caminho) as con:
        antes = {r["email"]: r["chave"]
                 for r in con.execute("SELECT email, chave FROM perfis")}

    assert migracoes.aplicar(caminho) == 0, "rodou de novo o que já estava feito"
    with fila.conectar(caminho) as con:
        depois = {r["email"]: r["chave"]
                  for r in con.execute("SELECT email, chave FROM perfis")}
    assert antes == depois


def test_v5_o_indice_unico_barra_duas_contas_com_a_mesma_chave(tmp_path):
    """A chave é o alvo de um POST: duas contas com a mesma chave significaria
    um pedido chegando na caixa da pessoa errada."""
    caminho = _banco_na_v4(tmp_path)
    migracoes.aplicar(caminho)
    with fila.conectar(caminho) as con:
        dele = con.execute("SELECT chave FROM perfis WHERE email=?",
                           ("g.freitas@ufrrj.br",)).fetchone()["chave"]
    with pytest.raises(sqlite3.IntegrityError):
        with fila.conectar(caminho) as con:
            con.execute("UPDATE perfis SET chave=? WHERE email=?",
                        (dele, "m.peckle@ufrrj.br"))
