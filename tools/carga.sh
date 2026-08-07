#!/usr/bin/env bash
# carga.sh — sobe uma instância ISOLADA e mede a máquina com tools/carga.py.
#
# Por que isolada: o arnês cria contas, envia corridas e escreve em disco. Medir
# contra a instalação de quem usa contaminaria o dado real — e o que se quer
# medir é o HARDWARE, não o banco de dados de ninguém. Nada aqui toca ./dados.
#
# Por que os tetos saem desligados: com EASYCONTIG_LIM_LEITURA no padrão (300
# leituras/min POR CONTA), uma rampa de 50 leitores mede o limitador e não a
# máquina — e o resultado pareceria apenas "a máquina é ruim". O limitador tem
# testes próprios; aqui ele está fora do caminho de propósito.
#
#   ./tools/carga.sh                 # mede esta máquina
#   ./tools/carga.sh 8321            # noutra porta
#
set -euo pipefail

PORTA="${1:-8123}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

# ⚠️ QUEM ATENDE A PORTA, ANTES DE QUALQUER HIPÓTESE. Em 2026-08-06 um uvicorn
# antigo seguia na 8099 com a configuração velha e mascarou uma correção depois
# de ela estar pronta. Um arnês apontado para o servidor errado mede outra coisa
# e não avisa.
if ss -ltn "sport = :$PORTA" 2>/dev/null | grep -q LISTEN; then
  echo "⚠️  já existe alguém escutando na porta $PORTA:"
  ss -ltnp "sport = :$PORTA" 2>/dev/null || true
  echo "    Escolha outra porta ( ./tools/carga.sh 8321 ) ou pare o processo."
  exit 2
fi

PY="$RAIZ/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

# ⚠️ NÃO EM /tmp. No Deck (e em muita distro) /tmp é tmpfs, ou seja RAM — e o
# gargalo que este arnês precisa reproduzir é DE DISCO: foi esse o achado de
# 06/08, a cota percorrendo cada pasta de lote (23 ms, 68% da página inicial).
# Medindo em RAM o número sai lisonjeiro e a VPS seria escolhida pequena demais.
# Some-se que 40 corridas semeadas são ~3,7 GB, que em tmpfs saem da memória.
BASE="${EASYCONTIG_CARGA_DIR:-$HOME/.cache}"
mkdir -p "$BASE"
TRABALHO="$(mktemp -d "$BASE/easycontig-carga-XXXXXX")"
if [ "$(findmnt -no FSTYPE --target "$TRABALHO" 2>/dev/null)" = "tmpfs" ]; then
  echo "⚠️  $TRABALHO está em tmpfs (RAM). O número de disco sairá otimista."
  echo "    Aponte EASYCONTIG_CARGA_DIR para um caminho em disco de verdade."
fi
# `hostname` não existe no SteamOS; o nome da máquina é o que torna dois
# relatórios comparáveis, então vale insistir em achá-lo por outro caminho.
NOME="$(hostname -s 2>/dev/null || cat /etc/hostname 2>/dev/null || echo maquina)"
SELO="${NOME}-$(date +%Y%m%d-%H%M%S)"
RELATORIO="$RAIZ/carga-$SELO.json"
# Os logs ficam FORA do temporário: quando a coisa falha é justamente quando o
# trap apaga tudo, e aí não sobra o que ler.
LOG_WEB="$RAIZ/carga-$SELO-web.log"
LOG_TRAB="$RAIZ/carga-$SELO-trabalhador.log"

# O `.env` traz onde estão o tracy, o blastn e os bancos — sem isso o
# trabalhador sobe e reprova toda amostra, e o arnês mediria uma fila de
# fracassos rápidos em vez do trabalho real. As nossas variáveis vêm DEPOIS,
# de propósito, para ganharem dele.
if [ -f "$RAIZ/.env" ]; then
  set -a; . "$RAIZ/.env"; set +a
  echo "li o .env para achar tracy/blastn/bancos"
fi

echo "instância isolada em $TRABALHO"
echo "relatório sairá em    $RELATORIO"

export EASYCONTIG_DATA_DIR="$TRABALHO/dados"
export EASYCONTIG_AUTH=dev
export EASYCONTIG_DOMINIO="carga.local"
export EASYCONTIG_SECRET_KEY="arnes-de-carga-nao-e-producao"
export EASYCONTIG_RETENCAO_DIAS=0
# Os tetos, fora do caminho — ver o cabeçalho.
export EASYCONTIG_LIM_LEITURA=0
export EASYCONTIG_LIM_ENVIO=0
export EASYCONTIG_LIM_LOGIN=0
# A cota de lotes ativos precisa caber nas corridas simultâneas da fase 2.
export EASYCONTIG_MAX_LOTES_ATIVOS=50
# E a cota de bytes precisa caber na SEMENTE: cada corrida ocupa ~93 MB em
# disco (26 MB de `entrada/` + 68 MB de intermediários do tracy em `trabalho/`),
# então 40 corridas são ~3,7 GB e o padrão de 2 GB recusaria a semente no meio
# — o arnês pararia com "cota estourada" numa medição que não é sobre cota.
export EASYCONTIG_MAX_BYTES_CONTA=$((32 * 1024 * 1024 * 1024))
# Os bancos e o tracy vêm do ambiente de quem chama, se estiverem definidos.
mkdir -p "$EASYCONTIG_DATA_DIR"

limpar() {
  echo
  echo "encerrando"
  [ -n "${PID_WEB:-}" ] && kill "$PID_WEB" 2>/dev/null || true
  [ -n "${PID_TRAB:-}" ] && kill "$PID_TRAB" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$TRABALHO"
}
trap limpar EXIT

"$PY" -m uvicorn easycontig_web.main:app --host 127.0.0.1 --port "$PORTA" \
      --log-level warning > "$LOG_WEB" 2>&1 &
PID_WEB=$!
"$PY" -m easycontig_web.trabalhador > "$LOG_TRAB" 2>&1 &
PID_TRAB=$!

echo -n "esperando o /saude responder"
for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORTA/saude" >/dev/null 2>&1; then
    echo " — de pé"
    break
  fi
  # Se o processo morreu, não adianta esperar os 40 segundos.
  if ! kill -0 "$PID_WEB" 2>/dev/null; then
    echo
    echo "⚠️  o servidor morreu no arranque:"
    cat "$LOG_WEB"
    exit 2
  fi
  echo -n "."
  sleep 0.5
done

"$PY" tools/carga.py --alvo "http://127.0.0.1:$PORTA" \
      --dominio carga.local --json "$RELATORIO" "${@:2}"

echo
echo "log do servidor:     $LOG_WEB"
echo "log do trabalhador:  $LOG_TRAB"
