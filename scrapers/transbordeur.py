"""Scraper for Le Transbordeur (transbordeur.fr/agenda).

The agenda page is a JS-rendered SPA. Diagnostic confirmed the WordPress
REST API exposes /wp/v2/evenement (singular, French). This scraper hits
that endpoint to get dates, titles and images.

Time extraction: the time is NOT in the WP REST API response — it lives
in the WordPress theme template. Strategy: fetch each event's detail page
and parse the time from the rendered HTML.

On the detail page, the time appears in two reliable locations:
  1. .Single__hero-cover__contain  →  two .ts-label divs (date + time)
  2. li containing "ouverture des portes" → sibling .ts-h2 with "18h00"
"""
from typing import List, Optional, Any
from datetime import datetime, date as Date
import re
import sys
import time as _time
import json
import requests
from bs4 import BeautifulSoup

from .base import Event, iso

VENUE = "Le Transbordeur"
SLUG  = "transbordeur"
SITE  = "https://www.transbordeur.fr"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

DATE_KEY_CANDIDATES = (
    "date_event", "date_evenement", "date_concert", "event_date",
    "date_debut", "start_date", "date", "date_de_l_evenement",
    "date_de_levenement", "jour", "concert_date", "date_spectacle",
)


def _strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    return BeautifulSoup(html_text, "html.parser").get_text(" ", strip=True)


def _normalize_date(val: Any) -> Optional[Date]:
    if not val or not isinstance(val, str):
        return None
    val = val.strip()
    if re.fullmatch(r"\d{8}", val):
        try:
            return Date(int(val[:4]), int(val[4:6]), int(val[6:8]))
        except ValueError:
            return None
    try:
        cleaned = val.replace("Z", "+00:00").replace(" ", "T")
        return datetime.fromisoformat(cleaned).date()
    except ValueError:
        pass
    try:
        return Date.fromisoformat(val[:10])
    except ValueError:
        pass
    m = re.match(r"(\d{2})[/-](\d{2})[/-](\d{4})", val)
    if m:
        d, mo, y = m.groups()
        try:
            return Date(int(y), int(mo), int(d))
        except ValueError:
            return None
    return None


def _extract_date(post: dict) -> Optional[Date]:
    acf = post.get("acf") or {}
    if isinstance(acf, dict):
        for k in DATE_KEY_CANDIDATES:
            d = _normalize_date(acf.get(k))
            if d:
                return d
    meta = post.get("meta") or {}
    if isinstance(meta, dict):
        for k in DATE_KEY_CANDIDATES + tuple("_" + x for x in DATE_KEY_CANDIDATES):
            d = _normalize_date(meta.get(k))
            if d:
                return d
    for k in DATE_KEY_CANDIDATES:
        d = _normalize_date(post.get(k))
        if d:
            return d
    return None


def _parse_hhmm(text: str) -> Optional[str]:
    """Extract HH:MM from text. Accepts 12h-03h range (concert hours)."""
    # "18:00" or "18h00"
    m = re.search(r"\b(\d{1,2})[h:](\d{2})\b", text)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if (12 <= hh <= 23) or (hh <= 3):
            return f"{hh:02d}:{mm:02d}"
    # "18h" alone
    m = re.search(r"\b(\d{1,2})h\b", text)
    if m:
        hh = int(m.group(1))
        if (12 <= hh <= 23) or (hh <= 3):
            return f"{hh:02d}:00"
    return None


def _fetch_detail_time(url: str) -> Optional[str]:
    """Fetch a /evenement/<slug>/ page and extract the show time.

    Priority:
      1. .Single__hero-cover__contain: contains [date_label, dot, time_label]
         → the second .ts-label is the time ("18:00")
      2. <li> containing "ouverture" / "portes" → sibling element with time
      3. General search in page visible text
    """
    try:
        r = requests.get(url, timeout=12, headers=HEADERS)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")

        # Method 1: hero section "date · time" pill
        hero = soup.select_one(".Single__hero-cover__contain")
        if hero:
            labels = hero.select(".ts-label")
            if len(labels) >= 2:
                t = _parse_hhmm(labels[1].get_text(" ", strip=True))
                if t:
                    return t

        # Method 2: "ouverture des portes" list item
        for li in soup.find_all("li"):
            li_text = li.get_text(" ", strip=True).lower()
            if "ouverture" in li_text or "portes" in li_text:
                t = _parse_hhmm(li.get_text(" ", strip=True))
                if t:
                    return t

        # Method 3: any ts-h2 that looks like a time ("18h00") in the bg-primary block
        for section in soup.select(".bg-primary"):
            for h2 in section.select(".ts-h2"):
                t = _parse_hhmm(h2.get_text(" ", strip=True))
                if t:
                    return t

        # Method 4: general time search in first 800 chars of visible text
        visible = soup.get_text(" ", strip=True)
        return _parse_hhmm(visible[:800])

    except requests.RequestException:
        return None


