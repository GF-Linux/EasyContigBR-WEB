"""
teste_filas.py — a fila dos lotes e a das arvores: quem pega o que, quando, e quem tem o direito de carimbar o desfecho.

    novo_lote (RECEBENDO, invisivel) --liberar_para_fila--> NA_FILA --reivindicar(eu)--> RODANDO --concluir/falhar(eu)--> PRONTO/FALHOU
                                                                                            |
                    trabalhador morreu? --reenfileirar_orfaos(eu)--> NA_FILA (so se o dono NAO pulsa)
                    upload abandonado? --falhar_envios_abandonados--> FALHOU "o envio foi interrompido"
    pulsar(eu) ---> fila_atendida(): a tela so promete "alguem vai pegar" quando ha alguem

O QUE SE PROTEGE
  1. o defeito de 33 de 40 amostras: o lote entrava no trabalhador ANTES do ultimo byte.
  2. o docker-compose sobe DUAS replicas: dois processos na mesma linha, no arranque e na disputa.
  3. o achado L4 (06/08/2026): o restart de um trabalhador matava o upload EM CURSO de outra pessoa.
  4. so o dono carimba PRONTO: o trabalhador desapossado nao publica a montagem abandonada por cima da boa.
"""

import multiprocessing

from datetime import datetime, timedelta, timezone

import pytest

from conftest import EMAIL
from easycontig_web.dados.banco_sqlite import BancoSqlite
from easycontig_web.processamento import fila, fila_de_arvores, fila_de_lotes
from easycontig_web.processamento.fila_de_arvores import FilaDeArvores
from easycontig_web.processamento.fila_de_lotes import FALHOU, NA_FILA, PRONTO, RECEBENDO, RODANDO, CotaEstourada, FilaDeLotes




def banco_novo(pasta):

    banco = BancoSqlite(pasta / 'fila.sqlite3')

    banco.preparar()

    return banco




@pytest.fixture
def lotes(tmp_path):

    return FilaDeLotes(banco_novo(tmp_path))




def lote_na_fila(lotes, dono='a@ufrrj.br', nome='corrida', n_arquivos=2, teto_ativos=0):

    lote_id = lotes.novo_lote(dono, nome, n_arquivos, teto_ativos=teto_ativos)

    lotes.liberar_para_fila(lote_id, n_arquivos)

    return lote_id




def lote_rodando(lotes, trabalhador):

    lote_id = lote_na_fila(lotes)

    assert lotes.reivindicar(trabalhador).id == lote_id

    return lote_id




def sugar_a_fila(caminho, saida, largada):

    #* corpo de um processo filho: reivindica ate a fila esvaziar e devolve os ids que levou

    largada.wait()  #* todos comecam a disputar no mesmo instante

    minha = FilaDeLotes(BancoSqlite(caminho))

    meus = []

    while True:

        lote = minha.reivindicar()

        if lote is None:
            break

        meus.append(lote.id)

    saida.put(meus)




def subir_e_reenfileirar(caminho, eu, saida):

    saida.put(FilaDeLotes(BancoSqlite(caminho)).reenfileirar_orfaos(eu))




def teste_lote_novo_nasce_recebendo_e_e_invisivel_para_o_trabalhador(lotes):

    lote_id = lotes.novo_lote('a@ufrrj.br', 'corrida', 80)

    assert lotes.pegar(lote_id).status == RECEBENDO

    assert lotes.reivindicar() is None, 'um lote ainda recebendo arquivos NUNCA pode ser pego pelo trabalhador'




def teste_so_apos_liberar_o_lote_entra_na_fila(lotes):

    lote_id = lotes.novo_lote('a@ufrrj.br', 'corrida', 80)

    lotes.liberar_para_fila(lote_id, 80)

    pego = lotes.reivindicar()

    assert pego is not None and pego.id == lote_id and pego.status == RODANDO, 'reivindicar devolve o registro como ficou'

    assert lotes.pegar(lote_id).status == RODANDO




def teste_liberar_grava_o_numero_realmente_recebido(lotes):

    #* o executor confere este numero contra o disco: tem de ser o real, nao o que o navegador prometeu ao iniciar

    lote_id = lotes.novo_lote('a@ufrrj.br', 'corrida', 80)

    lotes.liberar_para_fila(lote_id, 66)

    assert lotes.pegar(lote_id).n_arquivos == 66




