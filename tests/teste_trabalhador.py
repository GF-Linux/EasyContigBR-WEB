"""
teste_trabalhador.py — o processo que atende as duas filas: arranque, o laco, o pulso, a faxina e a parada.

    Trabalhador(cfg, eu)
        |
        |-- arrancar()       banco.preparar() -> reenfileirar_orfaos(eu) -> falhar_envios_abandonados() -> diagnostico no log
        |                    -> pulsar(eu) -> thread "pulso" (bater_pulso a cada INTERVALO_PULSO)
        |
        '-- rodar()          ate pedir_parada():   faxinar()  ->  rodar_um()  ou, se nao havia lote,  rodar_uma_arvore()
                                                       |              |                                    |
                                            expurgo.faxina     executor_de_lote.executar          executor_de_arvore.executar
                                            de hora em hora    PRONTO / FALHOU (so o dono)        PRONTO / FALHOU (so o dono)

O QUE SE PROTEGE
  1. quem reinicia com o mesmo nome recupera o proprio lote: `reenfileirar_orfaos(eu)` nao me conta entre os vivos,
     e o pulso que a encarnacao anterior deixou ha segundos (um restart) nao me engana. E o primeiro pulso sai ANTES
     da thread: entre o reenfileirar e o primeiro tique, um irmao subindo me daria por morto.
  2. o arranque nao mexe no lote de um irmao que esta pulsando, e da por perdido so o envio VELHO em `recebendo`:
     o upload que acontece neste segundo sobrevive a um deploy.
  3. o desfecho e do dono: o trabalhador desapossado (o lote passou para outro) nao carimba PRONTO nem FALHOU por
     cima de quem esta com o lote agora — nem no lote, nem na arvore.
  4. a falha diz o tipo e a mensagem (`RuntimeError: tracy explodiu`), e o lote que falhou nao volta para a fila.
  5. o laco atende o lote antes da arvore, dorme no proprio sinal de parada (acorda na hora, nao depois de OCIOSO)
     e um erro no laco — banco travado — nao mata o processo: espera PAUSA_APOS_ERRO e tenta de novo.
  6. a faxina roda no arranque e depois de hora em hora, e uma faxina que falha nao derruba o trabalhador nem e
     repetida a cada volta.
  7. o pulso segue batendo na thread mesmo depois de um pulso que falhou, e para quando o laco encerra.
  8. o main() instala SIGTERM e SIGINT no pedir_parada do MESMO trabalhador que arranca e roda.

O QUE MUDOU (em relacao aos testes originais)
  - nao havia teste do trabalhador: o original guardava tudo num global `_parar` e nas variaveis locais do `main()`, e a
    unica forma de exercita-lo era subir o processo inteiro. Os testes das filas (pulso, orfaos, fila_atendida) estao
    em teste_filas.py e teste_expurgo.py; aqui e a classe que os chama.
  - os executores sao substituidos por dubles na maior parte dos casos: o que se confere e o que o trabalhador faz
    com o resultado (ou com o erro), nao a montagem em si (teste_executor.py, teste_arvores.py).

O tracy e pedido so pelo teste de ponta a ponta (exigir_ferramenta): sem ele, o teste e pulado dizendo o que falta.
"""

import logging
import signal
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from conftest import EMAIL, exigir_ferramenta, relatorio_fabricado
from easycontig_web import configuracao
from easycontig_web.dados import expurgo_por_retencao as expurgo
from easycontig_web.dados.expurgo_por_retencao import BalancoDaFaxina
from easycontig_web.dados.leitura_de_amostras import carregar
from easycontig_web.processamento import executor_de_arvore, executor_de_lote, trabalhador_da_fila
from easycontig_web.processamento.fila import FALHOU, NA_FILA, PRONTO, RODANDO
from easycontig_web.processamento.fila_de_lotes import AVISO_DE_ENVIO_INTERROMPIDO, ENVIO_ABANDONADO, RECEBENDO
from easycontig_web.processamento.trabalhador_da_fila import INTERVALO_FAXINA, Trabalhador, identidade


