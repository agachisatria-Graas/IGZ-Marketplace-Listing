"""
Mapping logic for the Columbia Marketplace Listing Tool.

Columbia mixes apparel, footwear and bags, and mixes parent/variant listings
with single-SKU listings, so:

1. LISTING SHAPE (decided per Parent SKU group)
   - A group with ONE SKU, or rows whose Listing Type is "No Variation",
     is a SINGLE listing: no variation fields.
   - Otherwise it's a variant listing. The axes are detected from the data:
     Color is an axis only if the group has more than one colour, Size only
     if it has more than one size (so a one-colour jacket varies by Size
     only; a one-size multi-colour item varies by Color only).
   - Shopee / Lazada / Shopify use the Toms grouped layout (one header row
     per Parent SKU, then one row per variant SKU).
2. DESCRIPTION = "Main Description*" + line break + "Long Description".
   Shopee & TikTok use plain text; Lazada, Shopify & Zalora use HTML script.
3. CATEGORY = the category file's "Words in Title" found in the item title AND
   Gender equal. No match -> the Category ID stays empty.
4. TIKTOK is split into 5 files by product kind: Men's Apparels, Women's
   Apparels, Footwears, Bags, Accessories (classify_product_kind).
"""
import re

from mapping import (
    IMG_SEP, split_images, merged_images, zalora_image_list, parse_specs,
    to_number, parent_image_html_snippet, ensure_brand_prefix, text_to_html,
    lazada_short_description, ZALORA_HEADERS, SHOPEE_HEADERS, LAZADA_HEADERS,
)
from herschel_mapping import extract_material, resolve_color_families, TIKTOK_HEADERS as HERSCHEL_TIKTOK_HEADERS
from toms_mapping import TIKTOK_HEADERS as TOMS_TIKTOK_HEADERS

BRAND = "Columbia"
SHOPEE_SHIPPING = ("Reguler (Cashless):18000.00, Hemat:18000.00, Agen Shopee:18000.00, "
                   "Shopee Xpress Point:18000.00, Instant:18000.00")
GENDER_TO_ZALORA = {"MENS": "Men", "MEN": "Men", "WOMENS": "Women", "WOMEN": "Women", "UNISEX": "Unisex"}
SHOPIFY_HEADERS = [
    "Seller SKU", "Product Name", "Product Description 1", "Product Description 2",
    "Product Description 3", "Total variation", "Variation 1", "Variation 2", "Variation 3",
    "RRP", "compare At Price", "Currency Code", "Quantity", "Category ID",
    "Product Image URL(s)", "Weight (Kg)", "Vendor", "Product Type", "Taxable", "tags",
]


def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().upper())


# ---------------------------------------------------------------------------
# Product kind (drives the 5-way TikTok split, Lazada material key)
# ---------------------------------------------------------------------------

def _rx(words):
    return re.compile(r"(?<![a-z])(?:" + "|".join(words) + r")(?![a-z])", re.I)


_FOOTWEAR = _rx(["boots?", "shoes?", "sneakers?", "sandals?", "slippers?", "loafers?", "clogs?",
                 "flip[- ]?flops?", "trainers?", "slides?", "moccasins?", "footwear", "booties"])
_BAGS = _rx(["bags?", "backpacks?", "daypacks?", "duffel", "duffle", "totes?", "slings?",
             "cross[- ]?body", "messengers?", "satchels?", "rucksacks?", "luggage", "briefcases?",
             "(?:waist|hip|lumbar|fanny|chest|day) packs?"])
_ACCESSORIES = _rx(["hats?", "caps?", "beanies?", "visors?", "boonies?", "shawls?", "scarf", "scarves",
                    "gaiters?", "neck", "buffs?", "gloves?", "mittens?", "socks?", "headbands?",
                    "belts?", "umbrellas?", "towels?", "bottles?", "wallets?", "sunglasses", "bandanas?",
                    "balaclavas?", "ear ?warmers?", "wristbands?", "lanyards?", "keychains?", "pouch"])
