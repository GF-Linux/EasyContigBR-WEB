#!/usr/bin/env bash
#
# backup.sh — copia consistente dos dados do EasyContig BR. Roda no HOSPEDEIRO, nao dentro do conteiner.
#
# O que precisa ser salvo, e por que:
#
#     fila.sqlite3   quem enviou o que, quando, com que banco de referencia.
#                    Sem ele os .ab1 viram uma pasta de arquivos anonimos.
#     lotes/         os .ab1 enviados e os relatorios. ISTO NAO EXISTE EM OUTRO
#                    LUGAR: e dado de sequenciamento nao publicado; se sumir
#                    daqui, sumiu.
#     bancos/        os bancos BLAST montados. Entrou em 2026-08-11 por um motivo
#                    que nao e obvio: o FASTA que os gerou E APAGADO logo depois
#                    do makeblastdb, e o texto enviado nunca vai para o sqlite.
#                    Para um banco PRIVADO (as sequencias do grupo ainda nao
#                    publicadas) o indice aqui e a UNICA copia. Os do catalogo se
#                    remontam do NCBI; os do usuario nao se remontam de lugar nenhum.
#
# O SQLite NAO pode ser copiado com `cp` enquanto o servidor roda. Ele esta em
# modo WAL: metade do estado vive no -wal, e um cp do arquivo principal pega um
# banco pela metade que restaura calado e errado. Por isso VACUUM INTO, que e a
# copia consistente do proprio SQLite, feita sem parar ninguem.
#
#
# COMO FUNCIONA
# =============
#
#     0. a origem existe e nao esta vazia?      fila.sqlite3 ausente -> FALHOU, sai com 1
#                                               0 lotes E 0 arquivos -> FALHOU, salvo PERMITIR_VAZIO=1
#     1. o banco                                sqlite3 "VACUUM INTO" <destino>/<carimbo>/fila.sqlite3
#     2. os arquivos                            rsync -a --delete de lotes/ (e bancos/, se existir)
#                                               --link-dest no backup anterior: o que nao mudou vira link fisico
#     3. a copia foi lida?                      COUNT(*) da origem == COUNT(*) da copia; PRAGMA integrity_check == ok;
#                                               a arvore de arquivos foi copiada (origem > 0 e copia == 0 -> FALHOU)
#                                               qualquer falha apaga a copia e sai com 1, para o cron reclamar HOJE
#     4. expurgo                                apaga backups com mais de GUARDAR_DIAS (30), so os que tem cara
#                                               de backup deste script (AAAAMMDDTHHMMSSZ) e nunca o ultimo que restou
#
#
# COMO USAR
# =========
#
#     ./tools/backup.sh /caminho/dos/dados /destino/dos/backups
#     GUARDAR_DIAS=60 ./tools/backup.sh ...                     # retencao
#     PERMITIR_VAZIO=1 ./tools/backup.sh ...                    # instalacao nova, ainda sem lote
#
#     no cron do servidor (03:15 todo dia):
#     15 3 * * * /opt/easycontig/tools/backup.sh /var/lib/easycontig /backup >> /var/log/easycontig-backup.log 2>&1
#
#
# O QUE MUDOU DO ORIGINAL
# =======================
#
#  1. Comentarios de fim de linha (#* e #!) e nomes em portugues. As etapas, as
#     mensagens, os codigos de saida e o formato do destino sao os mesmos: um
#     backup feito pelo original e um feito por este sao intercambiaveis
#     (inclusive o --link-dest entre eles).
#  2. Origem com fila.sqlite3 mas sem lotes/ encerrava SEM MENSAGEM com saida 1
#     (o `find` falhava e o set -e derrubava o script). Agora diz o que faltou.
#  3. O backup anterior do --link-dest era a ultima pasta do destino em ordem
#     alfabetica, qualquer pasta: uma pasta estranha no destino (o expurgo ja
#     as ignorava desde 2026-08-11) desligava o link fisico em silencio e o
#     rsync avisava "--link-dest arg does not exist". Agora so pastas com cara
#     de backup deste script contam, como no expurgo.
#
set -euo pipefail

