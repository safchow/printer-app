# printer-api: the FastAPI server with the built web app inside.
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY api/ ./
COPY fakes/ /fakes/
COPY --from=web /web/dist ./static
RUN mkdir -p /data /library /timelapse /config && chmod 777 /data /library /timelapse
# Inside the container it listens on every interface; compose publishes the
# port only on BIND_ADDRESS from .env.
ENV BIND_ADDRESS=0.0.0.0 PORT=8090 DATA_DIR=/data LIBRARY_ROOT=/library PRINTERS_FILE=/config/printers.yaml
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/health')"
CMD ["python", "-m", "app"]
