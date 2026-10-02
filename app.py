"""
Spotify Weekly Release Bot : nouvelles sorties de la semaine -> playlist + mail récap.

    python app.py              # run normal (playlist + mail)
    python app.py --dry-run    # aucune écriture : ni playlist ni mail, aperçu dans out/

Variables d'environnement (.env, secrets/.env ou secrets GitHub) :
    SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI, SPOTIPY_REFRESH_TOKEN
    EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO
    GENIUS_ACCESS_TOKEN   (optionnel)
    ROLLING_PLAYLIST      (optionnel, "true" : une seule playlist "HEBDO" vidée et remplie
                           chaque semaine au lieu d'une nouvelle "HEBDO - JJ/MM")
"""
import argparse
import json
import os
import random
import re
import smtplib
import sys
import traceback
import unicodedata
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from dotenv import load_dotenv
from spotipy import Spotify
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.exceptions import SpotifyOauthError
from spotipy.oauth2 import SpotifyOAuth

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(BASE_DIR, "secrets", ".env"))

SPOTIFY_SCOPE = "playlist-modify-private playlist-modify-public"
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


def parse_release_date(value):
    """Spotify renvoie 'YYYY-MM-DD', 'YYYY-MM' ou 'YYYY'."""
    parts = [int(p) for p in value.split("-")]
    return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)


# ----------------------------------------------------------------- Spotify

class SpotifyTokenExpired(Exception):
    pass


def connect_spotify():
    auth = SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        scope=SPOTIFY_SCOPE,
        cache_handler=MemoryCacheHandler(),
        open_browser=False,
    )
    try:
        auth.refresh_access_token(os.getenv("SPOTIPY_REFRESH_TOKEN"))
    except SpotifyOauthError as e:
        if "invalid_grant" in str(e):
            raise SpotifyTokenExpired(str(e)) from e
        raise
    sp = Spotify(auth_manager=auth)
    print(f"✅ Connecté à Spotify en tant que : {sp.current_user()['display_name']}")
    return sp


def fetch_artist_releases(sp, artists, since):
    """Sorties des artistes suivis depuis `since`.
    Retourne (lignes pour le mail, URIs pour la playlist, erreurs)."""
    lines, uris, errors = [], [], []
    seen_albums, seen_tracks = set(), set()

    for artist in artists:
        name = artist["artist"]
        try:
            albums = sp.artist_albums(artist["id"], include_groups="album,single", limit=50)["items"]
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"⚠️ Erreur pour {name}: {e}")
            continue

        for album in albums:
            if album["id"] in seen_albums or parse_release_date(album["release_date"]) < since:
                continue
            seen_albums.add(album["id"])  # collab entre deux artistes suivis -> une seule fois
            title = album["name"]

            if REISSUE_RE.search(title):
                print(f"   ↩️ Réédition ignorée : {name} - {title}")
                continue
            if album["album_type"] == "album" or DELUXE_RE.search(title):
                tag = "Deluxe" if DELUXE_RE.search(title) else "Album"
                lines.append(f"{name} - {title} [{tag}]")
                continue

            for track in sp.album_tracks(album["id"])["items"]:
                key = (track["name"].lower(), tuple(sorted(a["id"] for a in track["artists"])))
                if key in seen_tracks:
                    continue
                seen_tracks.add(key)
                uris.append(track["uri"])
                featured = [a["name"] for a in track["artists"] if a["name"] != name]
                feat = f" ft. {', '.join(featured)}" if featured else ""
                lines.append(f"{name}{feat} - {track['name']}")

    return lines, uris, errors


def fetch_podcast_episodes(sp, podcasts, since):
    """Dernier épisode de la semaine pour chaque podcast suivi."""
    episodes, errors = [], []
    for show in podcasts:
        name, show_id = show.get("podcast"), show.get("id")
        if not show_id:
            print(f"⚠️ Pas d'ID pour le podcast '{name}'")
            continue
        try:
            items = sp.show_episodes(show_id, limit=50).get("items", [])
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
        recent = [ep for ep in items if ep and ep.get("release_date")
                  and parse_release_date(ep["release_date"]) >= since]
        if recent:
            latest = max(recent, key=lambda ep: ep["release_date"])
            episodes.append((name, latest["name"]))
    return episodes, errors