DADOS="${1:?uso: backup.sh <dir de dados> <dir de destino>}"

DESTINO="${2:?uso: backup.sh <dir de dados> <dir de destino>}"

GUARDAR_DIAS="${GUARDAR_DIAS:-30}"

PADRAO_DE_NOME='^[0-9]{8}T[0-9]{6}Z$'  #* a cara de um backup deste script; o expurgo so toca nisso

carimbo="$(date -u +%Y%m%dT%H%M%SZ)"

alvo="$DESTINO/$carimbo"

mkdir -p "$alvo"

chmod 700 "$DESTINO" "$alvo"  #! dado de sequenciamento nao publicado: a copia nao pode nascer legivel para todo mundo da maquina (varredura de 06/08)

echo "[$(date -Is)] backup de $DADOS → $alvo"

if [ ! -f "$DADOS/fila.sqlite3" ]; then  #! A ORIGEM PRECISA EXISTIR ANTES DE QUALQUER COISA (2026-08-11): volume desmontado + reinicio do conteiner produzem uma origem que existe e esta vazia, e um backup vazio certificado como bom e pior do que backup nenhum
  echo "  FALHOU: $DADOS/fila.sqlite3 não existe — origem errada ou volume desmontado" >&2
  rmdir "$alvo" 2>/dev/null || true
  exit 1
fi

if [ ! -d "$DADOS/lotes" ]; then  #! sem esta linha o `find` abaixo falhava e o set -e encerrava o script SEM MENSAGEM (saida 1 muda); o app cria lotes/ ao subir, entao a pasta ausente e origem errada
  echo "  FALHOU: $DADOS/lotes não existe — origem errada ou volume desmontado (o app cria lotes/ ao subir)" >&2
  rmdir "$alvo" 2>/dev/null || true
  exit 1
fi

n_lotes_no_banco="$(sqlite3 "$DADOS/fila.sqlite3" 'SELECT COUNT(*) FROM lotes' 2>/dev/null || echo 0)"

n_arquivos_na_origem="$(find "$DADOS/lotes" -type f 2>/dev/null | wc -l)"

if [ "$n_lotes_no_banco" -eq 0 ] && [ "$n_arquivos_na_origem" -eq 0 ] && [ "${PERMITIR_VAZIO:-0}" != "1" ]; then  #* instalacao nova tem zero lote e e legitima: por isso a saida e uma variavel declarada por quem instala, e nao um silencio
  echo "  FALHOU: a origem está vazia (0 lotes no banco, 0 arquivos em lotes/)." >&2
  echo "          Se a instalação é nova mesmo, rode com PERMITIR_VAZIO=1." >&2
  rmdir "$alvo" 2>/dev/null || true
  exit 1
fi

sqlite3 "$DADOS/fila.sqlite3" "VACUUM INTO '$alvo/fila.sqlite3'"  #* 1. o banco, consistente, sem parar o servidor

echo "  banco: $(du -h "$alvo/fila.sqlite3" | cut -f1)"

anterior="$(ls -1d "$DESTINO"/*/ 2>/dev/null | grep -Ev "$carimbo" | while read -r pasta; do basename "$pasta" | grep -Eq "$PADRAO_DE_NOME" && echo "$pasta"; done | tail -1 || true)"  #* 2. --link-dest aponta para o backup anterior: trinta dias de backup diario de um acervo que quase nao muda custam quase o tamanho de UM. so pastas com cara de backup: o original pegava a ultima pasta em ordem alfabetica, e uma pasta estranha no destino desligava o link-dest em silencio

rsync -a --delete ${anterior:+--link-dest="$anterior/lotes"} "$DADOS/lotes/" "$alvo/lotes/"

