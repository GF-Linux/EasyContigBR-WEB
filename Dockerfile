# A mesma imagem serve o site E roda o trabalhador — muda só o comando
# (ver docker-compose.yml). Uma imagem só evita a classe de bug em que web e
# trabalhador rodam versões diferentes do núcleo científico e discordam sobre a
# mesma amostra.
FROM python:3.12-slim

# blastn vem do repositório da distribuição; o Tracy vem do binário oficial
# v0.8.9. NÃO trocar por bioconda: de lá vinha a v0.5.3, sem o subcomando
# `consensus`, e o erro só aparece na hora de montar (ADR 0026).
ARG TRACY_VERSION=v0.8.9
RUN apt-get update && apt-get install -y --no-install-recommends \
        ncbi-blast+ curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fL -o /usr/local/bin/tracy \
        "https://github.com/gear-genomics/tracy/releases/download/${TRACY_VERSION}/tracy_linux_x86_64" \
    && chmod +x /usr/local/bin/tracy \
    && tracy --version

WORKDIR /opt/easycontig

# O núcleo entra como pacote, não copiado: é a fronteira da ADR 0050. Espera-se
# o repo do EasyContig ao lado deste, como `nucleo/` no contexto de build.
COPY nucleo/ /opt/nucleo/
RUN pip install --no-cache-dir /opt/nucleo

COPY pyproject.toml README.md ./
COPY easycontig_web/ ./easycontig_web/
RUN pip install --no-cache-dir --no-deps . \
    && pip install --no-cache-dir fastapi "uvicorn[standard]" python-multipart jinja2 itsdangerous

# Não roda como root: o processo recebe arquivo de fora e escreve em disco.
RUN useradd -m -u 10001 easycontig && mkdir -p /dados && chown easycontig /dados
USER easycontig

ENV EASYCONTIG_DATA_DIR=/dados \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "easycontig_web.main:app", "--host", "0.0.0.0", "--port", "8000"]