EU = 'w:1'  #* o nome do trabalhador da bancada

IRMAO = 'w:2'  #* a outra replica

PAR = ('amostra12_F_BTF2.ab1', 'amostra12_R_BTR2.ab1')  #* o par sintetico da fixture ab1

ESPERA = 5.0  #* segundos que um teste espera por uma thread antes de desistir e acusar o travamento




def envelhecer(banco, tabela, id, segundos):

    #* recua o criado_em de uma linha, como se ela tivesse nascido ha `segundos`

    nascido = (datetime.now(timezone.utc) - timedelta(seconds=segundos)).isoformat(timespec='seconds')

    with banco.conectar() as con:
        con.execute(f'UPDATE {tabela} SET criado_em=? WHERE id=?', (nascido, id))




def desapossar(banco, tabela, id, novo_dono):

    #* o lote (ou a arvore) passou para outro trabalhador enquanto este ainda o montava

    with banco.conectar() as con:
        con.execute(f'UPDATE {tabela} SET trabalhador=? WHERE id=?', (novo_dono, id))




def lote_na_fila(lotes, dono=EMAIL, nome='corrida', referencia=''):

    lote_id = lotes.novo_lote(dono, nome, 2, referencia=referencia)

    lotes.liberar_para_fila(lote_id, 2)

    return lote_id




def lote_rodando(lotes, trabalhador, referencia=''):

    lote_id = lote_na_fila(lotes, referencia=referencia)

    assert lotes.reivindicar(trabalhador).id == lote_id

    return lote_id




def lote_pronto(lotes, referencia=''):

    lote_id = lote_rodando(lotes, 'bancada', referencia)

    assert lotes.concluir(lote_id, 'bancada')

    return lote_id




def esperar(condicao):

    #* espera ate ESPERA segundos por uma condicao vinda de outra thread; False se nao chegou

    limite = time.monotonic() + ESPERA

    while time.monotonic() < limite:

        if condicao():
            return True

        time.sleep(0.01)

    return condicao()




@pytest.fixture
def cfg(tmp_path, ambiente):

    #* a Configuracao de uma pasta de dados temporaria, sem banco curado nenhum

    ambiente.setenv('EASYCONTIG_DATA_DIR', str(tmp_path / 'dados'))

    return configuracao.carregar()




@pytest.fixture
def trabalhador(cfg):

    #* um trabalhador com o banco ja preparado, mas sem arrancar: cada teste chama o pedaco que quer

    trabalhador = Trabalhador(cfg, EU)

    trabalhador.banco.preparar()

    yield trabalhador

    trabalhador.parar_pulso.set()  #* se algum teste arrancou, a thread do pulso morre aqui




@pytest.fixture
def lotes(trabalhador):

    return trabalhador.lotes




@pytest.fixture
def arvores(trabalhador):

    return trabalhador.arvores




def teste_a_identidade_e_maquina_e_pid(cfg):

    #* a tela pergunta "ha ALGUEM vivo", mas o lote precisa saber o dono: maquina + PID distingue duas replicas na mesma base

    import os
    import socket

    assert identidade() == f'{socket.gethostname()}:{os.getpid()}'

    assert Trabalhador(cfg).eu == identidade()

    assert Trabalhador(cfg, EU).eu == EU




def teste_nasce_com_as_duas_filas_no_mesmo_banco_e_sem_parada_pedida(cfg):

    trabalhador = Trabalhador(cfg, EU)

    assert trabalhador.lotes.banco is trabalhador.banco and trabalhador.arvores.banco is trabalhador.banco

    assert trabalhador.banco.caminho == cfg.caminho_do_sqlite

    assert not trabalhador.parada_pedida.is_set() and not trabalhador.parar_pulso.is_set()

    assert trabalhador.proxima_faxina == 0.0, 'a primeira faxina e no arranque'

    assert EU in repr(trabalhador) and str(cfg.caminho_do_sqlite) in repr(trabalhador)

    assert not cfg.caminho_do_sqlite.exists(), 'construir nao toca o disco; e o arrancar que prepara o banco'




