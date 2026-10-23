"""Spotify : connexion, suivi des artistes, sorties de la semaine, podcasts, playlists, best of du mois."""
import os
import re
from datetime import date, timedelta

from spotipy import Spotify
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.exceptions import SpotifyException, SpotifyOauthError
from spotipy.oauth2 import SpotifyOAuth

from bot.config import DELUXE_RE, REISSUE_RE, ROLLING_PLAYLIST_NAME, SPOTIFY_SCOPE


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
