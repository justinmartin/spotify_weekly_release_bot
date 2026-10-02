"""
Génère un nouveau SPOTIPY_REFRESH_TOKEN (à lancer en local quand le bot
signale « Refresh token expired »).

    python generate_refresh_token.py

Lit SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET / SPOTIPY_REDIRECT_URI depuis
.env ou secrets/.env (ou les demande). Le redirect URI doit être déclaré à
l'identique dans le dashboard Spotify (ex. http://127.0.0.1:8888/callback).
Ouvre le navigateur pour te connecter, puis affiche le token à copier dans
le secret GitHub SPOTIPY_REFRESH_TOKEN.
"""
import os

from dotenv import load_dotenv
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.oauth2 import SpotifyOAuth

from app import SPOTIFY_SCOPE

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(BASE_DIR, "secrets", ".env"))


def ask(var, default=None):
    value = os.getenv(var)
    if value:
        return value
    prompt = f"{var}" + (f" [{default}]" if default else "") + " : "
    return input(prompt).strip() or default


def main():
    auth = SpotifyOAuth(
        client_id=ask("SPOTIPY_CLIENT_ID"),
        client_secret=ask("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=ask("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        scope=SPOTIFY_SCOPE,
        open_browser=True,
        cache_handler=MemoryCacheHandler(),
    )
    token = auth.get_access_token(as_dict=True, check_cache=False)
    print("\n✅ Nouveau refresh token (à coller dans le secret GitHub SPOTIPY_REFRESH_TOKEN) :\n")
    print(token["refresh_token"])


if __name__ == "__main__":
    main()
