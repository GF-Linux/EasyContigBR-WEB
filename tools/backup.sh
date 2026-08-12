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
#   bancos/        os bancos BLAST montados — e este entrou em 2026-08-11, por
#                  um motivo que não é óbvio: o FASTA que os gerou **é apagado**
#                  logo depois do `makeblastdb` (`bancos.py`, o `unlink` no
#                  `finally`), e o texto enviado nunca é gravado no sqlite. Para
#                  um banco PRIVADO — que é onde ficam as sequências do grupo
#                  ainda não publicadas — o índice aqui é a ÚNICA cópia que o
#                  servidor tem. Os do catálogo se remontam do NCBI; os do
#                  usuário, não se remontam de lugar nenhum.
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

# 0. ⚠️ A ORIGEM PRECISA EXISTIR ANTES DE QUALQUER COISA (achado de 2026-08-11).
#    O cenário não é hipotético: o app RECRIA `lotes/` e um `fila.sqlite3` de
#    esquema vazio a cada arranque (`config.py`, `main.py`). Então um volume
#    desmontado mais um reinício do contêiner produzem uma origem que EXISTE e
#    está VAZIA — e todo o resto deste script continuava passando, terminando
#    com "conferido: 0 lotes, integridade ok". Um backup vazio certificado como
#    bom é pior do que backup nenhum: backup nenhum ninguém confia.
#
#    Instalação nova de verdade tem zero lote, e é legítima — por isso a saída é
#    uma variável, declarada UMA vez pela pessoa que instala, e não um silêncio.
if [ ! -f "$DADOS/fila.sqlite3" ]; then
  echo "  FALHOU: $DADOS/fila.sqlite3 não existe — origem errada ou volume desmontado" >&2
  rmdir "$alvo" 2>/dev/null || true
  exit 1
fi
n_lotes_db="$(sqlite3 "$DADOS/fila.sqlite3" 'SELECT COUNT(*) FROM lotes' 2>/dev/null || echo 0)"
n_arq_origem="$(find "$DADOS/lotes" -type f 2>/dev/null | wc -l)"
if [ "$n_lotes_db" -eq 0 ] && [ "$n_arq_origem" -eq 0 ] && [ "${PERMITIR_VAZIO:-0}" != "1" ]; then
  echo "  FALHOU: a origem está vazia (0 lotes no banco, 0 arquivos em lotes/)." >&2
  echo "          Se a instalação é nova mesmo, rode com PERMITIR_VAZIO=1." >&2
  rmdir "$alvo" 2>/dev/null || true
  exit 1
fi

# 1. O banco, consistente, sem parar o servidor.
sqlite3 "$DADOS/fila.sqlite3" "VACUUM INTO '$alvo/fila.sqlite3'"
echo "  banco: $(du -h "$alvo/fila.sqlite3" | cut -f1)"

# 2. Os arquivos. `--link-dest` aponta para o backup anterior: o que não mudou
#    vira link físico em vez de cópia, então trinta dias de backup diário de um
#    acervo que quase não muda custam quase o tamanho de UM.
anterior="$(ls -1d "$DESTINO"/*/ 2>/dev/null | grep -v "$carimbo" | tail -1 || true)"
rsync -a --delete \
  ${anterior:+--link-dest="$anterior/lotes"} \
  "$DADOS/lotes/" "$alvo/lotes/"
echo "  lotes: $(du -sh "$alvo/lotes" | cut -f1) ($(ls -1 "$alvo/lotes" | wc -l) corridas)"

# 2b. Os bancos BLAST. Ver a explicação no cabeçalho: o FASTA de origem de um
#     banco do usuário é apagado depois da montagem, então isto não se remonta.
#     `mkdir -p` na origem seria escrever no volume de dados a partir do script
#     de backup — então se a pasta não existe, apenas não há o que copiar.
if [ -d "$DADOS/bancos" ]; then
  rsync -a --delete \
    ${anterior:+--link-dest="$anterior/bancos"} \
    "$DADOS/bancos/" "$alvo/bancos/"
  echo "  bancos: $(du -sh "$alvo/bancos" | cut -f1)"
else
  echo "  bancos: nenhum ($DADOS/bancos não existe)"
fi

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

# ⚠️ E AGORA A ÁRVORE DE ARQUIVOS — que é o que este backup existe para salvar,
#    e que até 2026-08-11 NÃO era conferida por nada. Tudo acima toca só o
#    `fila.sqlite3`; a linha final dizia "conferido" e a pessoa lia isso como
#    "os .ab1 estão salvos". O banco é o índice, os `.ab1` são o acervo: só o
#    índice ter sido copiado não é o backup ter funcionado.
#
#    A contagem da origem é refeita AQUI, depois do rsync, e não reaproveitada
#    da etapa 0: entre uma coisa e outra o servidor continua no ar e um envio
#    novo pode ter chegado. Comparar com o número velho acusaria falha onde
#    houve só concorrência.
n_arq_pos="$(find "$DADOS/lotes" -type f 2>/dev/null | wc -l)"
n_arq_copia="$(find "$alvo/lotes" -type f 2>/dev/null | wc -l)"
if [ "$n_arq_pos" -gt 0 ] && [ "$n_arq_copia" -eq 0 ]; then
  echo "  FALHOU: a origem tem $n_arq_pos arquivos e a cópia não tem NENHUM" >&2
  rm -rf "$alvo"
  exit 1
fi
faltando=$(( n_arq_pos - n_arq_copia ))
# Diferença pequena é o envio que chegou durante o rsync — normal, e some no
# backup de amanhã. Vai para o log assim mesmo: número que ninguém vê não vigia
# nada, e foi exatamente por não estar escrito que isto passou despercebido.
if [ "$faltando" -gt 0 ]; then
  echo "  nota: $faltando arquivo(s) a mais na origem que na cópia (envio durante o backup?)"
fi
echo "  conferido: $n_copia lotes no banco, integridade ok, $n_arq_copia arquivos copiados"

# 4. Expurgo dos backups velhos — com o mesmo cuidado da retenção: nunca apagar
#    o último que restou, aconteça o que acontecer com o relógio.
#
# ⚠️ SÓ APAGA O QUE TEM CARA DE BACKUP DESTE SCRIPT (achado de 2026-08-11). O
#    `ls -1d "$DESTINO"/*/` listava QUALQUER subdiretório do destino, e o alvo
#    do `rm -rf` é um caminho que veio do `$2` da linha de comando. Se alguém
#    apontar o destino para uma pasta que já tem outra coisa dentro — ou errar o
#    argumento e mandar para `/backup` inteiro, onde mora o backup de outro
#    serviço —, o expurgo come essa outra coisa por antiguidade, calado. O nome
#    é gerado aqui mesmo (`date -u +%Y%m%dT%H%M%SZ`), então exigir a forma dele
#    não perde nenhum backup nosso e exclui tudo o que não é nosso.
padrao='^[0-9]{8}T[0-9]{6}Z$'
mapfile -t antigos < <(
  ls -1d "$DESTINO"/*/ 2>/dev/null \
  | while read -r d; do
      basename "$d" | grep -Eq "$padrao" && echo "$d"
    done | head -n -1)
for velho in "${antigos[@]:-}"; do
  [ -z "$velho" ] && continue
  if [ "$(find "$velho" -maxdepth 0 -mtime +"$GUARDAR_DIAS")" ]; then
    echo "  expurgando backup de $(basename "$velho")"
    rm -rf "$velho"
  fi
done

echo "[$(date -Is)] ok — $(du -sh "$alvo" | cut -f1)"
