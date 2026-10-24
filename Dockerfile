# Image de production de l'application TECPAP (tableau de bord et webhook WhatsApp).
#
# Construction : docker build -t tecpap-orders .
# Exécution     : docker run --env-file .env -e APP_ENV=production -p 5000:5000 \
#                   -v tecpap-db:/data/db -v tecpap-backups:/data/backups -v tecpap-media:/data/media tecpap-orders
#
# SECRET_KEY est obligatoire en production : sans elle, l'application refuse de démarrer.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg \
    APP_ENV=production \
    DATABASE_PATH=/data/db/orders.db \
    BACKUP_DIR=/data/backups \
    EXPORT_DIR=/data/exports \
    ATTACHMENTS_DIR=/data/media/attachments \
    WHATSAPP_MEDIA_DIR=/data/media/whatsapp

WORKDIR /app

# Dépendances installées avant la copie du code : cette couche reste en cache tant que
# requirements.txt ne change pas.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Utilisateur sans privilèges, propriétaire des seuls dossiers de données.
RUN useradd --system --create-home --uid 10001 tecpap \
    && mkdir -p /data/db /data/backups /data/exports /data/media/attachments /data/media/whatsapp \
    && chown -R tecpap:tecpap /data
USER tecpap

VOLUME ["/data/db", "/data/backups", "/data/media"]

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=4).status == 200 else 1)"

# Un seul processus : la base SQLite est partagée par une connexion unique et le
# planificateur de sauvegardes ne doit tourner qu'une fois ; les fils d'exécution
# (--threads) traitent les requêtes en parallèle.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "app:app"]