def fetch_recommendations(sp, track_uris):
    """API dépréciée par Spotify pour les apps récentes : best effort, silencieux si 404."""
    if not track_uris:
        return []
    try:
        seeds = [uri.split(":")[-1] for uri in track_uris[:5]]
        tracks = sp.recommendations(seed_tracks=seeds, limit=3)["tracks"]
        return [f"{', '.join(a['name'] for a in t['artists'])} - {t['name']}" for t in tracks]
    except Exception as e:
        print(f"ℹ️ Recommandations indisponibles : {e}")
        return []


def pick_classics(sp, genius, classics, n=3):
    picks = []
    for c in random.sample(classics, min(n, len(classics))):
        try:
            url = sp.album(c["id"])["external_urls"]["spotify"]
        except Exception as e:
            print(f"⚠️ Classique {c['album']} : {e}")
            continue
        picks.append({**c, "url": url, "genius_info": get_album_genius_info(genius, c["album"], c["artist"])})
    return picks


def pick_songs(sp, genius, songs, n=3):
    picks = []
    for s in random.sample(songs, min(n, len(songs))):
        try:
            track = sp.track(s["id"])
        except Exception as e:
            print(f"⚠️ Son {s['song']} : {e}")
            continue
        picks.append({**s, "url": track["external_urls"]["spotify"], "uri": track["uri"],
                      "genius_info": get_song_genius_info(genius, s["song"], s["artist"])})
    return picks


def publish_playlist(sp, uris, today, rolling):
    """Crée la playlist de la semaine (ou remplit la playlist tournante). Retourne son URL."""
    user_id = sp.me()["id"]
    if rolling:
        playlist = next((p for p in iter_user_playlists(sp) if p["name"] == ROLLING_PLAYLIST_NAME), None)
        if playlist is None:
            playlist = sp.user_playlist_create(user=user_id, name=ROLLING_PLAYLIST_NAME, public=False)
        sp.playlist_replace_items(playlist["id"], uris[:100])
        rest = uris[100:]
    else:
        name = f"HEBDO - {today.strftime('%d/%m')}"
        playlist = sp.user_playlist_create(user=user_id, name=name, public=False)
        rest = uris
    for i in range(0, len(rest), 100):  # l'API accepte 100 items max par appel
        sp.playlist_add_items(playlist["id"], rest[i:i + 100])
    url = playlist.get("external_urls", {}).get("spotify") or f"https://open.spotify.com/playlist/{playlist['id']}"
    print(f"✅ Playlist '{playlist['name']}' : {len(uris)} titres ({url})")
    return url


def iter_user_playlists(sp):
    page = sp.current_user_playlists(limit=50)
    while page:
        yield from page["items"]
        page = sp.next(page) if page.get("next") else None


# ------------------------------------------------------------------ Genius

def connect_genius():
    token = os.getenv("GENIUS_ACCESS_TOKEN")
    if not token:
        print("⚠️ GENIUS_ACCESS_TOKEN non défini - fonctionnalités Genius désactivées")
        return None
    from lyricsgenius import Genius
    return Genius(token, verbose=False, remove_section_headers=True, timeout=15)


