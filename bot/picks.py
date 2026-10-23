"""Sélections hebdo sans répétition : classiques hip-hop et meilleurs titres du XXIe siècle."""
import random
from datetime import date

from bot.genius import get_album_genius_info, get_song_genius_info
from bot.spotify import cover_url


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
