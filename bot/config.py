"""Réglages communs : chemins, .env, portée Spotify, filtres de titres."""
import json
import os
import re

from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(BASE_DIR, "secrets", ".env"))

# user-follow-* : suivre les artistes de artists.json ; user-library-* : Spotify passe
# désormais par /me/library pour le suivi d'artistes.
SPOTIFY_SCOPE = ("playlist-modify-private playlist-modify-public "
                 "user-follow-read user-follow-modify user-library-read user-library-modify")
ROLLING_PLAYLIST_NAME = "HEBDO"

# Rééditions à ignorer complètement (pas de nouveau son)
REISSUE_RE = re.compile(r"\b(remaster(ed)?|anniversary|expanded|re-?issue|reedition|réédition)\b", re.I)
# Éditions augmentées : signalées dans le mail mais pas ajoutées à la playlist
DELUXE_RE = re.compile(r"\b(deluxe|edition|édition|complete|extended)\b", re.I)


def load_json(name, default=None):
    path = os.path.join(BASE_DIR, name)
    if not os.path.exists(path):
        return default if default is not None else []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