def teste_arrancar_cria_o_banco_e_se_anuncia_vivo(cfg, caplog):

    trabalhador = Trabalhador(cfg, EU)

    with caplog.at_level(logging.INFO, logger='easycontig.trabalhador'):
        trabalhador.arrancar()

    try:
        assert cfg.caminho_do_sqlite.exists()

        assert trabalhador.lotes.fila_atendida() and trabalhador.arvores.fila_atendida(), 'a pagina diria que ninguem atende a fila'

        assert trabalhador.lotes.trabalhadores_vivos() == {EU}

        assert 'trabalhador pronto' in caplog.text

    finally:
        trabalhador.parar_pulso.set()




def teste_arrancar_devolve_os_orfaos_de_quem_morreu_e_respeita_quem_pulsa(trabalhador, lotes, arvores, caplog):

    do_morto = lote_rodando(lotes, 'morto:9')

    do_vivo = lote_rodando(lotes, IRMAO)

    lotes.pulsar(IRMAO)

    pedido_id = arvores.novo_pedido(lote_pronto(lotes), EMAIL)

    assert arvores.reivindicar('morto:9').id == pedido_id

    with caplog.at_level(logging.WARNING, logger='easycontig.trabalhador'):
        trabalhador.arrancar()

    assert lotes.pegar(do_morto).status == NA_FILA and lotes.pegar(do_morto).trabalhador == ''

    assert lotes.pegar(do_vivo).status == RODANDO and lotes.pegar(do_vivo).trabalhador == IRMAO, 'reenfileirou o lote que o irmao esta montando'

    assert arvores.pegar(pedido_id).status == NA_FILA

    assert '1 lote(s) sem dono vivo' in caplog.text and '1 árvore(s) sem dono vivo' in caplog.text




def teste_arrancar_da_por_perdidos_so_os_envios_parados_ha_mais_de_meia_hora(trabalhador, lotes, caplog):

    #* RECEBENDO e o estado de quem esta com o upload em curso NESTE segundo; um deploy nao pode matar esse envio

    velho = lotes.novo_lote(EMAIL, 'velho', 40)

    envelhecer(trabalhador.banco, 'lotes', velho, ENVIO_ABANDONADO + 60)

    recente = lotes.novo_lote(EMAIL, 'recente', 40)

    with caplog.at_level(logging.WARNING, logger='easycontig.trabalhador'):
        trabalhador.arrancar()

    assert lotes.pegar(velho).status == FALHOU and lotes.pegar(velho).erro == AVISO_DE_ENVIO_INTERROMPIDO

    assert lotes.pegar(recente).status == RECEBENDO and not lotes.pegar(recente).erro

    assert '1 envio(s) parados há mais de meia hora' in caplog.text




def teste_arrancar_recupera_o_proprio_lote_de_antes_da_queda(trabalhador, lotes):

    #! este lote ficou `rodando` comigo, e o pulso que deixei e recente (cai ha segundos, e o supervisor me subiu de novo):
    #! sem dizer a fila quem sou eu, eu me veria vivo e o lote ficaria preso em `rodando` para sempre

    lote_id = lote_rodando(lotes, EU)

    lotes.pulsar(EU)

    trabalhador.arrancar()

    assert lotes.pegar(lote_id).status == NA_FILA, 'o trabalhador nao recuperou o proprio lote ao reiniciar'

    assert lotes.reivindicar(EU).id == lote_id, 'o lote recuperado tem de estar a disposicao de novo'




def teste_arrancar_conta_a_instalacao_e_a_retencao_no_log(tmp_path, ambiente, caplog):

    ambiente.setenv('EASYCONTIG_DATA_DIR', str(tmp_path / 'dados'))

    trabalhador = Trabalhador(configuracao.carregar(), EU)

    with caplog.at_level(logging.INFO, logger='easycontig.trabalhador'):
        trabalhador.arrancar()

    trabalhador.parar_pulso.set()

    assert 'tracy' in caplog.text and 'blastn' in caplog.text, 'o diagnostico da instalacao nao foi para o log'

    assert 'retenção DESLIGADA' in caplog.text

    caplog.clear()

    ambiente.setenv('EASYCONTIG_RETENCAO_DIAS', '30')

    trabalhador = Trabalhador(configuracao.carregar(), EU)

    with caplog.at_level(logging.INFO, logger='easycontig.trabalhador'):
        trabalhador.arrancar()

    trabalhador.parar_pulso.set()

    assert 'apagados 30 dias após o envio' in caplog.text and 'DESLIGADA' not in caplog.text




