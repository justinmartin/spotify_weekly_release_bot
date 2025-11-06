import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# Charger les variables d'environnement
load_dotenv()

# Créer la session Spotify
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope="playlist-modify-private playlist-modify-public",
    cache_path=".cache-spotify"
))

# Vérifier la connexion
me = sp.current_user()
print(f"✅ Connecté à Spotify en tant que : {me['display_name']}")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE_DIR, "artists.json"), "r") as f:
    ARTISTS = json.load(f)


# ----- Déterminer la semaine passée -----
today = datetime.today()
last_week = today - timedelta(days=7)

# ----- Chercher les nouvelles sorties -----
new_tracks_set = set()

for artist in ARTISTS:
    artist_name = artist['artist']
    artist_id = artist['id']
    try:
        albums = sp.artist_albums(artist_id, album_type='album,single', limit=50)
        for album in albums['items']:
            release_date = album['release_date']
            if len(release_date) == 10:
                release_dt = datetime.strptime(release_date, "%Y-%m-%d")
            else:
                release_dt = datetime.strptime(release_date, "%Y")
            if release_dt >= last_week:
                for track in sp.album_tracks(album['id'])['items']:
                    new_tracks_set.append(track['uri'])
    except Exception as e:
        print(f"⚠️ Erreur pour {artist_name}: {e}")

# ----- Créer playlist si nouvelles sorties -----
if new_tracks_set:
    playlist_name = f"HEBDO - {today.strftime('%d/%m')}"
    user_id = sp.me()['id']
    playlist = sp.user_playlist_create(user=user_id, name=playlist_name, public=False)
    sp.playlist_add_items(playlist_id=playlist['id'], items=new_tracks_set)
    print(f"✅ Playlist '{playlist_name}' créée avec {len(new_tracks_set)} titres !")
else:
    print("ℹ️ Pas de nouvelles sorties cette semaine.")