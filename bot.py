
import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SOURCE = "https://minutepunchline.com/sorties/"
TZ = ZoneInfo("Africa/Lubumbashi")
STATE_FILE = "posted.json"
MAX_POSTS_PER_RUN = 3

MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2,
    "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12, "decembre": 12
}

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"initialized": False, "posted": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def parse_date(text):
    match = re.search(
        r"\b(\d{1,2})\s+([a-zéûôàèù]+)\s+(20\d{2})\b",
        text.lower()
    )
    if not match:
        return None
    day, month, year = match.groups()
    month_num = MONTHS.get(month)
    if not month_num:
        return None
    try:
        return datetime(int(year), month_num, int(day)).date()
    except ValueError:
        return None

def get_releases():
    response = requests.get(
        SOURCE,
        headers={"User-Agent": "RapUniversBot/1.0"},
        timeout=25
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    releases = {}

    for link in soup.select('a[href*="deezer.com"]'):
        title = link.get_text(" ", strip=True)
        url = link.get("href", "").strip()

        if not title or not url:
            continue
        if title.lower().startswith("écouter sur deezer"):
            continue

        # Chercher le bloc contenant le titre, l'artiste et la date.
        card = None
        for parent in link.parents:
            text = parent.get_text(" ", strip=True)
            if len(text) > 1800:
                break
            if parse_date(text) and re.search(
                r"\b(single|album|ep)\b", text, re.I
            ):
                card = parent
                break

        if card is None:
            continue

        text = card.get_text(" ", strip=True)
        release_date = parse_date(text)
        if not release_date:
            continue

        category_match = re.search(
            r"\b(Single|Album|EP)\b", text, re.I
        )
        category = category_match.group(1).upper() if category_match else "SORTIE"

        artist = "Artiste à confirmer"
        for artist_link in card.find_all("a"):
            artist_name = artist_link.get_text(" ", strip=True)
            artist_url = artist_link.get("href", "")
            if (
                artist_name
                and artist_url != url
                and "deezer.com" not in artist_url.lower()
                and artist_name.lower() not in {
                    "voir les punchlines", "écouter sur deezer"
                }
            ):
                artist = artist_name
                break

        # Identifiant stable pour éviter les publications en double.
        key = url.split("?")[0].rstrip("/")
        releases[key] = {
            "title": title,
            "artist": artist,
            "url": url,
            "date": release_date.isoformat(),
            "category": category
        }

    return list(releases.values())

def send_message(token, channel, release):
    message = (
        "🚨 NOUVELLE SORTIE RAP FR 🇫🇷\n\n"
        f"🎤 Artiste : {release['artist']}\n"
        f"🎵 Projet : {release['title']}\n"
        f"💿 Type : {release['category']}\n"
        f"📅 Sortie : {release['date']}\n\n"
        f"🎧 Écouter sur Deezer : {release['url']}\n\n"
        "📲 Rap Univers 2 — @frrapfrancais03"
    )

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": channel,
            "text": message,
            "disable_web_page_preview": False
        },
        timeout=25
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError("Telegram n'a pas confirmé la publication.")

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    channel = os.environ["TELEGRAM_CHANNEL_ID"]
    today = datetime.now(TZ).date()

    state = load_state()
    posted = set(state.get("posted", []))
    first_run = not state.get("initialized", False)

    releases = get_releases()
    if not releases:
        print("Aucune sortie reconnue. Vérifier la structure de la source.")
        state["initialized"] = True
        save_state(state)
        return

    max_age = 2 if first_run else 7
    oldest = today - timedelta(days=max_age)
    candidates = [
        r for r in releases
        if r["date"] not in ("", None)
        and oldest <= datetime.fromisoformat(r["date"]).date() <= today
        and r["url"].rstrip("/") not in posted
    ]
    candidates.sort(key=lambda r: r["date"], reverse=True)

    for release in candidates[:MAX_POSTS_PER_RUN]:
        send_message(token, channel, release)
        posted.add(release["url"].rstrip("/"))
        print("Publié :", release["artist"], "-", release["title"])

    state["posted"] = sorted(posted)
    state["initialized"] = True
    save_state(state)

if __name__ == "__main__":
    main()