def teste_o_pulso_bate_na_thread_e_sobrevive_a_um_banco_que_falhou(trabalhador, monkeypatch, caplog):

    monkeypatch.setattr(trabalhador_da_fila, 'INTERVALO_PULSO', 0.02)

    pulsos = []

    def pulsar(eu):

        pulsos.append(eu)

        if len(pulsos) == 1:
            raise sqlite3.OperationalError('database is locked')

    trabalhador.lotes = SimpleNamespace(pulsar=pulsar)

    thread = threading.Thread(target=trabalhador.bater_pulso, daemon=True)

    with caplog.at_level(logging.ERROR, logger='easycontig.trabalhador'):

        thread.start()

        assert esperar(lambda: len(pulsos) >= 3), 'o pulso parou de bater depois do erro'

    assert set(pulsos) == {EU}

    assert 'não consegui registrar o pulso' in caplog.text

    trabalhador.parar_pulso.set()

    thread.join(ESPERA)

    assert not thread.is_alive(), 'a thread do pulso nao obedeceu ao parar_pulso'

    batidos = len(pulsos)

    time.sleep(0.1)

    assert len(pulsos) == batidos, 'continuou pulsando depois de parada'




def teste_sem_lote_na_fila_rodar_um_nao_chama_o_executor(trabalhador, monkeypatch):

    monkeypatch.setattr(executor_de_lote, 'executar', lambda *a, **k: pytest.fail('chamou o executor sem lote'))

    monkeypatch.setattr(executor_de_arvore, 'executar', lambda *a, **k: pytest.fail('chamou o executor de arvore sem pedido'))

    assert trabalhador.rodar_um() is False

    assert trabalhador.rodar_uma_arvore() is False




def teste_rodar_um_leva_o_lote_do_inicio_ao_pronto(trabalhador, lotes, monkeypatch, caplog):

    lote_id = lote_na_fila(lotes, nome='F13719', referencia='anaplasmataceae_groel')

    vistos = []

    def executar(cfg, lote_id, nome='', n_esperado=0, referencia='', progresso=None):

        vistos.append((cfg, lote_id, nome, n_esperado, referencia))

        progresso(1, 2, 'amostra12')

        vistos.append(lotes.pegar(lote_id))

        progresso(2, 2, None)  #* o nucleo avisa o fim com a etapa vazia; None nao pode virar NULL na tabela

        vistos.append(lotes.pegar(lote_id))

        return relatorio_fabricado(nome='F13719')

    monkeypatch.setattr(executor_de_lote, 'executar', executar)

    with caplog.at_level(logging.INFO, logger='easycontig.trabalhador'):
        assert trabalhador.rodar_um() is True

    assert vistos[0] == (trabalhador.cfg, lote_id, 'F13719', 2, 'anaplasmataceae_groel'), 'o executor nao recebeu o lote como esta na fila'

    no_meio, no_fim = vistos[1], vistos[2]

    assert (no_meio.status, no_meio.feito, no_meio.total, no_meio.etapa) == (RODANDO, 1, 2, 'amostra12')

    assert (no_fim.feito, no_fim.total, no_fim.etapa) == (2, 2, '')

    lote = lotes.pegar(lote_id)

    assert lote.status == PRONTO and lote.trabalhador == EU and lote.etapa == '' and lote.terminado_em and not lote.erro

    assert f'lote {lote_id}: pronto em' in caplog.text and '(1 amostras)' in caplog.text

    assert trabalhador.rodar_um() is False, 'o lote pronto voltou para a fila'




