"""
Testes da árvore filogenética como trabalho do lote: a fila, o executor e as
rotas.

A ciência (alinhar, aparar, inferir) é testada em `test_filogenia.py`, contra
`app.core.phylogeny`. Aqui só se cobra o que é do lado web: o pedido não pode
duplicar, o marcador tem de sair do NOME da amostra, um caminho vindo da URL
não pode sair da pasta do lote, e quem não é dono não vê.

O caso que orienta o arquivo inteiro continua sendo a corrida real F13719: 40
amostras numa pasta só, de quatro marcadores diferentes.
"""
from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from easycontig_web.processamento import executor_de_arvore as executor_arvore
from easycontig_web.processamento import fila_de_arvores as arvores
from easycontig_web.processamento import fila_de_lotes as fila


@pytest.fixture()
def banco(tmp_path):
    p = tmp_path / "fila.sqlite3"
    fila.criar_esquema(p)          # a migração 6 cria a tabela `arvores`
    return p


@pytest.fixture()
def lote(banco):
    lote_id = fila.novo_lote(banco, dono="a@x", nome="F13719", n_arquivos=2)
    fila.liberar_para_fila(banco, lote_id, 2)
    fila.reivindicar(banco, "t1")
    fila.concluir(banco, lote_id, "t1")
    return lote_id


# ------------------------------------------------------------------- a fila

def test_a_migracao_cria_a_tabela_das_arvores(banco):
    from easycontig_web.dados import esquema_e_migracoes as migracoes
    assert migracoes.versao(banco) >= 6
    with fila.conectar(banco) as con:
        assert con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='arvores'").fetchone()


def test_pedir_duas_vezes_nao_cria_dois_pedidos(banco, lote):
    """O duplo-clique é o caso real: a página demora a responder e a pessoa
    clica de novo. Dois pedidos = dois trabalhadores montando na MESMA pasta."""
    a = arvores.novo_pedido(banco, lote_id=lote, dono="a@x")
    b = arvores.novo_pedido(banco, lote_id=lote, dono="a@x")
    assert a == b
    with fila.conectar(banco) as con:
        assert con.execute("SELECT COUNT(*) FROM arvores").fetchone()[0] == 1


def test_pedido_terminado_nao_bloqueia_um_novo(banco, lote):
    """Pedir de novo depois de pronto é legítimo — o anterior vira histórico."""
    primeiro = arvores.novo_pedido(banco, lote_id=lote, dono="a@x")
    arvores.reivindicar(banco, "t1")
    arvores.concluir(banco, primeiro, [{"marcador": "16S"}], "t1")
    segundo = arvores.novo_pedido(banco, lote_id=lote, dono="a@x")
    assert segundo != primeiro
    assert arvores.do_lote(banco, lote)["id"] == segundo, "a tela mostra o mais novo"


def test_so_um_trabalhador_leva_o_pedido(banco, lote):
    arvores.novo_pedido(banco, lote_id=lote, dono="a@x")
    assert arvores.reivindicar(banco, "t1") is not None
    assert arvores.reivindicar(banco, "t2") is None, "dois montariam a mesma pasta"


def test_quem_perdeu_o_pedido_nao_carimba_o_desfecho(banco, lote):
    """Mesma trava da fila de lotes: terminar não dá direito de publicar se o
    pedido já é de outro."""
    pedido = arvores.novo_pedido(banco, lote_id=lote, dono="a@x")
    arvores.reivindicar(banco, "t1")
    assert arvores.concluir(banco, pedido, [], "t2") is False
    assert arvores.pegar(banco, pedido)["status"] == arvores.RODANDO


def test_orfa_volta_para_a_fila_so_se_o_dono_morreu(banco, lote):
    pedido = arvores.novo_pedido(banco, lote_id=lote, dono="a@x")
    arvores.reivindicar(banco, "t1")
    assert arvores.reenfileirar_orfaos(banco, {"t1"}, "t2") == 0, \
        "t1 está vivo — é trabalho alheio em curso"
    assert arvores.reenfileirar_orfaos(banco, {"t2"}, "t2") == 1
    assert arvores.pegar(banco, pedido)["status"] == arvores.NA_FILA


