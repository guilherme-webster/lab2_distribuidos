FROM python:3.12-slim

# Logs saem na hora (o log.py já faz flush, isto cobre o resto)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install . && useradd --no-create-home --uid 10001 node

USER node
EXPOSE 9000/udp
CMD ["python", "-m", "dsnode.node"]
