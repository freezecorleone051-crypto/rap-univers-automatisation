
import requests

URLS = {
    "MinutePunchline": "https://minutepunchline.com/sorties/",
    "RSS principal": "https://minutepunchline.com/feed/",
    "RSS sorties": "https://minutepunchline.com/sorties/feed/",
    "API Deezer sorties": "https://api.deezer.com/editorial/0/releases?limit=5",
    "API Deezer recherche": "https://api.deezer.com/search/album?q=rap&limit=5",
}

headers = {
    "User-Agent": "RapUnivers2/1.0 (source availability test)"
}

for name, url in URLS.items():
    print(f"\n--- {name} ---")
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=20,
        )
        print("Statut :", response.status_code)
        print("Type :", response.headers.get("content-type", "inconnu"))
        print("URL finale :", response.url)

        if response.ok and "api.deezer.com" in response.url:
            try:
                data = response.json()
                if "data" in data:
                    print("Résultats reçus :", len(data["data"]))
                elif "error" in data:
                    print("Erreur API :", data["error"])
            except ValueError:
                print("Réponse reçue, mais pas en JSON.")

    except requests.RequestException as error:
        print("Échec :", error)