def teste_o_erro_do_executor_vira_falhou_com_tipo_e_mensagem(trabalhador, lotes, monkeypatch, caplog):

    lote_id = lote_na_fila(lotes)

    def executar(*_, **__):

        raise RuntimeError('tracy explodiu')

    monkeypatch.setattr(executor_de_lote, 'executar', executar)

    with caplog.at_level(logging.ERROR, logger='easycontig.trabalhador'):
        assert trabalhador.rodar_um() is True, 'havia trabalho, mesmo que tenha dado errado'

    lote = lotes.pegar(lote_id)

    assert lote.status == FALHOU and lote.erro == 'RuntimeError: tracy explodiu' and lote.terminado_em

    assert f'lote {lote_id}: falhou' in caplog.text and 'Traceback' in caplog.text, 'o rastro do erro e o que o operador vai ler no log'

    assert trabalhador.rodar_um() is False, 'o lote que falhou nao pode voltar para a fila'




@pytest.mark.parametrize('desfecho', ['pronto', 'falhou'])
def teste_desapossado_nao_carimba_pronto_nem_falhou(trabalhador, lotes, monkeypatch, caplog, desfecho):

    #* nenhuma trava de reenfileiramento e perfeita: se o lote passou para o irmao, o desfecho e dele

    lote_id = lote_na_fila(lotes)

    def executar(cfg, lote_id, **_):

        desapossar(trabalhador.banco, 'lotes', lote_id, IRMAO)

        if desfecho == 'falhou':
            raise RuntimeError('deu errado aqui')

        return relatorio_fabricado()

    monkeypatch.setattr(executor_de_lote, 'executar', executar)

    with caplog.at_level(logging.WARNING, logger='easycontig.trabalhador'):
        assert trabalhador.rodar_um() is True

    lote = lotes.pegar(lote_id)

    assert lote.status == RODANDO and lote.trabalhador == IRMAO and not lote.erro, f'o desapossado carimbou {lote.status} por cima do irmao'

    if desfecho == 'pronto':
        assert 'não sou mais o dono' in caplog.text




def teste_rodar_uma_arvore_leva_a_referencia_do_lote_e_grava_o_resumo(trabalhador, lotes, arvores, monkeypatch, caplog):

    lote_id = lote_pronto(lotes, referencia='anaplasmataceae_groel')

    pedido_id = arvores.novo_pedido(lote_id, EMAIL)

    resumo = [{'marcador': 'groEL', 'n': 5, 'colunas': 240}, {'marcador': 'sodB', 'n': 2, 'recusado': '2 amostra(s) de sodB'}]

    vistos = []

    def executar(cfg, lote_id, referencia='', etapa=None):

        vistos.append((cfg, lote_id, referencia))

        etapa('groEL (5 amostras)')

        vistos.append(arvores.pegar(pedido_id))

        return resumo

    monkeypatch.setattr(executor_de_arvore, 'executar', executar)

    with caplog.at_level(logging.INFO, logger='easycontig.trabalhador'):
        assert trabalhador.rodar_uma_arvore() is True

    assert vistos[0] == (trabalhador.cfg, lote_id, 'anaplasmataceae_groel'), 'a arvore precisa do banco do lote para saber o marcador de quem nao o tem no nome'

    assert (vistos[1].status, vistos[1].etapa) == (RODANDO, 'groEL (5 amostras)')

    pedido = arvores.pegar(pedido_id)

    assert pedido.status == PRONTO and pedido.resumo == resumo and pedido.trabalhador == EU and pedido.etapa == ''

    assert f'árvore {pedido_id}: pronta em' in caplog.text and '(1 marcador(es))' in caplog.text, 'o recusado nao conta como montado'

    assert trabalhador.rodar_uma_arvore() is False




