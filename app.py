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
import json
import os
import random
import re
import smtplib
import sys
import traceback
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from dotenv import load_dotenv
from spotipy import Spotify
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.exceptions import SpotifyException, SpotifyOauthError
from spotipy.oauth2 import SpotifyOAuth

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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


# "Titre - Radio Edit", "Titre (Sped Up)"... -> même morceau que "Titre"
VERSION_RE = re.compile(r"\s*(-\s*|\()\s*(radio edit|edit|clean|explicit|sped up|slowed|instrumental|remaster(ed)?[^)]*)\)?\s*$", re.I)


def base_title(name):
    return VERSION_RE.sub("", name).strip().lower()


# "Titre (feat. X)" -> "Titre" (l'invité est déjà dans les crédits)
FEAT_RE = re.compile(r"\s*[\(\[](feat\.?|ft\.?|with)\s[^\)\]]*[\)\]]", re.I)


def clean_title(name):
    return FEAT_RE.sub("", name).strip()


def credits_of(track):
    """Crédits dans l'ordre officiel : "Artiste principal ft. Invité1, Invité2"."""
    names = [a["name"] for a in track["artists"]]
    return names[0] + (f" ft. {', '.join(names[1:])}" if len(names) > 1 else "")


def isrc_year(isrc):
    """Année de référence encodée dans l'ISRC (CC-XXX-AA-NNNNN), ou None."""
    if not isrc or len(isrc) < 7 or not isrc[5:7].isdigit():
        return None
    yy = int(isrc[5:7])
    return 2000 + yy if 2000 + yy <= date.today().year else 1900 + yy


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


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def cover_url(images):
    """Pochette ~300px (nette en 64px sur écran retina), sinon la plus grande dispo."""
    images = sorted(images or [], key=lambda i: i.get("width") or 0)
    return next((i["url"] for i in images if (i.get("width") or 0) >= 300), images[-1]["url"] if images else "")


def sync_follows(sp, artists, dry_run=False):
    """Suit sur Spotify tous les artistes de artists.json (non bloquant si la permission manque)."""
    ids = [a["id"] for a in artists]
    names = {a["id"]: a["artist"] for a in artists}
    try:
        missing = []
        for chunk in chunks(ids, 40):
            flags = sp.current_user_following_artists(chunk)
            missing += [i for i, followed in zip(chunk, flags) if not followed]
        if not missing:
            print("✅ Tous les artistes de artists.json sont suivis sur Spotify")
            return
        label = ", ".join(names[i] for i in missing)
        if dry_run:
            print(f"⏭️ Dry run : à suivre sur Spotify : {label}")
            return
        for chunk in chunks(missing, 40):
            sp.user_follow_artists(chunk)
        print(f"➕ Désormais suivis sur Spotify : {label}")
    except SpotifyException as e:
        if e.http_status == 403:
            print("ℹ️ Suivi auto désactivé : le refresh token n'a pas la permission (relancer generate_refresh_token.py)")
        else:
            print(f"⚠️ Suivi des artistes impossible : {e}")


def release_item(kind, artist_name, title, credits, album, uri=None):
    return {
        "kind": kind,                # single | album | deluxe | feat
        "artist": artist_name,       # artiste suivi concerné
        "title": title,
        "credits": credits,          # "Artiste ft. Invité"
        "url": album["external_urls"].get("spotify", ""),
        "cover": cover_url(album.get("images")),
        "date": album["release_date"],
        "album_id": album["id"],
        "uri": uri,                  # morceau à mettre en playlist (None pour un album)
    }


