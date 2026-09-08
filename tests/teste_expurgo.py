"""
teste_expurgo.py — o que a faxina NAO pode apagar: o lote com upload em curso, o lote que o trabalhador
esta montando, o lote cuja arvore esta no MrBayes, e tudo — quando o prazo e 0 ou quando o relogio saltou.

    faxina(banco, pasta_de_lotes, dias, agora) ---> expirados() ---> freio? ---> apagar_lote() por vencido
                                                        |               |
                                                        |               '--> travado: "N de M venceriam de uma vez", NADA apagado
                                                        '--> recebendo/rodando/arvore em montagem: nunca; criado_em ilegivel: nunca

O QUE SE PROTEGE
  1. dado apagado por engano nao tem desfazer: quase todo teste aqui afirma uma AUSENCIA de estrago.
  2. linha e pasta somem JUNTAS: meia remocao e defeito dos dois lados.
  3. o freio (medido em 06/08/2026: relogio um ano a frente apagaria as 20 corridas do dia) trava e explica;
     o dia normal (6 de 40) e a instalacao pequena (2 de 3) passam direto — senao o freio vira retencao desligada.
"""

import shutil

from datetime import datetime, timedelta, timezone

import pytest

from easycontig_web.dados import expurgo_por_retencao as expurgo
from easycontig_web.dados.banco_sqlite import BancoSqlite
from easycontig_web.processamento.fila_de_arvores import FilaDeArvores
from easycontig_web.processamento.fila_de_lotes import FALHOU, NA_FILA, PRONTO, RECEBENDO, RODANDO, FilaDeLotes


AGORA = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)




class Acervo:

    #* um fila.sqlite3 com a pasta de lotes ao lado, e lotes de idade forjada em criado_em

    def __init__(self, pasta):

        self.banco = BancoSqlite(pasta / 'fila.sqlite3')

        self.banco.preparar()

        self.lotes = FilaDeLotes(self.banco)

        self.pasta = pasta / 'lotes'

        self.pasta.mkdir()


    def semear(self, dias_atras, status=PRONTO, bytes_ab1=1024, dono='a@ufrrj.br'):

        lote_id = self.lotes.novo_lote(dono, 'corrida', 2)

        criado = (AGORA - timedelta(days=dias_atras)).isoformat(timespec='seconds')

        with self.banco.conectar() as con:
            con.execute('UPDATE lotes SET status=?, criado_em=? WHERE id=?', (status, criado, lote_id))

        entrada = self.pasta / lote_id / 'entrada'

        entrada.mkdir(parents=True)

        (entrada / 'amostra_F.ab1').write_bytes(b'\0' * bytes_ab1)

        return lote_id


    def faxina(self, dias, agora=AGORA):

        return expurgo.faxina(self.banco, self.pasta, dias, agora=agora)


    def apagar(self, lote_id):

        return expurgo.apagar_lote(self.banco, self.pasta, lote_id)


    def pegar(self, lote_id):

        return self.lotes.pegar(lote_id)




@pytest.fixture
def acervo(tmp_path):

    return Acervo(tmp_path)




def teste_lote_vencido_perde_pasta_e_linha_juntas(acervo):

    lote_id = acervo.semear(dias_atras=200)

    balanco = acervo.faxina(dias=90)

    assert balanco.apagados == 1 and balanco.ids == [lote_id]

    assert not (acervo.pasta / lote_id).exists() and acervo.pegar(lote_id) is None




def teste_lote_dentro_do_prazo_fica(acervo):

    lote_id = acervo.semear(dias_atras=89)

    assert acervo.faxina(dias=90).apagados == 0

    assert (acervo.pasta / lote_id / 'entrada' / 'amostra_F.ab1').exists() and acervo.pegar(lote_id) is not None




@pytest.mark.parametrize('status', [RODANDO, RECEBENDO])
def teste_lote_velho_em_uso_nao_e_apagado(acervo, status):

    #* rodando: o trabalhador escreve NAQUELA pasta neste instante; recebendo: os bytes ainda estao chegando

    lote_id = acervo.semear(dias_atras=500, status=status)

    assert acervo.faxina(dias=90).apagados == 0

    assert (acervo.pasta / lote_id).exists() and acervo.pegar(lote_id).status == status




