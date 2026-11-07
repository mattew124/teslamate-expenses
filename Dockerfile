FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY tmexpenses ./tmexpenses

# Non-root, and it writes nothing, so any other uid works too (`user:` in compose).
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin expenses
USER 10001:10001

EXPOSE 8080
# Root filesystem is read-only: heartbeat file goes to tmpfs, and the control
# socket (unused, defaults to $HOME) is switched off.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--worker-tmp-dir", "/dev/shm", "--no-control-socket", \
     "--access-logfile", "-", "tmexpenses:create_app()"]