def fetch_artist_releases(sp, artists, since, until=None):
    """Sorties des artistes suivis entre `since` et `until` (inclus), featurings compris.
    Retourne (sorties, URIs pour la playlist, erreurs)."""
    items, uris, errors = [], [], []
    seen_albums, seen_tracks = set(), set()
    in_window = lambda album: since <= parse_release_date(album["release_date"]) <= (until or date.max)

    def add_track(track, item):
        key = (base_title(track["name"]), tuple(sorted(a["id"] for a in track["artists"])))
        if key in seen_tracks:
            return
        seen_tracks.add(key)
        uris.append(track["uri"])
        items.append(item)

    for artist in artists:
        name, artist_id = artist["artist"], artist["id"]
        try:
            own = sp.artist_albums(artist_id, include_groups="album,single", limit=50)["items"]
            feats = sp.artist_albums(artist_id, include_groups="appears_on", limit=50)["items"]
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"⚠️ Erreur pour {name}: {e}")
            continue

        for album in own:
            if album["id"] in seen_albums or not in_window(album):
                continue
            seen_albums.add(album["id"])  # collab entre deux artistes suivis -> une seule fois
            title = album["name"]
            if REISSUE_RE.search(title):
                print(f"   ↩️ Réédition ignorée : {name} - {title}")
                continue
            if album["album_type"] == "album" or DELUXE_RE.search(title):
                kind = "deluxe" if DELUXE_RE.search(title) else "album"
                items.append(release_item(kind, name, title, name, album))
                continue
            for track in sp.album_tracks(album["id"])["items"]:
                add_track(track, release_item("single", name, clean_title(track["name"]), credits_of(track), album, track["uri"]))

        # Featurings : morceaux d'autres artistes où l'artiste suivi est invité
        for album in feats:
            if album["id"] in seen_albums or album.get("album_type") == "compilation" or not in_window(album):
                continue
            seen_albums.add(album["id"])
            if REISSUE_RE.search(album["name"]):
                continue
            for track in sp.album_tracks(album["id"])["items"]:
                if artist_id in {a["id"] for a in track["artists"]}:
                    add_track(track, release_item("feat", name, clean_title(track["name"]), credits_of(track), album, track["uri"]))

    items = drop_old_recordings(sp, items, uris)  # filtre aussi `uris` en place
    return items, uris, errors


def drop_old_recordings(sp, items, uris):
    """Écarte les vieux titres republiés (ex. un classique de 2002 ressorti en single) :
    l'ISRC donne l'année d'enregistrement, on rejette s'il a plus d'un an d'écart."""
    old = set()
    for chunk in chunks([u.split(":")[-1] for u in uris], 50):
        for t in sp.tracks(chunk)["tracks"]:
            year = isrc_year((t or {}).get("external_ids", {}).get("isrc"))
            if year and year < parse_release_date(t["album"]["release_date"]).year - 1:
                old.add(t["uri"])
                print(f"   ↩️ Ancien titre republié ignoré : {credits_of(t)} - {t['name']} (enregistré en {year})")
    uris[:] = [u for u in uris if u not in old]
    return [i for i in items if i["uri"] not in old]


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
            ep = max(recent, key=lambda ep: ep["release_date"])
            episodes.append({"show": name, "title": ep["name"], "url": ep["external_urls"].get("spotify", ""),
                             "cover": cover_url(ep.get("images"))})
    return episodes, errors


# Rotation hebdo sans répétition : la liste est mélangée une fois par cycle (graine fixe)
# et chaque semaine prend les n éléments suivants, jusqu'à en avoir fait le tour.
ROTATION_EPOCH = date(2025, 1, 3)


def weekly_rotation(items, n, today, salt):
    if not items:
        return []
    weeks = (today - ROTATION_EPOCH).days // 7
    per_cycle = max(1, len(items) // n)
    cycle, slot = divmod(weeks, per_cycle)
    order = list(items)
    random.Random(f"{salt}-{cycle}").shuffle(order)
    return order[slot * n:(slot + 1) * n]


def pick_classics(sp, genius, classics, today, n=3):
    picks = []
    for c in weekly_rotation(classics, n, today, "classics"):
        try:
            album = sp.album(c["id"])
        except Exception as e:
            print(f"⚠️ Classique {c['album']} : {e}")
            continue
        picks.append({**c, "url": album["external_urls"]["spotify"], "cover": cover_url(album.get("images")),
                      "genius_info": get_album_genius_info(genius, c["album"], c["artist"])})
    return picks


def pick_songs(sp, genius, songs, today, n=3):
    picks = []
    for s in weekly_rotation(songs, n, today, "songs"):
        try:
            track = sp.track(s["id"])
        except Exception as e:
            print(f"⚠️ Son {s['song']} : {e}")
            continue
        picks.append({**s, "url": track["external_urls"]["spotify"], "uri": track["uri"],
                      "cover": cover_url(track["album"].get("images")),
                      "genius_info": get_song_genius_info(genius, s["song"], s["artist"])})
    return picks


MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]