def teste_lote_com_arvore_em_montagem_nao_e_apagado(acervo):

    #* a arvore roda com o lote em `pronto` (o estado livre para a faxina) e o MrBayes le trabalho/*.cons.fa o tempo todo

    lote_id = acervo.semear(dias_atras=500)

    arvores = FilaDeArvores(acervo.banco)

    arvores.novo_pedido(lote_id, 'a@ufrrj.br')

    assert arvores.reivindicar('mb-1') is not None

    assert acervo.apagar(lote_id) is False and acervo.faxina(dias=90).apagados == 0

    assert (acervo.pasta / lote_id).exists() and acervo.pegar(lote_id) is not None

    #* a trava e da montagem, nao do pedido: com a arvore pronta o lote volta a ser apagavel

    pedido_id = arvores.do_lote(lote_id).id

    assert arvores.concluir(pedido_id, [], 'mb-1') is True

    assert acervo.apagar(lote_id) is True and not (acervo.pasta / lote_id).exists()




def teste_apagar_o_lote_leva_os_pedidos_de_arvore_junto(acervo):

    lote_id = acervo.semear(dias_atras=1)

    arvores = FilaDeArvores(acervo.banco)

    arvores.novo_pedido(lote_id, 'a@ufrrj.br')

    assert acervo.apagar(lote_id) is True

    assert arvores.do_lote(lote_id) is None, 'montar a arvore de um lote apagado nao e trabalho pendente'




def teste_falhou_e_na_fila_vencidos_tambem_saem(acervo):

    ids = {acervo.semear(dias_atras=200, status=status) for status in (FALHOU, NA_FILA)}

    assert set(acervo.faxina(dias=90).ids) == ids




def teste_dias_zero_nao_apaga_nada(acervo):

    lote_id = acervo.semear(dias_atras=9999)

    balanco = acervo.faxina(dias=0)

    assert balanco.apagados == 0 and balanco.ids == [] and balanco.bytes == 0

    assert not balanco.travado, 'retencao desligada nao e o mesmo que freio disparado'

    assert (acervo.pasta / lote_id).exists() and acervo.pegar(lote_id) is not None

    assert expurgo.expirados(acervo.lotes.listar(limite=0), 0, agora=AGORA) == []




def teste_apagar_lote_e_idempotente(acervo):

    #* a faxina, o "apagar agora" da tela e um retry podem chegar na mesma pasta

    lote_id = acervo.semear(dias_atras=1)

    assert acervo.apagar(lote_id) is True

    assert acervo.apagar(lote_id) is False




def teste_lote_inexistente_devolve_falso(acervo):

    assert acervo.apagar('nunca-existiu') is False




def teste_pasta_ausente_ainda_assim_apaga_a_linha(acervo):

    lote_id = acervo.semear(dias_atras=200)

    shutil.rmtree(acervo.pasta / lote_id)

    assert acervo.apagar(lote_id) is True and acervo.pegar(lote_id) is None




def teste_pasta_orfa_sem_linha_tambem_e_removida(acervo):

    orfa = acervo.pasta / 'sobrou-de-um-crash'

    (orfa / 'entrada').mkdir(parents=True)

    assert acervo.apagar('sobrou-de-um-crash') is True and not orfa.exists()




@pytest.mark.parametrize('ruim', ['..', '../..', 'a/b', '/etc', '', '   ', '.', 'lote/../..', 'c\\d'])
def teste_id_que_sobe_de_pasta_e_recusado(acervo, ruim):

    #* esta funcao apaga arvore recursivamente: `..` viraria rm -rf na pasta de cima. a rota valida, mas a trava mora aqui tambem

    vizinho = acervo.pasta.parent / 'nao_me_toque'

    vizinho.mkdir()

    with pytest.raises(ValueError):
        acervo.apagar(ruim)

    assert vizinho.exists() and acervo.pasta.exists()




def teste_faxina_conta_apagados_e_bytes(acervo):

    velhos = [acervo.semear(dias_atras=100 + i, bytes_ab1=2048) for i in range(3)]

    novo = acervo.semear(dias_atras=10)

    balanco = acervo.faxina(dias=90)

    assert balanco.apagados == 3 and sorted(balanco.ids) == sorted(velhos) and balanco.bytes == 3 * 2048

    assert acervo.pegar(novo) is not None




