ARG BASE=python:3.12-slim
FROM ${BASE}

# The Db2 client driver bundled with ibm_db needs libxml2 and libcrypt.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libxml2 libcrypt1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --system --no-create-home corewatch
USER corewatch
EXPOSE 9953
ENTRYPOINT ["corewatch-db2"]
CMD ["--config", "/etc/corewatch/config.yml"]
