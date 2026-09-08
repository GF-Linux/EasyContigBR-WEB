"""
trabalhador_da_fila.py — o processo que esvazia as duas filas: monta o lote, monta a arvore e passa a faxina.

Roda SEPARADO do servidor web (decisao de 04/08/2026): a requisicao devolve o
protocolo em milissegundos e some; os ~26 s do lote acontecem aqui, sem
ninguem esperando com uma conexao aberta. Para dobrar a vazao, suba mais de
um: disputam a mesma fila, e o UPDATE condicional de `reivindicar` garante
que cada lote e de um so. Uma VPS de 4 vCPU comporta ~3 trabalhadores,
deixando um nucleo para a web.


COMO FUNCIONA — a vida do processo
==================================

    python -m easycontig_web.processamento.trabalhador_da_fila      (o docker-compose sobe TRABALHADORES replicas disto)
         |
         v
    trabalhador = Trabalhador(cfg)          eu = maquina:pid — a fila precisa saber o DONO de cada lote
         |
    trabalhador.arrancar()                  (uma vez, ao subir)
         |   banco.preparar()
         |   lotes.reenfileirar_orfaos(eu)      lote RODANDO de um irmao que morreu volta para a fila; o de um irmao vivo, nao
         |   lotes.falhar_envios_abandonados()  RECEBENDO ha mais de meia hora = pasta pela metade: FALHOU com o aviso
         |   arvores.reenfileirar_orfaos(eu)    o mesmo para os pedidos de arvore (a tabela de pulso e a mesma)
         |   diagnostico + politica de retencao no log   (quem opera descobre sem ler codigo se o expurgo esta ligado)
         |   primeiro pulso, e a thread "pulso" a cada INTERVALO_PULSO s
         v
    trabalhador.rodar()                     (o laco, ate SIGTERM/SIGINT; o lote em curso termina antes de sair)
         |
         |--> faxinar()            de hora em hora, a primeira no arranque (quando o operador esta olhando o log)
         |--> rodar_um()           lote na fila?  reivindicar -> executor_de_lote.executar -> concluir  (ou falhar, com o erro)
         |--> rodar_uma_arvore()   senao, pedido?  reivindicar -> executor_de_arvore.executar -> concluir (ou falhar)
         '--> nada?                dorme OCIOSO s e olha de novo

    Lote primeiro, arvore depois: quem espera o resultado da corrida tem
    prioridade sobre quem pediu uma analise extra sobre corrida que ja terminou.

    O pulso bate numa THREAD, nao no topo do laco (achado do painel de
    verificacao, 08/2026): `rodar_um` segura o laco o lote inteiro, e um lote
    de 400 arquivos passa dos PULSO_VELHO s — o trabalhador ocupado ficava
    indistinguivel de um morto, e o irmao subindo reenfileirava justamente o
    lote que estava sendo montado. Na thread o pulso diz o que precisa dizer:
    "este PROCESSO esta vivo". Ele continua batendo depois do SIGTERM, ate o
    lote em curso terminar: e o que impede o irmao de pegar o lote no meio.

    Um lote ruim nao derruba o trabalhador: FALHOU com `TipoDoErro: texto` no
    banco (e o que a pagina do lote mostra) e o proximo usuario segue sendo
    atendido. Terminei mas nao sou mais o dono (o irmao me deu por morto e
    reivindicou): nao carimbo PRONTO — publicaria a MINHA montagem por cima da
    dele. A faxina fica fora do `rodar_um`: falha de faxina nao pode impedir o
    lote de alguem de ser processado.


COMO USAR
=========

    trabalhador = Trabalhador(cfg)          cfg = configuracao.carregar()
    trabalhador.arrancar()
    trabalhador.rodar()                     bloqueia; trabalhador.pedir_parada() (ou SIGTERM) encerra depois do item atual

    trabalhador.rodar_um()                  um lote so; True se havia trabalho
    trabalhador.rodar_uma_arvore()          um pedido de arvore so; True se havia trabalho
    trabalhador.faxinar()                   a faxina, se ja passou a hora (proxima_faxina)

    EASYCONTIG_LOG=DEBUG muda o nivel do log (padrao INFO)


O QUE MUDOU DO trabalhador_da_fila.py ORIGINAL (215 linhas)
============================================================

    1. O trabalhador e uma classe: `eu`, as duas filas, os dois sinais de parada e a hora da proxima faxina
       moravam num global `_parar` e em variaveis locais do `main()`; agora sao atributos de `Trabalhador`, e
       cada pedaco do arranque e do laco e um metodo que a bancada chama sozinho (o original so testava inteiro).
    2. As filas sao objetos (`FilaDeLotes`, `FilaDeArvores`) sobre um `BancoSqlite`; o original passava
       `cfg.sqlite_path` em toda chamada. O lote e o pedido sao `Lote` e `PedidoDeArvore`, nao dicts.
    3. `falhar_envios_abandonados` e chamado aqui, com o seu proprio log; no original ficava escondido dentro
       de `reenfileirar_orfaos` e ninguem ficava sabendo quantos envios foram dados por perdidos.
    4. Os sinais sao instalados ANTES do arranque: um SIGTERM que chegasse durante o `reenfileirar_orfaos`
       matava o processo no meio; agora ele termina o arranque e sai pelo laco.
    5. Ocioso, o laco espera no proprio sinal de parada (`parada_pedida.wait(OCIOSO)`), nao num `sleep`.
    6. A faxina recebe o banco e a pasta de lotes e devolve `BalancoDaFaxina` (era dict).


POR QUE NASCE CLASSE
====================

    O trabalhador E um estado com vida: uma identidade que os outros processos
    leem na tabela de pulso, uma thread que bate enquanto ele vive, um pedido
    de parada que chega por sinal e a hora da proxima faxina. O original
    guardava isso num global e em locais do `main()`, e por isso so dava para
    testar subindo o processo inteiro.
"""