_APPAREL = _rx(["jackets?", "parkas?", "coats?", "vests?", "shirts?", "tees?", "t[- ]shirts?", "polos?",
                "tops?", "tanks?", "hoodies?", "fleece", "pullovers?", "sweaters?", "sweatshirts?",
                "pants?", "shorts?", "tights?", "leggings?", "joggers?", "skirts?", "skorts?", "dress",
                "dresses", "jumpsuits?", "bras?", "base ?layers?", "bottoms?", "capris?", "trousers?",
                "overalls?", "windbreakers?", "shells?", "anoraks?", "cardigans?", "thermals?",
                "long sleeves?", "short sleeves?", "ponchos?", "rain ?suits?", "bibs?"])


def classify_product_kind(title):
    """-> 'footwear' | 'bags' | 'accessories' | 'apparel'. Order matters
    (a 'Boot' is footwear even if its name has other words). Anything that
    matches nothing falls into 'accessories'."""
    t = str(title or "")
    if _FOOTWEAR.search(t):
        return "footwear"
    if _BAGS.search(t):
        return "bags"
    if _ACCESSORIES.search(t):
        return "accessories"
    if _APPAREL.search(t):
        return "apparel"
    return "accessories"


def tiktok_output_key(row):
    """Which of the 5 TikTok files a row belongs to."""
    kind = classify_product_kind(row.get("title"))
    if kind == "apparel":
        return "tiktok_womens_apparel" if _norm(row.get("gender")) in ("WOMENS", "WOMEN") else "tiktok_mens_apparel"
    return {"footwear": "tiktok_footwear", "bags": "tiktok_bags", "accessories": "tiktok_accessories"}[kind]


TIKTOK_OUTPUTS = {
    "tiktok_mens_apparel": "TikTok Men's Apparels",
    "tiktok_womens_apparel": "TikTok Women's Apparels",
    "tiktok_footwear": "TikTok Footwears",
    "tiktok_bags": "TikTok Bags",
    "tiktok_accessories": "TikTok Accessories",
}
TIKTOK_FILE_NAMES = {
    "tiktok_mens_apparel": "Columbia_TikTok_Mens_Apparels_Listing_File.xlsx",
    "tiktok_womens_apparel": "Columbia_TikTok_Womens_Apparels_Listing_File.xlsx",
    "tiktok_footwear": "Columbia_TikTok_Footwears_Listing_File.xlsx",
    "tiktok_bags": "Columbia_TikTok_Bags_Listing_File.xlsx",
    "tiktok_accessories": "Columbia_TikTok_Accessories_Listing_File.xlsx",
}

_TIKTOK_CORE = TOMS_TIKTOK_HEADERS[:TOMS_TIKTOK_HEADERS.index("Asuransi pengiriman") + 1]
TIKTOK_HEADERS_BY_OUTPUT = {
    "tiktok_footwear": TOMS_TIKTOK_HEADERS,            # same attribute columns as the Toms (footwear) file
    "tiktok_bags": HERSCHEL_TIKTOK_HEADERS,            # same attribute columns as the Herschel (bag) file
    "tiktok_mens_apparel": _TIKTOK_CORE + ["Bahan", "Pola", "Gaya", "Fitur"],
    "tiktok_womens_apparel": _TIKTOK_CORE + ["Bahan", "Pola", "Gaya", "Fitur"],
    "tiktok_accessories": _TIKTOK_CORE + ["Bahan", "Pola", "Gaya", "Fitur"],
}


# ---------------------------------------------------------------------------
# Category matching: Words in Title (substring) AND Gender (exact)
# ---------------------------------------------------------------------------

PLATFORM_CATEGORY_FIELD = {
    "shopee": "shopee_category_id", "lazada": "lazada_category_id",
    "tiktok": "tiktok_category", "zalora": "zalora_category",
}
PLATFORM_SIZECHART_FIELD = {
    "shopee": "shopee_sizechart_url", "lazada": "lazada_sizechart_url",
    "tiktok": "tiktok_sizechart_url", "zalora": "zalora_sizechart_url",
}


def match_category_entry(title, gender, entries):
    title_l = str(title or "").lower()
    g = _norm(gender)
    for e in entries:
        words = str(e.get("words") or "").strip().lower()
        if not words or words not in title_l:
            continue
        eg = _norm(e.get("gender"))
        if eg and eg != g:
            continue
        return e
    return None


