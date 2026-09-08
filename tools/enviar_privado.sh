#!/usr/bin/env bash
#
# enviar_privado.sh — leva para a VPS o que NAO esta no repositorio: o nucleo cientifico e o front.
#
# Por que este script existe (decisao de 08/09/2026):
#
#     Os .py da raiz (o nucleo: montagem, identificacao, filogenia, ...) e o
#     front (easycontig_web/templates e easycontig_web/estatico) ficaram FORA do
#     git. Sao o mecanismo -- a ciencia e a tela --, e ficam so nas maquinas que
#     precisam deles. O repositorio carrega o andaime: a web, a suite, o
#     empacotamento e a documentacao.
#
#     Consequencia direta: `git pull` na VPS deixou de ser suficiente. O
#     `docker compose up --build` monta a imagem a partir da PASTA, nao do
#     commit, entao o que este script poe no disco de la entra na imagem pelo
#     mesmo COPY de sempre. Sem isto o build FALHA (o Dockerfile confere), que e
#     melhor do que uma imagem que sobe e morre no primeiro .ab1.
#
#
# COMO FUNCIONA
# =============
#
#     0. confere que a origem tem o que mandar     falta um .py do nucleo ou o front -> FALHOU, sai com 1
#        e que o destino E o repositorio             sem pyproject.toml e easycontig_web/ la -> FALHOU, sai com 1
#     1. rsync dos .py da raiz                     so os arquivos, sem entrar em pasta nenhuma
#     2. rsync de templates/ e estatico/           --delete: template apagado aqui some de la tambem
#     3. confere do outro lado                     conta os arquivos que chegaram; divergiu -> FALHOU, sai com 1
#
#     O que este script NUNCA toca: o .env da VPS, a pasta de dados
#     (/var/lib/easycontig), os bancos e o codigo versionado, que vem do git.
#
#
# COMO USAR
# =========
#
#     ./tools/enviar_privado.sh                             # daqui para vps-easycontig:/opt/easycontig
#     ./tools/enviar_privado.sh vps-easycontig:/opt/easycontig
#     ./tools/enviar_privado.sh /caminho/local              # para uma pasta desta maquina (teste)
#     CONFERIR=0 ./tools/enviar_privado.sh ...              # pula a contagem do outro lado
#
#     na VPS, depois:
#     cd /opt/easycontig && git pull && docker compose up -d --build
#
#     para popular um clone NOVO (o caminho inverso), sem script:
#     rsync -a vps-easycontig:/opt/easycontig/'*.py' .
#     rsync -a vps-easycontig:/opt/easycontig/easycontig_web/{templates,estatico} easycontig_web/
#
set -euo pipefail

DESTINO="${1:-vps-easycontig:/opt/easycontig}"

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  #* a raiz do repositorio, seja de onde for que chamaram

CONFERIR="${CONFERIR:-1}"

mapfile -t NUCLEO < <(sed -n '/^py-modules = \[/,/^\]/p' "$RAIZ/pyproject.toml" | grep -oE '"[a-z_]+"' | tr -d '"')  #! a lista sai do pyproject.toml, e nao daqui nem de `ls *.py`: e a lista que o pip usa, a unica que ja e obrigada a estar certa (um teste da suite a compara com o que a web importa). `ls *.py` mandaria junto qualquer arquivo solto na raiz -- rascunho, .py velho de outra epoca

if [ "${#NUCLEO[@]}" -eq 0 ]; then
  echo "  FALHOU: nao achei py-modules em $RAIZ/pyproject.toml — sem a lista nao da para saber o que e nucleo" >&2
  exit 1
fi

faltando=()  #! 0. o que nao esta aqui nao pode ser mandado, e mandar pela metade e pior do que nao mandar: a imagem sobe sem um modulo e o erro so aparece no primeiro lote

arquivos=()

for modulo in "${NUCLEO[@]}"; do

  if [ -f "$RAIZ/$modulo.py" ]; then
    arquivos+=("$RAIZ/$modulo.py")

  else
    faltando+=("$modulo.py")
  fi
done

[ -f "$RAIZ/easycontig_web/templates/base.html" ] || faltando+=('easycontig_web/templates/')