def test_resumo_ilegivel_nao_derruba_a_tela(banco, lote):
    pedido = arvores.novo_pedido(banco, lote_id=lote, dono="a@x")
    with fila.conectar(banco) as con:
        con.execute("UPDATE arvores SET resumo='{isto nao e json' WHERE id=?",
                    (pedido,))
    assert arvores.resumo_lido(arvores.pegar(banco, pedido)) == []


# --------------------------------------------------------------- o executor

def test_o_marcador_vem_do_nome_da_amostra_e_nao_do_banco_do_lote():
    """⚠️ O ponto do fluxo inteiro. Na F13719 as 40 amostras subiram numa pasta
    só e eram de quatro marcadores. Confiar no banco do lote juntaria os quatro
    numa árvore, que é o erro que tudo isto existe para impedir."""
    assert executor_arvore.marcador_da_amostra(
        "F13719_am24_CAP08_groEL_E08-F08", "16S rRNA") == "groEL"
    assert executor_arvore.marcador_da_amostra(
        "F13719_am01_CAP08_16S_A09-B09", "groEL") == "16S"


def test_sem_marcador_no_nome_cai_no_banco_do_lote_e_depois_em_desconhecido():
    assert executor_arvore.marcador_da_amostra("amostra_qualquer",
                                               "16S rRNA") == "16S"
    assert executor_arvore.marcador_da_amostra("amostra_qualquer") == "desconhecido"


def test_marcador_nao_casa_no_meio_de_outra_palavra():
    """`_16S_` é marcador; `CAP16Sul` não é. Sem a fronteira, um nome de poço ou
    de projeto viraria marcador e criaria um grupo fantasma."""
    assert executor_arvore.marcador_da_amostra("F1_CAP16Sul_x") == "desconhecido"


def test_consensos_saem_dos_cons_fa_que_o_lote_ja_montou(tmp_path, monkeypatch):
    from easycontig_web import configuracao as config
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    cfg = config.carregar()
    trabalho = cfg.lotes_dir / "L1" / "trabalho"
    trabalho.mkdir(parents=True)
    (trabalho / "F13719_am01_CAP08_16S_A09-B09.cons.fa").write_text(
        ">consenso\nACGTACGT\nACGT\n")
    (trabalho / "F13719_am24_CAP08_groEL_E08-F08.cons.fa").write_text(
        ">consenso\nTTTTGGGG\n")
    (trabalho / "vazio.cons.fa").write_text(">consenso\n\n")

    cons = executor_arvore.consensos_do_lote(cfg, "L1")
    por_nome = {c.name: c for c in cons}
    assert set(por_nome) == {"F13719_am01_CAP08_16S_A09-B09",
                             "F13719_am24_CAP08_groEL_E08-F08"}, \
        "consenso vazio não vira táxon"
    assert por_nome["F13719_am01_CAP08_16S_A09-B09"].sequence == "ACGTACGTACGT", \
        "as linhas do FASTA são juntadas"
    assert por_nome["F13719_am24_CAP08_groEL_E08-F08"].marker == "groEL"


def test_o_consenso_de_um_par_e_o_arquivo_ponto_fa(tmp_path, monkeypatch):
    """⚠️ REGRESSÃO DE IMPLANTAÇÃO. O tracy dá três nomes, e só o do meio é o
    que a intuição sugere:

        <amostra>.fa          consenso do PAR F+R      <- o uso NORMAL
        <amostra>.cons.fa     consenso da leitura AVULSA
        <amostra>.align.fa    o ALINHAMENTO das duas leituras (2 registros)

    Isto nasceu procurando só `*.cons.fa` e passou em tudo — inclusive no teste
    de contêiner, que semeou arquivos com esse nome. Na primeira corrida REAL
    (40 pares) a busca achou ZERO, e a árvore teria falhado para todo mundo com
    "nenhum consenso encontrado".
    """
    from easycontig_web import configuracao as config
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    cfg = config.carregar()
    trabalho = cfg.lotes_dir / "L2" / "trabalho"
    trabalho.mkdir(parents=True)

    par = "F13719_am01_CAP08_16S_A09-B09"
    (trabalho / f"{par}.fa").write_text(">Consensus\nGAACGCTGGCGGCAAGCTTAA\n")
    (trabalho / f"{par}.align.fa").write_text(
        f">{par}_F\nGAACGCTGGCGG--------\n>{par}_R\n--------CAAGCTTAA\n")
    avulsa = "Sequenciamento_F13055_64712_16S"
    (trabalho / f"{avulsa}.cons.fa").write_text(">Consensus\nCGCGGTAGTGGCAGCTGAAT\n")

    cons = {c.name: c for c in executor_arvore.consensos_do_lote(cfg, "L2")}
    assert set(cons) == {par, avulsa}, \
        "o par entra pelo .fa, a avulsa pelo .cons.fa, e o .align.fa fica FORA"
    assert cons[par].sequence == "GAACGCTGGCGGCAAGCTTAA"
    assert "-" not in cons[avulsa].sequence


