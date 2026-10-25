#!/usr/bin/env bash
# Démarre l'image Docker de l'application et vérifie qu'elle répond sur /healthz.
#
# Usage : bash .github/scripts/smoke_image.sh IMAGE   (depuis la racine du dépôt)
#
# Deux démarrages, chacun avec le SECRET_KEY que l'application exige en production :
# 1. avec les seuls réglages de l'image ;
# 2. avec un fichier d'environnement copié de .env.example, comme le fait un opérateur qui part du
#    modèle : les variables du fichier remplacent celles de l'image, et la base doit rester sous
#    /data/db (dossier accessible en écriture à l'utilisateur du conteneur), pas dans /app.
# Un démarrage réussi crée la base : le test vérifie sa présence dans le conteneur.

set -euo pipefail

image="${1:?usage : smoke_image.sh IMAGE}"
port="${SMOKE_PORT:-5000}"
attempts="${SMOKE_ATTEMPTS:-30}"
delay="${SMOKE_DELAY:-2}"
names=(tecpap-smoke-image tecpap-smoke-env-file)
env_file="$(mktemp)"

cleanup() {
  docker rm --force "${names[@]}" >/dev/null 2>&1 || true
  rm -f "$env_file"
}
trap cleanup EXIT

# probe NOM [OPTION_DOCKER...] : lance le conteneur, attend la réponse de /healthz, puis l'arrête.
probe() {
  local name="$1"
  shift
  docker run --detach --name "$name" --publish "127.0.0.1:${port}:5000" "$@" "$image" >/dev/null
  for _ in $(seq 1 "$attempts"); do
    if [ "$(docker inspect --format '{{.State.Running}}' "$name")" != "true" ]; then
      break
    fi
    if curl --silent --fail --output /dev/null "http://127.0.0.1:${port}/healthz"; then
      if docker exec "$name" test -f /data/db/orders.db; then
        echo "Conteneur ${name} : /healthz répond et la base est sous /data/db."
        docker rm --force "$name" >/dev/null
        return 0
      fi
      echo "Conteneur ${name} : /healthz répond, mais la base n'est pas sous /data/db." >&2
      break
    fi
    sleep "$delay"
  done
  echo "Conteneur ${name} : démarrage en échec. Journal du conteneur :" >&2
  docker logs "$name" >&2 || true
  docker rm --force "$name" >/dev/null
  return 1
}

secret_key="$(openssl rand -hex 32)"
cp .env.example "$env_file"

probe "${names[0]}" --env "SECRET_KEY=${secret_key}"
probe "${names[1]}" --env-file "$env_file" --env "SECRET_KEY=${secret_key}"
