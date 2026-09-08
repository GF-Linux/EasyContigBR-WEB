#!/usr/bin/env bash
#
# carga.sh — sobe uma instancia ISOLADA do EasyContig BR e mede a maquina com tools/carga.py.
#
# POR QUE ISOLADA: o arnes cria contas, envia corridas e escreve em disco. Medir
# contra a instalacao de quem usa contaminaria o dado real, e o que se quer
# medir e o HARDWARE, nao o banco de dados de ninguem. Nada aqui toca ./dados.
#
# POR QUE OS TETOS SAEM DESLIGADOS: com EASYCONTIG_LIM_LEITURA no padrao (300
# leituras/min POR CONTA), uma rampa de 50 leitores mede o limitador e nao a
# maquina, e o resultado pareceria apenas "a maquina e ruim". O limitador tem
# testes proprios; aqui ele esta fora do caminho de proposito.
#
#
# COMO FUNCIONA
# =============
#
#     a porta esta livre?  (ss)                     senao: mostra quem atende e sai com 2
#     python = .venv/bin/python, ou o python3 do PATH
#     pasta de trabalho em $EASYCONTIG_CARGA_DIR (ou ~/.cache); avisa se for tmpfs
#     le o .env (tracy, blastn, bancos); as variaveis do arnes vem DEPOIS e ganham dele
#     sobe uvicorn easycontig_web.servidor_web:app + easycontig_web.processamento.trabalhador_da_fila
#         (pinados nos nucleos de EASYCONTIG_CARGA_CPUS, se definido e se houver taskset)
#     espera o /saude (ate 20 s; se o servidor morrer antes, mostra o log e sai com 2)
#     roda tools/carga.py --alvo http://127.0.0.1:$PORTA --dominio carga.local --json carga-<maquina>-<selo>.json <resto dos argumentos>
#     ao sair (trap): mata os dois processos e apaga a pasta de trabalho; os logs ficam na raiz do repositorio
#
#
# COMO USAR
# =========
#
#     ./tools/carga.sh                                              # mede esta maquina
#     ./tools/carga.sh 8321                                         # noutra porta
#     ./tools/carga.sh 8321 --amostras <pasta> --rampa 1,2 --voltas 2   # o resto vai para o carga.py
#
#     EASYCONTIG_CARGA_DIR=/disco/de/verdade ./tools/carga.sh      # onde a instancia isolada escreve
#     EASYCONTIG_CARGA_CPUS=0-1 ./tools/carga.sh                   # simula uma VPS de 2 vCPU nesta maquina
#
#
# O QUE MUDOU DO ORIGINAL
# =======================
#
#  1. Os modulos que sobem: easycontig_web.servidor_web:app e
#     easycontig_web.processamento.trabalhador_da_fila. O original chamava
#     easycontig_web.main:app e easycontig_web.trabalhador, que nao existiam.
#  2. Comentarios de fim de linha (#* e #!) e nomes em portugues; a logica, as
#     variaveis de ambiente exportadas e as mensagens sao as mesmas.
#
set -euo pipefail

PORTA="${1:-8123}"

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$RAIZ"

if ss -ltn "sport = :$PORTA" 2>/dev/null | grep -q LISTEN; then  #! QUEM ATENDE A PORTA, antes de qualquer hipotese: em 2026-08-06 um uvicorn antigo na 8099 mascarou uma correcao pronta
  echo "⚠️  já existe alguém escutando na porta $PORTA:"
  ss -ltnp "sport = :$PORTA" 2>/dev/null || true
  echo "    Escolha outra porta ( ./tools/carga.sh 8321 ) ou pare o processo."
  exit 2
fi

PYTHON="$RAIZ/.venv/bin/python"

