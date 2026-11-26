#!/usr/bin/env python3
"""Refresh the ARAM: Mayhem data in data/.

Sources (public pages only, fetched politely one at a time):
  - Riot Data Dragon: patch, champion list, champion icons
  - op.gg ARAM: Mayhem pages: ranked augments, #1 core build, top boots
  - Blitz ARAM: Mayhem page: per-champion augment/item tiers (1 = best),
    overall champion tier, augment descriptions and rarity

Usage:
  python3 scripts/update.py                 # all champions minus config.json "exclude"
  python3 scripts/update.py --only jinx,ahri
  python3 scripts/update.py --delay 0.5

Standard library only. A champion whose fetch or parse fails keeps its
previous data file; the run exits non-zero if more than 15% fail.
"""
import argparse
import datetime as dt
import gzip
import html
import json
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CHAMP_DIR = DATA / "champ"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
OPGG = "https://op.gg/lol/modes/aram-mayhem/{slug}/{page}"
BLITZ = "https://blitz.gg/lol/champions/{id}/aram-mayhem"
DDRAGON = "https://ddragon.leagueoflegends.com"
CDRAGON_POOLS = ("https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/"
                 "global/default/v1/augment-lists.json")
CDRAGON_AUGS = ("https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/"
                "global/default/v1/cherry-augments.json")
AUG_DESC = {}  # augment name -> description, written to data/augments.json
RARITY = {0: "silver", 1: "gold", 2: "prismatic"}
CD_RARITY = {"kSilver": "silver", "kGold": "gold", "kPrismatic": "prismatic"}


# ---------------------------------------------------------------- fetching

class Fetcher:
    def __init__(self, delay):
        self.delay = delay
        self.last = 0.0

    def get(self, url, tries=3):
        for attempt in range(tries):
            wait = self.delay - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Encoding": "gzip",
                "Accept-Language": "en-US,en;q=0.9"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                    return body.decode("utf-8")
            except Exception as e:  # noqa: BLE001 - retry anything transient
                code = getattr(e, "code", None)
                if code in (403, 404) or attempt == tries - 1:
                    raise
                time.sleep(3 * (attempt + 1))
        raise RuntimeError("unreachable")

    def json(self, url):
        return json.loads(self.get(url))


# ---------------------------------------------------------------- mini DOM

VOID = {"img", "br", "col", "input", "meta", "link", "source", "hr", "area",
        "base", "wbr", "path", "stop", "rect", "circle", "use"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def iter(self, tag=None):
        for c in self.children:
            if isinstance(c, Node):
                if tag is None or c.tag == tag:
                    yield c
                yield from c.iter(tag)

    def text(self):
        out = []
        for c in self.children:
            out.append(c if isinstance(c, str) else c.text())
        return re.sub(r"\s+", " ", "".join(out)).strip()


class DOM(HTMLParser):
    def __init__(self, src):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {}, None)
        self.cur = self.root
        self.skip = 0
        self.feed(src)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        if self.skip:
            return
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        if not self.skip:
            self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in VOID:
            return
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        if not self.skip:
            self.cur.children.append(data)


def clean_src(src):
    return src.split("?")[0] if src else src


def item_id(src):
    m = re.search(r"/item/(\d+)\.png", src or "")
    return int(m.group(1)) if m else None


def tables_by_heading(dom):
    """Map lower-case <th> text -> list of <table> nodes."""
    out = {}
    for t in dom.root.iter("table"):
        th = next(t.iter("th"), None)
        head = th.text() if th is not None else ""
        if not head:  # items page: heading is the <section>'s first <div>
            sec = t.parent
            while sec is not None and sec.tag != "section":
                sec = sec.parent
            first = next((c for c in sec.children if isinstance(c, Node)), None) if sec else None
            head = first.text() if first is not None else ""
        if head:
            out.setdefault(head.lower(), []).append(t)
    return out