def test_o_alinhamento_nunca_entra_como_se_fosse_consenso(tmp_path, monkeypatch):
    """`.align.fa` tem DOIS registros e gaps. Entrando como táxon, o lote viraria
    uma árvore das leituras — não das amostras — e ninguém perceberia."""
    from easycontig_web import configuracao as config
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    cfg = config.carregar()
    trabalho = cfg.lotes_dir / "L3" / "trabalho"
    trabalho.mkdir(parents=True)
    (trabalho / "am01_16S.align.fa").write_text(
        ">am01_16S_F\nACGT----\n>am01_16S_R\n----ACGT\n")

    assert executor_arvore.consensos_do_lote(cfg, "L3") == []


def test_caminho_de_saida_recusa_sair_da_pasta_do_lote(tmp_path, monkeypatch):
    """`pasta` e `arquivo` vêm da URL. Mesma regra do `pastas_do_lote`: quem
    decide onde se lê não confia em o chamador ter validado antes."""
    from easycontig_web import configuracao as config
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    cfg = config.carregar()
    for pasta, arquivo in ((".." , "x.png"), ("16S", "../../relatorio.json"),
                           ("16S", ""), ("a/b", "x.png")):
        with pytest.raises(ValueError):
            executor_arvore.caminho_de_saida(cfg, "L1", pasta, arquivo)


# ------------------------------------------------------------------ as rotas

@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    return main


def _entrar(c, email="a@ufrrj.br"):
    c.post("/entrar", data={"email": email}, follow_redirects=False)
    return c


def _lote_pronto(app, dono="a@ufrrj.br"):
    from easycontig_web import configuracao as config
    cfg = config.carregar()
    lote_id = fila.novo_lote(cfg.sqlite_path, dono=dono, nome="F13719",
                             n_arquivos=2)
    fila.liberar_para_fila(cfg.sqlite_path, lote_id, 2)
    fila.reivindicar(cfg.sqlite_path, "t1")
    fila.concluir(cfg.sqlite_path, lote_id, "t1")
    return lote_id


