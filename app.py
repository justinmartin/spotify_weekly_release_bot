import os
import json
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from lyricsgenius import Genius

# Charger les variables d'environnement depuis .env à la racine ou secrets/.env en local
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(BASE_DIR, '.env')
if not os.path.exists(env_path):
    # Fallback local : chercher secrets/.env
    env_path = os.path.join(BASE_DIR, 'secrets', '.env')
load_dotenv(env_path)

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

# Genius API
genius = Genius(os.getenv("GENIUS_ACCESS_TOKEN"), verbose=False, remove_section_headers=True)


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

# Charger les classiques hip-hop (200 albums Rolling Stone)
CLASSICS_HIPHOP = []
classics_hiphop_path = os.path.join(BASE_DIR, "classics_hiphop.json")
if os.path.exists(classics_hiphop_path):
    with open(classics_hiphop_path, "r") as cf:
        CLASSICS_HIPHOP = json.load(cf)

# Charger les meilleurs sons du 21e siècle (Rolling Stone)
BEST_SONGS = []
best_songs_path = os.path.join(BASE_DIR, "best_songs_21st_century.json")
if os.path.exists(best_songs_path):
    with open(best_songs_path, "r") as bs:
        BEST_SONGS = json.load(bs)


# ----- Fonction pour récupérer les infos Genius sur un album -----
def get_album_genius_info(album_name, artist_name):
    """
    Récupère les informations contextuelles d'un album depuis Genius.
    Retourne un dictionnaire avec description, producteurs, faits marquants, etc.
    """
    import re
    
    def format_for_genius_url(text):
        """Formate un texte pour une URL Genius (ex: 'The Blueprint' -> 'The-blueprint')"""
        # Remplacer les caractères spéciaux
        text = re.sub(r'[^\w\s-]', '', text)
        # Remplacer les espaces par des tirets
        text = re.sub(r'\s+', '-', text)
        return text.capitalize()
    
    try:
        # Construire l'URL de l'album directement (format Genius standard)
        artist_formatted = format_for_genius_url(artist_name)
        album_formatted = format_for_genius_url(album_name)
        album_url = f"https://genius.com/albums/{artist_formatted}/{album_formatted}"
        
        # Rechercher une chanson de l'album pour avoir des informations contextuelles
        song = genius.search_song(album_name, artist_name)
        
        if not song:
            # Essayer avec juste le nom de l'artiste
            song = genius.search_song(artist_name, artist_name)
        
        # Construire les informations
        info = {
            'url': album_url,  # URL directe vers l'album sur Genius
            'description': '',
            'release_date': '',
            'facts': []
        }
        
        # Récupérer la description de l'artiste
        try:
            artist = genius.search_artist(artist_name, max_songs=0, get_full_info=True)
            if artist and artist.description:
                desc = artist.description.get('plain', '') if isinstance(artist.description, dict) else str(artist.description)
                info['description'] = desc.split('\n')[0] if desc else ''
        except:
            pass
        
        # Extraire des faits marquants depuis les annotations de la chanson
        if song and hasattr(song, 'description_annotation') and song.description_annotation:
            try:
                desc = song.description_annotation.get('annotations', [{}])[0].get('body', {}).get('plain', '')
                if desc and len(desc) > 50:
                    info['facts'].append(desc[:300] + "..." if len(desc) > 300 else desc)
            except:
                pass
        
        return info
    except Exception as e:
        print(f"Erreur Genius pour {album_name} - {artist_name}: {e}")
        return None


