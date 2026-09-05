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