def test_pedir_arvore_enfileira_e_leva_para_a_tela(app):
    c = _entrar(TestClient(app.app))
    lote_id = _lote_pronto(app)
    r = c.post(f"/lotes/{lote_id}/arvore", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/lotes/{lote_id}/arvore"

    from easycontig_web import configuracao as config
    pedido = arvores.do_lote(config.carregar().sqlite_path, lote_id)
    assert pedido["status"] == arvores.NA_FILA


def test_arvore_de_lote_que_ainda_nao_terminou_e_recusada(app):
    """Montar árvore de um lote em curso leria consensos que ainda não existem
    — ou pior, metade deles."""
    c = _entrar(TestClient(app.app))
    from easycontig_web import configuracao as config
    cfg = config.carregar()
    lote_id = fila.novo_lote(cfg.sqlite_path, dono="a@ufrrj.br", nome="x",
                             n_arquivos=2)
    r = c.post(f"/lotes/{lote_id}/arvore", follow_redirects=False)
    assert r.status_code == 409


def test_arvore_de_outro_laboratorio_nao_aparece(app):
    """O link é compartilhável por descuido; o dono é conferido pelo LOTE."""
    lote_id = _lote_pronto(app, dono="dono@ufrrj.br")
    c = _entrar(TestClient(app.app), "intruso@ufrrj.br")
    assert c.post(f"/lotes/{lote_id}/arvore",
                  follow_redirects=False).status_code == 404
    assert c.get(f"/lotes/{lote_id}/arvore").status_code == 404


def test_a_tela_convida_a_montar_quando_ainda_nao_ha_pedido(app):
    c = _entrar(TestClient(app.app))
    lote_id = _lote_pronto(app)
    r = c.get(f"/lotes/{lote_id}/arvore")
    assert r.status_code == 200
    assert "Montar árvore" in r.text


def test_a_tela_mostra_o_marcador_recusado_com_o_motivo(app):
    """Sumir em silêncio com o sodB de 2 amostras seria pior que recusar: quem
    enviou não saberia que faltou."""
    c = _entrar(TestClient(app.app))
    lote_id = _lote_pronto(app)
    from easycontig_web import configuracao as config
    cfg = config.carregar()
    pedido = arvores.novo_pedido(cfg.sqlite_path, lote_id=lote_id, dono="a@ufrrj.br")
    arvores.reivindicar(cfg.sqlite_path, "t1")
    arvores.concluir(cfg.sqlite_path, pedido, [
        {"marcador": "sodB", "n": 2,
         "recusado": "2 amostra(s) de sodB — são precisas pelo menos 4 para uma "
                     "árvore dizer algo"}], "t1")

    r = c.get(f"/lotes/{lote_id}/arvore")
    assert "sodB" in r.text and "pelo menos 4" in r.text


def test_o_lote_pronto_oferece_o_caminho_da_arvore(app):
    c = _entrar(TestClient(app.app))
    lote_id = _lote_pronto(app)
    r = c.get(f"/lotes/{lote_id}?tela_do_lote=1")
    assert f"/lotes/{lote_id}/arvore" in r.text


def test_arquivo_da_arvore_com_caminho_forjado_da_404(app):
    c = _entrar(TestClient(app.app))
    lote_id = _lote_pronto(app)
    r = c.get(f"/lotes/{lote_id}/arvore/16S/%2e%2e%2f%2e%2e%2frelatorio.json")
    assert r.status_code == 404


# --------------------------------------------------------------- ponta a ponta

@pytest.mark.skipif(__import__("shutil").which("mb") is None,
                    reason="MrBayes (mb) não instalado")
def test_do_botao_ao_newick_passando_pelo_trabalhador(app):
    """O caminho inteiro do usuário: clica, o trabalhador pega, a árvore sai —
    e o marcador pequeno demais aparece recusado em vez de sumir.

    Reproduz a forma da F13719 em miniatura: dois marcadores na MESMA pasta,
    um com amostras suficientes e outro sem.
    """
    from easycontig_web import configuracao as config
    from easycontig_web.processamento import trabalhador_da_fila as trabalhador

    c = _entrar(TestClient(app.app))
    lote_id = _lote_pronto(app)
    cfg = config.carregar()

    base = "ACGTTGCAAGCTTACGGATCCTAGGCATGCATGCTAGCTAGCTTAAGGCCTTAAGGCATCG" * 4
    trabalho = cfg.lotes_dir / lote_id / "trabalho"
    trabalho.mkdir(parents=True, exist_ok=True)
    for i in range(5):                       # 16S: vira árvore
        seq = list(base)
        seq[i * 7] = "T" if seq[i * 7] != "T" else "A"
        (trabalho / f"F13719_am{i:02d}_CAP{i}_16S_A0{i}-B0{i}.cons.fa").write_text(
            ">consenso\n" + "".join(seq) + "\n")
    for i in range(2):                       # sodB: recusado, como na corrida real
        (trabalho / f"F13719_am9{i}_CAP9{i}_sodB_G0{i}-H0{i}.cons.fa").write_text(
            ">consenso\n" + base + "\n")

    assert c.post(f"/lotes/{lote_id}/arvore",
                  follow_redirects=False).status_code == 303
    assert trabalhador.rodar_uma_arvore(cfg, "t1") is True

    pedido = arvores.do_lote(cfg.sqlite_path, lote_id)
    assert pedido["status"] == arvores.PRONTO, pedido["erro"]
    resumo = {r["marcador"]: r for r in arvores.resumo_lido(pedido)}

    assert "pelo menos 4" in resumo["sodB"]["recusado"]
    dezesseis = resumo["16S"]
    assert dezesseis["n"] == 5 and dezesseis["asdsf"] is not None
    assert dezesseis["newick"], "o Newick é o que o usuário leva para o FigTree"

    pagina = c.get(f"/lotes/{lote_id}/arvore")
    assert "sodB" in pagina.text and "16S" in pagina.text

    baixado = c.get(f"/lotes/{lote_id}/arvore/16S/{dezesseis['newick']}")
    assert baixado.status_code == 200
    assert baixado.text.count("F13719_am") == 5, \
        "os nomes voltam como o laboratório os escreveu, com hífen e tudo"


# ------------------------------------------------- a imagem precisa levar tudo

def test_a_imagem_instala_o_mrbayes():
    """Sem o `mb` a imagem sobe inteira e só a árvore falha — em produção, na
    frente do usuário. Mesmo gênero do download errado do Tracy, que já custou
    uma imagem que nunca construiu."""
    from pathlib import Path
    docker = Path("Dockerfile").read_text()
    assert "MrBayes" in docker
    assert "install -m755 src/mb" in docker, "o binário tem de ir para o PATH"


def test_toda_dependencia_do_pyproject_esta_no_pip_do_dockerfile():
    """⚠️ O Dockerfile instala com `--no-deps` e uma lista à mão. Dependência
    nova no `pyproject.toml` e esquecida lá não quebra o build: some um recurso
    calado dentro do contêiner — foi o que quase aconteceu com o matplotlib."""
    import re
    from pathlib import Path
    pyproject = Path("pyproject.toml").read_text()
    bloco = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
    declaradas = {
        re.split(r"[<>=\[]", linha.strip().strip('",'))[0]
        for linha in bloco.splitlines()
        if linha.strip().startswith('"')
    }
    declaradas.discard("easycontig-br-core")     # entra pelo `pip install /opt/nucleo`
    docker = Path("Dockerfile").read_text()
    faltando = sorted(d for d in declaradas if d and d not in docker)
    assert not faltando, f"faltam no pip do Dockerfile: {faltando}"


# ------------------------------- a árvore e o expurgo têm de saber um do outro

def test_apagar_o_lote_leva_os_pedidos_de_arvore_junto(banco, lote, tmp_path):
    """Linha sem lote é o mesmo defeito que pasta sem linha, uma tabela adiante:
    o pedido continuaria no banco apontando para uma corrida que não existe."""
    from easycontig_web.dados import expurgo_por_retencao as retencao
    pedido = arvores.novo_pedido(banco, lote_id=lote, dono="a@x")
    lotes_dir = tmp_path / "lotes"
    (lotes_dir / lote).mkdir(parents=True)

    assert retencao.apagar_lote(banco, lotes_dir, lote) is True
    assert arvores.pegar(banco, pedido) is None


def test_o_expurgo_nao_puxa_o_tapete_de_uma_arvore_em_montagem(banco, lote, tmp_path):
    """⚠️ O lote fica `pronto` enquanto o MCMC roda — ou seja, no estado que a
    faxina considera livre —, e o trabalhador está lendo `trabalho/*.cons.fa`
    o tempo todo. Sem esta trava, o expurgo apagaria a pasta debaixo dele."""
    from easycontig_web.dados import expurgo_por_retencao as retencao
    arvores.novo_pedido(banco, lote_id=lote, dono="a@x")
    arvores.reivindicar(banco, "t1")            # agora está MONTANDO
    lotes_dir = tmp_path / "lotes"
    (lotes_dir / lote).mkdir(parents=True)

    assert retencao.apagar_lote(banco, lotes_dir, lote) is False
    assert (lotes_dir / lote).exists(), "a pasta que o trabalhador está lendo fica"
    assert fila.pegar(banco, lote) is not None

    # terminada a montagem, o lote volta a ser apagável
    pedido = arvores.do_lote(banco, lote)["id"]
    arvores.concluir(banco, pedido, [], "t1")
    assert retencao.apagar_lote(banco, lotes_dir, lote) is True
