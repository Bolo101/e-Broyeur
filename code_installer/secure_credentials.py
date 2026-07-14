#!/usr/bin/env python3
"""
secure_credentials.py – Stockage et vérification sécurisés d'un mot de passe
administrateur.

À COPIER TEL QUEL dans chaque application (shadowClone, e-Broyeur, VirtuPack,
Bastion-Antiviral, menu de sélection). Volontairement autonome et sans
dépendance vers un module partagé installé : chaque application reste
sécurisée indépendamment des autres, un défaut ou une compromission dans
l'une n'affecte pas les autres.

Mécanisme :
  - Hachage PBKDF2-HMAC-SHA256, sel aléatoire de 16 octets généré à
    l'installation, 600 000 itérations. Très supérieur à un simple
    sha256(mot_de_passe) : rend une attaque hors-ligne (fichier volé/copié)
    des milliers de fois plus coûteuse, et le sel empêche l'utilisation de
    tables précalculées (rainbow tables).
  - Comparaison en temps constant (hmac.compare_digest) : empêche une
    attaque par mesure de temps de réponse.
  - Écriture atomique (fichier temporaire + os.replace) : jamais de fichier
    tronqué/corrompu en cas de coupure de courant pendant l'écriture.
  - Permissions strictes : répertoire 0700, fichier 0600, appartenant à
    l'utilisateur qui exécute le programme (root pour les 4 applications
    métier puisqu'elles s'exécutent déjà en root ; l'utilisateur kiosque
    pour le menu de sélection).
  - Verrouillage progressif après échecs répétés (anti-bruteforce EN LIGNE,
    via l'interface elle-même) : délai croissant (1s, 2s, 4s, 8s... jusqu'à
    un plafond) après chaque échec consécutif, réinitialisé après un succès.
    Le délai est appliqué côté appelant (aucun blocage de la boucle Tk) :
    verify() renvoie immédiatement le temps d'attente restant plutôt que de
    dormir dans la fonction.

Utilisation typique :

    from secure_credentials import SecureCredentialStore

    store = SecureCredentialStore("/etc/mon_appli/admin.cred")

    ok, wait = store.verify(saisie_utilisateur)
    if wait:
        # encore verrouillé : afficher "réessayez dans {wait}s"
        ...
    elif not ok:
        # mot de passe incorrect
        ...
    else:
        # accès autorisé
        ...

    ok, message = store.change_password(ancien, nouveau)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
from typing import Tuple

DEFAULT_ITERATIONS = 600_000
SALT_BYTES = 16
MAX_BACKOFF_SECONDS = 30
MIN_PASSWORD_LENGTH = 4


class SecureCredentialStore:
    """Stocke et vérifie un unique mot de passe administrateur de façon
    sécurisée à l'emplacement `path` donné."""

    def __init__(self, path: str, default_password: str = "0000",
                 iterations: int = DEFAULT_ITERATIONS) -> None:
        self.path = path
        self.iterations = iterations
        self._ensure_storage(default_password)

    # ── Initialisation ────────────────────────────────────────────────────
    def _dir(self) -> str:
        return os.path.dirname(self.path) or "."

    def _ensure_storage(self, default_password: str) -> None:
        d = self._dir()
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        if not os.path.exists(self.path):
            self._write_new(default_password)

    # ── Hachage ───────────────────────────────────────────────────────────
    def _pbkdf2(self, password: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )

    def _write_new(self, password: str) -> None:
        salt = os.urandom(SALT_BYTES)
        digest = self._pbkdf2(password, salt, self.iterations)
        data = {
            "algo": "pbkdf2_sha256",
            "iterations": self.iterations,
            "salt": salt.hex(),
            "hash": digest.hex(),
            "failed_attempts": 0,
            "locked_until": 0,
        }
        self._atomic_write(data)

    # ── Persistance ───────────────────────────────────────────────────────
    def _load(self) -> dict:
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _atomic_write(self, data: dict) -> None:
        d = self._dir()
        fd, tmp_path = tempfile.mkstemp(dir=d, prefix=".cred_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ── API publique ──────────────────────────────────────────────────────
    def verify(self, password: str) -> Tuple[bool, int]:
        """Vérifie le mot de passe.

        Renvoie (ok, wait_seconds) :
          - (True, 0)           : mot de passe correct.
          - (False, 0)          : mot de passe incorrect (pas de verrou actif).
          - (False, wait>0)     : compte temporairement verrouillé, réessayer
                                  dans `wait` secondes (aucun calcul de hash
                                  n'est effectué tant que le verrou est actif).
        """
        try:
            data = self._load()
        except (OSError, ValueError):
            return False, 0

        now = time.time()
        locked_until = data.get("locked_until", 0)
        if now < locked_until:
            return False, int(locked_until - now) + 1

        salt = bytes.fromhex(data["salt"])
        iterations = data.get("iterations", self.iterations)
        computed = self._pbkdf2(password, salt, iterations)
        stored = bytes.fromhex(data["hash"])
        ok = hmac.compare_digest(computed, stored)

        if ok:
            data["failed_attempts"] = 0
            data["locked_until"] = 0
        else:
            attempts = data.get("failed_attempts", 0) + 1
            data["failed_attempts"] = attempts
            backoff = min(2 ** (attempts - 1), MAX_BACKOFF_SECONDS)
            data["locked_until"] = now + backoff

        self._atomic_write(data)
        return ok, 0

    def is_default_password(self, default_password: str = "0000") -> bool:
        """Indique si le mot de passe actuellement en vigueur est encore la
        valeur d'usine. N'incrémente PAS le compteur d'échecs (vérification
        interne, pas une tentative de connexion utilisateur)."""
        try:
            data = self._load()
            salt = bytes.fromhex(data["salt"])
            iterations = data.get("iterations", self.iterations)
            computed = self._pbkdf2(default_password, salt, iterations)
            stored = bytes.fromhex(data["hash"])
            return hmac.compare_digest(computed, stored)
        except (OSError, ValueError):
            return False

    def change_password(self, old_password: str, new_password: str) -> Tuple[bool, str]:
        """Change le mot de passe après vérification de l'ancien.
        Respecte le même mécanisme de verrouillage progressif que verify()."""
        ok, wait = self.verify(old_password)
        if wait:
            return False, f"Compte temporairement verrouillé, réessayez dans {wait}s."
        if not ok:
            return False, "Mot de passe actuel incorrect."
        if len(new_password) < MIN_PASSWORD_LENGTH:
            return False, f"Le nouveau mot de passe doit comporter au moins {MIN_PASSWORD_LENGTH} caractères."
        self._write_new(new_password)
        return True, "Mot de passe modifié avec succès."

    def force_set_password(self, new_password: str) -> Tuple[bool, str]:
        """Définit le mot de passe SANS vérifier l'ancien. Réservé aux flux
        où l'appelant a déjà validé l'autorisation par un autre moyen
        (ex. panneau d'administration déjà déverrouillé)."""
        if len(new_password) < MIN_PASSWORD_LENGTH:
            return False, f"Le mot de passe doit comporter au moins {MIN_PASSWORD_LENGTH} caractères."
        self._write_new(new_password)
        return True, "Mot de passe modifié avec succès."