def item_rows(table):
    """Each <tr> in tbody -> list of {id, name, icon, count?}."""
    rows = []
    tbody = next(table.iter("tbody"), None)
    if tbody is None:
        return rows
    for tr in tbody.iter("tr"):
        row = []
        for img in tr.iter("img"):
            iid = item_id(img.attrs.get("src"))
            if iid is None:
                continue
            entry = {"id": iid, "name": html.unescape(img.attrs.get("alt", ""))}
            # starter counts: a sibling <div>2</div> after the image wrapper
            wrap = img.parent.parent if img.parent else None
            if wrap is not None:
                tail = [c for c in wrap.children if isinstance(c, Node) and c.tag == "div"]
                if tail and tail[-1].text().isdigit():
                    entry["count"] = int(tail[-1].text())
            row.append(entry)
        if row:
            rows.append(row)
    return rows


def single_item_rows(table):
    """Items/boots tables on the items page: one item per row, name in text."""
    rows = []
    tbody = next(table.iter("tbody"), None)
    if tbody is None:
        return rows
    for tr in tbody.iter("tr"):
        img = next(tr.iter("img"), None)
        if img is None:
            continue
        iid = item_id(img.attrs.get("src"))
        if iid is None:
            continue
        name = html.unescape(img.attrs.get("alt", "")) or tr.text()
        rows.append({"id": iid, "name": name})
    return rows


# ---------------------------------------------------------------- op.gg

def parse_opgg_items(src):
    dom = DOM(src)
    tabs = tables_by_heading(dom)

    def first(name):
        return (tabs.get(name) or [None])[0]

    core = first("core builds")
    if core is None:
        raise ValueError("op.gg items page: core builds not found")
    out = {
        "core": item_rows(core),
        "boots": single_item_rows(first("boots")) if first("boots") else [],
    }
    if not out["core"]:
        raise ValueError("op.gg items page: empty core builds")
    return out


def parse_opgg_augments(src):
    dom = DOM(src)
    augs = []
    seen = set()
    for img in dom.root.iter("img"):
        s = img.attrs.get("src", "")
        if "/aram-augment/" not in s:
            continue
        name = html.unescape(img.attrs.get("alt", ""))
        if not name or name in seen:
            continue
        # only list entries (li ancestors), not the champion header art
        p = img.parent
        while p is not None and p.tag != "li":
            p = p.parent
        if p is None:
            continue
        seen.add(name)
        augs.append({"name": name, "icon": clean_src(s)})
    if not augs:
        raise ValueError("op.gg augments page: no augments found")
    return augs


def opgg_slugs(src):
    return sorted(set(re.findall(r'href="/lol/modes/aram-mayhem/([a-z0-9]+)/build"', src)))


# ---------------------------------------------------------------- Blitz

BLITZ_BLOB = re.compile(
    r'<script type="application/json" data-sveltekit-fetched data-url="([^"]+)"[^>]*>(.*?)</script>',
    re.S)


def blitz_blobs(src):
    out = {}
    for url, body in BLITZ_BLOB.findall(src):
        url = html.unescape(url)
        try:
            out[url] = json.loads(json.loads(body)["body"])
        except (ValueError, KeyError):
            continue
    return out


def parse_blitz(src):
    blobs = blitz_blobs(src)
    champ = augs = None
    for url, data in blobs.items():
        if "aram_mayhem_champion" in url:
            rows = data.get("data") or []
            champ = rows[0] if rows else None
        elif "/mayham/" in url and "augments" in url:
            augs = data
    return champ, augs


def cdragon_asset(path):
    """/lol-game-data/assets/ASSETS/UX/... -> CommunityDragon URL (lower-case)."""
    if not path or "/lol-game-data/assets/" not in path:
        return None
    return ("https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/"
            "global/default/" + path.split("/lol-game-data/assets/", 1)[1].lower())


