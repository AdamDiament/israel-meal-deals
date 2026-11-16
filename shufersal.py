"""Shufersal price-transparency data: download, parse, cache.

Shufersal publishes per-branch XML files (required by Israel's food price
transparency law) at https://prices.shufersal.co.il. Categories:
  2 = PriceFull (every product + shelf price)
  4 = PromoFull (every active deal)
  5 = Stores    (branch list)
Files are gzipped XML hosted on Azure blob storage with short-lived links.
"""
from __future__ import annotations

import gzip
import html
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

BASE = "https://prices.shufersal.co.il"
CAT_PRICEFULL, CAT_PROMOFULL, CAT_STORES = 2, 4, 5
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
CACHE_HOURS = 6


# ---------------------------------------------------------------- download

def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_file_url(cat: int, store_id: int) -> str:
    """Newest download link for a category at a branch (0 = chain-wide)."""
    page = _get(f"{BASE}/FileObject/UpdateCategory?catID={cat}&storeId={store_id}").decode("utf-8", "replace")
    links = [html.unescape(h) for h in re.findall(r'href="(https://[^"]+\.gz[^"]*)"', page)]
    if not links:
        raise RuntimeError(f"No files listed for category {cat}, store {store_id}")
    # Filenames end in -YYYYMMDD-HHMMSS(.gz) or -YYYYMMDD-NNN; newest = max stamp.
    def stamp(u: str) -> str:
        m = re.search(r"-(\d{8})-(\d+)\.gz", u)
        return (m.group(1) + m.group(2)) if m else ""
    return max(links, key=stamp)


def _decode(raw: bytes) -> str:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig", "replace")


def fetch_xml(cat: int, store_id: int, force: bool = False) -> str:
    """Download (or reuse a cached copy of) the newest file. Returns XML text."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"cat{cat}-store{store_id}.xml")
    if not force and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_HOURS * 3600:
        with open(path, encoding="utf-8") as f:
            return f.read()
    text = _decode(_get(latest_file_url(cat, store_id)))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return text


# ---------------------------------------------------------------- parsing

def _t(el, tag: str) -> str:
    x = el.find(tag)
    return (x.text or "").strip() if x is not None else ""


def _f(s: str, default: float = 0.0) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _root(xml_text: str):
    # Strip the XML declaration so ElementTree accepts an already-decoded str.
    return ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", xml_text))


def parse_stores(xml_text: str) -> list[dict]:
    root = _root(xml_text)
    out = []
    for sub in root.iter("SubChain"):
        sub_name = _t(sub, "SubChainName")
        for s in sub.iter("Store"):
            out.append({
                "id": int(_t(s, "StoreID") or 0),
                "name": _t(s, "StoreName"),
                "address": _t(s, "Address"),
                "subchain": sub_name,
            })
    out.sort(key=lambda s: s["name"])
    return out


def parse_prices(xml_text: str) -> dict[str, dict]:
    items = {}
    for it in _root(xml_text).iter("Item"):
        code = _t(it, "ItemCode")
        price = _f(_t(it, "ItemPrice"))
        if not code or price <= 0:
            continue
        items[code] = {
            "code": code,
            "name": _t(it, "ItemName"),
            "brand": _t(it, "ManufactureName"),
            "price": price,
            "qty": _f(_t(it, "Quantity")),
            "unit": _t(it, "UnitQty"),
            "unit_price": _f(_t(it, "UnitOfMeasurePrice")),
            "uom": _t(it, "UnitOfMeasure"),
            "weighted": _t(it, "bIsWeighted") == "1",
        }
    return items


def _dt(s: str):
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


MAX_ITEMS_PER_PROMO = 30   # bigger = brand-wide blanket discounts, not real deals


def parse_promos(xml_text: str, now: datetime | None = None, include_club: bool = False) -> dict[str, dict]:
    """Best usable deal per item code.

    Shufersal semantics (verified on live files): DiscountedPrice is the total
    price for MinQty units ("2 for 25" -> MinQty 2, DiscountedPrice 25), so
    the effective unit price is DiscountedPrice / MinQty.
    """
    now = now or datetime.now()
    best: dict[str, dict] = {}
    for p in _root(xml_text).iter("Promotion"):
        if _t(p, "AdditionalIsCoupon") == "1" or _t(p, "IsGiftItem") == "1":
            continue
        start, end = _dt(_t(p, "PromotionStartDateTime")), _dt(_t(p, "PromotionEndDateTime"))
        if (start and start > now) or (end and end < now):
            continue
        club = _t(p, "ClubID")
        if not include_club and club and not club.startswith("0"):
            continue
        if any(_t(g, "MinPurchaseAmount") for g in p.iter("Group")):
            continue
        pitems = list(p.iter("PromotionItem"))
        if not pitems or len(pitems) > MAX_ITEMS_PER_PROMO:
            continue
        desc = _t(p, "PromotionDescription")
        for pi in pitems:
            code = _t(pi, "ItemCode")
            min_qty = _f(_t(pi, "MinQty"), 1.0) or 1.0
            total = _f(_t(pi, "DiscountedPrice"), -1)
            if not code or total <= 0:
                continue
            # "2 for ₪30" on a by-weight item means per pack, not per kg — can't price it reliably.
            if _t(pi, "bIsWeighted") == "1" and min_qty > 1:
                continue
            deal = {
                "desc": desc,
                "min_qty": min_qty,
                "deal_total": total,
                "eff_unit": round(total / min_qty, 2),
                "ends": end.strftime("%d/%m") if end else "",
                "club": not club.startswith("0") if club else False,
            }
            if code not in best or deal["eff_unit"] < best[code]["eff_unit"]:
                best[code] = deal
    return best


# ---------------------------------------------------------------- pricing

def line_cost(item: dict, deal: dict | None, qty: float) -> tuple[float, bool]:
    """Cost of buying `qty` (units, or kg if weighted). Returns (cost, deal_applied)."""
    price = item["price"]
    if not deal or deal["eff_unit"] >= price:
        return round(price * qty, 2), False
    if item["weighted"]:
        return round(deal["eff_unit"] * qty, 2), True
    n = int(qty // deal["min_qty"])
    if n == 0:
        return round(price * qty, 2), False
    rest = qty - n * deal["min_qty"]
    return round(n * deal["deal_total"] + rest * price, 2), True


def load_branch(store_id: int, include_club: bool = False, force: bool = False):
    prices = parse_prices(fetch_xml(CAT_PRICEFULL, store_id, force))
    promos = parse_promos(fetch_xml(CAT_PROMOFULL, store_id, force), include_club=include_club)
    promos = {c: d for c, d in promos.items()
              if c in prices and d["eff_unit"] < prices[c]["price"]
              and not (prices[c]["weighted"] and d["min_qty"] > 1)}
    return prices, promos


def load_stores(force: bool = False) -> list[dict]:
    path = os.path.join(CACHE_DIR, "stores.json")
    if not force and os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    stores = parse_stores(fetch_xml(CAT_STORES, 0, force=True))
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stores, f, ensure_ascii=False)
    return stores