def previous_month(today):
    last = today.replace(day=1) - timedelta(days=1)
    return last.replace(day=1), last


def build_best_of(sp, artists, today, size=30):
    """Best of du mois précédent : sorties du mois (singles, featurings, titres d'albums)
    classées par popularité Spotify."""
    first, last = previous_month(today)
    items, uris, _ = fetch_artist_releases(sp, artists, first, last)
    by_id = {}
    for it in items:
        if it["uri"]:
            by_id[it["uri"].split(":")[-1]] = it
        else:  # album : on considère tous ses titres
            for t in sp.album_tracks(it["album_id"])["items"]:
                by_id.setdefault(t["id"], {**it, "title": t["name"], "uri": t["uri"], "kind": "album"})
    ranked = []
    for chunk in chunks(list(by_id), 50):
        for t in sp.tracks(chunk)["tracks"]:
            if t:
                ranked.append({**by_id[t["id"]], "popularity": t.get("popularity") or 0})
    ranked.sort(key=lambda x: -x["popularity"])
    seen, top = set(), []
    for it in ranked:
        key = base_title(it["title"])
        if key not in seen:
            seen.add(key)
            top.append(it)
    label = f"{MONTHS_FR[first.month - 1]} {first.year}"
    print(f"🏆 Best of {label} : {len(top[:size])} titres (sur {len(ranked)})")
    return label, top[:size]


def create_playlist(sp, name, uris):
    playlist = sp.user_playlist_create(user=sp.me()["id"], name=name, public=False)
    for chunk in chunks(uris, 100):  # l'API accepte 100 items max par appel
        sp.playlist_add_items(playlist["id"], chunk)
    url = playlist.get("external_urls", {}).get("spotify") or f"https://open.spotify.com/playlist/{playlist['id']}"
    print(f"✅ Playlist '{name}' : {len(uris)} titres ({url})")
    return url


def publish_playlist(sp, uris, today, rolling):
    """Crée la playlist de la semaine (ou remplit la playlist tournante). Retourne son URL."""
    if not rolling:
        return create_playlist(sp, f"HEBDO - {today.strftime('%d/%m')}", uris)
    playlist = next((p for p in iter_user_playlists(sp) if p["name"] == ROLLING_PLAYLIST_NAME), None)
    if playlist is None:
        return create_playlist(sp, ROLLING_PLAYLIST_NAME, uris)
    sp.playlist_replace_items(playlist["id"], uris[:100])
    for chunk in chunks(uris[100:], 100):
        sp.playlist_add_items(playlist["id"], chunk)
    url = playlist.get("external_urls", {}).get("spotify") or f"https://open.spotify.com/playlist/{playlist['id']}"
    print(f"✅ Playlist '{ROLLING_PLAYLIST_NAME}' : {len(uris)} titres ({url})")
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
    return Genius(token, remove_section_headers=True, timeout=15)


def first_hit(response):
    """Premier résultat d'une recherche Genius (format 'sections' ou 'hits')."""
    hits = response.get("sections", [{}])[0].get("hits") if "sections" in response else response.get("hits")
    return hits[0]["result"] if hits else None


def plain(field, max_len=None):
    """Texte brut d'un champ description Genius ({'plain': ...}) ; '' si vide ('?')."""
    text = (field or {}).get("plain", "") if isinstance(field, dict) else str(field or "")
    text = text.strip()
    if text in ("", "?"):
        return ""
    if max_len and len(text) > max_len:
        return text[:max_len].rsplit(" ", 1)[0] + "..."
    return text


def genius_artist_bio(genius, artist_id):
    artist = genius.artist(artist_id, text_format="plain")
    return plain(artist.get("artist", artist).get("description"), 250).split("\n")[0]