echo "  lotes: $(du -sh "$alvo/lotes" | cut -f1) ($(ls -1 "$alvo/lotes" | wc -l) corridas)"

if [ -d "$DADOS/bancos" ]; then  #* 2b. os bancos BLAST (ver o cabecalho). mkdir -p na origem seria escrever no volume de dados a partir do backup: se a pasta nao existe, apenas nao ha o que copiar
  rsync -a --delete ${anterior:+--link-dest="$anterior/bancos"} "$DADOS/bancos/" "$alvo/bancos/"
  echo "  bancos: $(du -sh "$alvo/bancos" | cut -f1)"
else
  echo "  bancos: nenhum ($DADOS/bancos não existe)"
fi

n_origem="$(sqlite3 "$DADOS/fila.sqlite3" 'SELECT COUNT(*) FROM lotes' 2>/dev/null || echo x)"  #! 3. um backup que nunca foi lido nao e backup: a copia abre? os numeros batem? senao, apaga e FALHA, para o cron reclamar hoje e nao no dia de restaurar

n_copia="$(sqlite3 "$alvo/fila.sqlite3" 'SELECT COUNT(*) FROM lotes' 2>/dev/null || echo y)"

if [ "$n_origem" != "$n_copia" ]; then
  echo "  FALHOU: origem tem $n_origem lotes, a cópia tem $n_copia" >&2
  rm -rf "$alvo"
  exit 1
fi

if ! sqlite3 "$alvo/fila.sqlite3" 'PRAGMA integrity_check' | grep -qx ok; then
  echo "  FALHOU: integrity_check recusou a cópia" >&2
  rm -rf "$alvo"
  exit 1
fi

n_arquivos_depois="$(find "$DADOS/lotes" -type f 2>/dev/null | wc -l)"  #! a arvore de arquivos e o que este backup existe para salvar, e ate 2026-08-11 nada a conferia: o banco e o indice, os .ab1 sao o acervo. contada DE NOVO depois do rsync: um envio pode ter chegado no meio

n_arquivos_na_copia="$(find "$alvo/lotes" -type f 2>/dev/null | wc -l)"

if [ "$n_arquivos_depois" -gt 0 ] && [ "$n_arquivos_na_copia" -eq 0 ]; then
  echo "  FALHOU: a origem tem $n_arquivos_depois arquivos e a cópia não tem NENHUM" >&2
  rm -rf "$alvo"
  exit 1
fi

faltando=$(( n_arquivos_depois - n_arquivos_na_copia ))

if [ "$faltando" -gt 0 ]; then  #* diferenca pequena e o envio que chegou durante o rsync: normal, some no backup de amanha. vai para o log assim mesmo: numero que ninguem ve nao vigia nada
  echo "  nota: $faltando arquivo(s) a mais na origem que na cópia (envio durante o backup?)"
fi

echo "  conferido: $n_copia lotes no banco, integridade ok, $n_arquivos_na_copia arquivos copiados"

mapfile -t antigos < <(  #! 4. SO APAGA O QUE TEM CARA DE BACKUP DESTE SCRIPT (2026-08-11): o alvo do rm -rf vem do $2 da linha de comando, e um destino apontado para a pasta errada faria o expurgo comer o backup de outro servico por antiguidade
  ls -1d "$DESTINO"/*/ 2>/dev/null \
  | while read -r pasta; do
      basename "$pasta" | grep -Eq "$PADRAO_DE_NOME" && echo "$pasta"
    done | head -n -1)  #* head -n -1: nunca o ultimo que restou, aconteca o que acontecer com o relogio

for velho in "${antigos[@]:-}"; do
  [ -z "$velho" ] && continue
  if [ "$(find "$velho" -maxdepth 0 -mtime +"$GUARDAR_DIAS")" ]; then
    echo "  expurgando backup de $(basename "$velho")"
    rm -rf "$velho"
  fi
done

echo "[$(date -Is)] ok — $(du -sh "$alvo" | cut -f1)"
