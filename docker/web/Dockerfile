FROM node:20-slim AS web-build

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY paper_agent/ ./paper_agent/
COPY --from=web-build /build/paper_agent/web/static ./paper_agent/web/static/
RUN python -m pip install --no-cache-dir . \
    && groupadd --gid 10001 techscout \
    && useradd --uid 10001 --gid techscout --no-create-home techscout \
    && mkdir -p /data/state /data/outputs \
    && chown -R techscout:techscout /data

USER techscout
EXPOSE 8000

ENTRYPOINT ["techscout"]
CMD ["serve", "--host", "0.0.0.0", "--allow-network", "--state-root", "/data/state", "--output-root", "/data/outputs"]