def apply_category_mapping(rows, category_sheets):
    if not category_sheets:
        return rows
    out = []
    for r in rows:
        r2 = dict(r)
        for platform, field in PLATFORM_CATEGORY_FIELD.items():
            e = match_category_entry(r.get("title"), r.get("gender"), category_sheets.get(platform) or [])
            if not e:
                continue
            if e.get("id") not in (None, ""):
                r2[field] = e["id"]
            if e.get("sizechart"):
                r2[PLATFORM_SIZECHART_FIELD[platform]] = e["sizechart"]
            if platform == "zalora" and e.get("subcat_type"):
                r2["zalora_subcat_type_resolved"] = e["subcat_type"]
            if platform == "shopee" and e.get("words"):
                r2["shopify_product_type"] = e["words"]
                r2["shopify_tags"] = e["words"]
        out.append(r2)
    return out


# ---------------------------------------------------------------------------
# Descriptions, material, specs
# ---------------------------------------------------------------------------

def combined_description(row):
    parts = [str(row.get("main_description") or "").strip(), str(row.get("long_description") or "").strip()]
    return "\n".join(p for p in parts if p)


def script_description(row):
    return text_to_html(combined_description(row))


def material_of(row):
    return extract_material(row.get("material"))


def shopee_default_specs(row):
    return [("Brand", BRAND), ("Material", material_of(row))]


def lazada_default_specs(row):
    key = "normal.material_filter" if classify_product_kind(row.get("title")) == "footwear" else "normal.material"
    return [("normal.delivery_option_economy", "No"), ("normal.Hazmat", "None"), (key, material_of(row))]


# ---------------------------------------------------------------------------
# Grouping: single listing vs variant listing, axes detection
# ---------------------------------------------------------------------------

_SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "XXXL", "3XL", "4XL"]


def size_rank(size):
    s = str(size or "").strip().upper()
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if s in _SIZE_ORDER:
        return (0, _SIZE_ORDER.index(s))
    if m:
        return (1, float(m.group(1)))
    return (2, 0)


def is_no_variation(row):
    return _norm(row.get("listing_type")) == "NO VARIATION"


def group_key(row):
    if is_no_variation(row):
        return "SINGLE-" + str(row.get("sku"))
    return str(row.get("parent_id") or row.get("sku"))


def build_groups(rows):
    """-> ordered list of groups; each group's rows sorted by colour (first
    appearance) then size."""
    groups, order = {}, []
    for r in rows:
        k = group_key(r)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)
    result = []
    for k in order:
        g = groups[k]
        colour_order = {}
        for r in g:
            colour_order.setdefault(str(r.get("variant_value_1") or "").strip().lower(), len(colour_order))
        g = sorted(g, key=lambda r: (colour_order[str(r.get("variant_value_1") or "").strip().lower()],
                                     size_rank(r.get("variant_value_2"))))
        result.append(g)
    return result


def group_axes(group):
    """List of ('Color'|'Size') axes that actually vary in this group; [] for single listings."""
    if len(group) <= 1:
        return []
    colours = {str(r.get("variant_value_1") or "").strip().lower() for r in group}
    sizes = {str(r.get("variant_value_2") or "").strip().lower() for r in group}
    axes = []
    if len(colours) > 1:
        axes.append("Color")
    if len(sizes) > 1:
        axes.append("Size")
    if not axes:
        axes.append("Size" if any(sizes - {""}) else "Color")
    return axes


def axis_value(row, axis):
    return str(row.get("variant_value_1" if axis == "Color" else "variant_value_2") or "").strip()


def _group_axes_by_row(rows):
    """{sku: axes of its group} — used by the per-row builders (TikTok/Zalora/Shopify)."""
    out = {}
    for g in build_groups(rows):
        ax = group_axes(g)
        for r in g:
            out[r["sku"]] = ax
    return out


# ---------------------------------------------------------------------------
# Shopee / Lazada grouped builders (Toms layout)
# ---------------------------------------------------------------------------