def teste_arvore_de_lote_sem_linha_roda_sem_referencia(trabalhador, lotes, arvores, monkeypatch):

    #* o apagar leva os pedidos junto (teste_expurgo); mas uma linha que sumiu por fora nao pode derrubar o trabalhador

    lote_id = lote_pronto(lotes, referencia='anaplasmataceae_groel')

    pedido_id = arvores.novo_pedido(lote_id, EMAIL)

    with trabalhador.banco.conectar() as con:
        con.execute('DELETE FROM lotes WHERE id=?', (lote_id,))

    vistos = []

    monkeypatch.setattr(executor_de_arvore, 'executar', lambda cfg, lote_id, referencia='', etapa=None: vistos.append(referencia) or [])

    assert trabalhador.rodar_uma_arvore() is True

    assert vistos == [''] and arvores.pegar(pedido_id).status == PRONTO




def teste_a_arvore_que_falha_grava_tipo_e_mensagem(trabalhador, lotes, arvores, monkeypatch, caplog):

    import filogenia

    pedido_id = arvores.novo_pedido(lote_pronto(lotes), EMAIL)

    def executar(*_, **__):

        raise filogenia.ErroDoMrBayes('mb saiu com código 1')

    monkeypatch.setattr(executor_de_arvore, 'executar', executar)

    with caplog.at_level(logging.ERROR, logger='easycontig.trabalhador'):
        assert trabalhador.rodar_uma_arvore() is True

    pedido = arvores.pegar(pedido_id)

    assert pedido.status == FALHOU and pedido.erro == 'ErroDoMrBayes: mb saiu com código 1' and pedido.resumo == []

    assert f'árvore {pedido_id}: falhou' in caplog.text

    assert trabalhador.rodar_uma_arvore() is False




def teste_arvore_desapossada_nao_carimba(trabalhador, lotes, arvores, monkeypatch, caplog):

    pedido_id = arvores.novo_pedido(lote_pronto(lotes), EMAIL)

    def executar(cfg, lote_id, referencia='', etapa=None):

        desapossar(trabalhador.banco, 'arvores', pedido_id, IRMAO)

        return [{'marcador': '16S', 'n': 5}]

    monkeypatch.setattr(executor_de_arvore, 'executar', executar)

    with caplog.at_level(logging.WARNING, logger='easycontig.trabalhador'):
        assert trabalhador.rodar_uma_arvore() is True

    pedido = arvores.pegar(pedido_id)

    assert pedido.status == RODANDO and pedido.trabalhador == IRMAO and pedido.resumo == []

    assert 'não sou mais o dono' in caplog.text




def teste_a_primeira_faxina_e_no_arranque_e_a_proxima_so_daqui_a_uma_hora(trabalhador, monkeypatch):

    chamadas = []

    monkeypatch.setattr(expurgo, 'faxina', lambda banco, pasta, dias: chamadas.append((banco, pasta, dias)) or BalancoDaFaxina())

    antes = time.monotonic()

    trabalhador.faxinar()

    assert chamadas == [(trabalhador.banco, trabalhador.cfg.pasta_de_lotes, trabalhador.cfg.retencao_dias)]

    assert trabalhador.proxima_faxina >= antes + INTERVALO_FAXINA

    trabalhador.faxinar()

    assert len(chamadas) == 1, 'faxinou de novo na mesma hora: ela varre o disco inteiro'

    trabalhador.proxima_faxina = 0.0

    trabalhador.faxinar()

    assert len(chamadas) == 2




def teste_a_faxina_que_falha_nao_derruba_e_nao_e_repetida_na_mesma_volta(trabalhador, monkeypatch, caplog):

    def faxina(*_):

        raise OSError('disco fora do ar')

    monkeypatch.setattr(expurgo, 'faxina', faxina)

    with caplog.at_level(logging.ERROR, logger='easycontig.trabalhador'):
        trabalhador.faxinar()

    assert 'faxina falhou' in caplog.text and 'disco fora do ar' in caplog.text

    assert trabalhador.proxima_faxina > time.monotonic(), 'a hora da proxima faxina e marcada ANTES de tentar: uma faxina que falha nao pode ser repetida a cada volta do laco'




