#!/usr/bin/env bash
# Prepara ./nucleo para o build da imagem.
#
# ⚠️ NÃO use `ln -s ../EasyContig-BR-Demo-Deck nucleo`, que era o que o README
# mandava: nem docker nem podman seguem link que aponta para FORA do contexto de
# build, e o `COPY nucleo/` falha com "no such file or directory". Descoberto em
# 2026-08-06, na primeira vez que alguém rodou o build.
#
# E copiar o repositório inteiro seria pior que não funcionar: ele tem os 40
# `.ab1` REAIS e não publicados do LHV (144 MB), e eles ficariam gravados numa
# camada da imagem — que é justamente o que não pode sair da máquina.
#
# Aqui vai só o que o `pip install` precisa: o pacote e o `pyproject.toml`.
set -euo pipefail
ORIGEM="${1:-../EasyContig-BR-Demo-Deck}"
DESTINO="$(dirname "$0")/../nucleo"

[ -f "$ORIGEM/pyproject.toml" ] || {
  echo "não achei o repo do núcleo em $ORIGEM" >&2; exit 1; }

rm -rf "$DESTINO"; mkdir -p "$DESTINO"
cp -a "$ORIGEM/app" "$DESTINO/"
cp -a "$ORIGEM/pyproject.toml" "$DESTINO/"
[ -f "$ORIGEM/README.md" ] && cp -a "$ORIGEM/README.md" "$DESTINO/"

# Cinto e suspensório: se algum dia o pacote passar a carregar dado junto, o
# build não pode ser o lugar onde isso é descoberto.
if find "$DESTINO" \( -name '*.ab1' -o -name '*.abi' -o -name '*.scf' \) | grep -q .; then
  echo "ABORTADO: há arquivo de sequenciamento em $DESTINO" >&2; exit 1
fi
echo "núcleo pronto: $(du -sh "$DESTINO" | cut -f1), $(find "$DESTINO" -type f | wc -l) arquivos"
