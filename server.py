#!/usr/bin/env python3
"""Minimal local server for the F1 Live dashboard.

It proxies Jolpica so the browser only talks to localhost, and caches responses
for five minutes to respect the provider's rate limit.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
API = "https://api.jolpi.ca/ergast/f1/current"
CACHE_SECONDS = 300
cache: dict[str, tuple[float, object]] = {}


def fetch_json(path: str) -> object:
    now = time.time()
    cached = cache.get(path)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    request = Request(f"{API}/{path}.json", headers={"User-Agent": "F1-Live-Dashboard/1.0"})
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    cache[path] = (now, payload)
    return payload


def unpack(payload: object, table_name: str) -> list[object]:
    try:
        return payload["MRData"][table_name]["Races"]
    except (KeyError, TypeError):
        return []


def dashboard() -> dict[str, object]:
    endpoints = {
        "drivers": "driverstandings",
        "constructors": "constructorstandings",
        "races": "races",
        "last": "last/results",
    }
    results: dict[str, object] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {key: pool.submit(fetch_json, endpoint) for key, endpoint in endpoints.items()}
        for key, future in futures.items():
            try:
                results[key] = future.result()
            except (URLError, TimeoutError, json.JSONDecodeError) as error:
                errors.append(f"{key}: {error}")
                results[key] = {}

    driver_table = results["drivers"].get("MRData", {}).get("StandingsTable", {})
    constructor_table = results["constructors"].get("MRData", {}).get("StandingsTable", {})
    last_races = unpack(results["last"], "RaceTable")
    last_race = last_races[0] if last_races else None
    return {
        "season": driver_table.get("season") or constructor_table.get("season") or time.gmtime().tm_year,
        "drivers": driver_table.get("StandingsLists", [{}])[0].get("DriverStandings", []),
        "constructors": constructor_table.get("StandingsLists", [{}])[0].get("ConstructorStandings", []),
        "races": unpack(results["races"], "RaceTable"),
        "lastRace": last_race,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "Jolpica F1 API",
        "errors": errors,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/dashboard":
            try:
                body = json.dumps(dashboard(), ensure_ascii=False).encode("utf-8")
                self.send_response(200)
            except Exception as error:  # A helpful response is better than a dead dashboard.
                body = json.dumps({"error": "Не удалось получить данные F1", "details": str(error)}, ensure_ascii=False).encode("utf-8")
                self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


if __name__ == "__main__":
    # Cloud platforms give the app its port through the PORT environment variable.
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"F1 Live is running at http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
