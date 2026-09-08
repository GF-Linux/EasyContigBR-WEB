"""
teste_banco_sqlite.py — o arquivo fila.sqlite3 sobrevive a uma atualizacao do EasyContig: o banco antigo
ganha o que falta sem perder linha, ha um backup de UM arquivo antes de mexer, e a migracao que quebra
nao deixa o banco pela metade.

    banco novo ------> preparar() ---> nasce inteiro em VERSAO_ALVO (0 migracoes)
    banco antigo ----> preparar() ---> migrar(): backup + SAVEPOINT por migracao ---> ESQUEMA (IF NOT EXISTS)
                                          '--> falhou: ROLLBACK TO, versao fica na ultima boa, excecao sobe
    deu errado ------> restaurar(backup) ---> o banco vira a copia (com o servidor parado)

O QUE SE PROTEGE
  1. o banco de producao tem lotes de gente real (e 'velho', da Maristela, e o representante deles).
  2. um backup por atualizacao, nao um por arranque: 5 arranques seguidos NAO enchem o volume de copias.
  3. a chave publica do perfil (v5) e sorteada UMA vez e nunca trocada por uma migracao seguinte:
     trocar a chave quebra o link /labs/<chave> que o laboratorio ja divulgou.
"""

import sqlite3

import pytest

from easycontig_web.dados import banco_sqlite
from easycontig_web.dados.banco_sqlite import MIGRACOES, VERSAO_ALVO, BancoSqlite, colunas, tabela_existe
from easycontig_web.contas.perfil_do_laboratorio import Perfis
from easycontig_web.processamento.fila_de_lotes import FilaDeLotes


ESQUEMA_ANTIGO = """
CREATE TABLE lotes (
    id TEXT PRIMARY KEY,
    dono TEXT NOT NULL DEFAULT '',
    nome TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    n_arquivos INTEGER NOT NULL DEFAULT 0,
    feito INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    etapa TEXT NOT NULL DEFAULT '',
    erro TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL,
    iniciado_em TEXT,
    terminado_em TEXT
);
INSERT INTO lotes (id, dono, nome, status, n_arquivos, criado_em)
VALUES ('velho', 'maristela@ufrrj.br', 'F13719', 'pronto', 80, '2026-07-30T10:00:00+00:00');
"""  #* o fila.sqlite3 como saiu da VPS em julho de 2026: sem referencia, sem trabalhador, sem bytes, versao 0

PERFIS_V4 = """
CREATE TABLE perfis (
    email TEXT PRIMARY KEY,
    nome TEXT NOT NULL DEFAULT '',
    laboratorio TEXT NOT NULL DEFAULT '',
    instituicao TEXT NOT NULL DEFAULT '',
    sobre TEXT NOT NULL DEFAULT '',
    especies TEXT NOT NULL DEFAULT '[]',
    marcadores TEXT NOT NULL DEFAULT '[]',
    foto TEXT NOT NULL DEFAULT '',
    atualizado TEXT NOT NULL DEFAULT '',
    links TEXT NOT NULL DEFAULT '[]'
);
INSERT INTO perfis (email, nome) VALUES ('g.freitas@ufrrj.br', 'Gustavo');
INSERT INTO perfis (email, nome) VALUES ('m.peckle@ufrrj.br', 'Maristela');
PRAGMA user_version = 4;
"""  #* perfis de antes da chave publica (v5): dois laboratorios cadastrados e nenhuma chave




def banco_antigo(pasta, extra=''):

    #* um fila.sqlite3 de julho de 2026 na pasta, com a corrida 'velho' dentro; `extra` e SQL a mais (ex.: PERFIS_V4)

    caminho = pasta / 'fila.sqlite3'

    con = sqlite3.connect(caminho)

    con.executescript(ESQUEMA_ANTIGO + extra)

    con.close()

    return BancoSqlite(caminho)




