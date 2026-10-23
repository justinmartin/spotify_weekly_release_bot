"""
Spotify Weekly Release Bot : nouvelles sorties de la semaine -> playlist + mail récap.

    python app.py              # run normal (playlist + mail)
    python app.py --dry-run    # aucune écriture : ni playlist ni mail, aperçu dans out/
    python app.py --best-of    # force le best of du mois précédent (auto le 1er vendredi du mois)

Variables d'environnement (.env, secrets/.env ou secrets GitHub) :
    SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI, SPOTIPY_REFRESH_TOKEN
    EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO
    GENIUS_ACCESS_TOKEN   (optionnel)
    ROLLING_PLAYLIST      (optionnel, "true" : une seule playlist "HEBDO" vidée et remplie
                           chaque semaine au lieu d'une nouvelle "HEBDO - JJ/MM")
"""
import argparse
import os
import sys
import traceback
from datetime import date, timedelta

from bot.config import BASE_DIR, load_json
from bot.genius import connect_genius
from bot.mail import build_email, send_email, send_failure_alert
from bot.picks import pick_classics, pick_songs
from bot.spotify import (build_best_of, connect_spotify, create_playlist, fetch_artist_releases,
                         fetch_podcast_episodes, publish_playlist, sync_follows)


# -------------------------------------------------------------------- Main

def is_first_friday_of_month(today):
    return today.weekday() == 4 and today.day <= 7


def run(dry_run=False, force_best_of=False):
    today = date.today()
    since = today - timedelta(days=6)  # vendredi -> les 7 derniers jours, aujourd'hui inclus
    rolling = os.getenv("ROLLING_PLAYLIST", "").lower() in {"1", "true", "yes"}
    artists = load_json("artists.json")

    sp = connect_spotify()
    genius = connect_genius()
    sync_follows(sp, artists, dry_run)

    releases, release_uris, errors = fetch_artist_releases(sp, artists, since)
    podcasts, podcast_errors = fetch_podcast_episodes(sp, load_json("podcasts.json"), since)
    errors += podcast_errors
    classics = pick_classics(sp, genius, load_json("classics_hiphop.json"), today)
    songs = pick_songs(sp, genius, load_json("best_songs_21st_century.json"), today)

    playlist_url = None
    if release_uris:
        uris = release_uris + [s["uri"] for s in songs]
        if dry_run:
            print(f"⏭️ Dry run : playlist non créée ({len(uris)} titres)")
        else:
            playlist_url = publish_playlist(sp, uris, today, rolling)
    else:
        print("ℹ️ Pas de nouvelles sorties cette semaine.")

    best_of = None
    if force_best_of or is_first_friday_of_month(today):
        label, top = build_best_of(sp, artists, today)
        if top:
            url = None if dry_run else create_playlist(sp, f"BEST OF - {label.capitalize()}", [t["uri"] for t in top])
            best_of = (label, top, url)

    subject, text_body, html_body = build_email(
        today, releases, playlist_url, podcasts, classics, songs, errors, best_of)

    if dry_run:
        os.makedirs(os.path.join(BASE_DIR, "out"), exist_ok=True)
        preview = os.path.join(BASE_DIR, "out", f"email_{today}.html")
        with open(preview, "w", encoding="utf-8") as f:
            f.write(html_body)
        print(f"⏭️ Dry run : mail non envoyé, aperçu dans {preview}\n\n{subject}\n{text_body}")
    else:
        send_email(subject, text_body, html_body)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--dry-run", action="store_true", help="ni playlist ni mail, aperçu dans out/")
    parser.add_argument("--best-of", action="store_true", help="forcer le best of du mois précédent")
    args = parser.parse_args()
    try:
        run(dry_run=args.dry_run, force_best_of=args.best_of)
    except Exception as e:
        traceback.print_exc()
        if not args.dry_run:
            send_failure_alert(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
