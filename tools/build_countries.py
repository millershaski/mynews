"""Builds the country data used by the globe (Flutter asset) and the server.

Source: Natural Earth 1:110m admin-0 countries (public domain).
Run from the repo root:  python tools/build_countries.py [path/to/ne_110m_admin_0_countries.geojson]
If no path is given the file is downloaded.

Outputs:
  assets/countries.json         compact polygons for the globe (outer rings, 2-decimal degrees)
  server/app/data/countries.json  ISO code -> name, search aliases, Guardian tag
"""
import json
import sys
import urllib.request
from pathlib import Path

SRC_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson"
ROOT = Path(__file__).resolve().parents[1]

# Disputed / unrecognised areas are merged into the country most news coverage files them under.
REMAP = {"CYN": "CY", "SOL": "SO"}

# Guardian tags that do not follow the default "world/<slug>" pattern.
GUARDIAN_TAG_OVERRIDES = {
    "US": "us-news/us-news",
    "GB": "uk/uk",
    "AU": "australia-news/australia-news",
}

# Extra names and demonyms used to match coverage. Case-sensitive, matched on word boundaries.
ALIASES = {
    "US": ["United States", "U.S.", "America", "American", "Americans", "Washington"],
    "GB": ["United Kingdom", "Britain", "British", "UK", "England", "Scotland", "Wales", "London"],
    "CN": ["China", "Chinese", "Beijing"],
    "RU": ["Russia", "Russian", "Russians", "Moscow", "Kremlin"],
    "UA": ["Ukraine", "Ukrainian", "Ukrainians", "Kyiv"],
    "FR": ["France", "French", "Paris"],
    "DE": ["Germany", "German", "Germans", "Berlin"],
    "IT": ["Italy", "Italian", "Rome"],
    "ES": ["Spain", "Spanish", "Madrid"],
    "JP": ["Japan", "Japanese", "Tokyo"],
    "KR": ["South Korea", "South Korean", "Seoul"],
    "KP": ["North Korea", "North Korean", "Pyongyang"],
    "IN": ["India", "Indian", "New Delhi"],
    "PK": ["Pakistan", "Pakistani", "Islamabad"],
    "IR": ["Iran", "Iranian", "Tehran"],
    "IQ": ["Iraq", "Iraqi", "Baghdad"],
    "IL": ["Israel", "Israeli", "Israelis", "Jerusalem", "Tel Aviv"],
    "PS": ["Palestine", "Palestinian", "Palestinians", "Gaza", "West Bank"],
    "SY": ["Syria", "Syrian", "Damascus"],
    "SA": ["Saudi Arabia", "Saudi", "Riyadh"],
    "TR": ["Turkey", "Turkish", "Türkiye", "Ankara", "Istanbul"],
    "EG": ["Egypt", "Egyptian", "Cairo"],
    "BR": ["Brazil", "Brazilian", "Brasília"],
    "MX": ["Mexico", "Mexican", "Mexico City"],
    "CA": ["Canada", "Canadian", "Canadians", "Ottawa"],
    "AU": ["Australia", "Australian", "Australians", "Canberra", "Sydney"],
    "NZ": ["New Zealand", "Wellington"],
    "ZA": ["South Africa", "South African", "Johannesburg", "Pretoria"],
    "NG": ["Nigeria", "Nigerian", "Lagos", "Abuja"],
    "KE": ["Kenya", "Kenyan", "Nairobi"],
    "ET": ["Ethiopia", "Ethiopian", "Addis Ababa"],
    "AR": ["Argentina", "Argentine", "Argentinian", "Buenos Aires"],
    "VE": ["Venezuela", "Venezuelan", "Caracas"],
    "CO": ["Colombia", "Colombian", "Bogotá"],
    "AF": ["Afghanistan", "Afghan", "Kabul"],
    "TW": ["Taiwan", "Taiwanese", "Taipei"],
    "VN": ["Vietnam", "Vietnamese", "Hanoi"],
    "ID": ["Indonesia", "Indonesian", "Jakarta"],
    "PH": ["Philippines", "Philippine", "Filipino", "Manila"],
    "PL": ["Poland", "Polish", "Warsaw"],
    "SE": ["Sweden", "Swedish", "Stockholm"],
    "NO": ["Norway", "Norwegian", "Oslo"],
    "GR": ["Greece", "Greek", "Athens"],
    "IE": ["Ireland", "Irish", "Dublin"],
    "NL": ["Netherlands", "Dutch", "Amsterdam"],
    "CH": ["Switzerland", "Swiss", "Geneva", "Zurich"],
    "CU": ["Cuba", "Cuban", "Havana"],
    "LB": ["Lebanon", "Lebanese", "Beirut"],
    "SD": ["Sudan", "Sudanese", "Khartoum"],
    "SS": ["South Sudan"],
    "CD": ["Democratic Republic of the Congo", "DR Congo", "DRC", "Kinshasa"],
}


def slug(name: str) -> str:
    out = []
    for ch in name.lower():
        out.append(ch if ch.isalnum() else "-")
    s = "".join(out)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def rings(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon":
        return [poly[0] for poly in geom["coordinates"]]
    return []


def main():
    if len(sys.argv) > 1:
        data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(SRC_URL, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))

    globe = {}
    server = {}
    for f in data["features"]:
        p = f["properties"]
        iso = REMAP.get(p["ADM0_A3"]) or p.get("ISO_A2_EH")
        if not iso or iso == "-99":
            continue
        name = p.get("NAME_EN") or p["NAME"]
        flat_rings = []
        for ring in rings(f["geometry"]):
            flat = []
            for lon, lat in ring:
                flat += [round(lon, 2), round(lat, 2)]
            flat_rings.append(flat)
        if iso in globe:  # merged area (e.g. Somaliland into Somalia)
            globe[iso]["p"] += flat_rings
            continue
        globe[iso] = {
            "c": iso,
            "n": name,
            "lx": round(p["LABEL_X"], 2),
            "ly": round(p["LABEL_Y"], 2),
            "p": flat_rings,
        }
        aliases = [name]
        for extra in (p.get("NAME"), p.get("NAME_LONG")):
            if extra and extra not in aliases:
                aliases.append(extra)
        for extra in ALIASES.get(iso, []):
            if extra not in aliases:
                aliases.append(extra)
        server[iso] = {
            "name": name,
            "aliases": aliases,
            "guardian_tag": GUARDIAN_TAG_OVERRIDES.get(iso, f"world/{slug(name)}"),
        }

    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "countries.json").write_text(
        json.dumps(sorted(globe.values(), key=lambda c: c["c"]), separators=(",", ":")), encoding="utf-8"
    )
    sdata = ROOT / "server" / "app" / "data"
    sdata.mkdir(parents=True, exist_ok=True)
    (sdata / "countries.json").write_text(
        json.dumps(dict(sorted(server.items())), indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"{len(globe)} countries written")


if __name__ == "__main__":
    main()
