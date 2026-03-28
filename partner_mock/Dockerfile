FROM python:3.12-slim-bookworm

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin partnermock

WORKDIR /app

RUN pip install --no-cache-dir "fastapi>=0.115,<0.116" "uvicorn[standard]>=0.30,<0.31"

COPY partner_mock/ /app/partner_mock/

USER partnermock

EXPOSE 8090

HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health')" || exit 1

CMD ["uvicorn", "partner_mock.app:app", "--host", "0.0.0.0", "--port", "8090"]