# ----- Fonction pour récupérer les infos Genius sur une chanson -----
def get_song_genius_info(song_name, artist_name):
    """
    Récupère les informations contextuelles d'une chanson depuis Genius.
    Retourne un dictionnaire avec description, annotations, et URL Genius.
    """
    try:
        # Rechercher la chanson sur Genius
        song = genius.search_song(song_name, artist_name)
        if not song:
            return None
        
        # Construire les informations
        info = {
            'url': song.url,
            'release_date': song.release_date if hasattr(song, 'release_date') else '',
        }
        
        # Extraire des faits marquants depuis les annotations
        facts = []
        if hasattr(song, 'description_annotation') and song.description_annotation:
            desc = song.description_annotation.get('annotations', [{}])[0].get('body', {}).get('plain', '')
            if desc and len(desc) > 50:
                facts.append(desc[:200] + "..." if len(desc) > 200 else desc)
        
        info['facts'] = facts
        
        return info
    except Exception as e:
        print(f"Erreur Genius pour {song_name} - {artist_name}: {e}")
        return None


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
        # Filtrer les épisodes de la semaine passée et trier par date (plus récent d'abord)
        week_episodes = []
        for ep in episodes.get('items', []):
            release_date = ep.get('release_date')
            if not release_date:
                continue
            if len(release_date) == 10:
                release_dt = datetime.strptime(release_date, "%Y-%m-%d")
            else:
                release_dt = datetime.strptime(release_date, "%Y")
            if release_dt >= last_week:
                week_episodes.append((release_dt, ep))
        
        # Prendre seulement le plus récent (limit 1 par podcast)
        if week_episodes:
            week_episodes.sort(key=lambda x: x[0], reverse=True)
            release_dt, ep = week_episodes[0]
            uri = ep.get('uri')
            # Ne pas ajouter à la playlist, seulement au mail
            # Stocker comme tuple (show_name, episode_name) pour pouvoir formater séparément
            podcast_releases.append((show_name, ep.get('name')))
    except Exception as e:
        errors_list.append(f"{show_name}: {str(e)}")
        print(f"⚠️ Erreur pour le show {show_name}: {e}")

# ----- Obtenir des recommandations basées sur les nouvelles sorties -----
recommendations = []
if new_tracks_set and len(new_tracks_set) > 0:
    try:
        # Prendre jusqu'à 5 tracks comme seeds pour les recommandations
        seed_tracks = list(new_tracks_set)[:min(5, len(new_tracks_set))]
        # Extraire juste l'ID depuis l'URI (format: spotify:track:ID)
        seed_track_ids = [uri.split(':')[-1] for uri in seed_tracks]
        
        recs = sp.recommendations(seed_tracks=seed_track_ids, limit=3)
        for track in recs['tracks']:
            artist_names = ', '.join([artist['name'] for artist in track['artists']])
            recommendations.append(f"{artist_names} - {track['name']}")
    except Exception as e:
        print(f"⚠️ Erreur lors de la récupération des recommandations: {e}")

# ----- Sélectionner 3 albums classiques hip-hop et 3 chansons aléatoires -----
classics_of_week = []
songs_of_week = []
classics_tracks = []  # URIs des tracks des albums classiques pour la playlist
songs_uris = []  # URIs des chansons du siècle pour la playlist

# Utiliser la semaine de l'année comme seed pour avoir les mêmes sélections toute la semaine
week_number = today.isocalendar()[1]
random.seed(week_number)

# Sélectionner 3 albums classiques aléatoires
if CLASSICS_HIPHOP and len(CLASSICS_HIPHOP) >= 3:
    selected_classics = random.sample(CLASSICS_HIPHOP, 3)
    for classic in selected_classics:
        try:
            album_info = sp.album(classic['id'])
            
            # Récupérer les infos Genius pour contexte enrichi
            genius_info = get_album_genius_info(classic['album'], classic['artist'])
            
            classics_of_week.append({
                'album': classic['album'],
                'artist': classic['artist'],
                'year': classic['year'],
                'url': album_info['external_urls']['spotify'],
                'genius_info': genius_info  # Ajout des infos Genius
            })
            # Ajouter toutes les tracks de l'album à la playlist
            for track in album_info['tracks']['items']:
                classics_tracks.append(track['uri'])
        except Exception as e:
            print(f"⚠️ Erreur lors de la récupération du classique {classic['album']}: {e}")

# Sélectionner 3 chansons aléatoires du 21e siècle
if BEST_SONGS and len(BEST_SONGS) >= 3:
    selected_songs = random.sample(BEST_SONGS, 3)
    for song in selected_songs:
        try:
            track_info = sp.track(song['id'])
            
            # Récupérer les infos Genius pour contexte enrichi
            genius_info = get_song_genius_info(song['song'], song['artist'])
            
            songs_of_week.append({
                'song': song['song'],
                'artist': song['artist'],
                'year': song['year'],
                'url': track_info['external_urls']['spotify'],
                'genius_info': genius_info  # Ajout des infos Genius
            })
            # Ajouter l'URI de la chanson pour la playlist
            songs_uris.append(track_info['uri'])
        except Exception as e:
            print(f"⚠️ Erreur lors de la récupération du son {song['song']}: {e}")

