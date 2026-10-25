"""Calcule l'empreinte (hachage) du mot de passe administrateur du tableau de bord.

Usage :
    python scripts/hash_password.py

Le mot de passe est saisi deux fois sans s'afficher à l'écran. Le script affiche la ligne à
copier dans le fichier .env (variable ADMIN_PASSWORD_HASH) ; le mot de passe lui-même n'est
jamais enregistré. L'empreinte est calculée par werkzeug.security.generate_password_hash
(fonction de dérivation de clé scrypt, avec un sel aléatoire) et vérifiée à la connexion par
check_password_hash.

La valeur est affichée entre apostrophes : elles empêchent docker compose d'interpréter les
caractères $ de l'empreinte. L'application retire une paire d'apostrophes qui entoure une
valeur (tecpap.config.strip_quotes) : la même ligne convient donc aussi à python-dotenv et à
docker run --env-file, qui transmet les apostrophes telles quelles.
"""

from __future__ import annotations

import getpass
import sys

from werkzeug.security import generate_password_hash

MIN_PASSWORD_LENGTH = 12


def _use_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def main() -> int:
    """Demande le mot de passe, affiche son empreinte ; renvoie le code de sortie du programme."""
    _use_utf8_console()
    password = getpass.getpass("Mot de passe administrateur : ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Mot de passe trop court : {MIN_PASSWORD_LENGTH} caractères au minimum.", file=sys.stderr)
        return 1
    if getpass.getpass("Confirmation du mot de passe : ") != password:
        print("Les deux saisies sont différentes.", file=sys.stderr)
        return 1

    password_hash = generate_password_hash(password)
    print("Ajoutez cette ligne au fichier .env. Les apostrophes protègent les caractères $ de docker compose ; "
          "l'application les retire à la lecture, y compris avec docker run --env-file :")
    print(f"ADMIN_PASSWORD_HASH='{password_hash}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
