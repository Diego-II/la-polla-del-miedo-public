"""Build data/fixtures.yaml from Wikipedia (MediaWiki action API wikitext)."""
from __future__ import annotations

import json
import re
import time

import requests
import yaml

from polla.config import DATA, ROOT

API = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "polla-del-miedo/1.0 (personal project)"}
CACHE = ROOT / "data_cache"


def _wikitext(group: str) -> str:
    cached = CACHE / f"grp{group}.json"
    if cached.exists():
        d = json.loads(cached.read_text())
        if "parse" in d:
            return d["parse"]["wikitext"]
    time.sleep(1.0)  # be polite; Wikipedia rate-limits bursts
    r = requests.get(API, headers=UA, timeout=30, params={
        "action": "parse", "page": f"2026 FIFA World Cup Group {group}",
        "prop": "wikitext", "format": "json", "formatversion": "2"})
    r.raise_for_status()
    CACHE.mkdir(exist_ok=True)
    cached.write_text(r.text)
    return r.json()["parse"]["wikitext"]


def _boxes(wt: str) -> list[str]:
    return ["{{#invoke:football box" + p for p in re.split(r"\{\{#invoke:football box", wt)[1:]]


def _parse_box(block: str) -> dict:
    dm = re.search(r"date=\{\{Start date\|(\d+)\|(\d+)\|(\d+)", block)
    date = f"{dm[1]}-{int(dm[2]):02d}-{int(dm[3]):02d}" if dm else ""
    tm = re.search(r"time=([^\n|]+)", block)
    kickoff = ""
    if tm:
        t = tm[1].replace("&nbsp;", " ")
        t = re.sub(r"\[\[UTC[^\]]*\|(UTC[^\]]+)\]\]", r"\1", t)
        kickoff = re.sub(r"\[\[|\]\]", "", t).strip()
    sl = re.search(r"score link\|[^#|]*#([^|]+?)\s*\|", block)
    home = away = ""
    if sl and " vs " in sl[1]:
        home, away = (s.strip() for s in sl[1].split(" vs ", 1))
    return {"home": home, "away": away, "date": date, "kickoff": kickoff}


def main() -> None:
    groups = {}
    for g in "ABCDEFGHIJKL":
        wt = _wikitext(g)
        teams = []
        for anchor in re.findall(r"score link\|[^#|]*#([^|]+?)\s*\|", wt):
            if " vs " in anchor:
                for t in anchor.split(" vs ", 1):
                    t = t.strip()
                    if t and t not in teams:
                        teams.append(t)
        matches = [_parse_box(b) for b in _boxes(wt)]
        groups[g] = {"teams": sorted(teams), "matches": matches, "first_match": matches[0]}

    doc = {"tournament": "FIFA World Cup 2026",
           "source": "en.wikipedia.org (MediaWiki action API)", "groups": groups}
    (DATA / "fixtures.yaml").write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    total = sum(len(i["matches"]) for i in groups.values())
    for g, info in groups.items():
        fm = info["first_match"]
        print(f"Group {g}: {len(info['matches'])} matches, opener {fm['home']} vs {fm['away']} ({fm['date']})")
    print(f"total group matches: {total}")


if __name__ == "__main__":
    main()
