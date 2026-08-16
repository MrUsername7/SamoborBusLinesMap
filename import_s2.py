import json
import re
import time
import unicodedata
from pathlib import Path

import requests


PROJECT = Path(__file__).parent
DATA = PROJECT / "data"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Samobor / Bregana area
BBOX = "45.74,15.62,45.88,15.82"

def normalize(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(
        c for c in text
        if unicodedata.category(c) != "Mn"
    )
    text = text.lower()
    text = text.replace("„", '"').replace("“", '"')
    text = text.replace("-", " ")
    text = re.sub(r"[^a-z0-9 ]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def get_osm_stops():
    query = f"""
    [out:json][timeout:60];

    (
      node["highway"="bus_stop"]({BBOX});
      way["highway"="bus_stop"]({BBOX});
      relation["highway"="bus_stop"]({BBOX});
      node["public_transport"="platform"]({BBOX});
      way["public_transport"="platform"]({BBOX});
    );

    out center tags;
    """
    # Overpass can reject requests that don't identify the client.
    headers = {
        "User-Agent": "SamoborBusLines/1.0 (local project)"
    }
    # Try more than one public Overpass server.
    servers = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.private.coffee/api/interpreter"
    ]
    last_error = None
    for server in servers:
        print(f"Trying {server}...")
        try:
            response = requests.get(
                server,
                params={"data": query},
                headers=headers,
                timeout=90
            )
            response.raise_for_status()
            data = response.json()
            print("OpenStreetMap data downloaded successfully.")
            return data["elements"]
        except Exception as error:
            print(f"Failed: {error}")
            last_error = error
        time.sleep(2)
    raise RuntimeError(
        "Could not download OpenStreetMap data "
        "from any Overpass server."
    ) from last_error

def element_coordinates(element):
    if element["type"] == "node":
        return element["lat"], element["lon"]
    center = element.get("center")
    if center:
        return center["lat"], center["lon"]
    return None

def stop_name(element):
    tags = element.get("tags", {})
    return (
        tags.get("name")
        or tags.get("name:hr")
        or tags.get("official_name")
    )

def find_best_match(target, osm_stops):
    aliases = {
        "Samobor AK": [
            "Samobor AK",
            "Samobor Ak",
            "Samobor autobusni kolodvor",
            "Autobusni kolodvor Samobor",
            "A.K. Samobor"
        ],
        "Samobor „Dom zdravlja“": [
            "Samobor Dom zdravlja",
            "Samobor „Dom zdravlja“",
            "Dom zdravlja"
        ],
        "Samobor - igralište": [
            "Samobor - igralište",
            "Samobor - Igralište",
            "Samobor Igralište"
        ],
        "Sv.Helena": [
            "Sv.Helena",
            "Sveta Helena",
            "Sv. Helena"
        ],
        "Bregana - tržnica": [
            "Bregana - tržnica",
            "Bregana tržnica",
            "Tržnica Bregana",
            "Tržnica"
        ],
        "Samobor „Gajeva-pošta“": [
            "Samobor „Gajeva-pošta“",
            "Samobor Gajeva-pošta",
            "Gajeva-pošta",
            "Gajeva"
        ]
    }
    names = aliases.get(target, [target])
    normalized_names = {
        normalize(name)
        for name in names
    }
    candidates = []
    for stop in osm_stops:
        name = stop_name(stop)
        if not name:
            continue
        coordinates = element_coordinates(stop)
        if not coordinates:
            continue
        lat, lon = coordinates
        normalized = normalize(name)
        # First: exact alias match
        if normalized in normalized_names:
            candidates.append(
                (
                    100,
                    name,
                    coordinates,
                    stop["id"]
                )
            )
    if candidates:
        # If there are several exact matches, choose the one
        # geographically closest to central Samobor/Bregana
        candidates.sort(
            key=lambda x: (
                -x[0],
                x[2][0]
            )
        )
        return candidates[0]
    # No exact match: don't guess.
    return None

def main():
    routes_file = DATA / "routes.json"
    with open(routes_file, encoding="utf-8") as f:
        routes = json.load(f)
    # --------------------------------------------------
    # VERIFIED / MANUALLY IDENTIFIED OSM LOCATIONS
    # --------------------------------------------------
    # --------------------------------------------------
    known_stops = {
        "Samobor AK": {
            "lat": 45.808861,
            "lon": 15.716556,
            "osm_name": "Autobusni kolodvor Samobor"
        },
        "Samobor - igralište": {
            "lat": 45.810028,
            "lon": 15.709056,
            "osm_name": "Stadion"
        },
        "Sv.Helena": {
            "lat": 45.814528,
            "lon": 15.705361,
            "osm_name": "Sv. Helena"
        },
        "Bregana - tržnica": {
            "lat": 45.837972,
            "lon": 15.688000,
            "osm_name": "Bregana - tržnica"
        }
    }
    # --------------------------------------------------
    # DOWNLOAD OSM STOPS
    # --------------------------------------------------
    osm_stops = get_osm_stops()
    print(
        f"Found {len(osm_stops)} "
        f"OSM stop/platform objects."
    )
    # --------------------------------------------------
    # GET UNIQUE STOPS USED BY S2
    # --------------------------------------------------
    needed_stops = []
    for direction in routes["S2"]["directions"].values():

        for stop in direction["stops"]:

            if stop not in needed_stops:
                needed_stops.append(stop)
    # --------------------------------------------------
    # MATCH STOPS
    # --------------------------------------------------
    output = {}
    print()
    print("Matching S2 stops:")
    print("------------------")
    for stop in needed_stops:
        # ----------------------------------------------
        # Use manually verified coordinates first
        # ----------------------------------------------
        if stop in known_stops:
            known = known_stops[stop]
            output[stop] = {
                "name": stop,
                "lat": known["lat"],
                "lon": known["lon"],
                "osm_name": known["osm_name"],
                "osm_id": None,
                "verified": True
            }
            print(
                f"{stop} -> VERIFIED "
                f"{known['osm_name']} "
                f"[{known['lat']:.6f}, "
                f"{known['lon']:.6f}]"
            )
            continue
        # ----------------------------------------------
        # Otherwise search OpenStreetMap
        # ----------------------------------------------
        match = find_best_match(
            stop,
            osm_stops
        )
        if match is None:
            print(
                f"NOT FOUND: {stop}"
            )
            continue
        score, osm_name, coordinates, osm_id = match
        lat, lon = coordinates
        output[stop] = {
            "name": stop,
            "lat": lat,
            "lon": lon,
            "osm_name": osm_name,
            "osm_id": osm_id,
            "verified": False
        }
        print(
            f"{stop} -> {osm_name} "
            f"[{lat:.6f}, {lon:.6f}] "
            f"score={score}"
        )
    # --------------------------------------------------
    # CHECK FOR MISSING STOPS
    # --------------------------------------------------
    missing = [
        stop
        for stop in needed_stops
        if stop not in output
    ]
    print()
    if missing:
        print(
            f"WARNING: {len(missing)} "
            f"S2 stops are still missing:"
        )
        for stop in missing:
            print(f"  - {stop}")
    else:
        print(
            "All S2 stops were successfully "
            "identified."
        )
    # --------------------------------------------------
    # SAVE STOPS
    # --------------------------------------------------
    stops_file = DATA / "stops.json"
    with open(
        stops_file,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )
    print()
    print(
        f"Saved {len(output)} stops "
        f"to {stops_file}"
    )

if __name__ == "__main__":
    main()
