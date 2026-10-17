import re
import requests
from bs4 import BeautifulSoup

SOURCE = "https://minutepunchline.com/sorties/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
}

session = requests.Session()
session.headers.update(HEADERS)


def main():
    print("1. Lecture de MinutePunchline...")

    response = session.get(SOURCE, timeout=25)
    print("Statut HTTP :", response.status_code)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    album_ids = []

    for link in soup.select('a[href*="deezer.com/album/"]'):
        match = re.search(r"/album/(\d+)", link.get("href", ""))
        if match and match.group(1) not in album_ids:
            album_ids.append(match.group(1))

    print("Albums Deezer trouvés :", len(album_ids))

    if not album_ids:
        print("Aucun lien d'album trouvé.")
        return

    print("\n2. Vérification des informations Deezer...\n")

    for album_id in album_ids[:10]:
        url = f"https://api.deezer.com/album/{album_id}"

        try:
            result = session.get(url, timeout=20)
            result.raise_for_status()
            album = result.json()

            if "error" in album:
                print("Album inaccessible :", album_id, album["error"])
                continue

            artist = album.get("artist") or {}

            print("=" * 45)
            print("Titre :", album.get("title", "Inconnu"))
            print("Artiste :", artist.get("name", "Inconnu"))
            print("Type :", album.get("record_type", "Inconnu"))
            print("Date :", album.get("release_date", "Inconnue"))
            print("Nombre de titres :", album.get("nb_tracks", "?"))
            print("Pochette :", album.get("cover_xl") or album.get("cover_big"))
            print("Lien officiel :", album.get("link"))

        except (requests.RequestException, ValueError) as error:
            print("Erreur sur l'album", album_id, ":", error)


if __name__ == "__main__":
    main()