def genius_url_slug(text):
    """'The Blueprint' -> 'The-blueprint' (format des URLs d'albums Genius)."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    return (text[0].upper() + text[1:].lower()) if text else text


def first_annotation(song, max_len):
    try:
        desc = song.description_annotation["annotations"][0]["body"]["plain"]
    except (AttributeError, KeyError, IndexError, TypeError):
        return []
    if not desc or len(desc) <= 50:
        return []
    return [desc[:max_len] + "..." if len(desc) > max_len else desc]


def get_album_genius_info(genius, album_name, artist_name):
    if not genius:
        return None
    info = {
        "url": f"https://genius.com/albums/{genius_url_slug(artist_name)}/{genius_url_slug(album_name)}",
        "description": "",
        "release_date": "",
        "facts": [],
    }
    try:
        song = genius.search_song(album_name, artist_name) or genius.search_song(artist_name, artist_name)
        if song:
            info["facts"] = first_annotation(song, 300)
    except Exception as e:
        print(f"  ⚠️ Genius (chanson) pour {album_name}: {e}")
    try:
        artist = genius.search_artist(artist_name, max_songs=0, get_full_info=True)
        if artist and artist.description:
            desc = artist.description.get("plain", "") if isinstance(artist.description, dict) else str(artist.description)
            info["description"] = desc.split("\n")[0]
    except Exception as e:
        print(f"  ⚠️ Genius (artiste) pour {artist_name}: {e}")
    return info


def get_song_genius_info(genius, song_name, artist_name):
    if not genius:
        return None
    try:
        song = genius.search_song(song_name, artist_name)
    except Exception as e:
        print(f"  ⚠️ Genius pour {song_name} - {artist_name}: {e}")
        return None
    if not song:
        return None
    return {"url": song.url, "release_date": getattr(song, "release_date", ""), "facts": first_annotation(song, 200)}


# -------------------------------------------------------------------- Mail

GREEN, GOLD = "#1DB954", "#FFD700"


def html_list(title, items, intro=None):
    out = f"<h4>{title}</h4>"
    if intro:
        out += f'<p style="font-size:0.9em; color:#666;">{intro}</p>'
    return out + "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def html_card(head, url, genius_info, about=False):
    out = (f'<div style="margin-bottom:18px; padding:14px; background-color:#f8f8f8; border-left:4px solid {GREEN};">'
           f'{head}<br><a href="{url}" target="_blank" style="text-decoration:none; color:{GREEN}; font-size:0.9em;">🎵 Spotify</a>')
    g = genius_info or {}
    if about and g.get("description"):
        out += (f'<p style="margin-top:10px; font-size:0.9em; color:#555;"><strong>À propos de l\'artiste :</strong>'
                f'<br>{escape(g["description"][:250])}...</p>')
    if g.get("facts"):
        out += (f'<p style="margin-top:10px; padding:10px; background-color:#fff; border-left:3px solid {GOLD}; '
                f'font-size:0.9em; color:#333;"><strong>💡 Le saviez-vous ?</strong><br>{escape(g["facts"][0])}</p>')
    if g.get("url"):
        out += (f'<p style="margin-top:5px;"><a href="{g["url"]}" target="_blank" style="text-decoration:none; '
                f'color:{GOLD}; font-weight:bold; font-size:0.85em;">💡Genius</a></p>')
    return out + "</div>"


def build_email(today, releases, playlist_url, podcasts, recommendations, classics, songs, errors):
    week = today.isocalendar()[1]
    text = ["🎶 Voici les sorties Spotify de cette semaine :", ""]
    html = ["<html><body><h3>🍝 Au menu cette semaine</h3>"]

    if releases:
        text += ["-- Musique --", *releases]
        html.append(html_list("🎶 Musique", [escape(r) for r in releases]))
        if playlist_url:
            text += ["", f"🔗 Playlist de la semaine : {playlist_url}"]
            html.append(f'<p style="margin-top:15px; padding:12px; background-color:{GREEN}; border-radius:8px; text-align:center;">'
                        f'<a href="{playlist_url}" target="_blank" style="text-decoration:none; color:white; font-weight:bold;">'
                        "🎧 Écouter la playlist de la semaine</a></p>")
    else:
        text.append("Pas de nouvelle sortie cette semaine.")
        html.append("<p>Pas de nouvelle sortie cette semaine.</p>")

    if podcasts:
        text += ["", "-- Podcasts --", *(f"{s} - {e}" for s, e in podcasts)]
        html.append(html_list("🎧 Podcasts", [f"<strong>{escape(s)}</strong> - {escape(e)}" for s, e in podcasts]))

    if recommendations:
        text += ["", "-- Découvertes --", *recommendations]
        html.append(html_list("🔍 Découvertes", [escape(r) for r in recommendations],
                              "3 morceaux que tu pourrais aimer basés sur tes nouvelles sorties :"))

    if classics:
        text += ["", "-- Les Classiques Hip-Hop de la semaine --"]
        html.append('<h4>📀 Les Classiques Hip-Hop de la semaine</h4>'
                    '<p style="font-size:0.9em; color:#666;">3 Classiques du Hip-Hop à (ré)écouter (Rolling Stone) :</p>')
        for c in classics:
            text += [f"• {c['artist']} - {c['album']} ({c['year']})", f"  {c['url']}"]
            head = f"<strong>{escape(c['artist'])}</strong> - <em>{escape(c['album'])}</em> ({c['year']})"
            html.append(html_card(head, c["url"], c["genius_info"], about=True))

    if songs:
        text += ["", "-- Les Sons du Siècle --"]
        html.append('<h4>🎵 Les Sons du Siècle</h4>'
                    '<p style="font-size:0.9em; color:#666;">3 morceaux parmi les meilleurs du 21e siècle (Rolling Stone) :</p>')
        for s in songs:
            text += [f"• {s['artist']} - {s['song']} ({s['year']})", f"  {s['url']}"]
            head = f"<strong>{escape(s['artist'])}</strong> - <em>{escape(s['song'])}</em> ({s['year']})"
            html.append(html_card(head, s["url"], s["genius_info"]))

    if errors:
        text += ["", "Erreurs rencontrées :", *errors]
        html.append("<h3>Erreurs rencontrées :</h3><ul>" + "".join(f"<li>{escape(e)}</li>" for e in errors) + "</ul>")

    html.append("</body></html>")
    return f"🎶 Sorties de la Semaine - WK{week}", "\n".join(text), "".join(html)


def send_email(subject, text_body, html_body=None):
    msg = MIMEMultipart("alternative")
    msg["From"] = os.getenv("EMAIL_USER")
    msg["To"] = os.getenv("EMAIL_TO")
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(os.getenv("EMAIL_USER"), os.getenv("EMAIL_PASSWORD"))
        server.send_message(msg)
    print(f"✅ Email envoyé : {subject}")


def send_failure_alert(exc):
    """Prévenir par mail plutôt que de découvrir la panne des semaines plus tard."""
    if isinstance(exc, SpotifyTokenExpired):
        subject = "⚠️ Spotify bot : refresh token expiré"
        body = ("Le refresh token Spotify a expiré, le bot ne peut plus tourner.\n\n"
                "Pour le renouveler, en local dans le repo :\n"
                "    python generate_refresh_token.py\n"
                "puis colle le token affiché dans le secret GitHub SPOTIPY_REFRESH_TOKEN :\n"
                "https://github.com/justinmartin/spotify_weekly_release_bot/settings/secrets/actions\n")
    else:
        subject = "❌ Spotify bot : échec du run hebdo"
        body = "Le run hebdomadaire a échoué :\n\n" + "".join(traceback.format_exception(exc))
    try:
        send_email(subject, body)
    except Exception as mail_err:
        print(f"⚠️ Impossible d'envoyer l'alerte : {mail_err}")


# -------------------------------------------------------------------- Main

def run(dry_run=False):
    today = date.today()
    since = today - timedelta(days=6)  # vendredi -> les 7 derniers jours, aujourd'hui inclus
    rolling = os.getenv("ROLLING_PLAYLIST", "").lower() in {"1", "true", "yes"}

    sp = connect_spotify()
    genius = connect_genius()

    releases, release_uris, errors = fetch_artist_releases(sp, load_json("artists.json"), since)
    podcasts, podcast_errors = fetch_podcast_episodes(sp, load_json("podcasts.json"), since)
    errors += podcast_errors
    recommendations = fetch_recommendations(sp, release_uris)
    classics = pick_classics(sp, genius, load_json("classics_hiphop.json"))
    songs = pick_songs(sp, genius, load_json("best_songs_21st_century.json"))

    playlist_url = None
    if release_uris:
        uris = release_uris + [s["uri"] for s in songs]
        if dry_run:
            print(f"⏭️ Dry run : playlist non créée ({len(uris)} titres)")
        else:
            playlist_url = publish_playlist(sp, uris, today, rolling)
    else:
        print("ℹ️ Pas de nouvelles sorties cette semaine.")

    subject, text_body, html_body = build_email(
        today, releases, playlist_url, podcasts, recommendations, classics, songs, errors)

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
    args = parser.parse_args()
    try:
        run(dry_run=args.dry_run)
    except Exception as e:
        traceback.print_exc()
        if not args.dry_run:
            send_failure_alert(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
