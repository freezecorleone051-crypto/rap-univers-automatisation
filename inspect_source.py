
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

URL = "https://minutepunchline.com/sorties/"

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
}

response = requests.get(URL, headers=headers, timeout=25)
print("Statut HTTP :", response.status_code)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

print("\n=== SCRIPTS JAVASCRIPT ===")
for script in soup.find_all("script", src=True):
    print(urljoin(URL, script["src"]))

print("\n=== INDICES DE SOURCES DE DONNÉES ===")
keywords = ("api", "json", "deezer", "release", "sortie")

for tag in soup.find_all(["script", "link"]):
    content = (
        tag.get("src", "")
        or tag.get("href", "")
        or ""
    )
    if any(word in content.lower() for word in keywords):
        print(content)

print("\n=== PREMIÈRES FICHES MUSICALES ===")
count = 0

for link in soup.select('a[href*="deezer.com"]'):
    title = link.get_text(" ", strip=True)
    if not title or title.lower().startswith("écouter sur"):
        continue

    parent = link
    for _ in range(6):
        if parent.parent is None:
            break
        parent = parent.parent
        text = " ".join(parent.get_text(" ", strip=True).split())

        if (
            len(text) < 700
            and any(word in text.lower()
                    for word in ("single", "album", "ep"))
            and len(text) > len(title)
        ):
            print("\nFiche :", text[:500])
            print("Lien :", link["href"])
            count += 1
            break

    if count >= 5:
        break

print("\nInspection terminée.")
