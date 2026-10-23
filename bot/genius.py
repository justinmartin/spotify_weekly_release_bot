"""Genius : bios et descriptions des sorties."""
import os



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
