#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rap Univers 2 — publication automatique des sorties rap FR
════════════════════════════════════════════════════════════════
Fonctionnement :
  1. Lit la page "sorties" de MinutePunchline et en extrait les
     liens d'albums Deezer.
  2. Récupère les métadonnées officielles de chaque album via
     l'API publique Deezer (titre, artiste, date, pochette, lien).
  3. Publie sur la chaîne Telegram : pochette + légende complète
     + lien d'écoute officiel.
  4. Conserve l'historique dans posted.json pour ne jamais publier
     deux fois la même sortie.

Variables d'environnement requises :
  TELEGRAM_BOT_TOKEN    token du bot (donné par @BotFather)
  TELEGRAM_CHANNEL_ID   chaîne cible (@nom ou ID en -100...)

Mode test :
  DRY_RUN=true (valeur par défaut) → affiche ce qui serait publié,
  n'envoie aucun message et ne sauvegarde pas l'historique.
"""

import json
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ─── Configuration ────────────────────────────────────────────
SOURCE_URL = "https://minutepunchline.com/sorties/"
DEEZER_API = "https://api.deezer.com/album/"
STATE_FILE = Path("posted.json")
LOCAL_TZ = ZoneInfo("Africa/Lubumbashi")   # fuseau de la chaîne
MAX_POSTS_PER_RUN = 5                      # limite anti-flood par exécution
DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() == "true"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
}

session = requests.Session()
session.headers.update(HEADERS)


# ─── Historique (posted.json) ─────────────────────────────────
def load_state():
    if not STATE_FILE.exists():
        return {"initialized": False, "albums": {}, "posted_ids": []}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        data.setdefault("initialized", False)
        data.setdefault("albums", {})
        data.setdefault("posted_ids", [])
        return data
    except (OSError, json.JSONDecodeError):
        print("Attention : historique illisible, démarrage sans historique.")
        return {"initialized": False, "albums": {}, "posted_ids": []}


def save_state(state):
    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


# ─── Sources de données ───────────────────────────────────────
REGEX_ALBUM_DEEZER = re.compile(r"deezer\.com/(?:[a-z]{2}/)?album/(\d+)")


def get_album_ids():
    """Extrait les identifiants d'albums Deezer depuis MinutePunchline.

    Gère les deux formats de liens :
      https://www.deezer.com/album/1234567
      https://www.deezer.com/fr/album/1234567
    """
    print("Lecture de MinutePunchline...")
    response = session.get(SOURCE_URL, timeout=25)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    album_ids = []
    for link in soup.select('a[href*="deezer.com"]'):
        match = REGEX_ALBUM_DEEZER.search(link.get("href", ""))
        if match:
            album_id = match.group(1)
            if album_id not in album_ids:
                album_ids.append(album_id)

    if not album_ids:
        raise RuntimeError(
            "Aucun lien d'album Deezer trouvé sur la page des sorties."
        )

    print(f"{len(album_ids)} liens d'albums trouvés.")
    return album_ids


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
            "title": data["title"],
            "artist": artist["name"],
            "type": (data.get("record_type") or "sortie").upper(),
            "date": release_date,
            "tracks": data.get("nb_tracks", "?"),
            "cover": data.get("cover_xl") or data.get("cover_big"),
            "link": official_link,
        }
    except (requests.RequestException, ValueError) as error:
        print(f"Échec pour l'album {album_id} : {error}")
        return None


# ─── Publication Telegram ─────────────────────────────────────
def build_caption(album):
    """Fabrique la légende du post (1024 caractères max sur Telegram)."""
    return (
        "🚨 NOUVELLE SORTIE RAP FR 🇫🇷\n\n"
        f"🎤 Artiste : {album['artist']}\n"
        f"💿 Projet : {album['title']}\n"
        f"📀 Type : {album['type']}\n"
        f"📅 Sortie : {album['date']}\n"
        f"🎵 Nombre de titres : {album['tracks']}\n\n"
        f"🎧 Écouter officiellement : {album['link']}\n\n"
        "📲 Rap Univers 2 • @frrapfrancais03"
    )[:1024]


def telegram_request(method, token, **kwargs):
    url = f"https://api.telegram.org/bot{token}/{method}"
    response = requests.post(url, data=kwargs, timeout=30)
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Erreur Telegram"))
    return result["result"]


def send_album(token, channel, album):
    """Envoie la pochette avec la légende ; repli sur le texte seul."""
    caption = build_caption(album)

    if album.get("cover"):
        try:
            telegram_request(
                "sendPhoto",
                token,
                chat_id=channel,
                photo=album["cover"],
                caption=caption,
            )
            return
        except (requests.RequestException, RuntimeError) as error:
            print(f"Envoi de la pochette impossible : {error}")
            print("Tentative d'envoi du texte seul (aperçu du lien).")

    telegram_request(
        "sendMessage",
        token,
        chat_id=channel,
        text=caption,
    )


# ─── Programme principal ──────────────────────────────────────
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    channel = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()

    if not token:
        raise RuntimeError("Le secret TELEGRAM_BOT_TOKEN est absent ou vide.")
    if not channel:
        raise RuntimeError("Le secret TELEGRAM_CHANNEL_ID est absent ou vide.")

    # Date du jour dans le fuseau de la chaîne (Lubumbashi = UTC+2)
    today = datetime.now(LOCAL_TZ).date()

    state = load_state()
    bootstrap = not state["initialized"]
    albums = state["albums"]
    posted_ids = set(state["posted_ids"])

    ids = get_album_ids()

    # Consulter Deezer seulement pour les identifiants inconnus
    for album_id in ids:
        if album_id not in albums:
            album = get_album(album_id)
            if album:
                albums[album_id] = album
            time.sleep(0.15)

    # Au premier lancement, ne pas inonder la chaîne avec l'historique
    # du site : les sorties antérieures à aujourd'hui sont ignorées.
    if bootstrap:
        for album in albums.values():
            try:
                if date.fromisoformat(album["date"]) < today:
                    album["ignored"] = True
            except (ValueError, KeyError):
                album["ignored"] = True

    earliest_date = today - timedelta(days=3)
    candidates = []

    for album_id in ids:
        album = albums.get(album_id)
        if not album or album_id in posted_ids or album.get("ignored"):
            continue
        try:
            release_date = date.fromisoformat(album["date"])
        except (ValueError, KeyError):
            continue
        if earliest_date <= release_date <= today:
            candidates.append(album)

    candidates.sort(key=lambda album: album["date"], reverse=True)

    print("Mode test :", DRY_RUN)
    print("Sorties candidates :", len(candidates))

    for album in candidates[:MAX_POSTS_PER_RUN]:
        if DRY_RUN:
            print("[SIMULATION]", album["artist"], "-", album["title"],
                  "|", album["link"])
            continue

        send_album(token, channel, album)
        posted_ids.add(album["id"])
        print("Publication confirmée :", album["artist"], "-", album["title"])
        time.sleep(2)   # petite pause anti-flood entre deux publications

    if DRY_RUN:
        print("Mode test : aucun message envoyé et aucun historique sauvegardé.")
        return

    state["initialized"] = True
    state["albums"] = albums
    state["posted_ids"] = sorted(posted_ids)
    save_state(state)
    print("Historique sauvegardé.")


if __name__ == "__main__":
    main()