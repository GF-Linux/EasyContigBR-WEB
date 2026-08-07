"""
Testes da fila. O foco é a corrida que produziu o defeito real: um lote entrar
no trabalhador antes de o upload terminar.
"""
from __future__ import annotations

import pytest

from easycontig_web import fila


@pytest.fixture()
def banco(tmp_path):
    p = tmp_path / "fila.sqlite3"
    fila.criar_esquema(p)
    return p


def test_lote_novo_nasce_recebendo_e_e_invisivel_para_o_trabalhador(banco):
    """O defeito de 33 de 40 amostras: enfileirar antes do último byte."""
    fila.novo_lote(banco, dono="a@x", nome="corrida", n_arquivos=80)
    assert fila.reivindicar(banco) is None, \
        "um lote ainda recebendo arquivos NUNCA pode ser pego pelo trabalhador"


def test_so_apos_liberar_o_lote_entra_na_fila(banco):
    lote_id = fila.novo_lote(banco, dono="a@x", nome="corrida", n_arquivos=80)
    fila.liberar_para_fila(banco, lote_id, 80)
    pego = fila.reivindicar(banco)
    assert pego is not None and pego["id"] == lote_id
    assert fila.pegar(banco, lote_id)["status"] == fila.RODANDO


def test_liberar_grava_o_numero_realmente_recebido(banco):
    """O executor confere este número contra o disco; tem que ser o real, e não
    o que o navegador prometeu ao iniciar o envio."""
    lote_id = fila.novo_lote(banco, dono="a@x", nome="c", n_arquivos=80)
    fila.liberar_para_fila(banco, lote_id, 66)
    assert fila.pegar(banco, lote_id)["n_arquivos"] == 66


def test_dois_trabalhadores_nao_pegam_o_mesmo_lote(banco):
    """É o UPDATE condicional que permite escalar por processos."""
    lote_id = fila.novo_lote(banco, dono="a@x", nome="c", n_arquivos=2)
    fila.liberar_para_fila(banco, lote_id, 2)
    primeiro = fila.reivindicar(banco)
    segundo = fila.reivindicar(banco)
    assert primeiro["id"] == lote_id
    assert segundo is None


def test_fila_e_atendida_na_ordem_de_chegada(banco):
    ids = []
    for i in range(3):
        lid = fila.novo_lote(banco, dono="a@x", nome=f"c{i}", n_arquivos=2)
        fila.liberar_para_fila(banco, lid, 2)
        ids.append(lid)
    assert [fila.reivindicar(banco)["id"] for _ in ids] == ids


def test_orfaos_de_rodando_voltam_para_a_fila(banco):
    """Queda de energia no meio do lote não pode deixar o usuário girando."""
    lote_id = fila.novo_lote(banco, dono="a@x", nome="c", n_arquivos=2)
    fila.liberar_para_fila(banco, lote_id, 2)
    fila.reivindicar(banco)
    assert fila.reenfileirar_orfaos(banco) == 1
    assert fila.pegar(banco, lote_id)["status"] == fila.NA_FILA


def test_upload_interrompido_falha_em_vez_de_ser_processado_pela_metade(banco):
    """RECEBENDO órfão vira FALHOU — reenfileirar reproduziria o defeito."""
    lote_id = fila.novo_lote(banco, dono="a@x", nome="c", n_arquivos=80)
    fila.reenfileirar_orfaos(banco)
    lote = fila.pegar(banco, lote_id)
    assert lote["status"] == fila.FALHOU
    assert "interrompido" in lote["erro"]


def test_progresso_e_conclusao(banco):
    lote_id = fila.novo_lote(banco, dono="a@x", nome="c", n_arquivos=4)
    fila.liberar_para_fila(banco, lote_id, 4)
    fila.reivindicar(banco)
    fila.progresso(banco, lote_id, 1, 2, "amostra12")
    l = fila.pegar(banco, lote_id)
    assert (l["feito"], l["total"], l["etapa"]) == (1, 2, "amostra12")
    fila.concluir(banco, lote_id)
    assert fila.pegar(banco, lote_id)["status"] == fila.PRONTO


def test_listar_separa_por_dono(banco):
    fila.liberar_para_fila(banco, fila.novo_lote(banco, dono="a@x", nome="a", n_arquivos=1), 1)
    fila.liberar_para_fila(banco, fila.novo_lote(banco, dono="b@x", nome="b", n_arquivos=1), 1)
    assert [x["nome"] for x in fila.listar(banco, dono="a@x")] == ["a"]
    assert len(fila.listar(banco)) == 2


# ── trabalhadores simultâneos ────────────────────────────────────────────────
# O `docker-compose.yml` sobe `replicas: ${TRABALHADORES:-2}`, então o PRIMEIRO
# arranque em produção já é com dois — e até 2026-08-06 nunca havia rodado com
# mais de um. Dois processos disputando a mesma linha é o tipo de coisa que só
# aparece no servidor, no dia, com o lote de alguém no meio.
import multiprocessing as _mp


