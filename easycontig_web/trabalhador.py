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
import time

from . import config, executor, fila, retencao

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


def rodar_um(cfg: config.Config) -> bool:
    """Pega um lote da fila e processa. True se havia trabalho."""
    lote = fila.reivindicar(cfg.sqlite_path)
    if not lote:
        return False

    lote_id = lote["id"]
    log.info("lote %s: iniciando (%s arquivos)", lote_id, lote["n_arquivos"])
    t0 = time.perf_counter()
    try:
        def _prog(feito, total, etapa):
            fila.progresso(cfg.sqlite_path, lote_id, feito, total, etapa or "")

        rep = executor.executar(cfg, lote_id, nome=lote["nome"],
                                n_esperado=lote["n_arquivos"],
                                referencia=lote.get("referencia") or "",
                                progresso=_prog)
        fila.concluir(cfg.sqlite_path, lote_id)
        log.info("lote %s: pronto em %.1f s (%d amostras)",
                 lote_id, time.perf_counter() - t0, len(rep.samples))
    except Exception as e:                      # noqa: BLE001
        # Um lote ruim não pode derrubar o trabalhador: ele volta para a fila e
        # o próximo usuário continua sendo atendido. O erro vai para o banco,
        # que é o que a página do lote mostra.
        log.exception("lote %s: falhou", lote_id)
        fila.falhar(cfg.sqlite_path, lote_id, f"{type(e).__name__}: {e}")
    return True


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("EASYCONTIG_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = config.carregar()
    fila.criar_esquema(cfg.sqlite_path)

    orfaos = fila.reenfileirar_orfaos(cfg.sqlite_path)
    if orfaos:
        log.warning("%d lote(s) presos em 'rodando' voltaram para a fila", orfaos)

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
    # Quem sou eu nesta fila. Máquina + PID basta: a pergunta que a tela faz é
    # "há ALGUÉM vivo atendendo", não "quem".
    eu = f"{socket.gethostname()}:{os.getpid()}"
    proximo_pulso = 0.0
    while not _parar:
        try:
            # Antes de qualquer trabalho: é o pulso que sustenta a frase "na
            # fila" na tela de quem enviou. Sem ele, em 2026-08-06 um lote
            # esperou para sempre enquanto a página prometia que alguém ia pegar
            # — não havia trabalhador nenhum olhando esta fila.
            if time.monotonic() >= proximo_pulso:
                proximo_pulso = time.monotonic() + fila.INTERVALO_PULSO
                try:
                    fila.pulsar(cfg.sqlite_path, eu)
                except Exception:               # noqa: BLE001
                    log.exception("não consegui registrar o pulso; segue processando")

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

            if not rodar_um(cfg):
                time.sleep(ocioso)
        except Exception:                       # noqa: BLE001
            log.exception("erro no laço do trabalhador")
            time.sleep(2)
    log.info("trabalhador encerrado")


if __name__ == "__main__":
    main()