def backups(banco):

    return sorted(banco.caminho.parent.glob(f'fila.sqlite3{banco_sqlite.SUFIXO_DO_BACKUP}*'))




def lotes_no(caminho):

    con = sqlite3.connect(caminho)

    try:
        return [linha[0] for linha in con.execute('SELECT id FROM lotes ORDER BY id')]

    finally:
        con.close()




def teste_banco_novo_nasce_na_versao_alvo_sem_migrar(tmp_path):

    banco = BancoSqlite(tmp_path / 'dados' / 'fila.sqlite3')

    assert banco.preparar() == 0, 'banco novo nao "migra": nasce inteiro'

    assert banco.versao == VERSAO_ALVO

    with banco.conectar() as con:
        for tabela in ('lotes', 'perfis', 'pedidos', 'arvores'):
            assert tabela_existe(con, tabela), tabela

    assert backups(banco) == [], 'nao havia nada para proteger'




def teste_banco_antigo_ganha_as_colunas_sem_perder_a_corrida(tmp_path):

    banco = banco_antigo(tmp_path)

    assert banco.versao == 0

    rodadas = banco.preparar()

    assert rodadas == len(MIGRACOES) and banco.versao == VERSAO_ALVO

    with banco.conectar() as con:
        nomes = colunas(con, 'lotes')
        assert {'referencia', 'trabalhador', 'bytes_previstos'} <= set(nomes)
        assert con.execute("SELECT referencia FROM lotes WHERE id='velho'").fetchone()[0] == '', 'coluna nova nasce vazia, nao NULL'

    velho = FilaDeLotes(banco).pegar('velho')

    assert velho is not None and velho.dono == 'maristela@ufrrj.br' and velho.nome == 'F13719' and velho.n_arquivos == 80




def teste_migrar_deixa_um_backup_legivel_do_banco_antigo(tmp_path):

    banco = banco_antigo(tmp_path)

    banco.preparar()

    copias = backups(banco)

    assert len(copias) == 1, copias

    copia = sqlite3.connect(copias[0])

    try:
        assert copia.execute("SELECT nome FROM lotes WHERE id='velho'").fetchone()[0] == 'F13719'
        assert 'referencia' not in [linha[1] for linha in copia.execute('PRAGMA table_info(lotes)')], 'o backup e o banco de ANTES'
        assert copia.execute('PRAGMA journal_mode').fetchone()[0] == 'delete', 'backup de um arquivo so, sem -wal'

    finally:
        copia.close()

    assert not copias[0].with_name(copias[0].name + '-wal').exists()




def teste_banco_ja_migrado_nao_gera_backup_novo(tmp_path):

    banco = banco_antigo(tmp_path)

    for _ in range(5):
        banco.preparar()

    assert len(backups(banco)) == 1, 'um backup por ATUALIZACAO; cada arranque nao pode encher o volume'




def teste_migrar_e_idempotente(tmp_path):

    banco = banco_antigo(tmp_path)

    assert banco.migrar() >= 1

    assert banco.migrar() == 0

    assert banco.migrar() == 0




def teste_restaurar_devolve_o_banco_de_antes(tmp_path):

    banco = banco_antigo(tmp_path)

    banco.preparar()

    FilaDeLotes(banco).novo_lote('gustavo@ufrrj.br', 'depois', 2)

    assert len(lotes_no(banco.caminho)) == 2

    banco.restaurar(backups(banco)[0])

    assert lotes_no(banco.caminho) == ['velho']

    assert banco.versao == 0

    assert banco.preparar() == len(MIGRACOES), 'o banco restaurado e o antigo de novo: migra outra vez, com backup novo'

    assert len(backups(banco)) == 2, 'o segundo backup nao pode gravar por cima do primeiro'