def _sugar_a_fila(caminho, saida, largada=None):
    from easycontig_web import fila as f
    if largada is not None:
        largada.wait()          # todos começam a disputar no mesmo instante
    meus = []
    while True:
        l = f.reivindicar(caminho)
        if not l:
            break
        meus.append(l["id"])
    saida.put(meus)


def test_lote_nao_e_reivindicado_por_dois_trabalhadores(tmp_path):
    """Cada lote sai para EXATAMENTE um. Duplicar significaria processar a
    corrida de alguém duas vezes; perder significaria o lote esperando para
    sempre — que é o defeito de hoje por outro caminho."""
    banco = tmp_path / "fila.sqlite3"
    fila.criar_esquema(banco)
    n = 60
    for i in range(n):
        lid = fila.novo_lote(banco, dono=f"p{i % 4}@ufrrj.br", nome=f"l{i}",
                             n_arquivos=2)
        fila.liberar_para_fila(banco, lid, 2)

    ctx = _mp.get_context("spawn")            # não herda estado do pytest
    saida = ctx.Queue()
    # ⚠️ Sem a barreira este teste é INSTÁVEL, e era meu: um processo subia
    # antes dos outros e levava os 60 lotes sozinho, então a asserção "a disputa
    # aconteceu" caía sem que nada estivesse errado no código. Um teste que
    # falha por corrida entre os próprios processos ensina a ignorá-lo.
    largada = ctx.Barrier(3)
    ps = [ctx.Process(target=_sugar_a_fila, args=(banco, saida, largada))
          for _ in range(3)]
    for p in ps:
        p.start()
    colhido = [saida.get(timeout=60) for _ in ps]
    for p in ps:
        p.join(timeout=60)

    todos = [x for lista in colhido for x in lista]
    assert len(todos) - len(set(todos)) == 0, "o mesmo lote saiu para dois trabalhadores"
    assert len(set(todos)) == n, "lote ficou na fila sem ninguém pegar"
    assert sum(1 for l in colhido if l) >= 2, (
        "um trabalhador só levou tudo; o teste não exercitou a disputa")


# ── o arranque de um trabalhador não pode reenfileirar o trabalho do outro ───
# Confirmado em 2026-08-06: `reenfileirar_orfaos` varria TODOS os lotes em
# `rodando`, sem olhar de quem eram. E o compose sobe `replicas: 2` — então
# subir o segundo trabalhador, reiniciar um deles, ou o `restart:
# unless-stopped` agindo depois de um OOM devolvia à fila o lote que o outro
# estava montando NAQUELE INSTANTE. Os dois passavam a escrever na mesma pasta.
import datetime as _dt


def _um_lote_rodando(banco, dono_trabalhador):
    lid = fila.novo_lote(banco, dono="a@ufrrj.br", nome="par", n_arquivos=2)
    fila.liberar_para_fila(banco, lid, 2)
    fila.reivindicar(banco, dono_trabalhador)
    return lid


def test_nao_reenfileira_lote_de_trabalhador_vivo(tmp_path):
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    lid = _um_lote_rodando(banco, "deck:111")
    fila.pulsar(banco, "deck:111")               # o dono está vivo

    n = fila.reenfileirar_orfaos(banco, eu="deck:222")   # o segundo sobe

    assert n == 0, "reenfileirou o lote que o outro trabalhador está montando"
    assert fila.pegar(banco, lid)["status"] == fila.RODANDO


def test_reenfileira_quando_o_dono_parou_de_pulsar(tmp_path):
    """O caso legítimo, que não pode ser perdido no conserto: queda de energia."""
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    lid = _um_lote_rodando(banco, "deck:111")
    fila.pulsar(banco, "deck:111")

    tarde = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(
        seconds=fila.PULSO_VELHO + 30)
    n = fila.reenfileirar_orfaos(banco, eu="deck:222", agora=tarde)

    assert n == 1
    assert fila.pegar(banco, lid)["status"] == fila.NA_FILA


def test_o_proprio_trabalhador_nao_se_considera_vivo_no_arranque(tmp_path):
    """`reenfileirar_orfaos` roda ANTES do primeiro pulso. Um trabalhador que
    reinicia com o mesmo identificador tem de recuperar o próprio lote — senão
    ele fica preso em `rodando` para sempre, que é o defeito que esta função
    existe para evitar."""
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    lid = _um_lote_rodando(banco, "deck:111")
    fila.pulsar(banco, "deck:111")

    n = fila.reenfileirar_orfaos(banco, eu="deck:111")   # sou eu, reiniciando

    assert n == 1, "o trabalhador não recuperou o próprio lote ao reiniciar"
    assert fila.pegar(banco, lid)["status"] == fila.NA_FILA


def test_lote_sem_dono_registrado_espera_se_alguem_esta_vivo(tmp_path):
    """Banco anterior à migração 3. Na dúvida não se toca no trabalho alheio: o
    preço de esperar é uma página girando; o de errar é um relatório com duas
    montagens misturadas."""
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    lid = _um_lote_rodando(banco, "")            # sem dono, como no esquema velho
    fila.pulsar(banco, "deck:111")               # alguém está vivo

    assert fila.reenfileirar_orfaos(banco, eu="deck:222") == 0
    assert fila.pegar(banco, lid)["status"] == fila.RODANDO


