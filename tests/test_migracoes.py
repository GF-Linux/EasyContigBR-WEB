"""
Migração de esquema: o banco que já está em produção não pode quebrar.

O caso que importa não é o banco novo — é o banco que já existe no volume da
VPS, criado antes de tudo isto, com corridas de verdade dentro. Estes testes
constroem esse banco antigo à mão e passam a migração por cima.
"""
from __future__ import annotations

import sqlite3

import pytest

from easycontig_web import fila, migracoes

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