def _extract_image(post: dict) -> Optional[str]:
    embedded = post.get("_embedded") or {}
    media_list = embedded.get("wp:featuredmedia") or []
    if media_list and isinstance(media_list[0], dict):
        m = media_list[0]
        url = m.get("source_url")
        if isinstance(url, str) and url.startswith("http"):
            return url
        sizes = (m.get("media_details") or {}).get("sizes") or {}
        for size_name in ("medium", "large", "full", "thumbnail"):
            size = sizes.get(size_name) or {}
            url = size.get("source_url")
            if isinstance(url, str) and url.startswith("http"):
                return url
    return None


def _diagnose_first_post():
    print("=" * 60, file=sys.stderr)
    print("DIAGNOSTIC: Le Transbordeur — inspecting first post", file=sys.stderr)
    try:
        resp = requests.get(SITE + "/wp-json/wp/v2/evenement?per_page=1&_embed=1",
                            timeout=20, headers=HEADERS)
        if resp.status_code != 200:
            print(f"  status: {resp.status_code}", file=sys.stderr)
            return
        data = resp.json()
        if not isinstance(data, list) or not data:
            print("  Empty result.", file=sys.stderr)
            return
        post = data[0]
        print(f"  Top-level keys: {sorted(post.keys())}", file=sys.stderr)
        if "acf" in post and isinstance(post["acf"], dict):
            print(f"  acf keys: {sorted(post['acf'].keys())}", file=sys.stderr)
            for k, v in list(post["acf"].items()):
                v_repr = repr(v)
                print(f"    acf[{k!r}] = {v_repr[:100]}", file=sys.stderr)
        title = post.get("title")
        if isinstance(title, dict):
            title = title.get("rendered", "")
        link = post.get("link", "")
        print(f"  title: {_strip_html(str(title))[:80]!r}", file=sys.stderr)
        print(f"  link:  {link!r}", file=sys.stderr)
    except (requests.RequestException, ValueError, json.JSONDecodeError) as e:
        print(f"  Failed: {e}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)


def fetch() -> List[Event]:
    url = SITE + "/wp-json/wp/v2/evenement?per_page=100&_embed=1"
    try:
        resp = requests.get(url, timeout=30, headers=HEADERS)
    except requests.RequestException as e:
        print(f"[Transbordeur] request failed: {e}", file=sys.stderr)
        return []

    if resp.status_code != 200:
        print(f"[Transbordeur] /wp/v2/evenement returned {resp.status_code}",
              file=sys.stderr)
        return []

    try:
        data = resp.json()
    except ValueError:
        print("[Transbordeur] non-JSON response", file=sys.stderr)
        return []

    if not isinstance(data, list):
        print(f"[Transbordeur] unexpected JSON type: {type(data).__name__}",
              file=sys.stderr)
        return []

    # Pass 1: collect stubs from API (no time yet)
    stubs: List[dict] = []
    today = Date.today()
    for post in data:
        if not isinstance(post, dict):
            continue
        d = _extract_date(post)
        if not d or d < today:
            continue

        title_field = post.get("title")
        if isinstance(title_field, dict):
            title = _strip_html(title_field.get("rendered", "")).strip()
        else:
            title = _strip_html(str(title_field or "")).strip()
        if not title:
            continue

        link = post.get("link") or SITE + "/agenda/"
        image = _extract_image(post)
        stubs.append({"d": d, "title": title, "url": link, "image": image})

    if not stubs:
        _diagnose_first_post()
        return []

    # Pass 2: fetch each detail page to extract time
    events: List[Event] = []
    seen: set = set()
    for i, stub in enumerate(stubs):
        if i > 0:
            _time.sleep(0.4)
        time_str = _fetch_detail_time(stub["url"])
        ev = Event(
            venue=VENUE,
            venue_slug=SLUG,
            title=stub["title"],
            subtitle=None,
            category="concert",
            date_start=iso(stub["d"]),
            date_end=None,
            time=time_str,
            url=stub["url"],
            image=stub["image"],
        )
        if ev.id not in seen:
            seen.add(ev.id)
            events.append(ev)

    events.sort(key=lambda e: (e.date_start, e.time or "00:00"))
    return events


if __name__ == "__main__":
    for e in fetch():
        print(e.date_start, e.time or "  -  ", "·", e.title, "·", e.url)