def clean_desc(s):
    s = s.replace("%i:meleeActive%", "Melee ").replace("%i:rangedActive%", "Ranged ")
    s = re.sub(r"%i:[^%]+%", "", s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


# ---------------------------------------------------------------- main

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8")


class PoolFilter:
    """Keep only augments in the live Mayhem pool (Riot's augment-lists.json).

    Riot ships several Mayhem pools (KIWI, KIWI_JADE, ...); config.json "pool"
    names the one in rotation. Augments CommunityDragon can't identify are kept.
    """

    def __init__(self, pool_ids, cherry):
        self.pool = pool_ids  # set of lower-case augmentNameIds, or None = keep all
        self.ids = {}
        for a in cherry:
            nid = a.get("augmentNameId", "").lower()
            for k in (a.get("nameTRA", "").lower(),
                      a.get("augmentLargeIconPath", "").rsplit("/", 1)[-1].lower()):
                if k:
                    self.ids.setdefault(k, set()).add(nid)

    def keep(self, name, icon):
        if self.pool is None:
            return True
        ids = self.ids.get(name.lower(), set()) | self.ids.get(icon.rsplit("/", 1)[-1].lower(), set())
        return not ids or bool(ids & self.pool)


def build_champion(f, champ, aug_db, cd_augs, pool):
    slug = champ["id"].lower()
    items = parse_opgg_items(f.get(OPGG.format(slug=slug, page="items")))
    augs = parse_opgg_augments(f.get(OPGG.format(slug=slug, page="augments")))

    bchamp = None
    try:
        bchamp, baugs = parse_blitz(f.get(BLITZ.format(id=champ["id"])))
        if baugs and not aug_db:
            aug_db.update(baugs)
    except Exception as e:  # noqa: BLE001 - Blitz tiers are optional extras
        print(f"    blitz: {e}", file=sys.stderr)

    btiers = (bchamp or {}).get("data", {})
    aug_tier = btiers.get("augments", {})
    item_tier = btiers.get("items", {})
    by_name = {v.get("displayName", "").lower(): k for k, v in aug_db.items()}
    by_icon = {v.get("iconLarge", "").lower(): k for k, v in aug_db.items()}

    augs = [a for a in augs if pool.keep(a["name"], a["icon"])]
    aug_out = []
    for rank, a in enumerate(augs, 1):
        key = by_name.get(a["name"].lower()) or by_icon.get(a["icon"].rsplit("/", 1)[-1].lower())
        meta = aug_db.get(key, {}) if key else {}
        cd = cd_augs.get(a["name"].lower()) or cd_augs.get(a["icon"].rsplit("/", 1)[-1].lower()) or {}
        if key is None and cd.get("id"):
            key = str(cd["id"])
        aug_out.append({
            "rank": rank,
            "id": int(key) if key else None,
            "name": a["name"],
            "icon": a["icon"],
            "icon2": cdragon_asset(cd.get("augmentLargeIconPath")),
            "rarity": RARITY.get(meta.get("rarity")) or CD_RARITY.get(cd.get("rarity")),
            "tier": (aug_tier.get(key) or {}).get("tier") if key else None,
        })
        desc = clean_desc(meta.get("description") or meta.get("tooltip") or "")
        if desc:
            AUG_DESC[a["name"]] = desc

    def tier_of(iid):
        return (item_tier.get(str(iid)) or {}).get("tier")

    for it in items["boots"]:
        it["tier"] = tier_of(it["id"])

    return {
        "id": champ["id"],
        "key": int(champ["key"]),
        "name": champ["name"],
        "tier": btiers.get("tier"),
        "patch": (bchamp or {}).get("patch"),
        "boots": items["boots"][:3],
        "core": items["core"][:1],
        "augments": aug_out,
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated champion ids/slugs (e.g. jinx,monkeyking)")
    ap.add_argument("--delay", type=float, default=None, help="seconds between requests")
    args = ap.parse_args()

    cfg = load_json(ROOT / "config.json", {})
    exclude = {s.lower() for s in cfg.get("exclude", [])}
    f = Fetcher(args.delay if args.delay is not None else cfg.get("delay", 1.0))

    version = f.json(f"{DDRAGON}/api/versions.json")[0]
    champs = f.json(f"{DDRAGON}/cdn/{version}/data/en_US/champion.json")["data"]
    champs = sorted(champs.values(), key=lambda c: c["name"])

    targets = [c for c in champs if c["id"].lower() not in exclude]
    if args.only:
        want = {s.strip().lower() for s in args.only.split(",")}
        targets = [c for c in champs if c["id"].lower() in want or c["name"].lower() in want]
        if not targets:
            sys.exit(f"--only matched nothing: {args.only}")

    aug_db = {}
    cd_augs = {}
    cherry = []
    try:  # CommunityDragon: rarity/ids for augments Blitz hasn't catalogued yet
        cherry = f.json(CDRAGON_AUGS)
        for a in cherry:
            cd_augs.setdefault(a.get("nameTRA", "").lower(), a)
            cd_augs.setdefault(a.get("augmentLargeIconPath", "").rsplit("/", 1)[-1].lower(), a)
    except Exception as e:  # noqa: BLE001
        print(f"cdragon augments: {e}", file=sys.stderr)
    pool_name = cfg.get("pool", "KIWI")
    pool_ids = None
    try:
        lists = {g["modeName"]: g["augmentList"] for g in f.json(CDRAGON_POOLS)}
        pool_ids = {p.rsplit("/", 1)[-1].lower() for p in lists[pool_name]}
        print(f"Augment pool {pool_name}: {len(pool_ids)} augments")
    except Exception as e:  # noqa: BLE001 - never drop augments on a lookup failure
        print(f"augment pool {pool_name!r} unavailable, keeping all augments: {e}", file=sys.stderr)
    pool = PoolFilter(pool_ids if cherry else None, cherry)
    failed, done = [], 0
    t0 = time.time()
    for i, c in enumerate(targets, 1):
        slug = c["id"].lower()
        print(f"[{i}/{len(targets)}] {c['name']}", flush=True)
        try:
            data = build_champion(f, c, aug_db, cd_augs, pool)
            write_json(CHAMP_DIR / f"{slug}.json", data)
            done += 1
        except Exception as e:  # noqa: BLE001 - keep previous data, report
            print(f"    FAILED: {e}", file=sys.stderr, flush=True)
            failed.append(c["name"])

    # Index of every champion that has a data file (excluded champs drop out
    # of the index but their old file is left alone).
    index = []
    for c in champs:
        slug = c["id"].lower()
        if slug in exclude:
            continue
        d = load_json(CHAMP_DIR / f"{slug}.json", None)
        if not d:
            continue
        index.append({"slug": slug, "id": c["id"], "name": c["name"],
                      "tier": d.get("tier"), "updated": d.get("updated"),
                      "top": [x["id"] for x in (d.get("core") or [[]])[0]]})
    write_json(DATA / "champions.json", index)
    descs = load_json(DATA / "augments.json", {})
    descs.update(AUG_DESC)
    write_json(DATA / "augments.json", dict(sorted(descs.items())))

    meta = load_json(DATA / "meta.json", {})
    meta.update({
        "ddragon": version,
        "patch": next((load_json(CHAMP_DIR / f"{t['id'].lower()}.json", {}).get("patch")
                       for t in targets if (CHAMP_DIR / f"{t['id'].lower()}.json").exists()), meta.get("patch")),
        "lastRun": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "lastRunScope": "partial" if args.only else "full",
        "updated": done,
        "failed": failed,
        "champions": len(index),
    })
    write_json(DATA / "meta.json", meta)

    mins = (time.time() - t0) / 60
    print(f"\nDone in {mins:.1f} min: {done} updated, {len(failed)} failed"
          + (f" ({', '.join(failed)})" if failed else ""))
    if targets and len(failed) / len(targets) > 0.15:
        sys.exit(1)


if __name__ == "__main__":
    main()