def teste_a_faxina_de_verdade_expurga_o_lote_vencido(tmp_path, ambiente, caplog):

    ambiente.setenv('EASYCONTIG_DATA_DIR', str(tmp_path / 'dados'))

    ambiente.setenv('EASYCONTIG_RETENCAO_DIAS', '1')

    trabalhador = Trabalhador(configuracao.carregar(), EU)

    trabalhador.banco.preparar()

    vencido = lote_pronto(trabalhador.lotes)

    envelhecer(trabalhador.banco, 'lotes', vencido, 3 * 86400)

    pasta = trabalhador.cfg.pastas_do_lote(vencido)

    pasta.raiz.mkdir(parents=True)

    pasta.relatorio_json.write_text('x' * 2048)

    de_hoje = lote_pronto(trabalhador.lotes)

    with caplog.at_level(logging.INFO, logger='easycontig.trabalhador'):
        trabalhador.faxinar()

    assert trabalhador.lotes.pegar(vencido) is None and not pasta.raiz.exists()

    assert trabalhador.lotes.pegar(de_hoje).status == PRONTO

    assert 'faxina: 1 lote(s) expurgados' in caplog.text




def teste_o_laco_atende_o_lote_antes_da_arvore_e_encerra_quando_pedido(trabalhador, lotes, arvores, monkeypatch, caplog):

    pedido_id = arvores.novo_pedido(lote_pronto(lotes), EMAIL)

    lote_id = lote_na_fila(lotes)  #* entra DEPOIS do pedido de arvore, e mesmo assim e atendido primeiro

    ordem = []

    def montar_lote(cfg, lote_id, **_):

        ordem.append(('lote', lote_id))

        return relatorio_fabricado()

    def montar_arvore(cfg, lote_id, referencia='', etapa=None):

        ordem.append(('arvore', lote_id))

        trabalhador.pedir_parada()

        return []

    monkeypatch.setattr(executor_de_lote, 'executar', montar_lote)

    monkeypatch.setattr(executor_de_arvore, 'executar', montar_arvore)

    monkeypatch.setattr(expurgo, 'faxina', lambda *_: ordem.append(('faxina', '')) or BalancoDaFaxina())

    thread = threading.Thread(target=trabalhador.rodar, daemon=True)

    with caplog.at_level(logging.INFO, logger='easycontig.trabalhador'):

        thread.start()

        thread.join(ESPERA)

    assert not thread.is_alive(), 'o laco nao encerrou depois de pedir_parada'

    assert ordem == [('faxina', ''), ('lote', lote_id), ('arvore', arvores.pegar(pedido_id).lote_id)], 'faxina no arranque, lote antes de arvore, e a faxina nao repete a cada volta'

    assert lotes.pegar(lote_id).status == PRONTO and arvores.pegar(pedido_id).status == PRONTO

    assert trabalhador.parar_pulso.is_set(), 'o pulso tem de parar junto com o laco'

    assert 'trabalhador encerrado' in caplog.text




def teste_ocioso_o_laco_dorme_no_sinal_e_acorda_na_hora(trabalhador, monkeypatch):

    monkeypatch.setattr(trabalhador_da_fila, 'OCIOSO', 60.0)

    thread = threading.Thread(target=trabalhador.rodar, daemon=True)

    thread.start()

    time.sleep(0.1)

    assert thread.is_alive()

    trabalhador.pedir_parada()

    thread.join(ESPERA)

    assert not thread.is_alive(), 'dormiu os 60 s de OCIOSO em vez de acordar no sinal'




def teste_um_erro_no_laco_nao_mata_o_trabalhador(trabalhador, monkeypatch, caplog):

    monkeypatch.setattr(trabalhador_da_fila, 'PAUSA_APOS_ERRO', 0.01)

    tentativas = []

    def reivindicar_com_o_banco_travado(eu):

        tentativas.append(eu)

        if len(tentativas) == 3:
            trabalhador.pedir_parada()

        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(trabalhador.lotes, 'reivindicar', reivindicar_com_o_banco_travado)

    thread = threading.Thread(target=trabalhador.rodar, daemon=True)

    with caplog.at_level(logging.ERROR, logger='easycontig.trabalhador'):

        thread.start()

        thread.join(ESPERA)

    assert not thread.is_alive()

    assert tentativas == [EU] * 3, 'o laco morreu no primeiro erro'

    assert caplog.text.count('erro no laço do trabalhador') == 3 and 'database is locked' in caplog.text