def teste_faxina_segue_mesmo_com_um_lote_problematico(acervo, monkeypatch):

    #* uma pasta travada nao pode fazer o volume voltar a crescer em silencio

    bom = acervo.semear(dias_atras=200)

    ruim = acervo.semear(dias_atras=300)

    de_verdade = expurgo.apagar_lote

    def explode(banco, pasta_de_lotes, lote_id):

        if lote_id == ruim:
            raise OSError('permissão negada no volume')

        return de_verdade(banco, pasta_de_lotes, lote_id)

    monkeypatch.setattr(expurgo, 'apagar_lote', explode)

    assert acervo.faxina(dias=90).ids == [bom]

    assert acervo.pegar(ruim) is not None




def teste_expirados_vem_do_mais_antigo_para_o_mais_novo(acervo):

    velho = acervo.semear(dias_atras=400)

    meio = acervo.semear(dias_atras=200)

    acervo.semear(dias_atras=1)

    assert [lote.id for lote in expurgo.expirados(acervo.lotes.listar(limite=0), 90, agora=AGORA)] == [velho, meio]




def teste_criado_em_ilegivel_nao_conta_como_velho(acervo):

    lote_id = acervo.semear(dias_atras=1)

    with acervo.banco.conectar() as con:
        con.execute('UPDATE lotes SET criado_em=? WHERE id=?', ('ontem', lote_id))

    assert expurgo.expirados(acervo.lotes.listar(limite=0), 90, agora=AGORA) == []

    assert expurgo.expira_em(acervo.pegar(lote_id), 90) is None




def teste_expira_em_diz_a_data_para_a_tela_avisar(acervo):

    lote = acervo.pegar(acervo.semear(dias_atras=10))

    assert expurgo.expira_em(lote, 90) == AGORA + timedelta(days=80)

    assert expurgo.expira_em(lote, 0) is None, 'retencao desligada nao tem data'




def teste_relogio_um_ano_a_frente_nao_apaga_o_acervo(acervo):

    #* o caso medido em 06/08/2026: 20 corridas do dia, retencao de 30 dias, relogio saltando. antes apagava as vinte

    for _ in range(20):
        acervo.semear(dias_atras=0)

    balanco = acervo.faxina(dias=30, agora=AGORA + timedelta(days=365))

    assert balanco.apagados == 0, 'o acervo inteiro foi apagado de uma vez'

    assert 'relógio' in balanco.travado and '30' in balanco.travado and 'EXPURGO_EM_MASSA' in balanco.travado, 'a mensagem tem de dizer o que conferir e como destravar'

    assert len(list(acervo.pasta.iterdir())) == 20




def teste_o_freio_e_destravavel_por_quem_decide(acervo, ambiente):

    #* a primeira faxina de uma instalacao antiga vence muita coisa com razao: o freio faz alguem decidir, em vez do relogio

    for _ in range(20):
        acervo.semear(dias_atras=0)

    ambiente.setenv('EASYCONTIG_EXPURGO_EM_MASSA', '1')

    balanco = acervo.faxina(dias=30, agora=AGORA + timedelta(days=365))

    assert balanco.apagados == 20 and not balanco.travado




def teste_o_dia_normal_nao_e_travado(acervo):

    for indice in range(40):
        acervo.semear(dias_atras=90 if indice < 6 else 0)

    balanco = acervo.faxina(dias=30)

    assert balanco.apagados == 6 and not balanco.travado




def teste_instalacao_pequena_nao_trava_por_apagar_a_maioria(acervo):

    #* com 3 lotes no total, apagar 2 e rotina, nao catastrofe

    for indice in range(3):
        acervo.semear(dias_atras=90 if indice < 2 else 0)

    balanco = acervo.faxina(dias=30)

    assert balanco.apagados == 2 and not balanco.travado




def teste_a_faxina_e_de_todas_as_contas(acervo):

    ids = {acervo.semear(dias_atras=200, dono=dono) for dono in ('a@ufrrj.br', 'b@usp.br')}

    assert set(acervo.faxina(dias=90).ids) == ids




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
