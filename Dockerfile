# A mesma imagem serve o site E roda o trabalhador — muda só o comando
# (ver docker-compose.yml). Uma imagem só evita a classe de bug em que web e
# trabalhador rodam versões diferentes do núcleo e discordam sobre a mesma
# amostra.
FROM python:3.12-slim

# blastn vem do repositório da distribuição; o Tracy vem do binário oficial
# v0.8.9. NÃO trocar por bioconda: de lá vinha a v0.5.3, sem o subcomando
# `consensus`, e o erro só aparece na hora de montar (ADR 0026).
# O nome do arquivo publicado é `tracy-<versão>-linux-amd64`, com hífens; o
# `tracy --version` no fim transforma download errado em erro de BUILD, em vez
# de um contêiner que sobe e só falha na hora de montar um par.
ARG TRACY_VERSION=v0.8.9
RUN apt-get update && apt-get install -y --no-install-recommends \
        ncbi-blast+ curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fL -o /usr/local/bin/tracy \
        "https://github.com/gear-genomics/tracy/releases/download/${TRACY_VERSION}/tracy-${TRACY_VERSION}-linux-amd64" \
    && chmod +x /usr/local/bin/tracy \
    && tracy --version

# O MrBayes é COMPILADO aqui porque não existe pacote: não está no Debian, não
# está em `pip` e não está em conda-forge. Sem esta camada a imagem sobe
# inteira e só a árvore falha — o `mb` no fim transforma isso em erro de BUILD.
#
# `--with-readline=no`: readline serve ao prompt interativo, e aqui o MrBayes
# nunca é digitado — o NEXUS traz o bloco `MRBAYES` com `autoclose=yes`.
ARG MRBAYES_VERSION=v3.2.7a
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git \
    && git clone --depth 1 --branch "${MRBAYES_VERSION}" \
        https://github.com/NBISweden/MrBayes.git /tmp/mrbayes \
    && cd /tmp/mrbayes \
    && ./configure --with-readline=no \
    && make -j"$(nproc)" \
    && install -m755 src/mb /usr/local/bin/mb \
    && cd / && rm -rf /tmp/mrbayes \
    && apt-get purge -y build-essential git \
    && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
    && printf 'version\nquit\n' | mb | grep -q "MrBayes"

WORKDIR /opt/easycontig

# A web entra do commit; o núcleo (os .py da raiz) e o front (templates e
# estáticos) NÃO estão no repositório desde 08/09/2026 — precisam já estar no
# disco desta máquina, postos por `tools/enviar_privado.sh`. Eles entram na
# imagem pelo mesmo COPY de sempre: o contexto de build é a pasta, não o commit.
# ⚠️ `COPY *.py` falhando com "no source files were specified" quer dizer que o
# núcleo não está nesta máquina — rode o tools/enviar_privado.sh, não procure
# erro no Dockerfile.
COPY pyproject.toml README.md ./
COPY *.py ./
COPY easycontig_web/ ./easycontig_web/

# ⚠️ Sem esta camada, faltar o núcleo ou o front dá uma imagem que SOBE e só
# falha na hora de montar um par (ImportError) ou de desenhar uma página (toda
# rota em 500). Aqui vira erro de BUILD, com o que fazer escrito — a mesma
# escolha das camadas do tracy e do mb acima.
# A lista dos módulos sai do `py-modules` do próprio pyproject.toml, e não
# escrita à mão aqui: escrita à mão, ela envelhece calada — a primeira versão
# desta camada conferia três dos doze e deixava passar um build sem `tracado.py`.
RUN python -c "import pathlib, tomllib, sys; \
    modulos = tomllib.loads(pathlib.Path('pyproject.toml').read_text())['tool']['setuptools']['py-modules']; \
    caminhos = [modulo + '.py' for modulo in modulos] + ['easycontig_web/templates/base.html', 'easycontig_web/estatico/oficina.js']; \
    faltam = [caminho for caminho in caminhos if not pathlib.Path(caminho).is_file()]; \
    sys.exit('FALHOU: nao estao aqui, e nao sao versionados: ' + ', '.join(faltam) + '. Rode tools/enviar_privado.sh da maquina de desenvolvimento para esta antes de construir a imagem.' if faltam else 0)"

RUN pip install --no-cache-dir .

# Não roda como root: o processo recebe arquivo de fora e escreve em disco.
RUN useradd -m -u 10001 easycontig && mkdir -p /dados && chown easycontig /dados
USER easycontig

ENV EASYCONTIG_DATA_DIR=/dados \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "easycontig_web.servidor_web:app", "--host", "0.0.0.0", "--port", "8000"]