def _shopee_common(row):
    specs = shopee_default_specs(row) + parse_specs(row.get("shopee_item_specifications"))
    out = {
        "Product Name": ensure_brand_prefix(row.get("title"), BRAND),
        "RRP": to_number(row.get("price")), "Currency Code": "IDR", "SRP": "", "Quantity": 0,
        "Category ID": row.get("shopee_category_id"),
        "Shipping Service Details": SHOPEE_SHIPPING,
        "Weight (Kg)": to_number(row.get("weight_kg")),
        "Package Length(cm)": to_number(row.get("length_cm")),
        "Package Width(cm)": to_number(row.get("width_cm")),
        "Package Height(cm)": to_number(row.get("height_cm")),
        "Size chart Image URL": row.get("shopee_sizechart_url", ""),
    }
    for i, (k, v) in enumerate(specs[:25], start=1):
        out[f"Product Specification {i}"] = f"{k}={v}"
    return out


def build_shopee_group_rows(group):
    axes = group_axes(group)
    if not axes:
        row = group[0]
        out = _shopee_common(row)
        out.update({"Seller SKU": row.get("sku"), "Product Description 1": combined_description(row),
                    "Product Image URL(s)": IMG_SEP.join(merged_images(row))})
        return [[out.get(h, "") for h in SHOPEE_HEADERS]]
    rep = group[0]
    header = _shopee_common(rep)
    header.update({"Seller SKU": "", "Product Description 1": combined_description(rep),
                   "Total variation": len(group),
                   "Variation 1": axes[0], "Variation 2": axes[1] if len(axes) > 1 else "",
                   "Product Image URL(s)": IMG_SEP.join(split_images(rep.get("parent_images")))})
    rows_out = [[header.get(h, "") for h in SHOPEE_HEADERS]]
    for row in group:
        c = _shopee_common(row)
        c.update({"Seller SKU": row.get("sku"), "Product Description 1": "",
                  "Variation 1": axis_value(row, axes[0]),
                  "Variation 2": axis_value(row, axes[1]) if len(axes) > 1 else "",
                  "Product Image URL(s)": IMG_SEP.join(merged_images(row))})
        rows_out.append([c.get(h, "") for h in SHOPEE_HEADERS])
    return rows_out


_LAZADA_AXIS = {"Color": "color_family", "Size": "size"}


def _lazada_common(row):
    specs = lazada_default_specs(row) + parse_specs(row.get("lazada_item_specifications"))
    title = ensure_brand_prefix(row.get("title"), BRAND)
    row = dict(row, brand=BRAND)
    out = {
        "Product Name": title, "Product Name (English)": title,
        "Short Description": lazada_short_description(row),
        "SRP": "", "RRP": to_number(row.get("price")), "Currency Code": "IDR", "Quantity": 0,
        "Category ID": row.get("lazada_category_id"), "Brand": BRAND,
        "Tax Class": "default", "Warranty Type": "No Warranty",
        "Package Weight (kg)": to_number(row.get("weight_kg")),
        "Package Height(cm)": to_number(row.get("height_cm")),
        "Package Length(cm)": to_number(row.get("length_cm")),
        "Package Width(cm)": to_number(row.get("width_cm")),
        "What's in the Box": f"1 x {title}",
        "Size chart Image URL": row.get("lazada_sizechart_url", ""),
    }
    for i, (k, v) in enumerate(specs[:25], start=1):
        out[f"Product Specification {i}"] = f"{k}={v}"
    return out


def build_lazada_group_rows(group):
    axes = group_axes(group)
    if not axes:
        row = group[0]
        out = _lazada_common(row)
        desc = script_description(row) + parent_image_html_snippet(row)
        out.update({"Seller SKU": row.get("sku"), "Product Description 1": desc,
                    "Product Description(English) 1": desc,
                    "Product Image URL(s)": IMG_SEP.join(merged_images(row))})
        return [[out.get(h, "") for h in LAZADA_HEADERS]]
    rep = group[0]
    header = _lazada_common(rep)
    hd = script_description(rep) + parent_image_html_snippet(rep)
    header.update({"Seller SKU": "", "Product Description 1": hd, "Product Description(English) 1": hd,
                   "Total variation": len(group),
                   "Variation 1": _LAZADA_AXIS[axes[0]],
                   "Variation 2": _LAZADA_AXIS[axes[1]] if len(axes) > 1 else "",
                   "Product Image URL(s)": IMG_SEP.join(split_images(rep.get("parent_images")))})
    rows_out = [[header.get(h, "") for h in LAZADA_HEADERS]]
    for row in group:
        c = _lazada_common(row)
        c.update({"Seller SKU": row.get("sku"), "Product Description 1": "",
                  "Product Description(English) 1": "",
                  "Variation 1": axis_value(row, axes[0]),
                  "Variation 2": axis_value(row, axes[1]) if len(axes) > 1 else "",
                  "Product Image URL(s)": IMG_SEP.join(merged_images(row))})
        rows_out.append([c.get(h, "") for h in LAZADA_HEADERS])
    return rows_out