def teste_dois_trabalhadores_nao_pegam_o_mesmo_lote(lotes):

    lote_id = lote_na_fila(lotes)

    assert lotes.reivindicar('deck:1').id == lote_id

    assert lotes.reivindicar('deck:2') is None




def teste_fila_e_atendida_na_ordem_de_chegada(lotes):

    ids = [lote_na_fila(lotes, nome=f'c{indice}') for indice in range(3)]

    assert [lotes.reivindicar().id for _ in ids] == ids




def teste_reivindicar_grava_quem_pegou(lotes):

    lote_id = lote_rodando(lotes, 'deck:777')

    assert lotes.pegar(lote_id).trabalhador == 'deck:777'




def teste_progresso_e_conclusao(lotes):

    lote_id = lote_rodando(lotes, 'deck:1')

    assert lotes.progresso(lote_id, 1, 2, 'amostra12', 'deck:1') is True

    lote = lotes.pegar(lote_id)

    assert (lote.feito, lote.total, lote.etapa) == (1, 2, 'amostra12')

    assert lotes.concluir(lote_id, 'deck:1') is True

    lote = lotes.pegar(lote_id)

    assert lote.status == PRONTO and lote.terminado_em and lote.etapa == ''




def teste_listar_separa_por_dono_e_limite_zero_e_todos(lotes):

    for dono in ('a@ufrrj.br', 'b@ufrrj.br'):
        lote_na_fila(lotes, dono=dono, nome=dono[0])

    assert [lote.nome for lote in lotes.listar(dono='a@ufrrj.br')] == ['a']

    assert len(lotes.listar()) == 2

    for _ in range(60):
        lote_na_fila(lotes)

    assert len(lotes.listar()) == 50 and len(lotes.listar(limite=0)) == 62, 'limite=0 e a faxina: TODOS'




def teste_orfaos_de_rodando_voltam_para_a_fila(lotes):

    #* queda de energia no meio do lote nao pode deixar a pessoa girando

    lote_id = lote_rodando(lotes, 'deck:morto')

    assert lotes.reenfileirar_orfaos(eu='deck:novo') == 1

    lote = lotes.pegar(lote_id)

    assert lote.status == NA_FILA and lote.trabalhador == '' and lote.etapa == ''




def teste_nao_reenfileira_lote_de_trabalhador_vivo(lotes):

    #* o compose sobe duas replicas: subir a segunda nao pode devolver a fila o lote que a primeira esta montando AGORA

    lote_id = lote_rodando(lotes, 'deck:111')

    lotes.pulsar('deck:111')

    assert lotes.reenfileirar_orfaos(eu='deck:222') == 0, 'reenfileirou o lote que o outro trabalhador esta montando'

    assert lotes.pegar(lote_id).status == RODANDO




def teste_reenfileira_quando_o_dono_parou_de_pulsar(lotes):

    lote_id = lote_rodando(lotes, 'deck:111')

    lotes.pulsar('deck:111')

    tarde = datetime.now(timezone.utc) + timedelta(seconds=fila.PULSO_VELHO + 30)

    assert lotes.reenfileirar_orfaos(eu='deck:222', momento=tarde) == 1

    assert lotes.pegar(lote_id).status == NA_FILA




def teste_o_proprio_trabalhador_nao_se_considera_vivo_no_arranque(lotes):

    #* reenfileirar_orfaos roda ANTES do primeiro pulso: quem reinicia com o mesmo identificador tem de recuperar o proprio lote

    lote_id = lote_rodando(lotes, 'deck:111')

    lotes.pulsar('deck:111')

    assert lotes.reenfileirar_orfaos(eu='deck:111') == 1, 'o trabalhador nao recuperou o proprio lote ao reiniciar'

    assert lotes.pegar(lote_id).status == NA_FILA




def teste_lote_sem_dono_registrado_espera_se_alguem_esta_vivo(lotes):

    #* banco anterior a migracao 3: na duvida nao se toca no trabalho alheio

    lote_id = lote_rodando(lotes, '')

    lotes.pulsar('deck:111')

    assert lotes.reenfileirar_orfaos(eu='deck:222') == 0

    assert lotes.pegar(lote_id).status == RODANDO




def teste_lote_sem_dono_volta_quando_ninguem_esta_vivo(lotes):

    lote_id = lote_rodando(lotes, '')

    assert lotes.reenfileirar_orfaos(eu='deck:222') == 1

    assert lotes.pegar(lote_id).status == NA_FILA