def teste_pedir_parada_aceita_os_argumentos_do_sinal(trabalhador, caplog):

    with caplog.at_level(logging.INFO, logger='easycontig.trabalhador'):
        trabalhador.pedir_parada(signal.SIGTERM, None)

    assert trabalhador.parada_pedida.is_set() and not trabalhador.parar_pulso.is_set(), 'o pulso so para depois do laco: o lote em curso ainda precisa parecer vivo'

    assert 'parada pedida' in caplog.text




def teste_main_instala_os_sinais_arranca_e_roda(cfg, ambiente, monkeypatch):

    ambiente.setenv('EASYCONTIG_LOG', 'DEBUG')

    passos = []

    sinais = {}

    monkeypatch.setattr(configuracao, 'carregar', lambda: cfg)

    monkeypatch.setattr(logging, 'basicConfig', lambda **opcoes: passos.append(('log', opcoes['level'])))

    monkeypatch.setattr(signal, 'signal', lambda numero, tratador: sinais.__setitem__(numero, tratador))

    monkeypatch.setattr(Trabalhador, 'arrancar', lambda self: passos.append(('arrancar', self)))

    monkeypatch.setattr(Trabalhador, 'rodar', lambda self: passos.append(('rodar', self)))

    trabalhador_da_fila.main()

    assert [passo for passo, _ in passos] == ['log', 'arrancar', 'rodar']

    assert passos[0][1] == 'DEBUG', 'EASYCONTIG_LOG nao chegou ao logging'

    trabalhador = passos[1][1]

    assert trabalhador.cfg is cfg and passos[2][1] is trabalhador and trabalhador.eu == identidade()

    assert set(sinais) == {signal.SIGTERM, signal.SIGINT}

    for tratador in sinais.values():
        assert tratador.__self__ is trabalhador and tratador.__func__ is Trabalhador.pedir_parada, 'o sinal tem de parar ESTE trabalhador'

    sinais[signal.SIGTERM](signal.SIGTERM, None)

    assert trabalhador.parada_pedida.is_set()




def teste_do_envio_ao_relatorio_passando_pelo_trabalhador(bancada, ab1):

    #* o caminho inteiro sem duble: o navegador envia o par, o trabalhador arranca, pega, monta com o tracy e a tela mostra

    exigir_ferramenta('tracy')

    cliente = bancada.entrar()

    lote_id = bancada.enviar_lote(cliente, [(nome, ab1[nome]) for nome in PAR], nome='sintetico')

    assert bancada.lote(lote_id).status == NA_FILA

    trabalhador = Trabalhador(bancada.cfg, EU)

    trabalhador.arrancar()

    try:
        assert trabalhador.rodar_um() is True

        assert trabalhador.rodar_um() is False

    finally:
        trabalhador.parar_pulso.set()

    lote = bancada.lote(lote_id)

    assert lote.status == PRONTO and lote.trabalhador == EU and not lote.erro

    assert (lote.feito, lote.total) == (1, 1), 'o par e UMA amostra; o progresso final e o que a tela mostra como concluido'

    pastas = bancada.cfg.pastas_do_lote(lote_id)

    for caminho in (pastas.relatorio_html, pastas.relatorio_json, pastas.resultado_csv):
        assert caminho.exists() and caminho.stat().st_size > 0

    relatorio = carregar(pastas.relatorio_json)

    assert len(relatorio.amostras) == 1 and len(relatorio.amostras[0].leituras) == 2 and relatorio.amostras[0].consenso_pb > 0

    html = cliente.get(f'/lotes/{lote_id}?tela_do_lote=1').text

    assert 'amostra12' in html and 'Nada está atendendo a fila' not in html

    assert cliente.get(f'/lotes/{lote_id}').headers['location'].startswith(f'/lotes/{lote_id}/'), 'com UMA amostra a tela do lote leva direto a amostra'




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