# ---------------------------------------------------------------------------
# Shopify (Toms layout: header row per parent, US sizes as 'US:7')
# ---------------------------------------------------------------------------

def _shopify_size(v):
    return re.sub(r"^([A-Za-z]+)\s+(?=\d)", r"\1:", str(v or "").strip())


def _shopify_value(row, axis):
    v = axis_value(row, axis)
    return _shopify_size(v) if axis == "Size" else v


def _shopify_common(row):
    return {
        "Product Name": str(row.get("title") or "").strip(),  # no brand prefix
        "Product Description 1": script_description(row),
        "RRP": to_number(row.get("price")), "Currency Code": "IDR", "Quantity": 0,
        "Category ID": row.get("shopify_category_id"),
        "Weight (Kg)": to_number(row.get("weight_kg")),
        "Product Type": row.get("shopify_product_type", ""), "tags": row.get("shopify_tags", ""),
    }


def _shopify_images(row, parent_only=False):
    own = split_images(row.get("shopify_images"))
    if own:
        return own
    return split_images(row.get("parent_images")) if parent_only else merged_images(row)


def build_shopify_group_rows(group):
    axes = group_axes(group)
    if not axes:
        row = group[0]
        out = _shopify_common(row)
        out.update({"Seller SKU": row.get("sku"), "Product Image URL(s)": IMG_SEP.join(_shopify_images(row))})
        return [[out.get(h, "") for h in SHOPIFY_HEADERS]]
    rep = group[0]
    header = _shopify_common(rep)
    header.update({"Seller SKU": "", "Total variation": len(group),
                   "Variation 1": axes[0], "Variation 2": axes[1] if len(axes) > 1 else "",
                   "Product Image URL(s)": IMG_SEP.join(_shopify_images(rep, parent_only=True))})
    rows_out = [[header.get(h, "") for h in SHOPIFY_HEADERS]]
    for row in group:
        c = _shopify_common(row)
        c.update({"Seller SKU": row.get("sku"),
                  "Variation 1": _shopify_value(row, axes[0]),
                  "Variation 2": _shopify_value(row, axes[1]) if len(axes) > 1 else "",
                  "Product Image URL(s)": IMG_SEP.join(_shopify_images(row))})
        rows_out.append([c.get(h, "") for h in SHOPIFY_HEADERS])
    return rows_out


# ---------------------------------------------------------------------------
# TikTok (one row per SKU; 5 separate files) and Zalora
# ---------------------------------------------------------------------------

