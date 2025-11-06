import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Charger les variables d'environnement
load_dotenv()

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE_DIR, "artists.json"), "r") as f:
    ARTISTS = json.load(f)

# ----- Déterminer la semaine passée -----
today = datetime.today()
last_week = today - timedelta(days=7)

# ----- Chercher les nouvelles sorties -----
new_tracks_set = set()
releases_list = []
errors_list = []

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
                        releases_list.append(f"{artist_name} - {album['name']} [Album]")
                    else:
                        releases_list.append(f"{artist_name} - {track['name']}")
    except Exception as e:
        errors_list.append(f"{artist_name}: {str(e)}")
        print(f"⚠️ Erreur pour {artist_name}: {e}")

# ----- Créer playlist si nouvelles sorties -----
if new_tracks_set:
    playlist_name = f"HEBDO - {today.strftime('%d/%m')}"
    user_id = sp.me()['id']
    playlist = sp.user_playlist_create(user=user_id, name=playlist_name, public=False)
    sp.playlist_add_items(playlist_id=playlist['id'], items=list(new_tracks_set))
    print(f"✅ Playlist '{playlist_name}' créée avec {len(new_tracks_set)} titres !")
else:
    print("ℹ️ Pas de nouvelles sorties cette semaine.")

# ----- Fonction d'envoi d'email -----
def send_email(subject, body):
    msg = MIMEMultipart()
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_TO
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    with smtplib.SMTP('smtp.office365.com', 587) as server:
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)
    print("✅ Email envoyé !")

# ----- Envoi du rapport -----
if SEND_EMAIL:
    week_number = today.isocalendar()[1]

    report_body = "Voici les sorties Spotify de la semaine :\n\n"
    for line in releases_list:
        report_body += f"{line}\n"

    if errors_list:
        report_body += "\nErreurs rencontrées :\n"
        for e in errors_list:
            report_body += f"{e}\n"

    send_email(f"Sorties de la Semaine - WK{week_number}", report_body)