def teste_upload_abandonado_falha_em_vez_de_ser_processado_pela_metade(lotes):

    #* RECEBENDO velho vira FALHOU: reenfileirar reproduziria o relatorio de 33 de 40 amostras

    lote_id = lotes.novo_lote('a@ufrrj.br', 'corrida', 80)

    depois = datetime.now(timezone.utc) + timedelta(seconds=fila_de_lotes.ENVIO_ABANDONADO + 60)

    assert lotes.falhar_envios_abandonados(momento=depois) == 1

    lote = lotes.pegar(lote_id)

    assert lote.status == FALHOU and 'interrompido' in lote.erro

    assert lotes.reenfileirar_orfaos(eu='deck:1') == 0 and lotes.pegar(lote_id).status == FALHOU, 'nao volta a fila'




def teste_reinicio_nao_mata_o_envio_que_esta_acontecendo_agora(lotes):

    #* achado L4: o restart varria TODO recebendo, sem filtro de idade — duas pessoas viam "envio interrompido" sem nada ter acontecido

    ids = [lotes.novo_lote(dono, 'enviando', 40) for dono in ('a@ufrrj.br', 'b@ufrrj.br')]

    assert lotes.reenfileirar_orfaos(eu='deck:1') == 0

    assert lotes.falhar_envios_abandonados() == 0

    for lote_id in ids:
        assert lotes.pegar(lote_id).status == RECEBENDO, 'o envio em curso foi marcado por um reinicio de trabalhador'




def teste_so_o_dono_carimba_o_desfecho(lotes):

    #* nenhuma trava e perfeita: se um trabalhador for desapossado, ele nao publica a montagem abandonada por cima da boa

    lote_id = lote_rodando(lotes, 'deck:111')

    with lotes.banco.conectar() as con:
        con.execute('UPDATE lotes SET trabalhador=? WHERE id=?', ('deck:222', lote_id))

    assert lotes.concluir(lote_id, 'deck:111') is False, 'o trabalhador desapossado carimbou PRONTO por cima do irmao'

    assert lotes.falhar(lote_id, 'erro', 'deck:111') is False

    assert lotes.progresso(lote_id, 1, 2, 'x', 'deck:111') is False

    lote = lotes.pegar(lote_id)

    assert lote.status == RODANDO and lote.feito == 0

    assert lotes.concluir(lote_id, 'deck:222') is True




def teste_o_servidor_web_continua_marcando_falha_sem_dono(lotes):

    #* trabalhador='' grava sem conferir de proposito: e o caminho do disco cheio, num lote que ninguem reivindicou

    lote_id = lotes.novo_lote('a@ufrrj.br', 'corrida', 2)

    assert lotes.falhar(lote_id, 'disco cheio') is True

    lote = lotes.pegar(lote_id)

    assert lote.status == FALHOU and lote.erro == 'disco cheio'




def teste_o_erro_gravado_e_cortado_para_nao_encher_a_tabela(lotes):

    lote_id = lote_rodando(lotes, 'deck:1')

    lotes.falhar(lote_id, 'x' * 5000, 'deck:1')

    assert len(lotes.pegar(lote_id).erro) == fila.TAMANHO_DO_ERRO




def teste_a_cota_de_ativos_e_conferida_dentro_da_transacao_do_insert(lotes):

    #* medido em 06/08/2026: teto 1 e oito envios simultaneos, SETE entraram — a conferencia morava na rota, antes de o lote existir

    lote_na_fila(lotes, teto_ativos=2)

    lotes.novo_lote('a@ufrrj.br', '2', 2, teto_ativos=2)

    with pytest.raises(CotaEstourada) as erro:
        lotes.novo_lote('a@ufrrj.br', '3', 2, teto_ativos=2)

    assert erro.value.ativos == 2 and erro.value.teto == 2, 'a recusa diz quantas e quantas cabem'

    lotes.novo_lote('b@ufrrj.br', '1', 2, teto_ativos=1)  #* o teto e por conta, nao do servidor: a vizinha nao gasta a vaga de ninguem




def teste_lote_terminado_devolve_a_vaga(lotes):

    lote_id = lote_rodando(lotes, 'deck:1')

    with pytest.raises(CotaEstourada):
        lotes.novo_lote('a@ufrrj.br', '2', 2, teto_ativos=1)

    lotes.concluir(lote_id, 'deck:1')

    lotes.novo_lote('a@ufrrj.br', '2', 2, teto_ativos=1)




