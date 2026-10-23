#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rap Univers 2 — détection de sorties rap FR et publication Telegram.

Fonctions :
  1. Essaie MinutePunchline pour découvrir les albums Deezer.
  2. Si MinutePunchline répond notamment 403, utilise le classement Deezer
     comme solution de secours (couverture moins complète), puis l'historique.
  3. Lit les métadonnées officielles d'un album via l'API Deezer.
  4. Publie la pochette et les informations de sortie sur Telegram.
  5. Peut recevoir des fichiers audio directement dans la conversation privée
     du bot, mais uniquement depuis les comptes Telegram autorisés dans
     AUTHORIZED_SENDER_IDS. Le fichier doit porter une légende avec
     "album_id: 123456789" (identifiant d'album Deezer).
  6. Envoie les fichiers autorisés associés à un album récent, puis garde
     les identifiants des fichiers envoyés pour éviter les doublons.

Secrets / variables GitHub Actions :
  TELEGRAM_BOT_TOKEN      token du bot (secret, ne jamais le publier)
  TELEGRAM_CHANNEL_ID     chaîne cible, par exemple @frrapfrancais03
  AUTHORIZED_SENDER_IDS   IDs Telegram autorisés, séparés par des virgules
                          (exemple de format : 123456789,987654321)
  DRY_RUN                 true = simulation, aucun message envoyé et aucun
                          nouvel update Telegram consommé

Important : n'ajouter que des fichiers pour lesquels l'artiste, le label ou le
  distributeur a donné l'autorisation de diffusion sur la chaîne.
"""

import json
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ─── Configuration ───────────────────────────────────────────────────────
SOURCE_URLS = [
    "https://minutepunchline.com/sorties/",
    f"https://minutepunchline.com/sorties/{datetime.now(ZoneInfo('Africa/Lubumbashi')).year}/",
]
DEEZER_API = "https://api.deezer.com/album/"
DEEZER_SEARCH_ALBUM_API = "https://api.deezer.com/search/album"
DEEZER_CHART_ALBUMS_API = "https://api.deezer.com/chart/0/albums?limit=100"
TELEGRAM_API = "https://api.telegram.org"
STATE_FILE = Path("posted.json")
LOCAL_TZ = ZoneInfo("Africa/Lubumbashi")
MAX_POSTS_PER_RUN = 5
MAX_AUDIO_PER_RUN = 10
MAX_CAPTION_LENGTH = 1024
RELEASE_LOOKBACK_DAYS = 3
DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() == "true"

# Les ID d'expéditeur doivent être ajoutés dans le secret GitHub Actions
# AUTHORIZED_SENDER_IDS. Aucun compte n'est autorisé par défaut.
def get_authorized_sender_ids():
    raw = os.getenv("AUTHORIZED_SENDER_IDS", "").strip()
    ids = set()
    for item in re.split(r"[,;\s]+", raw):
        if item.isdigit():
            ids.add(int(item))
    return ids


HEADERS = {
    "User-Agent": (
        "RapUniversBot/1.0 (+https://github.com/freezecorleone051-crypto/"
        "rap-univers-automatisation) Python requests"
    )
}
session = requests.Session()
session.headers.update(HEADERS)


def empty_state():
    return {
        "initialized": False,
        "albums": {},
        "posted_ids": [],
        "audio_files": [],
        "posted_audio_ids": [],
        "update_offset": 0,
    }


# ─── Historique ──────────────────────────────────────────────────────────
def load_state():
    if not STATE_FILE.exists():
        return empty_state()
    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        defaults = empty_state()
        for key, default in defaults.items():
            data.setdefault(key, default)
        # Garantir les types attendus si un ancien fichier d'état est utilisé.
        if not isinstance(data.get("albums"), dict):
            data["albums"] = {}
        for key in ("posted_ids", "audio_files", "posted_audio_ids"):
            if not isinstance(data.get(key), list):
                data[key] = []
        try:
            data["update_offset"] = int(data.get("update_offset", 0))
        except (TypeError, ValueError):
            data["update_offset"] = 0
        return data
    except (OSError, json.JSONDecodeError) as error:
        # Ne pas écraser silencieusement un historique illisible : l'utilisateur
        # doit pouvoir voir le problème au lieu de risquer des doublons.
        raise RuntimeError(f"Impossible de lire {STATE_FILE}: {error}") from error


def save_state(state):
    temporary = STATE_FILE.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
    temporary.replace(STATE_FILE)


# ─── Découverte des albums ────────────────────────────────────────────────
REGEX_ALBUM_DEEZER = re.compile(r"deezer\.com/(?:[a-z]{2}/)?album/(\d+)", re.I)


def parse_album_ids_from_html(html):
    soup = BeautifulSoup(html, "html.parser")
    album_ids = []
    for link in soup.select('a[href*="deezer.com"]'):
        match = REGEX_ALBUM_DEEZER.search(link.get("href", ""))
        if match and match.group(1) not in album_ids:
            album_ids.append(match.group(1))
    return album_ids


def get_deezer_chart_album_ids():
    """Solution de secours : le classement Deezer n'est pas un flux exhaustif
    des nouveautés, mais permet au workflow de continuer si le site source bloque
    les runners GitHub Actions.
    """
    print("Essai du classement Deezer comme source de secours...")
    response = session.get(DEEZER_CHART_ALBUMS_API, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"Erreur API Deezer : {payload['error']}")
    ids = []
    for item in payload.get("data", []):
        album_id = item.get("id")
        if album_id is not None and str(album_id) not in ids:
            ids.append(str(album_id))
    return ids



def get_album_ids():
    """Utilise uniquement les sources consacrées
    aux sorties rap françaises.
    En cas d'échec, ne publie rien.
    """
    found_ids = []
    errors = []

    for source_url in SOURCE_URLS:
        print(f"Lecture de la source : {source_url}")

        try:
            response = session.get(source_url, timeout=25)
            response.raise_for_status()

            ids = parse_album_ids_from_html(response.text)

            for album_id in ids:
                if album_id not in found_ids:
                    found_ids.append(album_id)

            print(f"{len(ids)} albums trouvés sur cette source.")

        except (requests.RequestException, ValueError) as error:
            errors.append(f"{source_url}: {error}")
            print(f"Source indisponible : {error}")

    if not found_ids:
        raise RuntimeError(
            "Aucune sortie rap trouvée dans les sources "
            "autorisées. Publication annulée pour éviter "
            "les albums hors sujet. "
            + " | ".join(errors)
        )

    print(f"{len(found_ids)} albums trouvés au total.")
    return found_ids


def get_album(album_id):
    """Récupère les métadonnées officielles d'un album via l'API Deezer."""
    url = f"{DEEZER_API}{album_id}"
    try:
        response = session.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            print(f"Deezer a refusé l'album {album_id} : {data['error']}")
            return None

        artist = data.get("artist") or {}
        release_date = data.get("release_date")
        official_link = data.get("link")
        if not data.get("title") or not artist.get("name"):
            print(f"Métadonnées incomplètes pour l'album {album_id}.")
            return None
        if not release_date or not official_link:
            print(f"Date ou lien officiel manquant pour l'album {album_id}.")
            return None

        return {
            "id": str(album_id),
            "title": str(data["title"]),
            "artist": str(artist["name"]),
            "type": str(data.get("record_type") or "sortie").upper(),
            "date": str(release_date),
            "tracks": data.get("nb_tracks", "?"),
            "cover": data.get("cover_xl") or data.get("cover_big"),
            "link": str(official_link),
        }
    except (requests.RequestException, ValueError) as error:
        print(f"Échec pour l'album {album_id} : {error}")
        return None




def get_album_by_artist_title(artist_name, album_title):
    """Recherche un album Deezer par artiste et titre, puis vérifie une égalité exacte.

    Retourne None si la recherche est ambiguë ou si aucun résultat exact n'existe.
    """
    artist_key = normalize_match_text(artist_name)
    title_key = normalize_match_text(album_title)
    if not artist_key or not title_key:
        return None
    try:
        response = session.get(
            DEEZER_SEARCH_ALBUM_API,
            params={"q": f"{artist_name} {album_title}", "limit": 25},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            print(f"Recherche Deezer refusée : {payload['error']}")
            return None
        exact_ids = []
        for result in payload.get("data", []):
            result_artist = (result.get("artist") or {}).get("name", "")
            if (
                normalize_match_text(result.get("title")) == title_key
                and normalize_match_text(result_artist) == artist_key
                and result.get("id") is not None
            ):
                result_id = str(result["id"])
                if result_id not in exact_ids:
                    exact_ids.append(result_id)
        if len(exact_ids) != 1:
            if len(exact_ids) > 1:
                print(f"Recherche ambiguë pour {artist_name} - {album_title}; fichier en attente.")
            return None
        return get_album(exact_ids[0])
    except (requests.RequestException, ValueError) as error:
        print(f"Recherche Deezer impossible pour {artist_name} - {album_title} : {error}")
        return None


# ─── Entrée des fichiers audio autorisés ───────────────────────────────────
ALBUM_ID_IN_CAPTION = re.compile(
    r"(?:album_id|deezer_id)\s*[:=]\s*(\d+)", re.IGNORECASE
)
ARTIST_IN_CAPTION = re.compile(r"^\s*(?:artiste|artist)\s*[:=]\s*(.+?)\s*$", re.I | re.M)
PROJECT_IN_CAPTION = re.compile(
    r"^\s*(?:projet|album|titre_album)\s*[:=]\s*(.+?)\s*$", re.I | re.M
)
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus"}


def normalize_match_text(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def parse_audio_reference(caption):
    """Retourne soit un album_id Deezer, soit le couple artiste/projet."""
    match = ALBUM_ID_IN_CAPTION.search(caption or "")
    if match:
        return {"album_id": match.group(1), "match_artist": "", "match_title": ""}
    artist_match = ARTIST_IN_CAPTION.search(caption or "")
    project_match = PROJECT_IN_CAPTION.search(caption or "")
    if artist_match and project_match:
        return {
            "album_id": "",
            "match_artist": artist_match.group(1).strip(),
            "match_title": project_match.group(1).strip(),
        }
    return None


def find_album_for_audio(audio_item, albums):
    album_id = str(audio_item.get("album_id") or "")
    if album_id and album_id in albums:
        return albums[album_id]
    wanted_artist = normalize_match_text(audio_item.get("match_artist"))
    wanted_title = normalize_match_text(audio_item.get("match_title"))
    if not wanted_artist or not wanted_title:
        return None
    matches = [
        album for album in albums.values()
        if normalize_match_text(album.get("artist")) == wanted_artist
        and normalize_match_text(album.get("title")) == wanted_title
    ]
    # Éviter de publier le mauvais projet si les métadonnées ne sont pas uniques.
    return matches[0] if len(matches) == 1 else None


def is_audio_message(message):
    if isinstance(message.get("audio"), dict):
        return "audio", message["audio"]
    document = message.get("document")
    if isinstance(document, dict):
        mime = str(document.get("mime_type") or "").lower()
        filename = str(document.get("file_name") or "").lower()
        extension = Path(filename).suffix
        if mime.startswith("audio/") or extension in AUDIO_EXTENSIONS:
            return "document", document
    return None, None


def telegram_api_call(method, token, *, http_method="POST", **kwargs):
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    if http_method.upper() == "GET":
        response = requests.get(url, params=kwargs, timeout=30)
    else:
        response = requests.post(url, data=kwargs, timeout=30)
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Erreur Telegram"))
    return result["result"]


def notify_sender(token, sender_id, text):
    try:
        telegram_api_call("sendMessage", token, chat_id=str(sender_id), text=text)
    except (requests.RequestException, RuntimeError) as error:
        print(f"Impossible d'envoyer l'accusé de réception à {sender_id} : {error}")


def ingest_audio_updates(token, state, authorized_ids, *, acknowledge=True):
    """Lit les fichiers envoyés au bot, en n'acceptant que les expéditeurs autorisés.

    On ne consomme pas les updates si aucun expéditeur autorisé n'est configuré.
    Elle peut aussi être appelée en DRY_RUN : les fichiers restent en attente sans publication.
    """
    if not authorized_ids:
        print(
            "Réception audio désactivée : configure le secret AUTHORIZED_SENDER_IDS "
            "avec les ID Telegram des artistes/distributeurs autorisés."
        )
        return

    params = {
        "timeout": 0,
        "allowed_updates": json.dumps(["message"]),
    }
    if state.get("update_offset", 0) > 0:
        params["offset"] = int(state["update_offset"]) + 1

    try:
        updates = telegram_api_call(
            "getUpdates", token, http_method="GET", **params
        )
    except (requests.RequestException, RuntimeError) as error:
        print(
            "Avertissement : impossible de lire les fichiers reçus par Telegram. "
            "Vérifie que le bot n'utilise pas déjà un webhook. Détail :", error
        )
        return

    if not updates:
        print("Aucun nouveau message entrant pour le bot.")
        return

    known_unique_ids = {
        item.get("file_unique_id") for item in state.get("audio_files", [])
        if item.get("file_unique_id")
    }

    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            state["update_offset"] = max(int(state.get("update_offset", 0)), update_id)

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            # Les fichiers doivent être envoyés en conversation privée avec le bot.
            continue
        sender = message.get("from") or {}
        sender_id = sender.get("id")
        if not isinstance(sender_id, int) or sender_id not in authorized_ids:
            if sender_id is not None:
                print(f"Message ignoré : expéditeur Telegram non autorisé ({sender_id}).")
            continue

        media_method, media = is_audio_message(message)
        if not media:
            continue

        caption = str(message.get("caption") or "")
        reference = parse_audio_reference(caption)
        if not reference:
            if acknowledge:
                notify_sender(
                    token,
                    sender_id,
                    "Fichier reçu, mais non associé à une sortie. Ajoute soit "
                    "`album_id: 123456789`, soit deux lignes dans la légende : "
                    "`Artiste: NOM` et `Projet: TITRE EXACT`. Le fichier ne sera "
                    "publié qu'après vérification des métadonnées et des autorisations.",
                )
            print(f"Fichier audio de {sender_id} ignoré : référence absente dans la légende.")
            continue

        album_id = reference.get("album_id", "")
        file_unique_id = media.get("file_unique_id")
        if file_unique_id and file_unique_id in known_unique_ids:
            print(f"Fichier déjà reçu, doublon ignoré : {file_unique_id}")
            continue

        item = {
            "album_id": album_id,
            "match_artist": reference.get("match_artist", ""),
            "match_title": reference.get("match_title", ""),
            "file_id": media.get("file_id"),
            "file_unique_id": file_unique_id,
            "method": "sendAudio" if media_method == "audio" else "sendDocument",
            "filename": media.get("file_name") or media.get("title") or "fichier_audio",
            "track_title": media.get("title") or Path(str(media.get("file_name") or "")).stem,
            "performer": media.get("performer") or "",
            "received_at": datetime.now(LOCAL_TZ).isoformat(),
            "published": False,
        }
        if not item["file_id"]:
            print(f"Fichier sans file_id valide : album {album_id}, ignoré.")
            continue

        state.setdefault("audio_files", []).append(item)
        if file_unique_id:
            known_unique_ids.add(file_unique_id)

        # Récupérer les métadonnées même si la page source est bloquée.
        if album_id and album_id not in state.get("albums", {}):
            album = get_album(album_id)
            if album:
                state.setdefault("albums", {})[album_id] = album
        elif not album_id:
            album = get_album_by_artist_title(item["match_artist"], item["match_title"])
            if album:
                album_id = str(album["id"])
                item["album_id"] = album_id
                state.setdefault("albums", {})[album_id] = album

        if acknowledge:
            notify_sender(
                token,
                sender_id,
                (
                    f"Fichier reçu pour l'album Deezer {album_id}. "
                    if album_id else "Fichier reçu avec la référence artiste/projet. "
                )
                + "Il sera publié uniquement si les métadonnées correspondent à une sortie récente. "
                "Merci de ne "
                "transmettre que des fichiers autorisés pour diffusion sur Rap Univers.",
            )
        print(
            f"Fichier audio reçu : album {album_id or item['match_artist'] + ' / ' + item['match_title']}, "
            f"nom {item['filename']}."
        )


# ─── Publication Telegram ────────────────────────────────────────────────
def build_caption(album):
    text = (
        "🚨 NOUVELLE SORTIE RAP FR 🇫🇷\n\n"
        f"🎤 Artiste : {album['artist']}\n"
        f"💿 Projet : {album['title']}\n"
        f"📀 Type : {album['type']}\n"
        f"📅 Sortie : {album['date']}\n"
        f"🎵 Nombre de titres : {album['tracks']}\n\n"
        f"🎧 Écouter officiellement : {album['link']}\n\n"
        "📲 Rap Univers 2 • @frrapfrancais03"
    )
    return text[:MAX_CAPTION_LENGTH]


def send_album(token, channel, album):
    """Envoie la pochette avec la légende ; repli sur le texte seul."""
    caption = build_caption(album)
    if album.get("cover"):
        try:
            telegram_api_call(
                "sendPhoto", token, chat_id=channel,
                photo=album["cover"], caption=caption,
            )
            return
        except (requests.RequestException, RuntimeError) as error:
            print(f"Envoi de la pochette impossible : {error}")
            print("Tentative d'envoi du texte seul.")
    telegram_api_call("sendMessage", token, chat_id=channel, text=caption)


def build_audio_caption(album, audio_item):
    track = str(audio_item.get("track_title") or audio_item.get("filename") or "Audio")
    text = (
        "🎧 FICHIER AUDIO OFFICIEL\n\n"
        f"🎤 Artiste : {audio_item.get('performer') or album['artist']}\n"
        f"🎵 Titre : {track}\n"
        f"💿 Projet : {album['title']}\n"
        f"🔗 Écoute officielle : {album['link']}\n\n"
        "📲 Rap Univers 2 • @frrapfrancais03"
    )
    return text[:MAX_CAPTION_LENGTH]


def send_audio_file(token, channel, album, audio_item):
    if not audio_item.get("file_id"):
        raise RuntimeError("Fichier audio sans file_id Telegram.")
    kwargs = {
        "chat_id": channel,
        "audio" if audio_item.get("method") == "sendAudio" else "document": audio_item["file_id"],
        "caption": build_audio_caption(album, audio_item),
    }
    if audio_item.get("method") == "sendAudio":
        title = str(audio_item.get("track_title") or "")[:64]
        performer = str(audio_item.get("performer") or album.get("artist") or "")[:64]
        if title:
            kwargs["title"] = title
        if performer:
            kwargs["performer"] = performer
        method = "sendAudio"
    else:
        method = "sendDocument"
    telegram_api_call(method, token, **kwargs)


def release_date_is_recent(album, today):
    try:
        release_date = date.fromisoformat(str(album["date"]))
    except (ValueError, KeyError, TypeError):
        return False
    earliest = today - timedelta(days=RELEASE_LOOKBACK_DAYS)
    return earliest <= release_date <= today


# ─── Programme principal ─────────────────────────────────────────────────
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    channel = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
    if not token:
        raise RuntimeError("Le secret TELEGRAM_BOT_TOKEN est absent ou vide.")
    if not channel:
        raise RuntimeError("Le secret TELEGRAM_CHANNEL_ID est absent ou vide.")

    today = datetime.now(LOCAL_TZ).date()
    state = load_state()
    bootstrap = not state["initialized"]
    albums = state["albums"]
    posted_ids = set(str(value) for value in state["posted_ids"])
    posted_audio_ids = set(str(value) for value in state["posted_audio_ids"])

    # Lire les fichiers entrants même en DRY_RUN, si une liste d'expéditeurs
    # autorisés est configurée. Le mode test n'envoie aucun accusé de réception
    # et n'en publie aucun dans la chaîne, mais mémorise la file d'attente.
    ingest_audio_updates(
        token, state, get_authorized_sender_ids(), acknowledge=not DRY_RUN
    )
    albums = state["albums"]

    for audio_item in state.get("audio_files", []):
        if audio_item.get("album_id"):
            continue
        artist_name = str(audio_item.get("match_artist") or "").strip()
        album_title = str(audio_item.get("match_title") or "").strip()
        if artist_name and album_title:
            matched_album = get_album_by_artist_title(artist_name, album_title)
            if matched_album:
                matched_id = str(matched_album["id"])
                audio_item["album_id"] = matched_id
                albums[matched_id] = matched_album
            time.sleep(0.15)

    pending_audio_album_ids = [
        str(item.get("album_id"))
        for item in state.get("audio_files", [])
        if item.get("album_id")
    ]
    ids = get_album_ids()

    # Ne consulter Deezer que pour les ID inconnus.
    for album_id in ids:
        album_id = str(album_id)
        if album_id not in albums:
            album = get_album(album_id)
            if album:
                albums[album_id] = album
            time.sleep(0.15)

    # Au premier lancement, ne pas inonder la chaîne avec les anciennes sorties.
    if bootstrap:
        for album in albums.values():
            try:
                if date.fromisoformat(album["date"]) < today:
                    album["ignored"] = True
            except (ValueError, KeyError, TypeError):
                album["ignored"] = True

    
      candidates = []
      
      for album_id in ids:
          album = albums.get(str(album_id))
      
          if not album:
              continue
      
          if str(album_id) in posted_ids or album.get("ignored"):
              continue
      
          if release_date_is_recent(album, today):
              candidates.append(album)
      
      candidates.sort(
          key=lambda item: (
              item.get("date", ""),
              item.get("id", "")
          ),
          reverse=True
      )

        if album_id in posted_ids or album.get("ignored"):
            continue
        if release_date_is_recent(album, today):
            candidates.append(album)
    candidates.sort(key=lambda item: (item.get("date", ""), item.get("id", "")), reverse=True)

    print("Mode test :", DRY_RUN)
    print("Sorties candidates :", len(candidates))

    for album in candidates[:MAX_POSTS_PER_RUN]:
        if DRY_RUN:
            print("[SIMULATION SORTIE]", album["artist"], "-", album["title"], "|", album["link"])
            continue
        send_album(token, channel, album)
        posted_ids.add(str(album["id"]))
        print("Publication confirmée :", album["artist"], "-", album["title"])
        time.sleep(2)

    # Envoyer ensuite les fichiers autorisés associés à un album récent.
    audio_sent_this_run = 0
    for audio_item in state.get("audio_files", []):
        if audio_item.get("published"):
            continue
        album = find_album_for_audio(audio_item, albums)
        if not album or album.get("ignored") or not release_date_is_recent(album, today):
            continue
        unique_id = str(audio_item.get("file_unique_id") or audio_item.get("file_id"))
        if unique_id in posted_audio_ids:
            audio_item["published"] = True
            continue
        if DRY_RUN:
            print(
                "[SIMULATION AUDIO]", album.get("artist"), "-", album.get("title"),
                "| fichier :", audio_item.get("filename"),
            )
            continue
        if audio_sent_this_run >= MAX_AUDIO_PER_RUN:
            print("Limite de fichiers audio atteinte pour cette exécution.")
            break
        try:
            send_audio_file(token, channel, album, audio_item)
            audio_item["published"] = True
            posted_audio_ids.add(unique_id)
            audio_sent_this_run += 1
            print("Audio publié :", album.get("artist"), "-", audio_item.get("filename"))
            time.sleep(2)
        except (requests.RequestException, RuntimeError) as error:
            print("Échec de publication audio, nouvelle tentative à la prochaine exécution :", error)

    state["initialized"] = True
    state["albums"] = albums
    state["posted_ids"] = sorted(posted_ids)
    state["posted_audio_ids"] = sorted(posted_audio_ids)
    save_state(state)
    if DRY_RUN:
        print(
            "Mode test : aucune publication dans la chaîne. L'historique de test et "
            "les fichiers audio en attente ont été sauvegardés."
        )
    else:
        print("Historique sauvegardé.")


if __name__ == "__main__":
    main()