# ----- Créer playlist si nouvelles sorties -----
if new_tracks_set:
    playlist_name = f"HEBDO - {today.strftime('%d/%m')}"
    user_id = sp.me()['id']
    playlist = sp.user_playlist_create(user=user_id, name=playlist_name, public=False)
    
    # Ajouter les nouvelles sorties
    sp.playlist_add_items(playlist_id=playlist['id'], items=list(new_tracks_set))
    
    # Ajouter les tracks des 3 albums classiques
    if classics_tracks:
        sp.playlist_add_items(playlist_id=playlist['id'], items=classics_tracks)
    
    # Ajouter les 3 chansons du siècle
    if songs_uris:
        sp.playlist_add_items(playlist_id=playlist['id'], items=songs_uris)
    
    # Récupérer l'URL publique Spotify de la playlist pour l'inclure dans l'email
    if playlist.get('external_urls') and playlist['external_urls'].get('spotify'):
        playlist_url = playlist['external_urls']['spotify']
    else:
        # fallback vers l'URL construite depuis l'ID
        playlist_url = f"https://open.spotify.com/playlist/{playlist['id']}"

    total_tracks = len(new_tracks_set) + len(classics_tracks) + len(songs_uris)
    print(f"✅ Playlist '{playlist_name}' créée avec {total_tracks} titres ! ({playlist_url})")
    print(f"   - Nouvelles sorties: {len(new_tracks_set)}")
    print(f"   - Albums classiques: {len(classics_tracks)} tracks")
    print(f"   - Sons du siècle: {len(songs_uris)}")
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
        
        # Lien vers la playlist juste après les sorties musicales
        if playlist_url:
            text_body += f"\n🔗 Playlist de la semaine : {playlist_url}\n"
            html_body += f"<p style=\"margin-top:15px; padding:12px; background-color:#1DB954; border-radius:8px; text-align:center;\"><a href=\"{playlist_url}\" target=\"_blank\" style=\"text-decoration:none; color:white; font-weight:bold; font-size:1em;\">🎧 Écouter la playlist de la semaine</a></p>"

    # Section Podcasts
    if podcast_releases:
        text_body += "\n-- Podcasts --\n"
        html_body += "<h4>🎧 Podcasts</h4><ul>"
        for show_name, episode_name in podcast_releases:
            text_body += f"{show_name} - {episode_name}\n"
            safe_show = show_name.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            safe_episode = episode_name.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html_body += f"<li><strong>{safe_show}</strong> - {safe_episode}</li>"
        html_body += "</ul>"

    # Section Découvertes (Recommandations)
    if recommendations:
        text_body += "\n-- Découvertes --\n"
        html_body += "<h4>🔍 Découvertes</h4><p style=\"font-size:0.9em; color:#666;\">3 morceaux que tu pourrais aimer basés sur tes nouvelles sorties :</p><ul>"
        for line in recommendations:
            text_body += f"{line}\n"
            safe_line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html_body += f"<li>{safe_line}</li>"
        html_body += "</ul>"

    # Section Classiques hip-hop de la semaine
    if classics_of_week:
        text_body += f"\n-- Les Classiques Hip-Hop de la semaine --\n"
        text_body += "3 albums incontournables du classement Rolling Stone des 200 meilleurs albums hip-hop :\n\n"
        
        html_body += f"<h4>📀 Les Classiques Hip-Hop de la semaine</h4>"
        html_body += "<p style=\"font-size:0.9em; color:#666;\">3 Classiques du Hip-Hop à (ré)écouter (Rolling Stone) :</p>"
        
        for classic in classics_of_week:
            # Version texte
            text_body += f"• {classic['artist']} - {classic['album']} ({classic['year']})\n"
            text_body += f"  {classic['url']}\n"
            
            # Ajouter infos Genius si disponibles
            if classic.get('genius_info'):
                ginfo = classic['genius_info']
                if ginfo.get('description'):
                    text_body += f"  À propos : {ginfo['description'][:200]}...\n"
                if ginfo.get('facts'):
                    text_body += f"  💡 Le saviez-vous ? {ginfo['facts'][0][:200]}...\n"
            text_body += "\n"
            
            # Version HTML enrichie
            html_body += f"<div style=\"margin-bottom:20px; padding:15px; background-color:#f8f8f8; border-left:4px solid #1DB954;\">"
            html_body += f"<strong style=\"font-size:1.1em;\">{classic['artist']}</strong> - <em>{classic['album']}</em> ({classic['year']})<br>"
            html_body += f"<a href=\"{classic['url']}\" target=\"_blank\" style=\"text-decoration:none; color:#1DB954; font-size:0.9em;\">🎵 Spotify</a>"
            
            # Ajouter les infos Genius en HTML
            if classic.get('genius_info'):
                ginfo = classic['genius_info']
                
                if ginfo.get('description'):
                    html_body += f"<p style=\"margin-top:10px; font-size:0.9em; color:#555;\"><strong>À propos de l'artiste :</strong><br>{ginfo['description'][:250]}...</p>"
                
                if ginfo.get('release_date'):
                    html_body += f"<p style=\"margin-top:5px; font-size:0.85em; color:#777;\">📅 Sorti le : {ginfo['release_date']}</p>"
                
                if ginfo.get('facts') and len(ginfo['facts']) > 0:
                    html_body += f"<p style=\"margin-top:10px; padding:10px; background-color:#fff; border-left:3px solid #FFD700; font-size:0.9em; color:#333;\">"
                    html_body += f"<strong>💡 Le saviez-vous ?</strong><br>{ginfo['facts'][0][:300]}..."
                    html_body += f"</p>"
                
                if ginfo.get('url'):
                    html_body += f"<p style=\"margin-top:5px;\"><a href=\"{ginfo['url']}\" target=\"_blank\" style=\"text-decoration:none; color:#FFD700; font-weight:bold; font-size:0.85em;\">💡Genius</a></p>"
            
            html_body += f"</div>"


    # Section Meilleurs sons du 21e siècle
    if songs_of_week:
        text_body += f"\n-- Les Sons du Siècle --\n"
        text_body += "3 morceaux parmi les meilleurs du 21e siècle (Rolling Stone) :\n\n"
        
        html_body += f"<h4>🎵 Les Sons du Siècle</h4>"
        html_body += "<p style=\"font-size:0.9em; color:#666;\">3 morceaux parmi les meilleurs du 21e siècle (Rolling Stone) :</p>"
        
        for song in songs_of_week:
            text_body += f"• {song['artist']} - {song['song']} ({song['year']})\n"
            text_body += f"  {song['url']}\n"
            
            # Ajouter infos Genius en texte
            if song.get('genius_info'):
                ginfo = song['genius_info']
                if ginfo.get('facts'):
                    text_body += f"  💡 {ginfo['facts'][0][:150]}...\n"
                if ginfo.get('url'):
                    text_body += f"  📖 Genius: {ginfo['url']}\n"
            text_body += "\n"
            
            # Version HTML enrichie
            html_body += f"<div style=\"margin-bottom:15px; padding:12px; background-color:#f8f8f8; border-left:4px solid #1DB954;\">"
            html_body += f"<strong style=\"font-size:1.05em;\">{song['artist']}</strong> - <em>{song['song']}</em> ({song['year']})<br>"
            html_body += f"<a href=\"{song['url']}\" target=\"_blank\" style=\"text-decoration:none; color:#1DB954; font-size:0.9em;\">🎵Spotify</a>"
            
            # Ajouter les infos Genius en HTML
            if song.get('genius_info'):
                ginfo = song['genius_info']
                
                if ginfo.get('facts') and len(ginfo['facts']) > 0:
                    html_body += f"<p style=\"margin-top:10px; padding:8px; background-color:#fff; border-left:3px solid #FFD700; font-size:0.85em; color:#333;\">"
                    html_body += f"<strong>💡 Le saviez-vous ?</strong><br>{ginfo['facts'][0][:200]}..."
                    html_body += f"</p>"
                
                if ginfo.get('url'):
                    html_body += f"<p style=\"margin-top:5px;\"><a href=\"{ginfo['url']}\" target=\"_blank\" style=\"text-decoration:none; color:#FFD700; font-weight:bold; font-size:0.85em;\">💡Genius</a></p>"
            
            html_body += f"</div>"

    if errors_list:
        text_body += "\nErreurs rencontrées :\n"
        html_body += "<h3>Erreurs rencontrées :</h3><ul>"
        for e in errors_list:
            text_body += f"{e}\n"
            safe_e = e.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html_body += f"<li>{safe_e}</li>"
        html_body += "</ul>"

    html_body += "</body></html>"

    send_email(f" 🎶 Sorties de la Semaine - WK{week_number}", text_body, html_body)
