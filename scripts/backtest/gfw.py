"""Fetch vessel identity history from the Global Fishing Watch API.

The token is read from the GFW_TOKEN environment variable and never written to
disk by this module. Responses are cached as JSON in a directory that .gitignore
excludes, because the GFW terms are non-commercial and the raw records must not
be redistributed through the repository.

The API rejects Python's default User-Agent with 403 (error code 1010), so a
browser-like one is sent.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from features import IdentityRecord

SEARCH_URL = "https://gateway.api.globalfishingwatch.org/v3/vessels/search"
IDENTITY_DATASET = "public-global-vessel-identity:latest"
USER_AGENT = "Mozilla/5.0 (shifting-hulls research client)"


def _request(token: str, imo: str, timeout: float = 60.0) -> dict:
    query = urllib.parse.urlencode(
        {"query": imo, "datasets[0]": IDENTITY_DATASET, "includes[0]": "MATCH_CRITERIA"}
    )
    request = urllib.request.Request(
        f"{SEARCH_URL}?{query}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def identity_payload(imo: str, token: str, attempts: int = 4) -> dict:
    """One hull's identity records and vessel classes, or an error marker."""
    last = ""
    for attempt in range(attempts):
        try:
            raw = _request(token, imo)
        except urllib.error.HTTPError as error:
            last = f"HTTP {error.code}"
            if error.code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            return {"error": last, "records": [], "classes": []}
        except (urllib.error.URLError, TimeoutError) as error:
            last = str(error)[:80]
            time.sleep(3)
            continue
        return _normalise(raw)

    return {"error": last, "records": [], "classes": []}


def _normalise(raw: dict) -> dict:
    records: list[dict[str, str | None]] = []
    classes: set[str] = set()
    for entry in raw.get("entries") or []:
        for item in entry.get("selfReportedInfo") or []:
            records.append(
                {
                    "mmsi": item.get("ssvid"),
                    "name": item.get("shipname"),
                    "flag": item.get("flag"),
                    "callsign": item.get("callsign"),
                    "start": item.get("transmissionDateFrom"),
                    "end": item.get("transmissionDateTo"),
                }
            )
        for source in entry.get("combinedSourcesInfo") or []:
            for item in source.get("vesselClassSc") or []:
                if item.get("value"):
                    classes.add(str(item["value"]))
    return {"error": None, "records": records, "classes": sorted(classes)}


def fetch_all(
    imos: list[str],
    cache_path: Path,
    delay: float = 0.25,
    progress_every: int = 50,
) -> dict[str, dict]:
    """Fetch each IMO, skipping anything already cached, saving as it goes."""
    token = os.environ.get("GFW_TOKEN", "").strip()
    if not token:
        raise RuntimeError("set GFW_TOKEN; see docs/backtest/data_access.md")

    cache: dict[str, dict] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    for index, imo in enumerate(imos, start=1):
        if imo in cache:
            continue
        cache[imo] = identity_payload(imo, token)
        if index % progress_every == 0:
            _save(cache, cache_path)
            print(f"  fetched {len(cache)}", flush=True)
        time.sleep(delay)

    _save(cache, cache_path)
    return cache


def _save(cache: dict[str, dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache), encoding="utf-8")


def records_of(payload: dict) -> list[IdentityRecord]:
    return [
        IdentityRecord(
            mmsi=item.get("mmsi"),
            name=item.get("name"),
            flag=item.get("flag"),
            callsign=item.get("callsign"),
            start=item.get("start"),
            end=item.get("end"),
        )
        for item in payload.get("records", [])
    ]


def is_tanker(payload: dict) -> bool:
    return any("tanker" in value for value in payload.get("classes", []))