def test_lote_sem_dono_volta_quando_ninguem_esta_vivo(tmp_path):
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    lid = _um_lote_rodando(banco, "")
    assert fila.reenfileirar_orfaos(banco, eu="deck:222") == 1
    assert fila.pegar(banco, lid)["status"] == fila.NA_FILA


def test_reivindicar_grava_quem_pegou(tmp_path):
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    lid = _um_lote_rodando(banco, "deck:777")
    assert fila.pegar(banco, lid)["trabalhador"] == "deck:777"


def test_upload_interrompido_continua_falhando_e_nao_volta_a_fila(tmp_path):
    """O que já estava certo e não pode regredir no conserto."""
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    lid = fila.novo_lote(banco, dono="a@ufrrj.br", nome="par", n_arquivos=2)
    fila.reenfileirar_orfaos(banco, eu="deck:1")
    l = fila.pegar(banco, lid)
    assert l["status"] == fila.FALHOU and "interrompido" in l["erro"]


# ── e o conserto acima também tinha buraco ───────────────────────────────────
# O painel de verificação de 2026-08-06 apontou três, todos reais. O pior: o
# pulso batia só no topo do laço, e `rodar_um` segura o laço o LOTE INTEIRO. Um
# lote de 40 amostras leva ~26 s e cabia nos 90 s; mas o app aceita 400
# arquivos, e aí o trabalhador ocupado fica indistinguível de um morto — o irmão
# subindo reenfileira justamente o lote que está sendo montado. O defeito que o
# pulso existe para impedir, entrando pela porta dos fundos.
def test_so_o_dono_carimba_o_desfecho(tmp_path):
    """Nenhuma trava de reenfileiramento é perfeita. Se um trabalhador for
    desapossado, ele não pode publicar a montagem abandonada por cima da boa."""
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    lid = _um_lote_rodando(banco, "deck:111")

    # o lote passou para outro trabalhador
    with fila.conectar(banco) as con:
        con.execute("UPDATE lotes SET trabalhador=? WHERE id=?", ("deck:222", lid))

    assert fila.concluir(banco, lid, "deck:111") is False, (
        "o trabalhador desapossado carimbou PRONTO por cima do irmão")
    assert fila.pegar(banco, lid)["status"] == fila.RODANDO
    assert fila.concluir(banco, lid, "deck:222") is True


def test_o_dono_certo_carimba(tmp_path):
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    lid = _um_lote_rodando(banco, "deck:111")
    assert fila.concluir(banco, lid, "deck:111") is True
    assert fila.pegar(banco, lid)["status"] == fila.PRONTO


def test_falhar_e_progresso_tambem_conferem_o_dono(tmp_path):
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    lid = _um_lote_rodando(banco, "deck:111")
    with fila.conectar(banco) as con:
        con.execute("UPDATE lotes SET trabalhador=? WHERE id=?", ("deck:222", lid))
    assert fila.falhar(banco, lid, "erro", "deck:111") is False
    assert fila.progresso(banco, lid, 1, 2, "x", "deck:111") is False
    assert fila.pegar(banco, lid)["feito"] == 0


def test_o_servidor_web_continua_marcando_falha_sem_dono(tmp_path):
    """`trabalhador=""` grava sem conferir de propósito: é o caminho do upload
    interrompido, num lote que ninguém chegou a reivindicar."""
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    lid = fila.novo_lote(banco, dono="a@ufrrj.br", nome="p", n_arquivos=2)
    assert fila.falhar(banco, lid, "disco cheio") is True
    assert fila.pegar(banco, lid)["status"] == fila.FALHOU


def test_dois_trabalhadores_subindo_juntos_nao_reenfileiram_em_dobro(tmp_path):
    """`reenfileirar_orfaos` lia e escrevia em autocommit: dois trabalhadores
    subindo juntos — o caso normal do `docker compose up` com duas réplicas —
    viam a mesma lista de órfãos e os dois agiam sobre ela."""
    import multiprocessing as mp
    banco = tmp_path / "f.sqlite3"
    fila.criar_esquema(banco)
    for _ in range(20):
        _um_lote_rodando(banco, "deck:morto")

    ctx = mp.get_context("spawn")
    saida = ctx.Queue()
    ps = [ctx.Process(target=_subir_e_reenfileirar, args=(banco, f"deck:{i}", saida))
          for i in range(4)]
    for p in ps:
        p.start()
    somas = [saida.get(timeout=60) for _ in ps]
    for p in ps:
        p.join(timeout=60)

    assert sum(somas) == 20, (
        f"20 órfãos e {sum(somas)} reenfileiramentos: alguém agiu duas vezes")


def _subir_e_reenfileirar(banco, eu, saida):
    from easycontig_web import fila as f
    saida.put(f.reenfileirar_orfaos(banco, eu=eu))
