FROM python:3.15-slim

# Debian security fixes that landed after the base image was built (a scanner gates releases on these).
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

# PDF text (poppler) and OCR for scanned invoices (tesseract), used when importing
# existing invoices from OneDrive.
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt \
 # The app never runs pip. Removing it also drops pip's vendored copies of urllib3 and
 # msgpack, which vulnerability scanners flag in the image.
 && pip uninstall -y pip

COPY gunicorn.conf.py .
COPY tmexpenses ./tmexpenses
COPY grafana/cost-of-ownership.json ./grafana/cost-of-ownership.json
# Where the app writes its Grafana dashboard when DASHBOARD_DIR points here. World-writable
# (sticky) so a new named volume mounted here is writable by whatever uid compose runs us as.
RUN mkdir /dashboards && chmod 1777 /dashboards

# Non-root, and it writes nothing, so any other uid works too (`user:` in compose).
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin expenses
USER 10001:10001

EXPOSE 8080
# Root filesystem is read-only: heartbeat file goes to tmpfs, and the control
# socket (unused, defaults to $HOME) is switched off.
CMD ["gunicorn", "-c", "/srv/gunicorn.conf.py", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120", "--worker-tmp-dir", "/dev/shm", "--no-control-socket", \
     "--access-logfile", "-", "tmexpenses:create_app()"]
