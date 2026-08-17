"""
trabalhador.py — o processo que esvazia a fila. `python -m easycontig_web.trabalhador`

Roda SEPARADO do servidor web de propósito. É o que faz a conta da ADR 0050
fechar: a requisição HTTP devolve um protocolo em milissegundos e some, e os
~26 s de um lote de 40 amostras acontecem aqui, onde ninguém está esperando com
uma conexão aberta.

Para dobrar a vazão, suba mais de um: eles disputam a mesma fila e o UPDATE
condicional de `fila.reivindicar` garante que cada lote é de um só. Uma VPS de
4 vCPU comporta ~3 trabalhadores (deixando um núcleo para o servidor web).
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time

from .. import configuracao as config
from ..processamento import executor_de_lote as executor
from ..processamento import fila_de_lotes as fila
from ..dados import expurgo_por_retencao as retencao

log = logging.getLogger("easycontig.trabalhador")

# De hora em hora, e não a cada volta do laço: a faxina varre o disco inteiro
# para somar tamanhos, e nada do que ela decide muda em 0,25 s — um lote leva
# dias para vencer. Mora aqui, e não num cron, porque o trabalhador é o processo
# que a universidade já vai manter de pé; um agendamento a mais no sistema é
# mais uma coisa para alguém esquecer de configurar na migração de servidor.
INTERVALO_FAXINA = 3600.0

_parar = False


def _pedir_parada(*_):
    global _parar
    _parar = True
    log.info("parada pedida — encerro depois do lote atual")


def bater_pulso(cfg: config.Config, eu: str, parar: threading.Event) -> None:
    """O pulso em thread PRÓPRIA, e é o ponto todo.

    ⚠️ Ele batia no topo do laço, e `rodar_um` segura o laço o lote INTEIRO.
    Um lote de 40 amostras leva ~26 s e cabia nos 90 s de `PULSO_VELHO` — mas o
    app aceita 400 arquivos, e aí o trabalhador ocupado fica indistinguível de
    um morto. O irmão subindo reenfileirava justamente o lote que estava sendo
    montado: o defeito que o pulso existe para impedir, entrando pela porta dos
    fundos. Achado pelo painel de verificação horas depois do conserto.

    Numa thread, o pulso passa a dizer o que precisa dizer — "este PROCESSO está
    vivo" — em vez de "este processo está entre lotes". `daemon` para não segurar
    o encerramento, e cada `pulsar` abre a própria conexão.
    """
    while not parar.wait(fila.INTERVALO_PULSO):
        try:
            fila.pulsar(cfg.sqlite_path, eu)
        except Exception:                       # noqa: BLE001
            log.exception("não consegui registrar o pulso; segue processando")


def rodar_um(cfg: config.Config, eu: str = "") -> bool:
    """Pega um lote da fila e processa. True se havia trabalho."""
    lote = fila.reivindicar(cfg.sqlite_path, eu)
    if not lote:
        return False

    lote_id = lote["id"]
    log.info("lote %s: iniciando (%s arquivos)", lote_id, lote["n_arquivos"])
    t0 = time.perf_counter()
    try:
        def _prog(feito, total, etapa):
            fila.progresso(cfg.sqlite_path, lote_id, feito, total, etapa or "", eu)

        rep = executor.executar(cfg, lote_id, nome=lote["nome"],
                                n_esperado=lote["n_arquivos"],
                                referencia=lote.get("referencia") or "",
                                progresso=_prog)
        if fila.concluir(cfg.sqlite_path, lote_id, eu):
            log.info("lote %s: pronto em %.1f s (%d amostras)",
                     lote_id, time.perf_counter() - t0, len(rep.samples))
        else:
            # Perdi o lote no meio do caminho: outro trabalhador é o dono agora.
            # Carimbar PRONTO aqui publicaria a MINHA montagem por cima da dele.
            log.warning("lote %s: terminei mas não sou mais o dono — desfecho "
                        "deixado para quem está com ele", lote_id)
    except Exception as e:                      # noqa: BLE001
        # Um lote ruim não pode derrubar o trabalhador: ele volta para a fila e
        # o próximo usuário continua sendo atendido. O erro vai para o banco,
        # que é o que a página do lote mostra.
        log.exception("lote %s: falhou", lote_id)
        fila.falhar(cfg.sqlite_path, lote_id, f"{type(e).__name__}: {e}", eu)
    return True


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("EASYCONTIG_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = config.carregar()
    fila.criar_esquema(cfg.sqlite_path)

    # Quem sou eu nesta fila. Máquina + PID basta: a pergunta que a tela faz é
    # "há ALGUÉM vivo atendendo", não "quem" — mas o LOTE precisa saber o dono,
    # para o arranque de um trabalhador não reenfileirar o trabalho do outro.
    eu = f"{socket.gethostname()}:{os.getpid()}"

    # ⚠️ Passa `eu` porque isto roda ANTES do primeiro pulso: sem dizer quem
    # sou, eu apareceria para mim mesmo como um trabalhador morto.
    orfaos = fila.reenfileirar_orfaos(cfg.sqlite_path, eu)
    if orfaos:
        log.warning("%d lote(s) sem dono vivo voltaram para a fila", orfaos)

    for item, ok, det in config.diagnostico(cfg):
        log.info("%-10s %s  %s", item, "OK " if ok else "FALTA", det)

    # A política de retenção vai para o log toda vez que o processo sobe. Quem
    # opera o servidor tem que conseguir descobrir se o expurgo está ligado sem
    # ler código — e "desligado" é um estado legítimo, não um defeito.
    if cfg.retencao_dias:
        log.info("retenção: lotes são apagados %d dias após o envio",
                 cfg.retencao_dias)
    else:
        log.info("retenção DESLIGADA: nenhum lote é apagado automaticamente")

    signal.signal(signal.SIGTERM, _pedir_parada)
    signal.signal(signal.SIGINT, _pedir_parada)

    log.info("trabalhador pronto — aguardando lotes")
    ocioso = 0.25
    # Zero e não `time.monotonic()`: a primeira faxina roda no arranque. É quando
    # o operador está olhando o log, então é a hora em que um expurgo inesperado
    # ainda dá para perceber e desligar.
    proxima_faxina = 0.0

    # O primeiro pulso ANTES da thread: entre o `reenfileirar_orfaos` acima e o
    # primeiro tique haveria uma janela em que eu não existo para os outros.
    fila.pulsar(cfg.sqlite_path, eu)
    parar_pulso = threading.Event()
    threading.Thread(target=bater_pulso, args=(cfg, eu, parar_pulso),
                     daemon=True, name="pulso").start()

    while not _parar:
        try:
            if time.monotonic() >= proxima_faxina:
                proxima_faxina = time.monotonic() + INTERVALO_FAXINA
                # Fora do `rodar_um` de propósito: falha de faxina não pode
                # impedir o lote de alguém de ser processado.
                try:
                    r = retencao.faxina(cfg.sqlite_path, cfg.lotes_dir,
                                        dias=cfg.retencao_dias)
                    if r["apagados"]:
                        log.info("faxina: %d lote(s) expurgados, %.1f MB liberados",
                                 r["apagados"], r["bytes"] / 1048576)
                except Exception:               # noqa: BLE001
                    log.exception("faxina falhou; segue processando a fila")

            if not rodar_um(cfg, eu):
                time.sleep(ocioso)
        except Exception:                       # noqa: BLE001
            log.exception("erro no laço do trabalhador")
            time.sleep(2)
    parar_pulso.set()
    log.info("trabalhador encerrado")


if __name__ == "__main__":
    main()
