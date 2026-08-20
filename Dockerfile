# A mesma imagem serve o site E roda o trabalhador — muda só o comando
# (ver docker-compose.yml). Uma imagem só evita a classe de bug em que web e
# trabalhador rodam versões diferentes do núcleo científico e discordam sobre a
# mesma amostra.
FROM python:3.12-slim

# blastn vem do repositório da distribuição; o Tracy vem do binário oficial
# v0.8.9. NÃO trocar por bioconda: de lá vinha a v0.5.3, sem o subcomando
# `consensus`, e o erro só aparece na hora de montar (ADR 0026).
ARG TRACY_VERSION=v0.8.9
# ⚠️ O nome do arquivo publicado é `tracy-<versão>-linux-amd64`, com hífens.
# Aqui estava `tracy_linux_x86_64`, e **esta imagem nunca construiu**: o
# `curl -fL` levava 404 e saía com 22. Descoberto em 2026-08-06, na primeira vez
# que alguém rodou o build — ou seja, o `docker compose up --build` que a
# ADR 0050 chama de "o que torna a entrega institucional possível" morria na
# terceira camada, e ninguém sabia.
# O `tracy --version` no fim é o que transforma download errado em erro de
# BUILD, em vez de um contêiner que sobe e só falha na hora de montar um par.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ncbi-blast+ curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fL -o /usr/local/bin/tracy \
        "https://github.com/gear-genomics/tracy/releases/download/${TRACY_VERSION}/tracy-${TRACY_VERSION}-linux-amd64" \
    && chmod +x /usr/local/bin/tracy \
    && tracy --version

# O MrBayes é COMPILADO aqui porque não existe pacote: não está no Debian, não
# está em `pip` e não está em conda-forge. Sem esta camada a imagem sobe inteira
# e só a árvore falha — o gênero de defeito que a camada do Tracy acima já
# custou uma vez, e que o `mb -v` no fim transforma em erro de BUILD.
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

# O núcleo entra como pacote, não copiado: é a fronteira da ADR 0050. Espera-se
# o repo do EasyContig ao lado deste, como `nucleo/` no contexto de build.
COPY nucleo/ /opt/nucleo/
RUN pip install --no-cache-dir /opt/nucleo

COPY pyproject.toml README.md ./
COPY easycontig_web/ ./easycontig_web/
# ⚠️ A lista é escrita à mão por causa do `--no-deps`, e por isso é fácil
# esquecer uma: `matplotlib` entrou com a árvore filogenética, e sem ela a
# imagem sobe, a árvore sai — e só a FIGURA some, calada (o desenho falha para o
# lado seguro). Dependência nova no `pyproject.toml` precisa vir também aqui.
RUN pip install --no-cache-dir --no-deps . \
    && pip install --no-cache-dir fastapi "uvicorn[standard]" python-multipart \
        jinja2 itsdangerous matplotlib

# Não roda como root: o processo recebe arquivo de fora e escreve em disco.
RUN useradd -m -u 10001 easycontig && mkdir -p /dados && chown easycontig /dados
USER easycontig

ENV EASYCONTIG_DATA_DIR=/dados \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "easycontig_web.servidor_web:app", "--host", "0.0.0.0", "--port", "8000"]
