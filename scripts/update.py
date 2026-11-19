#!/usr/bin/env python3
"""
Què fem a Mataró — robot d'actualització diària.

Llegeix diverses agendes públiques, unifica els esdeveniments i escriu
data/events.json, que és el que llegeix la pàgina.

Fonts:
  1. Dades obertes de la Diputació de Barcelona (biblioteques, museus,
     espais escènics). API oficial, llicència CC BY 4.0.
  2. Webs municipals fetes amb Plone (Cultura Mataró, agenda d'entitats,
     centres cívics). Es proven tres vies en ordre: API REST, calendari
     .ics i RSS.
  3. data/manual.json: esdeveniments i correccions afegits a mà.

Si una font falla, es conserven els darrers esdeveniments bons d'aquella
font, de manera que la pàgina mai no es queda buida.

Traducció opcional: si existeix la variable DEEPL_API_KEY, els títols i
descripcions nous es tradueixen a ES/EN/FR i es guarden a
data/translations.json perquè no es tornin a traduir.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "events.json"
MANUAL = DATA / "manual.json"
TRANSL = DATA / "translations.json"
CACHE = DATA / "sources-cache.json"

UA = "QueFemMataroBot/1.0 (+https://github.com/RoseSunshineG/que-fem-mataro)"
TIMEOUT = 30
TODAY = dt.date.today()
HORIZON = TODAY + dt.timedelta(days=120)

# ---------------------------------------------------------------- helpers

def log(*a):
    print(*a, file=sys.stderr, flush=True)


def fetch(url: str, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def fetch_json(url: str):
    return json.loads(fetch(url, "application/json").decode("utf-8"))


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def short(s: str, n: int = 180) -> str:
    """Primera frase, curta. Mostrem un resum, no el text sencer de la font."""
    s = strip_html(s)
    if not s:
        return ""
    m = re.match(r"(.+?[.!?])(\s|$)", s)
    s = m.group(1) if m and len(m.group(1)) >= 25 else s
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def parse_dt(s: str) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip()
    if re.fullmatch(r"\d{8}T\d{6}Z", s):
        return dt.datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d"):
        if re.fullmatch(r"\d{8}(T\d{6})?", s):
            try:
                return dt.datetime.strptime(s, fmt)
            except ValueError:
                continue
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def to_local(d: dt.datetime) -> dt.datetime:
    if d.tzinfo is None:
        return d
    # Hora peninsular: UTC+1 a l'hivern, UTC+2 a l'estiu (aproximació per DST europeu)
    y = d.year
    last_sun = lambda m: max(dt.date(y, m, day) for day in range(25, 32) if dt.date(y, m, day).weekday() == 6)
    start = dt.datetime(y, 3, last_sun(3).day, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(y, 10, last_sun(10).day, 1, tzinfo=dt.timezone.utc)
    off = 2 if start <= d.astimezone(dt.timezone.utc) < end else 1
    return (d.astimezone(dt.timezone.utc) + dt.timedelta(hours=off)).replace(tzinfo=None)


def hhmm(d: dt.datetime | None) -> str:
    if not d or (d.hour == 0 and d.minute == 0):
        return ""
    return f"{d.hour:02d}:{d.minute:02d}"


# ------------------------------------------------------- classification

BARRI_BY_VENUE = [
    ("antoni comas", "roca"), ("rocafonda", "roca"), ("escorxador", "roca"), ("palau", "roca"),
    ("pompeu fabra", "pcentral"), ("parc central", "pcentral"),
    ("cerdanyola", "cerda"), ("gatassa", "cerda"),
    ("molins", "molins"), ("cirera", "molins"),
    ("pla d en boet", "boet"), ("peramas", "boet"),
    ("llantia", "llantia"), ("via europa", "llantia"),
    ("monumental", "centre"), ("cafe nou", "centre"), ("ateneu", "centre"), ("can gassol", "centre"),
    ("biblioteca popular", "centre"), ("aliança", "centre"), ("alianca", "centre"), ("nau gaudi", "centre"),
    ("presó", "centre"), ("preso", "centre"), ("can marfa", "centre"), ("can serra", "centre"),
    ("cabot i barba", "centre"), ("santa anna", "centre"), ("sant simo", "centre"), ("havana", "centre"),
    ("riera", "centre"), ("can palauet", "centre"),
]

CAT_RULES = [
    ("biblio", ["biblioteca", "hora del conte", "club de lectura", "bibliolab"]),
    ("familia", ["infantil", "familiar", "families", "famílies", "titelles", "nadons", "per a infants"]),
    ("musica", ["concert", "música", "musica", "coral", "jazz", "rock", "orquestra", "havaneres", "dj "]),
    ("escena", ["teatre", "dansa", "espectacle", "circ", "monòleg", "monolog", "improv", "òpera", "opera"]),
    ("fira", ["fira", "mercat", "market", "mostra comercial"]),
    ("festa", ["festa", "gegants", "castells", "cercavila", "correfoc", "diada", "bestiari", "les santes", "carnaval"]),
    ("taller", ["taller", "curs", "classe", "xerrada", "conferència", "trobada", "presentació"]),
    ("cultura", ["exposició", "exposicio", "visita", "museu", "art"]),
]

KID_WORDS = ["infant", "familiar", "família", "families", "nadons", "nens", "nenes", "petits", "canalla", "titelles", "hora del conte"]


def barri_for(venue: str) -> str:
    v = norm(venue)
    for key, b in BARRI_BY_VENUE:
        if norm(key) in v:
            return b
    return "diversos"


def cat_for(*texts: str, default: str = "cultura") -> str:
    t = " ".join(texts).lower()
    for cat, words in CAT_RULES:
        if any(w in t for w in words):
            return cat
    return default


def is_kids(*texts: str) -> bool:
    t = " ".join(texts).lower()
    return any(w in t for w in KID_WORDS)


def price_for(raw: str):
    r = (raw or "").strip()
    if not r:
        return None
    if re.search(r"gratu|gratis|lliure|free|entrada lliure", r, re.I):
        return 0
    m = re.findall(r"\d+(?:[.,]\d+)?", r)
    if m:
        nums = sorted({float(x.replace(",", ".")) for x in m})
        fmt = lambda x: f"{x:g}"
        return f"{fmt(nums[0])} €" if len(nums) == 1 else f"{fmt(nums[0])}–{fmt(nums[-1])} €"
    return None


def make_event(*, title, start, end=None, venue="", url, source, desc="", category=None,
               kids=None, price=None, extra_text=""):
    s = start
    e = end if end and end.date() != s.date() else None
    ev = {
        "t": title.strip(),
        "d": s.date().isoformat(),
        "h": hhmm(s),
        "v": venue.strip() or "Mataró",
        "b": barri_for(venue),
        "c": category or cat_for(title, venue, extra_text, desc),
        "k": bool(kids) if kids is not None else is_kids(title, extra_text),
        "p": price,
        "u": url,
        "s": source,
        "x": {"ca": short(desc)},
    }
    if e:
        ev["e"] = e.date().isoformat()
    return ev


# ----------------------------------------------------- source: Diputació

DIBA = "https://do.diba.cat/api/dataset/{ds}/format/json/camp-municipi_nom-like/Matar/camp-data_inici-greaterequal/{frm}/pag-ini/1/pag-fi/500"
DIBA_DATASETS = {
    "actesbiblioteques": ("Biblioteques de Mataró", "biblio"),
    "actesmuseus": ("Museu de Mataró", "cultura"),
    "escenari": ("Espais escènics", None),
}


def src_diba() -> dict[str, list]:
    out = {}
    # incloem actes que van començar fa poc (exposicions en curs)
    frm = (TODAY - dt.timedelta(days=90)).isoformat()
    for ds, (label, cat) in DIBA_DATASETS.items():
        key = f"diba:{ds}"
        try:
            data = fetch_json(DIBA.format(ds=ds, frm=frm))
            items = []
            for it in data.get("elements", []):
                ga = it.get("grup_adreca") or {}
                if "matar" not in norm(ga.get("municipi_nom", "")):
                    continue
                s, e = parse_dt(it.get("data_inici")), parse_dt(it.get("data_fi"))
                if not s:
                    continue
                if (e or s).date() < TODAY:
                    continue
                pub = " ".join(it.get("public") or []) + " " + " ".join(it.get("tags") or [])
                tipus = " ".join(it.get("tipus") or [])
                venue = (ga.get("adreca_nom") or it.get("acte_organitzadors") or "").replace("Mataró.", "").strip()
                items.append(make_event(
                    title=it.get("titol", ""), start=s, end=e, venue=venue,
                    url=it.get("url_general") or it.get("acte_url") or "",
                    source=label, desc=it.get("descripcio") or it.get("cos") or "",
                    category=cat or cat_for(it.get("titol", ""), tipus, default="escena"),
                    kids=is_kids(pub, tipus, it.get("titol", "")),
                    price=price_for(it.get("preu", "")), extra_text=tipus,
                ))
            out[key] = items
            log(f"OK   {key}: {len(items)}")
        except Exception as ex:  # noqa: BLE001
            log(f"FAIL {key}: {ex}")
    return out


# --------------------------------------------------------- source: Plone

PLONE_SITES = {
    "plone:culturamataro": ("Cultura Mataró", "https://www.culturamataro.cat/agenda-cultura", None),
    "plone:entitats": ("Agenda d'entitats", "https://entitatsmataro.cat/agenda", None),
    "plone:civics": ("Xarxa d'Equipaments Cívics", "https://www.mataro.cat/sites/xarxa-dequipaments-civics-xec/agenda", "taller"),
}


BAD_TITLES = ("indexes all content", "searchabletext")


def title_from_url(url: str) -> str:
    """'copy47_of_mitja-i-ganxet-1' → 'Mitja i ganxet'"""
    slug = urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1])
    slug = re.sub(r"^(copy\d*_of_)+", "", slug)
    slug = re.sub(r"^\d{4}-", "", slug)
    slug = re.sub(r"-\d+$", "", slug)
    return slug.replace("-", " ").replace("_", " ").strip().capitalize()


def good_title(*cands, url=""):
    for c in cands:
        if isinstance(c, str) and c.strip() and not any(b in c.lower() for b in BAD_TITLES):
            return c.strip()
    return title_from_url(url)


def venue_name(loc: str) -> str:
    return re.split(r"\s+-\s+|,", loc or "", maxsplit=1)[0].strip()


def plone_restapi(base: str):
    q = {
        "portal_type": "Event", "sort_on": "start", "b_size": "300",
        "end.query": TODAY.isoformat(), "end.range": "min",
        "metadata_fields": ["start", "end", "location", "Subject", "Description", "Title"],
    }
    url = base.rstrip("/") + "/@search?" + urllib.parse.urlencode(q, doseq=True)
    data = fetch_json(url)
    for it in data.get("items", []):
        yield {
            "title": good_title(it.get("Title"), it.get("title"), url=it.get("@id", "")),
            "start": parse_dt(it.get("start")), "end": parse_dt(it.get("end")),
            "location": it.get("location") or "", "url": it.get("@id", ""),
            "desc": it.get("Description") or it.get("description") or "",
            "tags": " ".join(it.get("Subject") or []),
        }


def ics_unfold(txt: str) -> list[str]:
    lines = []
    for raw in txt.splitlines():
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def ics_unescape(s: str) -> str:
    return s.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def parse_ics(txt: str):
    ev = None
    for line in ics_unfold(txt):
        if line == "BEGIN:VEVENT":
            ev = {}
        elif line == "END:VEVENT" and ev is not None:
            yield {
                "title": ics_unescape(ev.get("SUMMARY", "")),
                "start": parse_dt(ev.get("DTSTART", "")), "end": parse_dt(ev.get("DTEND", "")),
                "location": ics_unescape(ev.get("LOCATION", "")), "url": ev.get("URL", ""),
                "desc": ics_unescape(ev.get("DESCRIPTION", "")), "tags": ics_unescape(ev.get("CATEGORIES", "")),
            }
            ev = None
        elif ev is not None and ":" in line:
            k, v = line.split(":", 1)
            ev[k.split(";", 1)[0].upper()] = v


def plone_ics(base: str):
    return list(parse_ics(fetch(base.rstrip("/") + "/ics_view", "text/calendar").decode("utf-8", "replace")))


def plone_rss(base: str):
    """L'RSS no porta la data de l'esdeveniment: llegim cada fitxa en .ics."""
    xml = fetch(base.rstrip("/") + "/RSS", "application/rss+xml").decode("utf-8", "replace")
    links = re.findall(r"<link>([^<]+)</link>", xml)[1:] or re.findall(r'rdf:about="([^"]+)"', xml)
    out = []
    for link in links[:120]:
        try:
            out += list(parse_ics(fetch(link.rstrip("/") + "/ics_view", "text/calendar").decode("utf-8", "replace")))
            if out and not out[-1]["url"]:
                out[-1]["url"] = link
        except Exception:  # noqa: BLE001
            continue
    return out


def src_plone() -> dict[str, list]:
    out = {}
    for key, (label, base, cat) in PLONE_SITES.items():
        rows, how = None, None
        for how, fn in (("restapi", plone_restapi), ("ics", plone_ics), ("rss", plone_rss)):
            try:
                rows = list(fn(base))
                if rows:
                    break
            except Exception as ex:  # noqa: BLE001
                log(f"     {key} via {how}: {ex}")
        if not rows:
            log(f"FAIL {key}: cap via ha funcionat")
            continue
        items = []
        for r in rows:
            s = r["start"]
            if not s or not r["title"] or not r["url"]:
                continue
            s, e = to_local(s), to_local(r["end"]) if r["end"] else None
            if (e or s).date() < TODAY or s.date() > HORIZON:
                continue
            items.append(make_event(
                title=good_title(r["title"], url=r["url"]), start=s, end=e, venue=venue_name(r["location"]),
                url=r["url"], source=label, desc=r["desc"],
                category=cat_for(r["title"], r["tags"], r["desc"], r["location"], default=cat or "cultura"),
                extra_text=r["tags"] + " " + r["desc"],
            ))
        out[key] = items
        log(f"OK   {key} ({how}): {len(items)}")
    return out


# -------------------------------------------------------- source: manual

def src_manual():
    if not MANUAL.exists():
        return [], {}
    m = json.loads(MANUAL.read_text("utf-8"))
    evs = [e for e in m.get("events", []) if (e.get("e") or e["d"]) >= TODAY.isoformat()]
    for e in evs:
        e.setdefault("s", "Què fem a Mataró")
        e.setdefault("b", barri_for(e.get("v", "")))
        e.setdefault("k", False)
        e.setdefault("p", None)
        e.setdefault("h", "")
        e.setdefault("x", {"ca": ""})
    return evs, m.get("overrides", {})


# ----------------------------------------------------------- translation

def translate_all(events):
    key = os.environ.get("DEEPL_API_KEY", "").strip()
    cache = json.loads(TRANSL.read_text("utf-8")) if TRANSL.exists() else {}
    todo = set()
    for e in events:
        for txt in (e["t"], e["x"].get("ca", "")):
            if txt and txt not in cache:
                todo.add(txt)
    if key and todo:
        host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
        texts = sorted(todo)
        for lang, code in (("es", "ES"), ("en", "EN-GB"), ("fr", "FR")):
            for i in range(0, len(texts), 40):
                chunk = texts[i:i + 40]
                body = urllib.parse.urlencode([("text", t) for t in chunk] + [("source_lang", "CA"), ("target_lang", code)]).encode()
                req = urllib.request.Request(f"https://{host}/v2/translate", data=body,
                                             headers={"Authorization": f"DeepL-Auth-Key {key}", "User-Agent": UA})
                try:
                    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                        res = json.loads(r.read())["translations"]
                    for src, tr in zip(chunk, res):
                        cache.setdefault(src, {})[lang] = tr["text"]
                except Exception as ex:  # noqa: BLE001
                    log(f"DeepL {lang}: {ex}")
                    break
        TRANSL.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
    elif todo:
        log(f"Sense DEEPL_API_KEY: {len(todo)} textos queden en català")
    for e in events:
        t = cache.get(e["t"], {})
        if t:
            e.setdefault("tt", {})
            for lang, v in t.items():
                e["tt"].setdefault(lang, v)
        d = cache.get(e["x"].get("ca", ""), {})
        for lang, v in d.items():
            e["x"].setdefault(lang, v)


# ------------------------------------------------------------------ main

PRIORITY = ["manual", "plone:culturamataro", "plone:entitats", "plone:civics",
            "diba:actesbiblioteques", "diba:actesmuseus", "diba:escenari"]


def main():
    DATA.mkdir(exist_ok=True)
    prev = json.loads(OUT.read_text("utf-8")) if OUT.exists() else {}
    prev_by = json.loads(CACHE.read_text("utf-8")) if CACHE.exists() else {}

    fresh = {}
    fresh.update(src_diba())
    fresh.update(src_plone())
    manual, overrides = src_manual()

    by_source, status = {}, {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    for key in PRIORITY[1:]:
        if key in fresh:
            by_source[key] = fresh[key]
            status[key] = {"ok": True, "n": len(fresh[key]), "at": now}
        else:
            kept = [e for e in prev_by.get(key, []) if (e.get("e") or e["d"]) >= TODAY.isoformat()]
            by_source[key] = kept
            old = prev.get("status", {}).get(key, {})
            status[key] = {"ok": False, "n": len(kept), "at": old.get("at")}

    merged, seen = [], set()
    for key in PRIORITY:
        for e in (manual if key == "manual" else by_source.get(key, [])):
            sig = (norm(e["t"])[:40], e["d"])
            if sig in seen:
                continue
            seen.add(sig)
            merged.append(dict(e))

    for e in merged:
        if e["t"] in overrides:
            o = overrides[e["t"]]
            if "x" in o:
                e["x"] = {**e["x"], **o["x"]}
            e.update({k: v for k, v in o.items() if k != "x"})

    translate_all(merged)
    merged.sort(key=lambda e: (e["d"], e["h"], e["t"]))

    for k, v in status.items():
        log(f"  {'✓' if v['ok'] else '✗'} {k}: {v['n']}")
    if not merged:
        sys.exit("Cap esdeveniment: no sobreescrivim res")

    payload = {"updated": now, "status": status, "events": merged}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
    CACHE.write_text(json.dumps(by_source, ensure_ascii=False, indent=1), "utf-8")
    log(f"\nTotal: {len(merged)} esdeveniments → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