[ -f "$RAIZ/easycontig_web/estatico/oficina.js" ] || faltando+=('easycontig_web/estatico/')

if [ "${#faltando[@]}" -gt 0 ]; then
  echo "  FALHOU: faltam ${#faltando[@]} item(ns) nesta maquina: ${faltando[*]}" >&2
  echo "          eles nao sao versionados; traga-os de onde estao antes de enviar" >&2
  exit 1
fi

parece_repositorio='[ -f "$1/pyproject.toml" ] && [ -d "$1/easycontig_web" ]'  #! 0b. destino que nao e o repositorio: o rsync do front morreria com erro cru de mkdir, e o do nucleo teria ESPALHADO 12 arquivos numa pasta qualquer antes disso. Pior ainda se a pasta existir mas o git de la estiver desatualizado: a imagem sairia com front novo e web velha

case "$DESTINO" in
  *:*) ssh "${DESTINO%%:*}" "sh -c '$parece_repositorio' _ '${DESTINO#*:}'" && e_repositorio=1 || e_repositorio=0 ;;
    *) sh -c "$parece_repositorio" _ "$DESTINO" && e_repositorio=1 || e_repositorio=0 ;;
esac

if [ "$e_repositorio" = '0' ]; then
  echo "  FALHOU: $DESTINO nao parece o repositorio (falta pyproject.toml ou easycontig_web/)" >&2
  echo "          o nucleo e o front vao POR CIMA do que o git traz; clone ou atualize o repositorio la primeiro" >&2
  exit 1
fi

echo "[$(date -Is)] enviando o nucleo e o front de $RAIZ → $DESTINO"

n_nucleo="${#NUCLEO[@]}"

rsync -a "${arquivos[@]}" "$DESTINO/"  #* 1. os modulos do pyproject, um a um. sem --delete: ele apagaria os .py que o git de la trouxe

echo "  nucleo: $n_nucleo modulo(s)"

n_templates="$(ls -1 "$RAIZ/easycontig_web/templates" | wc -l)"

n_estatico="$(ls -1 "$RAIZ/easycontig_web/estatico" | wc -l)"

rsync -a --delete "$RAIZ/easycontig_web/templates/" "$DESTINO/easycontig_web/templates/"  #! 2. --delete aqui e de proposito: template renomeado deixaria o antigo na VPS, e o Jinja serviria o velho para quem soubesse a URL

rsync -a --delete "$RAIZ/easycontig_web/estatico/" "$DESTINO/easycontig_web/estatico/"

echo "  front: $n_templates template(s), $n_estatico estatico(s)"

if [ "$CONFERIR" = '1' ]; then  #! 3. um envio que ninguem leu nao e envio: o rsync sai com 0 quando copia 0 arquivo por caminho errado

  contar='cd "$1" || exit 1; encontrados=0; for modulo in $2; do [ -f "$modulo.py" ] && encontrados=$((encontrados + 1)); done; echo "$encontrados $(ls -1 easycontig_web/templates 2>/dev/null | wc -l) $(ls -1 easycontig_web/estatico 2>/dev/null | wc -l)"'  #* conta OS MODULOS DA LISTA, nao todo .py: um .py estranho no destino nao pode fazer a conta fechar

  case "$DESTINO" in
    *:*) chegou="$(ssh "${DESTINO%%:*}" "sh -c '$contar' _ '${DESTINO#*:}' '${NUCLEO[*]}'")" ;;  #* destino remoto: a contagem roda la
      *) chegou="$(sh -c "$contar" _ "$DESTINO" "${NUCLEO[*]}")" ;;
  esac

  esperado="$n_nucleo $n_templates $n_estatico"

  if [ "$chegou" != "$esperado" ]; then
    echo "  FALHOU: o destino ficou com [$chegou] e esta maquina tem [$esperado]" >&2
    echo "          (nucleo, templates, estaticos) — confira o caminho do destino e as permissoes" >&2
    exit 1
  fi

  echo "  conferido: o destino tem os mesmos $n_nucleo + $n_templates + $n_estatico arquivos"
fi

echo "[$(date -Is)] ok — agora, no destino: git pull && docker compose up -d --build"
