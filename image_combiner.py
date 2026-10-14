"""
Image Link Combiner — standalone logic, kept separate from the marketplace
listing tool's mapping.py on purpose.

Groups a flat list of image URLs by the SKU encoded in their filename
(e.g. 'https://cdn.com/DSM427123Y-M_1.jpg', '..._2.jpg', '..._3.jpg') and joins
each group's URLs — sorted by that trailing number — with a separator.
"""
import re
from collections import defaultdict

FILENAME_RE = re.compile(r"^(?P<sku>.+?)_(?P<idx>\d+)\.(?P<ext>[a-zA-Z0-9]+)$")
PLAIN_FILENAME_RE = re.compile(r"^(?P<sku>.+?)\.(?P<ext>[a-zA-Z0-9]+)$")


def extract_sku_and_index(url):
    """'https://cdn.com/path/DSM427123Y-M_2.jpg?x=1' -> ('DSM427123Y-M', 2).
    Returns (None, None) if the filename doesn't match the SKU_N.ext pattern."""
    filename = str(url).strip().split("/")[-1].split("?")[0].split("#")[0]
    m = FILENAME_RE.match(filename)
    if not m:
        return None, None
    return m.group("sku"), int(m.group("idx"))


def group_and_combine(urls, sep=" ; "):
    """urls: list/iterable of raw URL strings, in any order, where the SKU_N
    pattern is expected to be IN the URL itself.
    Returns (combined: {sku: "url1 ; url2 ; ..."} in first-seen order,
             unmatched: [urls that didn't match the SKU_N.ext pattern]).
    """
    groups = defaultdict(list)  # sku -> [(idx, url), ...]
    order = []
    unmatched = []
    for raw in urls:
        u = str(raw).strip()
        if not u:
            continue
        sku, idx = extract_sku_and_index(u)
        if sku is None:
            unmatched.append(u)
            continue
        if sku not in groups:
            order.append(sku)
        groups[sku].append((idx, u))

    combined = {}
    for sku in order:
        sorted_urls = [u for _, u in sorted(groups[sku], key=lambda t: t[0])]
        combined[sku] = sep.join(sorted_urls)
    return combined, unmatched


def group_and_combine_from_pairs(filename_url_pairs, sep=" ; "):
    """filename_url_pairs: iterable of (filename, url) tuples, e.g. from a CSV
    where the SKU_N pattern lives in a 'File Name' column (like
    '2100329047_4.jpg') but the actual link to use is a *different*,
    unrelated column (like 'https://imageserver.graas.ai/.../32bb_...file.jpg').

    Groups by the SKU parsed out of `filename`, sorts each group by that
    filename's trailing number, and joins the corresponding `url`s.
    Returns (combined: {sku: "url1 ; url2 ; ..."} in first-seen order,
             unmatched: [filenames that didn't match the SKU_N.ext pattern]).
    """
    groups = defaultdict(list)  # sku -> [(idx, url), ...]
    order = []
    unmatched = []
    for filename, url in filename_url_pairs:
        fname = str(filename).strip()
        u = str(url).strip()
        if not fname and not u:
            continue
        sku, idx = extract_sku_and_index(fname)
        if sku is None:
            unmatched.append(fname or u)
            continue
        if sku not in groups:
            order.append(sku)
        groups[sku].append((idx, u))

    combined = {}
    for sku in order:
        sorted_urls = [u for _, u in sorted(groups[sku], key=lambda t: t[0])]
        combined[sku] = sep.join(sorted_urls)
    return combined, unmatched


def extract_sku_plain(filename):
    """'2100329447.jpg' -> '2100329447' (no _N suffix). None if no match."""
    fname = str(filename).strip().split("/")[-1].split("?")[0].split("#")[0]
    m = PLAIN_FILENAME_RE.match(fname)
    return m.group("sku") if m else None


def group_lists_from_pairs(filename_url_pairs):
    """Like group_and_combine_from_pairs but returns ({sku: [urls sorted by _N]}, unmatched)."""
    groups = defaultdict(list)
    order = []
    unmatched = []
    for filename, url in filename_url_pairs:
        fname = str(filename).strip()
        u = str(url).strip()
        if not fname and not u:
            continue
        sku, idx = extract_sku_and_index(fname)
        if sku is None:
            unmatched.append(fname or u)
            continue
        if sku not in groups:
            order.append(sku)
        groups[sku].append((idx, u))
    return {sku: [u for _, u in sorted(groups[sku], key=lambda t: t[0])] for sku in order}, unmatched


def load_shopify_hero_images_from_pairs(filename_url_pairs):
    """Shopify first-image file. Filenames are SKU_N.ext like Lazada/Shopee;
    only the _1 image of each SKU is used (it becomes the first image of the
    Shopify combination). Bare-SKU filenames (no _N) are still accepted as
    the first image. Returns ({sku: url}, unmatched). Last duplicate wins."""
    heroes = {}
    unmatched = []
    for filename, url in filename_url_pairs:
        fname = str(filename).strip()
        u = str(url).strip()
        if not fname and not u:
            continue
        sku, idx = extract_sku_and_index(fname)
        if sku is not None:
            if idx == 1 and u:
                heroes[sku] = u
            continue  # _2, _3... in the Shopify file are not used
        sku = extract_sku_plain(fname)
        if sku is None or not u:
            unmatched.append(fname or u)
            continue
        heroes[sku] = u
    return heroes, unmatched


def build_shopify_combination(hero_images, lazada_shopee_grouped, sep=" ; "):
    """Hero image first, then Lazada/Shopee images minus the first (_1) one.
    No hero -> full Lazada list; hero without Lazada -> hero alone."""
    result = {}
    skus = list(lazada_shopee_grouped.keys()) + [s for s in hero_images if s not in lazada_shopee_grouped]
    for sku in skus:
        lz = lazada_shopee_grouped.get(sku, [])
        hero = hero_images.get(sku)
        urls = ([hero] + lz[1:]) if hero else list(lz)
        result[sku] = sep.join(urls)
    return result
