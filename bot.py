import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SOURCE = "https://minutepunchline.com/sorties/"
TZ = ZoneInfo("Africa/Lubumbashi")
STATE_FILE = "posted.json"
MAX_POSTS_PER_RUN = 3
MAX_AGE_DAYS = 14

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
        text.lower(),
    )
    if not match:
        return None
    day = int(match.group(1))
    month = MONTHS.get(match.group(2))
    year = int(match.group(3))
    if month is None:
        return None
    try:
        return datetime(year, month, day, tzinfo=TZ)
    except ValueError:
        return None


def clean_label(text):
    return re.sub(r"\s+", " ", text).strip()


def split_artist_title(label):
    for separator in (" – ", " - "):
        if separator in label:
            artist, title = label.split(separator, 1)
            return artist.strip(), title.strip()
    return "", label.strip()


def fetch_releases():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }
    response = requests.get(SOURCE, headers=headers, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    releases = []
    seen = set()

    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if "deezer.com" not in href:
            continue

        url = href.split("?", 1)[0].rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)

        # Remonter au bloc parent pour trouver artiste, titre et date
        container = link.find_parent(["article", "li", "td"])
        if container is None:
            container = link
            for _ in range(3):
                if container.parent is None:
                    break
                container = container.parent

        context = container.get_text(" ", strip=True)

        label = clean_label(link.get_text(" ", strip=True))
        if not label:
            heading = container.find(["h1", "h2", "h3", "h4", "h5", "strong", "b"])
            if heading:
                label = clean_label(heading.get_text(" ", strip=True))
        if not label:
            label = clean_label(context)

        artist, title = split_artist_title(label)

        releases.append({
            "artist": artist,
            "title": title,
            "url": url,
            "date": parse_date(context),
        })

    if not releases:
        print("Aucun lien Deezer détecté — la structure de la page a peut-être changé.")
        print("Aperçu du texte de la page :")
        print(soup.get_text(" ", strip=True)[:600])

    return releases


def send_message(token, channel, release):
    date_str = release["date"].strftime("%d/%m/%Y") if release["date"] else "date inconnue"
    text = (
        "🎵 Nouvelle sortie rap\n\n"
        f"{release['artist'] or 'Artiste inconnu'} — {release['title']}\n"
        f"📅 Sortie : {date_str}\n\n"
        f"▶️ Écouter : {release['url']}"
    )
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": channel, "text": text},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram a refusé le message : {data}")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    channel = os.getenv("TELEGRAM_CHANNEL_ID", "")

    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

    state = load_state()
    posted = set(state.get("posted", []))

    try:
        releases = fetch_releases()
    except requests.RequestException as exc:
        raise SystemExit(f"Impossible de récupérer la page : {exc}")

    print(f"{len(releases)} sortie(s) détectée(s) sur la page.")

    now = datetime.now(TZ)
    oldest = now - timedelta(days=MAX_AGE_DAYS)

    candidates = []
    for release in releases:
        if release["url"].rstrip("/") in posted:
            continue
        if release["date"] is None:
            continue
        if release["date"] < oldest:
            continue
        candidates.append(release)

    candidates.sort(key=lambda r: r["date"], reverse=True)
    print(f"{len(candidates)} nouveauté(s) récente(s) non encore publiée(s).")

    if not dry_run and (not token or not channel):
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN et TELEGRAM_CHANNEL_ID sont obligatoires "
            "quand DRY_RUN est désactivé."
        )

    for release in candidates[:MAX_POSTS_PER_RUN]:
        if dry_run:
            print(
                "[TEST SANS PUBLICATION]",
                release["artist"],
                "-",
                release["title"],
                release["url"]
            )
        else:
            send_message(token, channel, release)
            posted.add(release["url"].rstrip("/"))
            print("Publié :", release["artist"], "-", release["title"])
            time.sleep(2)

    state["posted"] = sorted(posted)
    state["initialized"] = True
    save_state(state)

    if dry_run:
        print("Mode test terminé : aucun message envoyé à Telegram.")


if __name__ == "__main__":
    main()
