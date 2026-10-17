
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

URL = "https://minutepunchline.com/rss.xml"

response = requests.get(
    URL,
    headers={"User-Agent": "RapUnivers2/1.0"},
    timeout=20,
)
response.raise_for_status()

root = ET.fromstring(response.content)

def get_text(element, name):
    for child in element.iter():
        if child.tag.split("}")[-1].lower() == name.lower():
            if child.text:
                return child.text.strip()
    return ""

def get_link(element):
    for child in element.iter():
        if child.tag.split("}")[-1].lower() == "link":
            href = child.attrib.get("href")
            if href:
                return href
            if child.text:
                return child.text.strip()
    return ""

entries = [
    element for element in root.iter()
    if element.tag.split("}")[-1].lower() in ("item", "entry")
]

print("Flux RSS :", URL)
print("Nombre d'éléments :", len(entries))

for i, entry in enumerate(entries[:15], start=1):
    title = get_text(entry, "title")
    link = get_link(entry)
    date = get_text(entry, "pubDate") or get_text(entry, "published")
    description = get_text(entry, "description") or get_text(entry, "summary")
    description = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)

    print(f"\n--- ÉLÉMENT {i} ---")
    print("Titre :", title)
    print("Date :", date)
    print("Lien :", link)
    print("Description :", description[:300])