import logging
import os
import signal
import socket
import threading
import time

from easycontig_web import configuracao
from easycontig_web.dados import expurgo_por_retencao as expurgo
from easycontig_web.dados.banco_sqlite import BancoSqlite
from easycontig_web.processamento import executor_de_arvore, executor_de_lote
from easycontig_web.processamento.fila import INTERVALO_PULSO
from easycontig_web.processamento.fila_de_arvores import FilaDeArvores
from easycontig_web.processamento.fila_de_lotes import FilaDeLotes


REGISTRO = logging.getLogger('easycontig.trabalhador')

INTERVALO_FAXINA = 3600.0  #* segundos entre faxinas. de hora em hora, nao a cada volta: ela varre o disco inteiro e um lote leva dias para vencer

OCIOSO = 0.25  #* segundos de espera quando as duas filas estao vazias

PAUSA_APOS_ERRO = 2.0  #* segundos de espera depois de um erro no proprio laco (banco fora do ar), para nao encher o log




def identidade():

    #* quem sou eu nesta fila: maquina + PID basta. a tela pergunta "ha ALGUEM vivo", mas o lote precisa saber o dono

    return f'{socket.gethostname()}:{os.getpid()}'




class Trabalhador:

    #* um processo que atende as duas filas; arrancar() uma vez, rodar() ate pedir_parada()


    def __init__(self, cfg, eu=''):

        self.cfg = cfg
        self.banco = BancoSqlite(cfg.caminho_do_sqlite)
        self.lotes = FilaDeLotes(self.banco)
        self.arvores = FilaDeArvores(self.banco)
        self.eu = eu or identidade()
        self.parada_pedida = threading.Event()  #* SIGTERM/SIGINT: o laco sai depois do item em curso
        self.parar_pulso = threading.Event()    #* so depois do laco: o pulso segue batendo enquanto o ultimo lote termina
        self.proxima_faxina = 0.0               #* zero, e nao monotonic(): a primeira faxina roda no arranque


    def pedir_parada(self, *_):

        #* o tratador de sinal; os argumentos (numero do sinal, quadro) nao importam

        self.parada_pedida.set()

        REGISTRO.info('parada pedida — encerro depois do lote atual')


    def bater_pulso(self):

        #* o alvo da thread "pulso": registra que este processo esta vivo a cada INTERVALO_PULSO s, ate parar_pulso

        while not self.parar_pulso.wait(INTERVALO_PULSO):

            try:
                self.lotes.pulsar(self.eu)

            except Exception:
                REGISTRO.exception('não consegui registrar o pulso; segue processando')


    def arrancar(self):

        #* o que acontece uma vez, ao subir: prepara o banco, devolve orfaos a fila, conta a instalacao no log, comeca a pulsar

        self.banco.preparar()

        orfaos = self.lotes.reenfileirar_orfaos(self.eu)  #* com `eu`: isto roda ANTES do primeiro pulso, e sem dizer quem sou eu apareceria para mim mesmo como morto

        if orfaos:
            REGISTRO.warning('%d lote(s) sem dono vivo voltaram para a fila', orfaos)

        abandonados = self.lotes.falhar_envios_abandonados()

        if abandonados:
            REGISTRO.warning('%d envio(s) parados há mais de meia hora foram dados por interrompidos', abandonados)

        orfas = self.arvores.reenfileirar_orfaos(self.eu)

        if orfas:
            REGISTRO.warning('%d árvore(s) sem dono vivo voltaram para a fila', orfas)


        for item, deu_certo, detalhe in configuracao.diagnostico(self.cfg):
            REGISTRO.info('%-10s %s  %s', item, 'OK ' if deu_certo else 'FALTA', detalhe)

        if self.cfg.retencao_dias:
            REGISTRO.info('retenção: lotes são apagados %d dias após o envio', self.cfg.retencao_dias)

        else:
            REGISTRO.info('retenção DESLIGADA: nenhum lote é apagado automaticamente')


        self.lotes.pulsar(self.eu)  #* o primeiro pulso ANTES da thread: entre o reenfileirar acima e o primeiro tique eu nao existiria para os outros

        threading.Thread(target=self.bater_pulso, daemon=True, name='pulso').start()

        REGISTRO.info('trabalhador pronto — aguardando lotes')


    def rodar_um(self):

        #* pega um lote da fila e processa; True se havia trabalho

        lote = self.lotes.reivindicar(self.eu)

        if not lote:
            return False

        REGISTRO.info('lote %s: iniciando (%s arquivos)', lote.id, lote.n_arquivos)

        inicio = time.perf_counter()

        def avisar(feito, total, etapa):

            self.lotes.progresso(lote.id, feito, total, etapa or '', self.eu)

        try:
            relatorio = executor_de_lote.executar(self.cfg, lote.id, nome=lote.nome, n_esperado=lote.n_arquivos,
                                                  referencia=lote.referencia, progresso=avisar)

            if self.lotes.concluir(lote.id, self.eu):
                REGISTRO.info('lote %s: pronto em %.1f s (%d amostras)', lote.id, time.perf_counter() - inicio, len(relatorio.amostras))

            else:
                REGISTRO.warning('lote %s: terminei mas não sou mais o dono — desfecho deixado para quem está com ele', lote.id)

        except Exception as erro:
            REGISTRO.exception('lote %s: falhou', lote.id)
            self.lotes.falhar(lote.id, f'{type(erro).__name__}: {erro}', self.eu)

        return True


    def rodar_uma_arvore(self):

        #* pega um pedido de arvore e monta; True se havia trabalho. gemeo de rodar_um, de proposito: mesmo erro, mesma conferencia de dono

        pedido = self.arvores.reivindicar(self.eu)

        if not pedido:
            return False

        REGISTRO.info('árvore %s: iniciando (lote %s)', pedido.id, pedido.lote_id)

        inicio = time.perf_counter()

        def avisar(texto):

            self.arvores.etapa(pedido.id, texto, self.eu)

        try:
            lote = self.lotes.pegar(pedido.lote_id)

            resumo = executor_de_arvore.executar(self.cfg, pedido.lote_id, referencia=lote.referencia if lote else '', etapa=avisar)

            if self.arvores.concluir(pedido.id, resumo, self.eu):
                montadas = sum(1 for marcador in resumo if not marcador.get('recusado'))
                REGISTRO.info('árvore %s: pronta em %.1f s (%d marcador(es))', pedido.id, time.perf_counter() - inicio, montadas)

            else:
                REGISTRO.warning('árvore %s: terminei mas não sou mais o dono', pedido.id)

        except Exception as erro:
            REGISTRO.exception('árvore %s: falhou', pedido.id)
            self.arvores.falhar(pedido.id, f'{type(erro).__name__}: {erro}', self.eu)

        return True


    def faxinar(self):

        #* a faxina de retencao, se ja e hora; nunca derruba o laco

        if time.monotonic() < self.proxima_faxina:
            return

        self.proxima_faxina = time.monotonic() + INTERVALO_FAXINA

        try:
            balanco = expurgo.faxina(self.banco, self.cfg.pasta_de_lotes, self.cfg.retencao_dias)

            if balanco.apagados:
                REGISTRO.info('faxina: %d lote(s) expurgados, %.1f MB liberados', balanco.apagados, balanco.bytes / 1048576)

        except Exception:
            REGISTRO.exception('faxina falhou; segue processando a fila')


    def rodar(self):

        #* o laco: faxina na hora, lote primeiro, arvore depois, ate pedir_parada()

        while not self.parada_pedida.is_set():

            try:
                self.faxinar()

                if not (self.rodar_um() or self.rodar_uma_arvore()):  #* o `or` curto-circuita: havendo lote, nem se pergunta pelas arvores
                    self.parada_pedida.wait(OCIOSO)

            except Exception:
                REGISTRO.exception('erro no laço do trabalhador')
                self.parada_pedida.wait(PAUSA_APOS_ERRO)

        self.parar_pulso.set()

        REGISTRO.info('trabalhador encerrado')


    def __repr__(self):

        return f'Trabalhador({self.eu}, {self.banco.caminho})'




def main():

    logging.basicConfig(level=os.environ.get('EASYCONTIG_LOG', 'INFO'), format='%(asctime)s %(levelname)s %(name)s %(message)s')

    trabalhador = Trabalhador(configuracao.carregar())

    signal.signal(signal.SIGTERM, trabalhador.pedir_parada)
    signal.signal(signal.SIGINT, trabalhador.pedir_parada)

    trabalhador.arrancar()
    trabalhador.rodar()




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
