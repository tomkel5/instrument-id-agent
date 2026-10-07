FROM python:3.13-slim
RUN apt-get update && apt-get install --yes --no-install-recommends nodejs npm && npm install --global @openai/codex@latest && rm -rf /var/lib/apt/lists/* && useradd --create-home --uid 10001 --shell /bin/bash appuser && mkdir -p /var/lib/instrument-id-agent /home/appuser/.codex /workspace && chown -R appuser:appuser /var/lib/instrument-id-agent /home/appuser /workspace
WORKDIR /app
COPY src/main.py /app/main.py
ENV PYTHONUNBUFFERED=1
USER appuser
ENTRYPOINT ["python", "/app/main.py"]