def teste_fila_sem_trabalhador_nenhum_nao_esta_atendida(lotes):

    #* o caso de 06/08/2026: ninguem NUNCA bateu pulso neste banco, e a pagina prometia que alguem ia pegar

    assert lotes.fila_atendida() is False




def teste_pulso_recente_significa_atendida_e_o_velho_nao(lotes):

    lotes.pulsar('deck:123')

    assert lotes.fila_atendida() is True

    tarde = datetime.now(timezone.utc) + timedelta(seconds=fila.PULSO_VELHO + 30)

    assert lotes.fila_atendida(momento=tarde) is False




def teste_um_trabalhador_vivo_basta(lotes):

    #* vale o pulso MAIS RECENTE: um trabalhador morto ao lado de um vivo nao faz a fila parecer abandonada

    lotes.pulsar('morto:1')

    lotes.pulsar('vivo:2')

    quase = datetime.now(timezone.utc) + timedelta(seconds=fila.PULSO_VELHO - 5)

    assert lotes.fila_atendida(momento=quase) is True

    assert lotes.trabalhadores_vivos() == {'morto:1', 'vivo:2'}




def teste_pulsar_duas_vezes_nao_duplica_o_trabalhador(lotes):

    lotes.pulsar('deck:123')

    lotes.pulsar('deck:123')

    with lotes.banco.conectar() as con:
        assert con.execute('SELECT COUNT(*) FROM pulso').fetchone()[0] == 1




def teste_registro_ilegivel_erra_para_o_lado_de_atendida(lotes):

    #* anunciar abandono por engano assusta a toa; o caso que importa — pulso NENHUM — nao depende de ler data alguma

    with lotes.banco.conectar() as con:
        con.execute("INSERT INTO pulso VALUES ('x', 'isto não é uma data')")

    assert lotes.fila_atendida() is True

    assert lotes.trabalhadores_vivos() == set()




def teste_banco_sem_a_tabela_de_pulso_nao_dispara_alarme_falso(lotes):

    #* a janela entre subir o codigo novo e reiniciar o servidor: dizer "nada esta atendendo" para todos seria mentira

    with lotes.banco.conectar() as con:
        con.execute('DROP TABLE pulso')

    assert lotes.fila_atendida() is True

    lotes.banco.preparar()  #* o arranque fecha a janela: ESQUEMA e IF NOT EXISTS

    assert lotes.fila_atendida() is False, 'depois de reiniciar, a fila sem trabalhador tem que aparecer como tal'




def teste_a_pagina_avisa_quando_ninguem_esta_atendendo(bancada):

    cliente = bancada.entrar()

    lote_id = lote_na_fila(bancada.instalacao.lotes, dono=EMAIL)

    html = cliente.get(f'/lotes/{lote_id}?tela_do_lote=1').text

    assert 'Nada está atendendo a fila' in html, 'a pagina continua prometendo que alguem vai pegar o lote'

    assert 'seus arquivos chegaram' in html.lower(), 'nao diz que o envio esta a salvo — a pessoa vai reenviar a toa'

    assert 'trabalhador' in html, 'nao diz o que fazer'

    bancada.instalacao.lotes.pulsar('deck:1')

    html = cliente.get(f'/lotes/{lote_id}?tela_do_lote=1').text

    assert 'Nada está atendendo a fila' not in html and 'Esta página se atualiza sozinha' in html




def teste_o_aviso_e_so_de_quem_espera_na_fila(bancada):

    #* RECEBENDO ainda nao e promessa de processamento; PRONTO ja foi processado: nos dois o aviso seria ruido

    cliente = bancada.entrar()

    lote_id = bancada.instalacao.lotes.novo_lote(EMAIL, 'par', 2)

    assert 'Nada está atendendo a fila' not in cliente.get(f'/lotes/{lote_id}?tela_do_lote=1').text