def teste_migracao_que_falha_nao_avanca_a_versao_nem_deixa_metade(tmp_path, monkeypatch):

    def quebrada(con):

        con.execute("ALTER TABLE lotes ADD COLUMN meia_feita TEXT NOT NULL DEFAULT ''")

        raise RuntimeError('quebrou no meio')

    monkeypatch.setattr(banco_sqlite, 'MIGRACOES', (*MIGRACOES, (99, 'quebrada', quebrada)))

    banco = banco_antigo(tmp_path)

    with pytest.raises(RuntimeError, match='quebrou no meio'):
        banco.preparar()

    assert banco.versao == VERSAO_ALVO, 'as boas ficam gravadas; a quebrada nao conta'

    with banco.conectar() as con:
        assert 'meia_feita' not in colunas(con, 'lotes'), 'o ALTER de dentro da migracao quebrada tem de ser desfeito'
        assert 'referencia' in colunas(con, 'lotes')

    assert lotes_no(banco.caminho) == ['velho']

    assert len(backups(banco)) == 1




def teste_v5_sorteia_chave_para_os_perfis_que_ja_existiam(tmp_path):

    banco = banco_antigo(tmp_path, PERFIS_V4)

    assert banco.versao == 4

    banco.preparar()

    with banco.conectar() as con:
        chaves = [linha[0] for linha in con.execute('SELECT chave FROM perfis')]
        assert tabela_existe(con, 'pedidos')

    assert len(chaves) == 2 and all(chaves) and len(set(chaves)) == 2, chaves

    assert Perfis(banco).email_da_chave(chaves[0]) in ('g.freitas@ufrrj.br', 'm.peckle@ufrrj.br')

    assert lotes_no(banco.caminho) == ['velho']




def teste_v5_nao_troca_a_chave_de_quem_ja_tem(tmp_path):

    banco = banco_antigo(tmp_path, PERFIS_V4)

    banco.preparar()

    with banco.conectar() as con:
        antes = dict(con.execute('SELECT email, chave FROM perfis'))

    assert banco.migrar() == 0

    with banco.conectar() as con:
        depois = dict(con.execute('SELECT email, chave FROM perfis'))

    assert antes == depois, 'trocar a chave quebra o link /labs/<chave> que o laboratorio divulgou'




def teste_indice_unico_barra_duas_contas_com_a_mesma_chave(tmp_path):

    banco = BancoSqlite(tmp_path / 'fila.sqlite3')

    banco.preparar()

    with banco.conectar() as con:

        con.execute("INSERT INTO perfis (email, chave) VALUES ('a@ufrrj.br', 'repetida')")

        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO perfis (email, chave) VALUES ('b@ufrrj.br', 'repetida')")

        con.execute("INSERT INTO perfis (email, chave) VALUES ('c@ufrrj.br', '')")

        con.execute("INSERT INTO perfis (email, chave) VALUES ('d@ufrrj.br', '')")  #* chave vazia e "ainda sem chave": pode repetir




def teste_transacao_desfaz_tudo_quando_o_with_quebra(tmp_path):

    banco = BancoSqlite(tmp_path / 'fila.sqlite3')

    banco.preparar()

    with pytest.raises(RuntimeError):

        with banco.transacao() as con:
            con.execute("INSERT INTO perfis (email) VALUES ('a@ufrrj.br')")
            raise RuntimeError('desiste')

    with banco.conectar() as con:
        assert con.execute('SELECT COUNT(*) FROM perfis').fetchone()[0] == 0




def teste_backup_no_mesmo_segundo_nao_grava_por_cima(tmp_path):

    banco = BancoSqlite(tmp_path / 'fila.sqlite3')

    banco.preparar()

    primeiro = banco.reservar_backup('20260901T120000Z')

    segundo = banco.reservar_backup('20260901T120000Z')

    assert primeiro != segundo and segundo.name.endswith('-2')




def teste_fazer_backup_sem_banco_devolve_none(tmp_path):

    assert BancoSqlite(tmp_path / 'nao-existe.sqlite3').fazer_backup() is None




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
