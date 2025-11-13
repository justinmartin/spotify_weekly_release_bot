import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Charger les variables d'environnement depuis .gitignore/.env
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.gitignore', '.env'))

EMAIL_USER = os.getenv("EMAIL_USER")           # ton email Outlook
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")   # mot de passe App
EMAIL_TO = os.getenv("EMAIL_TO")               # email destinataire
SEND_EMAIL = True                              # True/False selon si on envoie l'email

# Spotify OAuth
auth_manager = SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope="playlist-modify-private playlist-modify-public",
    cache_path=".cache-spotify"
)
auth_manager.refresh_access_token(os.getenv("SPOTIPY_REFRESH_TOKEN"))

sp = Spotify(auth_manager=auth_manager)


# Vérifier la connexion
me = sp.current_user()
print(f"✅ Connecté à Spotify en tant que : {me['display_name']}")

with open(os.path.join(BASE_DIR, "artists.json"), "r") as f:
    ARTISTS = json.load(f)

# Charger les podcasts (optionnel)
PODCASTS = []
podcasts_path = os.path.join(BASE_DIR, "podcasts.json")
if os.path.exists(podcasts_path):
    with open(podcasts_path, "r") as pf:
        PODCASTS = json.load(pf)

# ----- Déterminer la semaine passée -----
today = datetime.today()
last_week = today - timedelta(days=7)

# ----- Chercher les nouvelles sorties -----
new_tracks_set = set()
music_releases = []
podcast_releases = []
errors_list = []
playlist_url = None

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
                    new_tracks_set.add(track['uri'])
                    # Formatage texte pour le mail
                    if album['album_type'] == 'album':
                        music_releases.append(f"{artist_name} - {album['name']} [Album]")
                    else:
                        music_releases.append(f"{artist_name} - {track['name']}")
                        
    except Exception as e:
        errors_list.append(f"{artist_name}: {str(e)}")
        print(f"⚠️ Erreur pour {artist_name}: {e}")

# ----- Chercher nouveaux épisodes de podcasts (shows) -----
for show in PODCASTS:
    show_name = show.get('podcast')
    show_id = show.get('id')
    if not show_id:
        print(f"⚠️ Pas d'ID pour le podcast '{show_name}'")
        continue
    try:
        episodes = sp.show_episodes(show_id, limit=50)
        for ep in episodes.get('items', []):
            release_date = ep.get('release_date')
            if not release_date:
                continue
            if len(release_date) == 10:
                release_dt = datetime.strptime(release_date, "%Y-%m-%d")
            else:
                release_dt = datetime.strptime(release_date, "%Y")
            if release_dt >= last_week:
                # Les épisodes de podcast ont des 'uri' utilisables
                uri = ep.get('uri')
                if uri:
                    new_tracks_set.add(uri)
                    podcast_releases.append(f"{show_name} - {ep.get('name')}")
    except Exception as e:
        errors_list.append(f"{show_name}: {str(e)}")
        print(f"⚠️ Erreur pour le show {show_name}: {e}")

# ----- Créer playlist si nouvelles sorties -----
if new_tracks_set:
    playlist_name = f"HEBDO - {today.strftime('%d/%m')}"
    user_id = sp.me()['id']
    playlist = sp.user_playlist_create(user=user_id, name=playlist_name, public=False)
    sp.playlist_add_items(playlist_id=playlist['id'], items=list(new_tracks_set))
    # Récupérer l'URL publique Spotify de la playlist pour l'inclure dans l'email
    if playlist.get('external_urls') and playlist['external_urls'].get('spotify'):
        playlist_url = playlist['external_urls']['spotify']
    else:
        # fallback vers l'URL construite depuis l'ID
        playlist_url = f"https://open.spotify.com/playlist/{playlist['id']}"

    print(f"✅ Playlist '{playlist_name}' créée avec {len(new_tracks_set)} titres ! ({playlist_url})")
else:
    print("ℹ️ Pas de nouvelles sorties cette semaine.")

# ----- Fonction d'envoi d'email -----
def send_email(subject, text_body, html_body=None):
    msg = MIMEMultipart('alternative')
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_TO
    msg['Subject'] = subject

    # Partie texte (fallback)
    part1 = MIMEText(text_body, 'plain')
    msg.attach(part1)

    # Partie HTML (optionnelle)
    if html_body:
        part2 = MIMEText(html_body, 'html')
        msg.attach(part2)

    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)
    print("✅ Email envoyé !")

# ----- Envoi du rapport -----
# Dédupliquer les listes
music_releases = list(dict.fromkeys(music_releases))
podcast_releases = list(dict.fromkeys(podcast_releases))

if SEND_EMAIL:
    week_number = today.isocalendar()[1]

    # Construire corps texte et HTML avec deux sections : Musique et Podcasts
    text_body = "🎶 Voici les sorties Spotify de cette semaine :\n\n"
    html_body = "<html><body><h3> 🍝 Au menu cette semaine</h3>"

    # Section Musique
    if music_releases:
        text_body += "-- Musique --\n"
        html_body += "<h4>🎶 Musique</h4><ul>"
        for line in music_releases:
            text_body += f"{line}\n"
            safe_line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html_body += f"<li>{safe_line}</li>"
        html_body += "</ul>"

    # Section Podcasts
    if podcast_releases:
        text_body += "\n-- Podcasts --\n"
        html_body += "<h4>🎧 Podcasts</h4><ul>"
        for line in podcast_releases:
            text_body += f"{line}\n"
            safe_line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html_body += f"<li>{safe_line}</li>"
        html_body += "</ul>"

    if errors_list:
        text_body += "\nErreurs rencontrées :\n"
        html_body += "<h3>Erreurs rencontrées :</h3><ul>"
        for e in errors_list:
            text_body += f"{e}\n"
            safe_e = e.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html_body += f"<li>{safe_e}</li>"
        html_body += "</ul>"

    # Inclure le lien vers la playlist créée (si applicable)
    if playlist_url:
        text_body += f"\nPlaylist créée : {playlist_url}\n"
        html_body += f"<p>🔗 Playlist créée : <a href=\"{playlist_url}\" target=\"_blank\" style=\"text-decoration:none; color:#1DB954;\">Ouvrir la playlist</a></p>"

    html_body += "</body></html>"

    send_email(f" 🎶 Sorties de la Semaine - WK{week_number}", text_body, html_body)