[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

BASE="${EASYCONTIG_CARGA_DIR:-$HOME/.cache}"  #! NAO EM /tmp: em muita distro /tmp e tmpfs (RAM), e o gargalo que este arnes reproduz e DE DISCO (a cota percorrendo cada pasta de lote, 06/08); 40 corridas semeadas sao ~3,7 GB

mkdir -p "$BASE"

TRABALHO="$(mktemp -d "$BASE/easycontig-carga-XXXXXX")"

if [ "$(findmnt -no FSTYPE --target "$TRABALHO" 2>/dev/null)" = "tmpfs" ]; then
  echo "⚠️  $TRABALHO está em tmpfs (RAM). O número de disco sairá otimista."
  echo "    Aponte EASYCONTIG_CARGA_DIR para um caminho em disco de verdade."
fi

MAQUINA="$(hostname -s 2>/dev/null || cat /etc/hostname 2>/dev/null || echo maquina)"  #* o nome da maquina e o que torna dois relatorios comparaveis; `hostname` nao existe no SteamOS

SELO="${MAQUINA}-$(date +%Y%m%d-%H%M%S)"

RELATORIO="$RAIZ/carga-$SELO.json"

LOG_WEB="$RAIZ/carga-$SELO-web.log"  #* os logs ficam FORA do temporario: quando falha e justamente quando o trap apaga tudo

LOG_TRABALHADOR="$RAIZ/carga-$SELO-trabalhador.log"

if [ -f "$RAIZ/.env" ]; then  #* o .env diz onde estao o tracy, o blastn e os bancos; sem isso o trabalhador reprova toda amostra e o arnes mede uma fila de fracassos rapidos
  set -a; . "$RAIZ/.env"; set +a
  echo "li o .env para achar tracy/blastn/bancos"
fi

echo "instância isolada em $TRABALHO"
echo "relatório sairá em    $RELATORIO"

export EASYCONTIG_DATA_DIR="$TRABALHO/dados"  #* as variaveis do arnes vem DEPOIS do .env, de proposito, para ganharem dele
export EASYCONTIG_AUTH=dev
export EASYCONTIG_DOMINIO="carga.local"
export EASYCONTIG_SECRET_KEY="arnes-de-carga-nao-e-producao"
export EASYCONTIG_RETENCAO_DIAS=0
export EASYCONTIG_LIM_LEITURA=0  #* os tetos, fora do caminho (ver o cabecalho)
export EASYCONTIG_LIM_ENVIO=0
export EASYCONTIG_LIM_LOGIN=0
export EASYCONTIG_MAX_LOTES_ATIVOS=50  #* a cota de lotes ativos precisa caber nas corridas simultaneas da fase 2
export EASYCONTIG_MAX_BYTES_CONTA=$((32 * 1024 * 1024 * 1024))  #! a cota de bytes precisa caber na SEMENTE: cada corrida ocupa ~93 MB em disco, 40 corridas sao ~3,7 GB, e o padrao de 2 GB pararia a medicao com "cota estourada"

mkdir -p "$EASYCONTIG_DATA_DIR"

limpar() {
  echo
  echo "encerrando"
  [ -n "${PID_WEB:-}" ] && kill "$PID_WEB" 2>/dev/null || true
  [ -n "${PID_TRABALHADOR:-}" ] && kill "$PID_TRABALHADOR" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$TRABALHO"
}

trap limpar EXIT

FREIO=()  #* EASYCONTIG_CARGA_CPUS=0-1 simula uma VPS de 2 vCPU: so o servidor e o trabalhador sao pinados; o arnes fica com os outros nucleos, senao disputaria CPU com o que mede

if [ -n "${EASYCONTIG_CARGA_CPUS:-}" ]; then
  if command -v taskset >/dev/null 2>&1; then
    FREIO=(taskset -c "$EASYCONTIG_CARGA_CPUS")
    echo "servidor e trabalhador pinados nos núcleos $EASYCONTIG_CARGA_CPUS"
  else
    echo "⚠️  taskset não existe aqui; EASYCONTIG_CARGA_CPUS foi IGNORADO."
    echo "    O número sairá o da máquina inteira — não o da VPS simulada."
  fi
fi

"${FREIO[@]}" "$PYTHON" -m uvicorn easycontig_web.servidor_web:app --host 127.0.0.1 --port "$PORTA" --log-level warning > "$LOG_WEB" 2>&1 &
PID_WEB=$!

"${FREIO[@]}" "$PYTHON" -m easycontig_web.processamento.trabalhador_da_fila > "$LOG_TRABALHADOR" 2>&1 &
PID_TRABALHADOR=$!

echo -n "esperando o /saude responder"

for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORTA/saude" >/dev/null 2>&1; then
    echo " — de pé"
    break
  fi
  if ! kill -0 "$PID_WEB" 2>/dev/null; then  #* se o processo morreu, nao adianta esperar os 20 segundos
    echo
    echo "⚠️  o servidor morreu no arranque:"
    cat "$LOG_WEB"
    exit 2
  fi
  echo -n "."
  sleep 0.5
done

"$PYTHON" tools/carga.py --alvo "http://127.0.0.1:$PORTA" --dominio carga.local --json "$RELATORIO" "${@:2}"

echo
echo "log do servidor:     $LOG_WEB"
echo "log do trabalhador:  $LOG_TRABALHADOR"
