# Image de production de l'application TECPAP (tableau de bord et webhook WhatsApp).
#
# Construction : docker build -t tecpap-orders .
# Exécution    : docker run --env-file .env -p 5000:5000 --stop-timeout 130 \
#                  -v tecpap-db:/data/db -v tecpap-backups:/data/backups -v tecpap-media:/data/media tecpap-orders
#
# Le fichier .env ne contient que les réglages et les secrets (modèle : .env.example). Les
# variables passées par --env-file ou -e remplacent celles de l'image : ne définissez pas de
# chemin de données dans ce fichier, car l'application tourne sans droit d'écriture dans /app et
# ne trouverait plus sa base. Les dossiers de données sont ceux de /data, montés en volumes.
#
# SECRET_KEY est obligatoire en production : sans elle, l'application refuse de démarrer.
# Les dépendances sont installées aux versions exactes de requirements.txt, celles qui ont passé
# les tests.

FROM python:3.11-slim

# Environnement de production et emplacement des données : la base, les sauvegardes, les exports
# et les médias reçus se trouvent sous /data, jamais dans le dossier de l'application.
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

# Le dossier knowledge/ (base de connaissances de l'assistant) est copié dans l'image, dans
# /app/knowledge : ce n'est pas un volume. Pour modifier les documents, reconstruire l'image, ou
# monter le dossier de l'hôte (-v "$PWD/knowledge:/app/knowledge:ro") puis lancer la
# réindexation depuis la page « Base de connaissances » du tableau de bord.
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

# Un seul processus gunicorn. L'application garde en mémoire de ce processus un planificateur de
# sauvegardes, un exécuteur des tâches de fond (traitement différé des messages WhatsApp), les
# verrous par expéditeur, la liste des messages en cours de traitement et les compteurs d'échecs
# de connexion : plusieurs processus lanceraient chacun les sauvegardes et le traitement, et ne
# partageraient pas ces compteurs. Chaque fil d'exécution ouvre sa propre connexion SQLite (mode
# WAL : lectures en parallèle, écritures l'une après l'autre) : les 4 fils (--threads) servent
# donc des requêtes en parallèle dans ce seul processus.
# --graceful-timeout laisse aux tâches en cours le temps de finir à l'arrêt ; --stop-timeout de
# docker run doit le dépasser, sinon Docker interrompt le conteneur au bout de 10 secondes.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120", "--graceful-timeout", "120", "--access-logfile", "-", "app:app"]