def get_album_genius_info(genius, album_name, artist_name):
    if not genius:
        return None
    try:
        hit = first_hit(genius.search_albums(f"{album_name} {artist_name}"))
        if not hit:
            return None
        album = genius.album(hit["id"], text_format="plain")
        album = album.get("album", album)
        annotation = (album.get("description_annotation") or {}).get("annotations") or [{}]
        fact = plain(annotation[0].get("body"), 300)
        return {
            "url": album.get("url") or hit.get("url"),
            "release_date": album.get("release_date_for_display") or "",
            "facts": [fact] if fact else [],
            "description": genius_artist_bio(genius, album["artist"]["id"]) if album.get("artist") else "",
        }
    except Exception as e:
        print(f"  ⚠️ Genius pour {album_name} - {artist_name}: {e}")
        return None


def get_song_genius_info(genius, song_name, artist_name):
    if not genius:
        return None
    try:
        hit = first_hit(genius.search_songs(f"{song_name} {artist_name}"))
        if not hit:
            return None
        song = genius.song(hit["id"], text_format="plain")
        song = song.get("song", song)
        fact = plain(song.get("description"), 200)
        return {"url": song.get("url"), "release_date": song.get("release_date_for_display") or "", "facts": [fact] if fact else []}
    except Exception as e:
        print(f"  ⚠️ Genius pour {song_name} - {artist_name}: {e}")
        return None


# -------------------------------------------------------------------- Mail
# HTML compatible clients mail (Gmail, Outlook, Apple Mail) : tables + styles inline.

GREEN, INK, MUTED, LINE, BG = "#1DB954", "#191414", "#6a6a6a", "#ececec", "#f4f4f4"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
BADGES = {"single": ("Single", "#e8f8ee", "#137a3a"), "album": ("Album", "#eef0ff", "#3a46a8"),
          "deluxe": ("Deluxe", "#fff4e0", "#8a5a00"), "feat": ("Feat", "#fdeef5", "#a3205e")}


def section(title, subtitle=""):
    sub = f'<div style="font-size:13px;color:{MUTED};margin-top:2px;">{subtitle}</div>' if subtitle else ""
    return (f'<tr><td style="padding:28px 24px 8px;"><div style="font-size:18px;font-weight:700;color:{INK};">'
            f'{title}</div>{sub}</td></tr>')


def row(cover, title, line2, url, badge=None, extra=""):
    """Une ligne : pochette 56px, titre, sous-titre, badge, lien Spotify."""
    img = (f'<img src="{escape(cover)}" width="56" height="56" alt="" '
           f'style="display:block;border-radius:6px;border:0;">') if cover else ""
    tag = ""
    if badge:
        label, bg, fg = BADGES[badge]
        tag = (f'<span style="display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;'
               f'border-radius:10px;background:{bg};color:{fg};margin-left:6px;vertical-align:middle;">{label}</span>')
    return (f'<tr><td style="padding:6px 24px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed;">'
            f'<tr><td width="68" valign="top" style="width:68px;padding-right:12px;"><a href="{escape(url)}">{img}</a></td>'
            f'<td valign="middle" style="border-bottom:1px solid {LINE};padding-bottom:10px;word-wrap:break-word;overflow-wrap:anywhere;">'
            f'<a href="{escape(url)}" style="font-size:15px;font-weight:600;color:{INK};text-decoration:none;">{escape(title)}</a>{tag}'
            f'<div style="font-size:13px;color:{MUTED};margin-top:2px;">{escape(line2)}</div>{extra}</td></tr></table></td></tr>')


def button(url, label):
    return (f'<tr><td style="padding:16px 24px 4px;" align="center"><a href="{escape(url)}" '
            f'style="display:inline-block;background:{GREEN};color:#fff;font-weight:700;font-size:15px;'
            f'padding:12px 26px;border-radius:24px;text-decoration:none;">{label}</a></td></tr>')


def genius_extra(g, about=False):
    g = g or {}
    out = ""
    if about and g.get("description"):
        out += f'<div style="font-size:13px;color:#444;margin-top:8px;line-height:1.45;">{escape(g["description"])}</div>'
    if g.get("facts"):
        out += (f'<div style="font-size:13px;color:#444;margin-top:8px;line-height:1.45;padding:8px 10px;'
                f'background:#fffbe6;border-left:3px solid #f5c518;">💡 {escape(g["facts"][0])}</div>')
    if g.get("url"):
        out += (f'<div style="margin-top:6px;"><a href="{escape(g["url"])}" style="font-size:12px;font-weight:600;'
                f'color:#8a6d00;text-decoration:none;">Lire sur Genius →</a></div>')
    return out


