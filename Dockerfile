# Taste-Fit: FastAPI app + static UI. Runs on Hugging Face Spaces (Docker, port 7860) or anywhere.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=7860

# Non-root user (required by Hugging Face Spaces)
RUN useradd -m -u 1000 app
WORKDIR /home/app/taste-fit

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app app ./app
COPY --chown=app:app web ./web
COPY --chown=app:app README.md LICENSE ./

USER app
EXPOSE 7860
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('PORT','7860'))" || exit 1
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
