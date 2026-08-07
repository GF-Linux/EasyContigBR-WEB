#!/usr/bin/env bash
#
# Backup do EasyContig BR. Roda no HOSPEDEIRO, não dentro do contêiner.
#
# O que precisa ser salvo, e por quê:
#
#   fila.sqlite3   quem enviou o quê, quando, com que banco de referência.
#                  Sem ele os `.ab1` viram uma pasta de arquivos anônimos.
#   lotes/         os `.ab1` enviados e os relatórios. **Isto não existe em
#                  outro lugar.** É dado de sequenciamento não publicado: se
#                  sumir daqui, sumiu.
#
# ⚠️ O SQLite NÃO pode ser copiado com `cp` enquanto o servidor roda. Ele está
# em modo WAL: metade do estado vive no `-wal`, e um `cp` do arquivo principal
# pega um banco pela metade que restaura calado e errado. Por isso `VACUUM INTO`,
# que é a cópia consistente do próprio SQLite, feita sem parar ninguém.
#
# Uso:
#   ./tools/backup.sh /caminho/dos/dados /destino/dos/backups
#
# No cron do servidor (03:15 todo dia):
#   15 3 * * * /opt/easycontig/tools/backup.sh /var/lib/easycontig /backup >> /var/log/easycontig-backup.log 2>&1
set -euo pipefail

DADOS="${1:?uso: backup.sh <dir de dados> <dir de destino>}"
DESTINO="${2:?uso: backup.sh <dir de dados> <dir de destino>}"
GUARDAR_DIAS="${GUARDAR_DIAS:-30}"

carimbo="$(date -u +%Y%m%dT%H%M%SZ)"
alvo="$DESTINO/$carimbo"
mkdir -p "$alvo"
# Dado de sequenciamento nao publicado: a copia nao pode nascer legivel para
# todo mundo da maquina. Achado pela varredura de 06/08.
chmod 700 "$DESTINO" "$alvo"

echo "[$(date -Is)] backup de $DADOS → $alvo"

# 1. O banco, consistente, sem parar o servidor.
if [ -f "$DADOS/fila.sqlite3" ]; then
  sqlite3 "$DADOS/fila.sqlite3" "VACUUM INTO '$alvo/fila.sqlite3'"
  echo "  banco: $(du -h "$alvo/fila.sqlite3" | cut -f1)"
fi

# 2. Os arquivos. `--link-dest` aponta para o backup anterior: o que não mudou
#    vira link físico em vez de cópia, então trinta dias de backup diário de um
#    acervo que quase não muda custam quase o tamanho de UM.
anterior="$(ls -1d "$DESTINO"/*/ 2>/dev/null | grep -v "$carimbo" | tail -1 || true)"
rsync -a --delete \
  ${anterior:+--link-dest="$anterior/lotes"} \
  "$DADOS/lotes/" "$alvo/lotes/"
echo "  lotes: $(du -sh "$alvo/lotes" | cut -f1) ($(ls -1 "$alvo/lotes" | wc -l) corridas)"

# 3. ⚠️ Um backup que nunca foi lido não é backup. Confere aqui mesmo que o
#    banco copiado abre e que os números batem com a origem — se não bater, o
#    backup é apagado e o comando FALHA, para o cron reclamar hoje e não no dia
#    em que alguém precisar restaurar.
n_origem="$(sqlite3 "$DADOS/fila.sqlite3" 'SELECT COUNT(*) FROM lotes' 2>/dev/null || echo x)"
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
echo "  conferido: $n_copia lotes, integridade ok"

# 4. Expurgo dos backups velhos — com o mesmo cuidado da retenção: nunca apagar
#    o último que restou, aconteça o que acontecer com o relógio.
mapfile -t antigos < <(ls -1d "$DESTINO"/*/ 2>/dev/null | head -n -1)
for velho in "${antigos[@]:-}"; do
  [ -z "$velho" ] && continue
  if [ "$(find "$velho" -maxdepth 0 -mtime +"$GUARDAR_DIAS")" ]; then
    echo "  expurgando backup de $(basename "$velho")"
    rm -rf "$velho"
  fi
done

echo "[$(date -Is)] ok — $(du -sh "$alvo" | cut -f1)"