def teste_lote_nao_e_reivindicado_por_dois_trabalhadores(tmp_path):

    #* tres processos de verdade (spawn, sem herdar o pytest) disputando 60 lotes: cada lote sai para EXATAMENTE um.
    #! sem a barreira o teste era instavel: um processo subia antes e levava tudo sozinho, e a assercao da disputa caia a toa

    banco = banco_novo(tmp_path)

    lotes = FilaDeLotes(banco)

    for indice in range(60):
        lote_na_fila(lotes, dono=f'p{indice % 4}@ufrrj.br', nome=f'l{indice}')

    contexto = multiprocessing.get_context('spawn')

    saida = contexto.Queue()

    largada = contexto.Barrier(3)

    processos = [contexto.Process(target=sugar_a_fila, args=(banco.caminho, saida, largada)) for _ in range(3)]

    for processo in processos:
        processo.start()

    colhido = [saida.get(timeout=60) for _ in processos]

    for processo in processos:
        processo.join(timeout=60)

    todos = [lote_id for lista in colhido for lote_id in lista]

    assert len(todos) == len(set(todos)), 'o mesmo lote saiu para dois trabalhadores'

    assert len(set(todos)) == 60, 'lote ficou na fila sem ninguem pegar'

    assert sum(1 for lista in colhido if lista) >= 2, 'um trabalhador so levou tudo; o teste nao exercitou a disputa'




def teste_dois_trabalhadores_subindo_juntos_nao_reenfileiram_em_dobro(tmp_path):

    #* reenfileirar_orfaos lia e escrevia em autocommit: duas replicas subindo juntas viam a mesma lista e as duas agiam

    banco = banco_novo(tmp_path)

    lotes = FilaDeLotes(banco)

    for _ in range(20):
        lote_rodando(lotes, 'deck:morto')

    contexto = multiprocessing.get_context('spawn')

    saida = contexto.Queue()

    processos = [contexto.Process(target=subir_e_reenfileirar, args=(banco.caminho, f'deck:{indice}', saida)) for indice in range(4)]

    for processo in processos:
        processo.start()

    somas = [saida.get(timeout=60) for _ in processos]

    for processo in processos:
        processo.join(timeout=60)

    assert sum(somas) == 20, f'20 orfaos e {sum(somas)} reenfileiramentos: alguem agiu duas vezes'




def teste_pedir_arvore_duas_vezes_nao_cria_dois_pedidos(lotes):

    arvores = FilaDeArvores(lotes.banco)

    lote_id = lote_na_fila(lotes)

    primeiro = arvores.novo_pedido(lote_id, 'a@ufrrj.br')

    assert arvores.novo_pedido(lote_id, 'a@ufrrj.br') == primeiro, 'o segundo clique devolve o pedido ativo'

    assert arvores.do_lote(lote_id).id == primeiro and arvores.do_lote(lote_id).status == NA_FILA




def teste_pedido_terminado_nao_bloqueia_um_novo(lotes):

    arvores = FilaDeArvores(lotes.banco)

    lote_id = lote_na_fila(lotes)

    primeiro = arvores.novo_pedido(lote_id, 'a@ufrrj.br')

    assert arvores.reivindicar('mb:1').id == primeiro

    assert arvores.concluir(primeiro, [{'marcador': '18S', 'ok': True}], 'mb:1') is True

    segundo = arvores.novo_pedido(lote_id, 'a@ufrrj.br')

    assert segundo != primeiro and arvores.do_lote(lote_id).id == segundo, 'a tela mostra o pedido mais recente'




def teste_as_duas_filas_partilham_o_pulso_mas_nao_os_registros(lotes):

    arvores = FilaDeArvores(lotes.banco)

    lote_id = lote_na_fila(lotes)

    pedido = arvores.novo_pedido(lote_id, 'a@ufrrj.br')

    assert arvores.reivindicar('mb:1').id == pedido and lotes.reivindicar('mb:1').id == lote_id

    lotes.pulsar('mb:1')

    assert arvores.fila_atendida() and arvores.reenfileirar_orfaos(eu='outro') == 0

    with lotes.banco.conectar() as con:
        con.execute('UPDATE arvores SET trabalhador=? WHERE id=?', ('mb:2', pedido))

    assert arvores.concluir(pedido, [], 'mb:1') is False, 'quem perdeu o pedido nao carimba o desfecho'

    assert arvores.pegar(pedido).status == RODANDO




def teste_resumo_ilegivel_nao_derruba_a_tela(lotes):

    arvores = FilaDeArvores(lotes.banco)

    lote_id = lote_na_fila(lotes)

    pedido = arvores.novo_pedido(lote_id, 'a@ufrrj.br')

    with lotes.banco.conectar() as con:
        con.execute('UPDATE arvores SET resumo=? WHERE id=?', ('{isto nao e json', pedido))

    assert arvores.pegar(pedido).resumo == [] and fila_de_arvores.ler_resumo('[1, 2') == []




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