def build_tiktok_row(row, axes, headers):
    imgs = split_images(row.get("parent_images"))
    if row.get("tiktok_sizechart_url"):
        imgs = imgs + [row["tiktok_sizechart_url"]]
    slots = (imgs + [""] * 9)[:9]
    out = {
        "Kategori": row.get("tiktok_category"), "Merek": BRAND,
        "Nama produk": ensure_brand_prefix(row.get("title"), BRAND),
        "Deskripsi produk": combined_description(row),
        "Gambar utama": slots[0], "Gambar 2": slots[1], "Gambar 3": slots[2], "Gambar 4": slots[3],
        "Gambar 5": slots[4], "Gambar 6": slots[5], "Gambar 7": slots[6],
        "Gambar Produk 8": slots[7], "Gambar Produk 9": slots[8],
        "Nama varian utama (tema)": axes[0] if len(axes) >= 1 else "",
        "Nilai varian utama (opsi)": axis_value(row, axes[0]).title() if len(axes) >= 1 and axes[0] == "Color" else (axis_value(row, axes[0]) if len(axes) >= 1 else ""),
        "Nama varian sekunder (tema)": axes[1] if len(axes) >= 2 else "",
        "Nilai varian sekunder (opsi)": axis_value(row, axes[1]) if len(axes) >= 2 else "",
        "Berat paket(g)": to_number(row.get("weight_kg")) * 1000,
        "Panjang paket(cm)": to_number(row.get("length_cm")),
        "Lebar paket(cm)": to_number(row.get("width_cm")),
        "Tinggi paket(cm)": to_number(row.get("height_cm")),
        "Harga Ritel (Mata Uang Lokal)": to_number(row.get("price")),
        "Kuantitas": 0, "SKU Penjual": row.get("sku"),
        "Pilih apakah akan mendukung pembayaran di tempat.": "N",
        "Asuransi pengiriman": "Wajib",
        "Bahan": material_of(row), "Musim": row.get("season"),
    }
    return [out.get(h, "") for h in headers]


def build_zalora_row(row):
    imgs = zalora_image_list(row)
    if row.get("zalora_sizechart_url"):
        imgs = imgs + [row["zalora_sizechart_url"]]
    slots = (imgs + [""] * 8)[:8]
    size = str(row.get("variant_value_2") or "").strip()
    us = re.match(r"^US\s*(.+)$", size, re.I)
    parent = row.get("sku") if is_no_variation(row) else row.get("parent_id")
    out = {
        "SkuSupplierConfig": parent, "ParentSku": parent,
        "SellerSku": row.get("sku"), "Brand": BRAND,
        "PrimaryCategory": row.get("zalora_category"),
        "Gender": GENDER_TO_ZALORA.get(_norm(row.get("gender")), ""),
        "SubCatType": row.get("zalora_subcat_type_resolved", ""),
        "Name": ensure_brand_prefix(row.get("title"), BRAND),
        "ColorFamily": row.get("zalora_color_family_resolved", ""),
        "Sizesystembrand": "US" if us else "International",
        "Color": str(row.get("zalora_color") or "").title(),
        "Variation": us.group(1).strip() if us else (size or "One Size"),
        "Quantity": 0, "Price": to_number(row.get("price")),
        "Description": script_description(row),
        "Year": row.get("year"), "Season": row.get("season"),
        "Material": extract_material(row.get("zalora_material") or row.get("material")),
        "BoxHeightSimple": to_number(row.get("height_cm")),
        "BoxLengthSimple": to_number(row.get("length_cm")),
        "BoxWidthSimple": to_number(row.get("width_cm")),
        "WeightSimple": to_number(row.get("weight_kg")),
        "MainImage": slots[0], "Image2": slots[1], "Image3": slots[2], "Image4": slots[3],
        "Image5": slots[4], "Image6": slots[5], "Image7": slots[6], "Image8": slots[7],
        "ProductGroup": "",
    }
    return [out.get(h, "") for h in ZALORA_HEADERS]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_platform_rows(platform, rows):
    """platform: shopee | lazada | zalora | shopify | one of TIKTOK_OUTPUTS."""
    if platform in ("shopee", "lazada", "shopify"):
        builder = {"shopee": build_shopee_group_rows, "lazada": build_lazada_group_rows,
                   "shopify": build_shopify_group_rows}[platform]
        headers = {"shopee": SHOPEE_HEADERS, "lazada": LAZADA_HEADERS, "shopify": SHOPIFY_HEADERS}[platform]
        out = []
        for g in build_groups(rows):
            out.extend(builder(g))
        return headers, out
    if platform == "zalora":
        ordered = [r for g in build_groups(rows) for r in g]
        return ZALORA_HEADERS, [build_zalora_row(r) for r in ordered]
    headers = TIKTOK_HEADERS_BY_OUTPUT[platform]
    axes_by_sku = _group_axes_by_row(rows)
    out = []
    for g in build_groups(rows):
        for r in g:
            if tiktok_output_key(r) == platform:
                out.append(build_tiktok_row(r, axes_by_sku[r["sku"]], headers))
    return headers, out
