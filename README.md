# Spotify Weekly Release Bot

**Spotify Weekly Release Bot** est un outil d’automatisation en Python qui détecte toutes les **nouvelles sorties (albums, singles, featurings)** d’une liste de vos artistes préférés, les compile dans une playlist et vous envoie un **résumé par e-mail** listant toutes les sorties de la semaine.

Ce projet est conçu pour les amateurs de musique qui veulent écouter chaque semaine les nouveautés de leurs artistes favoris.

---

## Fonctionnalités

| Fonctionnalité | Description |
|----------------|------------|
| **Création automatique de playlists Spotify** | Chaque semaine, une nouvelle playlist est créée |
| **Suivi des artistes via `artists.json`** | Définissez la liste des artistes à suivre en ajoutant leurs IDs Spotify dans le fichier |
| **Gestion des doublons pour les collaborations** | Si deux artistes de votre liste sortent une collaboration, le morceau n’apparaît qu’une seule fois |
| **Résumé par e-mail** | Envoie un e-mail chaque vendredi listant toutes les nouvelles sorties (et les erreurs éventuelles) |
| **Rapport des erreurs** | Si une requête échoue ou si un ID artiste est invalide, cela est inclus dans le rapport envoyé |
| **Exécution hebdomadaire automatique** | Automatisé via GitHub Actions chaque vendredi matin |

---

## Fonctionnement

1. **Chargement des variables d’environnement**  
   Le bot récupère vos identifiants Spotify (depuis `.env` ou GitHub secrets).

2. **Lecture de votre liste d’artistes** (`artists.json`)  
   Exemple :
   ```json
   [
    { "artist": "Fred Again..",      "id": "4oLeXFyACqeem2VImYeBFe" },
    { "artist": "Future",            "id": "1RyvyyTE3xzB2ZywiAwp0i" },
    { "artist": "Don Toliver",       "id": "4Gso3d4CscCijv0lmajZWs" },
    { "artist": "Ken Carson",        "id": "3gBZUcNeVumkeeJ19CY2sX" },
    { "artist": "Drake",             "id": "3TVXtAsR1Inumwj472S9r4" },
    { "artist": "Playboi Carti",     "id": "699OTQXzgjhIYAHMy9RyPD" },
    { "artist": "La Fève",           "id": "2sBKOwN0fSjx39VtL2WpjJ" },
    { "artist": "Tyler, the Creator", "id": "4V8LLVI7PbaPR0K2TGSxFF" },
    { "artist": "Destroy Lonely",    "id": "1HPW4jeRjXBFRoUnSvBzoD" },
    { "artist": "Jay-Z",             "id": "3nFkdlSjzX9mRTtwJOzDYB" },
    { "artist": "Westside Gunn",     "id": "0ABk515kENDyATUdpCKVfW" },
    { "artist": "Young Thug",        "id": "50co4Is1HCEo8bhOyUWKpn" },
    { "artist": "A$AP Rocky",        "id": "13ubrt8QOOCPljQ2FL1Kca" },
    { "artist": "Paul Kalkbrenner",  "id": "0rasA5Z5h1ITtHelCpfu9R" },
    { "artist": "The Weeknd",        "id": "1Xyo4u8uXC1ZmMpatF05PJ" },
    { "artist": "Travis Scott",      "id": "0Y5tJX1MQlPlqiwlOH1tJY" }
   ]

   
3. **Vérification des nouvelles sorties**  
Le bot interroge l’API Spotify pour chaque artiste de `artists.json` et récupère ses albums, singles et EPs récents (maximum 50 par artiste). Pour chaque sortie, il vérifie la date de publication et ne conserve que celles publiées au cours des 7 derniers jours.

4. **Filtrage et normalisation des morceaux**  
Pour chaque sortie retenue, le bot récupère les données et construit une représentation lisible :
- Pour les singles ou collaborations : `Artiste principal ft. Artiste secondaire - Titre`
- Pour les albums/EP : `Artiste - Titre [Album/EP]`

Toutes les informations sont stockées avec leurs **URIs Spotify** pour ajoutés à la playlist. Toutes ces URIs collectées sont ajoutées dans un set et dédupliqués pour garantir l’unicité.

7. **Création de la playlist hebdomadaire (HEBDO)**  
Si de nouvelles pistes sont détectées, le bot crée une playlist nommée `HEBDO - JJ/MM` (date du jour) sur ton compte Spotify et y ajoute toutes les URIs uniques.

8. **Organisation / dossier HEBDO**  
Spotify ne permet pas la création de vrais dossiers via son API publique. Pour regrouper les playlists, le bot **préfixe** chaque playlist par `HEBDO -`. Sur l’application Spotify (desktop ou mobile), les playlists apparaîtront proches les unes des autres et pourront être rangées dans un dossier manuellement si souhaité.

9. **Rapport & notifications par e-mail**  
À la fin du processus, un rapport est généré contenant :
- La liste des nouvelles sorties par artiste
- Les erreurs éventuelles (ID artiste invalide, API timeout, etc.)

Ce rapport est envoyé automatiquement par e-mail (Outlook / SMTP) avec pour objet : `Sorties de la semaine - WK__`.
Le corps du mail contient le listing des titres détectés et la section `Èrreurs rencontrées`.
