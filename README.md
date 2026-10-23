# Spotify Weekly Release Bot

Chaque vendredi matin, le bot repère les **nouvelles sorties** (albums, singles, featurings) des artistes de `artists.json`, les range dans une **playlist Spotify** et envoie un **mail récap** avec pochettes et liens.

## Ce que fait le bot chaque vendredi

- **Sorties de la semaine** (7 derniers jours) des artistes suivis, **featurings compris** : les morceaux d'autres artistes où ils sont invités.
- **Playlist** `HEBDO - JJ/MM`, ou une seule playlist `HEBDO` vidée puis remplie chaque semaine si `ROLLING_PLAYLIST=true`.
- **Suivi automatique** : tous les artistes de `artists.json` sont suivis sur le compte Spotify.
- **Classiques hip-hop et sons du siècle** : 3 + 3 par semaine, **sans répétition** jusqu'à avoir fait le tour des listes (rotation déterministe, sans fichier d'état), avec une description Genius.
- **Podcasts** de `podcasts.json` : les épisodes de la semaine.
- **Best of mensuel** : le 1er vendredi du mois, playlist `BEST OF - <mois>` des sorties du mois précédent classées par popularité, avec le top 10 dans le mail.
- **Mail** compatible mobile : pochettes, badges (Single / Album / Deluxe / Feat), liens vers chaque sortie et épisode, erreurs éventuelles en bas.
- **Alerte par mail** si le run échoue, avec la marche à suivre quand le refresh token Spotify a expiré.

### Filtrage

- Les **rééditions** (remaster, anniversary, expanded…) sont ignorées.
- Les **vieux titres republiés** sont ignorés : l'ISRC donne l'année d'enregistrement (ex. un titre de 2002 ressorti en single).
- Les **éditions deluxe** sont signalées dans le mail mais pas ajoutées à la playlist.
- Les versions **Radio Edit / Sped Up / Remastered** sont regroupées avec l'original, et une collab entre deux artistes suivis n'apparaît qu'une fois.

## Utilisation

```bash
pip install -r requirements.txt
python app.py --dry-run   # aperçu sans playlist ni mail : out/email_<date>.html
python app.py             # run complet
python app.py --best-of   # force le best of du mois précédent
```

Depuis GitHub : **Actions → Spotify Weekly Release Bot → Run workflow** (case « Dry run » disponible).

Le workflow (`.github/workflows/spotify-weekly.yml`) tourne le vendredi (`6 6 * * FRI`, en UTC). GitHub lance les tâches planifiées avec du retard, si bien que le mail arrive en pratique en fin de matinée. Un job `keepalive` empêche GitHub de désactiver le workflow après 60 jours sans commit.

## Configuration

| Où | Nom | Rôle |
|---|---|---|
| Secret | `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI` | Application Spotify |
| Secret | `SPOTIPY_REFRESH_TOKEN` | Accès au compte (voir plus bas pour le renouveler) |
| Secret | `EMAIL_USER`, `EMAIL_PASSWORD`, `EMAIL_TO` | Envoi Gmail (mot de passe d'application) |
| Secret | `GENIUS_ACCESS_TOKEN` | Facultatif : descriptions Genius ([genius.com/api-clients](https://genius.com/api-clients)) |
| Variable | `ROLLING_PLAYLIST` | `true` : une seule playlist `HEBDO` réutilisée |

En local, les mêmes valeurs vont dans `secrets/.env` (gitignoré).

Les listes se modifient directement dans les fichiers JSON : `artists.json` (artistes suivis, avec leur id Spotify), `podcasts.json`, `classics_hiphop.json` et `best_songs_21st_century.json`.

## Renouveler le token Spotify

Si le mail d'alerte annonce un refresh token expiré :

```bash
python generate_refresh_token.py
```

Connecte-toi dans le navigateur, puis colle le token affiché dans le secret GitHub `SPOTIPY_REFRESH_TOKEN`.

## Organisation du code

```
app.py              point d'entrée : run() enchaîne les étapes, main() gère les options et l'alerte
bot/
├── config.py       chemins, chargement du .env, portée Spotify, filtres de titres
├── spotify.py      connexion, suivi, sorties et featurings, podcasts, playlists, best of
├── picks.py        rotation hebdo sans répétition (classiques, sons du siècle)
├── genius.py       bios et descriptions Genius
└── mail.py         mail HTML, envoi SMTP, alerte d'échec
generate_refresh_token.py   renouvellement du token Spotify
```
