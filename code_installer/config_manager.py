#!/usr/bin/env python3
"""
config_manager.py – Gestion du mot de passe administrateur et des paramètres.

- Les paramètres applicatifs restent stockés dans /etc/disk_eraser/admin.conf
- Le mot de passe administrateur est stocké séparément dans
  /etc/disk_eraser/admin.cred via SecureCredentialStore
"""

import json
import os
from typing import Tuple

from secure_credentials import SecureCredentialStore

CONFIG_DIR = "/etc/disk_eraser"
CONFIG_FILE = os.path.join(CONFIG_DIR, "admin.conf")
ADMIN_CRED_FILE = os.path.join(CONFIG_DIR, "admin.cred")

DEFAULT_PASSES = 5
DEFAULT_PASSWORD = "0000"
MIN_PASSWORD_LENGTH = 8

_store = SecureCredentialStore(
    path=ADMIN_CRED_FILE,
    default_password=DEFAULT_PASSWORD,
)


# ── Helpers internes ───────────────────────────────────────────────────────────

def _read_config() -> dict:
    """Lit le fichier de configuration, retourne un dict vide si absent ou illisible."""
    if not os.path.isfile(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _write_config(data: dict) -> None:
    """Écrit le fichier de configuration de façon atomique."""
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG_FILE)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ── API mot de passe ───────────────────────────────────────────────────────────

def is_password_set() -> bool:
    """Vérifie qu'un mot de passe admin a déjà été configuré."""
    return os.path.isfile(ADMIN_CRED_FILE)


def is_default_password() -> bool:
    """Indique si le mot de passe administrateur est encore la valeur d'usine."""
    return _store.is_default_password(DEFAULT_PASSWORD)


def set_password(password: str) -> None:
    """
    Enregistre (ou remplace) le mot de passe admin sans vérifier l'ancien.
    À utiliser pour l'initialisation ou les flux explicitement autorisés.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Le mot de passe doit comporter au moins {MIN_PASSWORD_LENGTH} caractères."
        )

    ok, message = _store.force_set_password(password)
    if not ok:
        raise ValueError(message)


def verify_password(password: str) -> bool:
    """
    Vérifie si le mot de passe fourni correspond au mot de passe enregistré.
    Retourne uniquement True/False pour compatibilité.
    """
    ok, _wait = _store.verify(password)
    return ok


def verify_password_with_wait(password: str) -> Tuple[bool, int]:
    """
    Vérifie le mot de passe et retourne (ok, wait_seconds).
    - ok=True, wait=0 : mot de passe correct
    - ok=False, wait=0 : mot de passe incorrect
    - ok=False, wait>0 : verrouillage temporaire en cours
    """
    return _store.verify(password)


def change_password(old_password: str, new_password: str) -> None:
    """
    Change le mot de passe après vérification de l'ancien.
    Lève ValueError en cas d'erreur fonctionnelle.
    """
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Le nouveau mot de passe doit comporter au moins {MIN_PASSWORD_LENGTH} caractères."
        )

    ok, message = _store.change_password(old_password, new_password)
    if not ok:
        raise ValueError(message)


# ── API paramètres d'effacement ────────────────────────────────────────────────

def get_passes() -> int:
    """
    Retourne le nombre de passes configuré (défaut : DEFAULT_PASSES).
    Ne lève jamais d'exception.
    """
    config = _read_config()
    try:
        return max(1, int(config.get("passes", DEFAULT_PASSES)))
    except (ValueError, TypeError):
        return DEFAULT_PASSES


def set_passes(passes: int) -> None:
    """
    Enregistre le nombre de passes.
    Lève ValueError si la valeur est invalide (< 1).
    Lève PermissionError si le fichier n'est pas accessible en écriture.
    """
    if not isinstance(passes, int) or passes < 1:
        raise ValueError("Le nombre de passes doit être un entier supérieur ou égal à 1.")
    config = _read_config()
    config["passes"] = passes
    _write_config(config)