def build_email(today, releases, playlist_url, podcasts, classics, songs, errors, best_of=None):
    week = today.isocalendar()[1]
    text = [f"🎶 Sorties de la semaine {week}", ""]
    html = [f'<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            f'<body style="margin:0;padding:0;background:{BG};font-family:{FONT};">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};"><tr><td align="center" style="padding:16px 8px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#fff;border-radius:14px;overflow:hidden;table-layout:fixed;">'
            f'<tr><td style="background:{INK};padding:26px 24px;"><div style="font-size:12px;letter-spacing:2px;color:{GREEN};font-weight:700;">SEMAINE {week}</div>'
            f'<div style="font-size:24px;font-weight:800;color:#fff;margin-top:4px;">Au menu cette semaine 🍝</div>'
            f'<div style="font-size:13px;color:#b3b3b3;margin-top:4px;">{len(releases)} sortie{"s" if len(releases) > 1 else ""} de tes artistes</div></td></tr>']

    html.append(section("🎶 Nouvelles sorties"))
    if releases:
        text.append("-- Musique --")
        for r in releases:
            text.append(f"{r['credits']} - {r['title']} [{BADGES[r['kind']][0]}]")
            html.append(row(r["cover"], r["title"], r["credits"], r["url"], r["kind"]))
        if playlist_url:
            text += ["", f"🔗 Playlist de la semaine : {playlist_url}"]
            html.append(button(playlist_url, "🎧 Écouter la playlist de la semaine"))
    else:
        text.append("Pas de nouvelle sortie cette semaine.")
        html.append(f'<tr><td style="padding:4px 24px;color:{MUTED};font-size:14px;">Pas de nouvelle sortie cette semaine.</td></tr>')

    if best_of:
        label, top, url = best_of
        text += ["", f"-- Best of {label} --", *(f"{i}. {t['credits']} - {t['title']}" for i, t in enumerate(top[:10], 1))]
        html.append(section(f"🏆 Best of {label}", f"Les sorties du mois les plus écoutées ({len(top)} titres)"))
        for i, t in enumerate(top[:10], 1):
            html.append(row(t["cover"], f"{i}. {t['title']}", t["credits"], t["url"]))
        if url:
            text.append(f"🔗 {url}")
            html.append(button(url, f"🏆 Écouter le best of {label}"))

    if podcasts:
        text += ["", "-- Podcasts --", *(f"{p['show']} - {p['title']}" for p in podcasts)]
        html.append(section("🎧 Podcasts"))
        for p in podcasts:
            html.append(row(p["cover"], p["title"], p["show"], p["url"]))

    if classics:
        text += ["", "-- Les Classiques Hip-Hop de la semaine --"]
        html.append(section("📀 Classiques hip-hop", "3 albums du top 200 Rolling Stone à (ré)écouter"))
        for c in classics:
            text += [f"• {c['artist']} - {c['album']} ({c['year']})", f"  {c['url']}"]
            html.append(row(c["cover"], c["album"], f"{c['artist']} · {c['year']}", c["url"],
                            extra=genius_extra(c["genius_info"], about=True)))

    if songs:
        text += ["", "-- Les Sons du Siècle --"]
        html.append(section("🎵 Sons du siècle", "3 morceaux parmi les meilleurs du 21e siècle (Rolling Stone)"))
        for t in songs:
            text += [f"• {t['artist']} - {t['song']} ({t['year']})", f"  {t['url']}"]
            html.append(row(t["cover"], t["song"], f"{t['artist']} · {t['year']}", t["url"],
                            extra=genius_extra(t["genius_info"])))

    if errors:
        text += ["", "Erreurs rencontrées :", *errors]
        html.append(section("⚠️ Erreurs rencontrées"))
        html.append(f'<tr><td style="padding:4px 24px;font-size:12px;color:#b00020;">'
                    + "<br>".join(escape(e) for e in errors) + "</td></tr>")

    html.append(f'<tr><td style="padding:24px;font-size:11px;color:#aaa;text-align:center;">Spotify Weekly Release Bot</td></tr>'
                "</table></td></tr></table></body></html>")
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
