"""
Mapping logic for the Toms Marketplace Listing Tool.

Fully separate module from mapping.py (Hydro Flask) and herschel_mapping.py
(Herschel) — including its own exact output header layouts for
Lazada/TikTok/Zalora, since Toms' real templates differ from both. Only
brand-agnostic, low-level helpers are reused from mapping.py.

Toms-specific rules, as given by Agachi:
1. Category ID: a THIRD, hybrid matching rule — a row in the category
   mapping file matches when its "Gender in Title" word AND its "Words in
   Title" word both appear (as substrings) in the item's Title, AND its
   "Specific Category" exactly matches the raw row's own Specific Category
   field (e.g. SNEAKERS/BCKSANDALS/SLIPONS). First matching row wins.
2. Shopee specs: 1=Brand=Toms, 2=Material=Rubber (fixed, not derived from
   any column), then whatever's manually typed.
3. Lazada specs: 1-2 = same fixed pair as Hydro Flask/Herschel
   (delivery_option_economy, Hazmat), 3=normal.material_filter=Rubber (fixed), then
   manually typed ones.
4. Description = Main Description + Main Description 2 (2 fields, unlike
   Herschel's 3 — Toms' raw file has no separate Measurement column).
5. Gender* code -> Zalora's own Gender field: WN=Female confirmed so far;
   Agachi will add more codes (e.g. a Men's code) later.
6. Zalora ColorFamily: same as Herschel — auto-classified from the first
   Zalora image's dominant color, falling back to a keyword match on Color,
   since Toms' raw "Color family" column just duplicates Color verbatim
   rather than being a valid Zalora family.
7. Zalora SubCatType: auto-looked-up from a fixed PrimaryCategory ->
   SubCatType table, same approach as Herschel.
"""
import io
import re

import requests
from PIL import Image

from mapping import (
    split_images, merged_images, zalora_image_list, text_to_html,
    lines_to_bullets_html, parse_specs, variant_label, group_rows_by_parent,
    to_number, parent_image_html_snippet, ensure_brand_prefix,
)

IMG_SEP = " ; "

# ---------------------------------------------------------------------------
# Gender* code -> Zalora's own Gender field. Confirmed so far: WN=Female.
# Extend this as Agachi provides more codes (e.g. a Men's code -> Male).
# ---------------------------------------------------------------------------

GENDER_CODE_TO_ZALORA_GENDER = {"WN": "Female"}


# ---------------------------------------------------------------------------
# Zalora ColorFamily: image-based classification first, keyword fallback
# (identical approach to Herschel's — same 18 Zalora-accepted families)
# ---------------------------------------------------------------------------

FAMILY_RGB = {
    "black": (20, 20, 20), "grey": (140, 140, 140), "white": (245, 245, 245),
    "red": (190, 30, 40), "pink": (240, 150, 180), "orange": (225, 120, 40),
    "yellow": (225, 195, 60), "green": (60, 120, 60), "blue": (50, 90, 170),
    "purple": (110, 60, 140), "turquoise": (55, 180, 170), "bronze": (140, 100, 60),
    "lilac purple": (170, 140, 190), "silver": (190, 190, 195), "beige": (210, 180, 140),
    "gold": (190, 160, 70), "navy": (25, 35, 70), "brown": (90, 60, 40),
}

KEYWORD_TO_FAMILY = [
    ("lilac", "lilac purple"), ("navy", "navy"), ("turquoise", "turquoise"), ("teal", "turquoise"),
    ("black", "black"), ("grey", "grey"), ("gray", "grey"),
    ("cream", "white"), ("ivory", "white"), ("white", "white"),
    ("burgundy", "red"), ("maroon", "red"), ("crimson", "red"), ("wine", "red"), ("red", "red"),
    ("fuchsia", "pink"), ("blush", "pink"), ("rose", "pink"), ("pink", "pink"),
    ("rust", "orange"), ("coral", "orange"), ("orange", "orange"),
    ("mustard", "yellow"), ("lemon", "yellow"), ("yellow", "yellow"),
    ("olive", "green"), ("sage", "green"), ("mint", "green"), ("forest", "green"), ("green", "green"),
    ("denim", "blue"), ("cobalt", "blue"), ("sky", "blue"), ("blue", "blue"),
    ("violet", "purple"), ("plum", "purple"), ("purple", "purple"),
    ("bronze", "bronze"), ("silver", "silver"),
    ("camel", "beige"), ("stone", "beige"), ("nude", "beige"), ("khaki", "beige"),
    ("sand", "beige"), ("tan", "beige"), ("cobblestone", "beige"), ("natural", "beige"), ("beige", "beige"),
    ("gold", "gold"),
    ("chocolate", "brown"), ("coffee", "brown"), ("espresso", "brown"),
    ("mocha", "brown"), ("chestnut", "brown"), ("nubuck", "brown"), ("brown", "brown"),
]


def classify_color_by_keyword(color_name):
    if not color_name:
        return None
    name_l = str(color_name).lower()
    for kw, family in KEYWORD_TO_FAMILY:
        if kw in name_l:
            return family
    return None


def _nearest_family(rgb):
    r, g, b = rgb
    best, best_dist = None, float("inf")
    for family, (fr, fg, fb) in FAMILY_RGB.items():
        dist = (r - fr) ** 2 + (g - fg) ** 2 + (b - fb) ** 2
        if dist < best_dist:
            best_dist, best = dist, family
    return best


def classify_color_by_image(url, timeout=6):
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        img = img.resize((50, 50))
        crop = img.crop((17, 17, 33, 33))
        pixels = list(crop.getdata())
        if not pixels:
            return None
        n = len(pixels)
        avg = (
            sum(p[0] for p in pixels) / n,
            sum(p[1] for p in pixels) / n,
            sum(p[2] for p in pixels) / n,
        )
        return _nearest_family(avg)
    except Exception:
        return None


def resolve_color_families(rows):
    """Adds 'zalora_color_family_resolved' to each row. Returns
    (new_rows, unresolved_skus). Does not mutate the input.

    Keyword match on the Color name is tried FIRST: descriptive names like
    'Black/White' or 'Cadet Blue Brushed Twill' are far more reliable than
    an image's average color, which gets thrown off by multi-tone products
    (e.g. a black/white upper with a tan sole averages to a muddy brown/grey
    that matches no real color in the photo). Image classification is only
    a fallback for names with no recognizable color word at all."""
    out = []
    unresolved = []
    for r in rows:
        r2 = dict(r)
        family = classify_color_by_keyword(r.get("zalora_color"))
        if not family:
            imgs = zalora_image_list(r)
            if imgs:
                family = classify_color_by_image(imgs[0])
        r2["zalora_color_family_resolved"] = family or ""
        if not family:
            unresolved.append(r.get("sku"))
        out.append(r2)
    return out, unresolved


# ---------------------------------------------------------------------------
# Zalora PrimaryCategory -> SubCatType (fixed lookup, extend as new
# categories come up — extracted from Agachi's real Toms Zalora file).
# ---------------------------------------------------------------------------

PRIMARY_CATEGORY_TO_SUBCAT = {
    "11 - Sepatu / Sepatu Pria / Sneakers": "Sneakers",
    "171 - Sepatu / Sepatu Pria / Boots": "Boots",
    "15 - Sepatu / Sepatu Wanita / Boots": "Boots",
    "12 - Sepatu / Sepatu Wanita / Sneakers": "Sneakers",
    "2171 - Sepatu / Sepatu Wanita / Slip On": "Slip Ons & Espadrilles",
    "6 - Sepatu / Sepatu Wanita / Flats": "Ballerina & Flats",
    "7328 - Sepatu / Sepatu Wanita / Sandal / Espadrilles": "Slip Ons & Espadrilles",
}


def lookup_subcat_type(primary_category):
    return PRIMARY_CATEGORY_TO_SUBCAT.get(str(primary_category or "").strip(), "")


def find_missing_subcat_categories(rows):
    """Returns the sorted set of distinct PrimaryCategory values that were
    resolved (non-blank) but have no entry in PRIMARY_CATEGORY_TO_SUBCAT —
    useful for flagging which categories need the lookup table extended."""
    missing = set()
    for r in rows:
        pc = str(r.get("zalora_category") or "").strip()
        if pc and not lookup_subcat_type(pc):
            missing.add(pc)
    return sorted(missing)


# ---------------------------------------------------------------------------
# Category matching: Gender-in-Title AND Words-in-Title (both substrings of
# the item's Title) AND an exact match on Specific Category.
# ---------------------------------------------------------------------------

PLATFORM_CATEGORY_FIELD = {
    "shopee": "shopee_category_id",
    "lazada": "lazada_category_id",
    "tiktok": "tiktok_category",
    "zalora": "zalora_category",
}


def _norm(v):
    return str(v or "").strip().upper()


def match_toms_category_entry(title, specific_category, entries):
    """Same matching logic as match_toms_category, but returns the whole
    matched entry dict (so callers can also read its 'sizechart' URL),
    not just the id."""
    title_l = str(title or "").lower()
    sc = _norm(specific_category)
    for e in entries:
        gender_word = str(e.get("gender_word") or "").lower()
        words = str(e.get("words") or "").lower()
        if gender_word and gender_word not in title_l:
            continue
        if words and words not in title_l:
            continue
        if _norm(e.get("specific_category")) != sc:
            continue
        return e
    return None


def match_toms_category(title, specific_category, entries):
    """entries: list of {'gender_word':.., 'words':.., 'specific_category':.., 'id':..}.
    Returns the id of the first entry where both gender_word and words are
    found (case-insensitive substring) in `title`, AND specific_category
    exactly matches (case-insensitive). Returns None if nothing matches."""
    e = match_toms_category_entry(title, specific_category, entries)
    return e.get("id") if e else None


PLATFORM_SIZECHART_FIELD = {
    "shopee": "shopee_sizechart_url",
    "lazada": "lazada_sizechart_url",
    "tiktok": "tiktok_sizechart_url",
    "zalora": "zalora_sizechart_url",
}


def apply_toms_category_mapping(rows, category_sheets):
    if not category_sheets:
        return rows
    out = []
    for r in rows:
        r2 = dict(r)
        for platform, field in PLATFORM_CATEGORY_FIELD.items():
            entries = category_sheets.get(platform) or []
            entry = match_toms_category_entry(r.get("title"), r.get("specific_category"), entries)
            if entry:
                if entry.get("id") not in (None, ""):
                    r2[field] = entry.get("id")
                sizechart = entry.get("sizechart")
                if sizechart:
                    r2[PLATFORM_SIZECHART_FIELD[platform]] = sizechart
        out.append(r2)
    return out


# ---------------------------------------------------------------------------
# Descriptions & default specs
# ---------------------------------------------------------------------------

def combined_description(row):
    parts = [
        str(row.get("main_description") or "").strip(),
        str(row.get("main_description_2") or "").strip(),
    ]
    return "\n".join(p for p in parts if p)


def script_description(row):
    override = row.get("description_script_override")
    if override and str(override).strip():
        return str(override)
    return text_to_html(combined_description(row))


def short_description_html(row):
    sd = row.get("short_description")
    if sd and str(sd).strip():
        return lines_to_bullets_html(sd)
    specs = parse_specs(row.get("lazada_item_specifications"))
    if specs:
        return "<ul>" + "".join(f"<li>{k}: {v}</li>" for k, v in specs) + "</ul>"
    return ""


def shopee_default_specs(row):
    return [("Brand", "Toms"), ("Material", "Rubber")]


def lazada_default_specs(row):
    return [
        ("normal.delivery_option_economy", "No"),
        ("normal.Hazmat", "None"),
        ("normal.material_filter", "Rubber"),
    ]


# ---------------------------------------------------------------------------
# Exact output headers — copied verbatim from Agachi's real Toms files
# ---------------------------------------------------------------------------

SHOPEE_HEADERS = [
    "Seller SKU", "Product Name", "Product Description 1", "Product Description 2",
    "Product Description 3", "Total variation", "Variation 1", "Variation 2", "Variation 3",
    "tags", "RRP", "Currency Code", "SRP", "Sale Start Date", "Sale End Date", "Quantity",
    "Product Image URL(s)", "Category ID", "Shipping Service Details", "Weight (Kg)",
    "Package Length(cm)", "Package Width(cm)", "Package Height(cm)", "Size chart Image URL",
    "Shipping Duration",
] + [f"Product Specification {i}" for i in range(1, 26)]

LAZADA_HEADERS = [
    "Seller SKU", "Product Name", "Product Name (English)",
    "Product Description 1", "Product Description 2", "Product Description 3",
    "Product Description(English) 1", "Product Description(English) 2", "Product Description(English) 3",
    "Total variation", "Variation 1", "Variation 2", "Variation 3",
    "Short Description", "Product Highlights \n(English)",
    "SRP", "Sale Start Date", "Sale End Date", "RRP", "Currency Code", "Quantity",
    "Product Image URL(s)", "Category ID", "Tax Class", "Brand", "Model", "Warranty Type",
    "Package Weight (kg)", "Package Height(cm)", "Package Length(cm)", "Package Width(cm)",
    "What's in the Box", "What's in the Box(English)", "Size chart Image URL",
] + [f"Product Specification {i}" for i in range(1, 26)] + [
    "Template Attribute 1", "Template Attribute 2", "Template Attribute 3",
    "Template Attribute 4", "Template Attribute 5", "Post As Non Variant",
]

TIKTOK_HEADERS = [
    "Kategori", "Merek", "Nama produk", "Deskripsi produk",
    "Gambar utama", "Gambar 2", "Gambar 3", "Gambar 4", "Gambar 5", "Gambar 6", "Gambar 7",
    "Gambar Produk 8", "Gambar Produk 9",
    "Nama varian utama (tema)", "Nilai varian utama (opsi)", "Gambar varian utama 1",
    "Nama varian sekunder (tema)", "Nilai varian sekunder (opsi)",
    "Berat paket(g)", "Panjang paket(cm)", "Lebar paket(cm)", "Tinggi paket(cm)",
    "Opsi Pengiriman", "Harga Ritel (Mata Uang Lokal)", "Pre-sale: Waktu proses pesanan",
    "Kuantitas", "SKU Penjual", "Pembelian minimum per pesanan", "Bagan Ukuran",
    "Pilih apakah akan mendukung pembayaran di tempat.", "Asuransi pengiriman",
    "Bahan", "Acara", "Musim", "Bentuk Jari Kaki", "Tinggi Hak", "Tipe Pengencang",
    "Tipe Hak", "Sertifikat SNI",
]
TIKTOK_NAMED_ATTRIBUTE_COLUMNS = [
    "Bahan", "Acara", "Musim", "Bentuk Jari Kaki", "Tinggi Hak",
    "Tipe Pengencang", "Tipe Hak", "Sertifikat SNI",
]

ZALORA_HEADERS = [
    "SkuSupplierConfig", "ParentSku", "SellerSku", "Brand", "PrimaryCategory", "Gender",
    "SubCatType", "BrowseNodes", "Name", "ColorFamily", "Color", "IsSample", "Sizesystembrand",
    "Variation", "Quantity", "Price", "SalePrice", "SaleStartDate", "SaleEndDate", "Model",
    "Description", "CareLabel", "Measurements", "Modeiswearing", "Modelbodymeasurements",
    "Year", "Season", "Material", "UpperMaterial", "InnerMaterial", "SoleMaterial",
    "InnerSoleMaterial", "VideoLink", "LeatherType", "EarthEditTag", "EarthEditCriteria",
    "EarthEditProof", "Condition", "BoxHeightSimple", "BoxLengthSimple", "BoxWidthSimple",
    "WeightSimple", "Activity", "PickedByEditor", "MainImage", "Image2", "Image3", "Image4",
    "Image5", "Image6", "Image7", "Image8", "ProductGroup",
]


# ---------------------------------------------------------------------------
# Shopee & Lazada — grouped variant output.
#
# When a Parent SKU has more than one variant, Agachi's real Shopee/Lazada
# templates add ONE extra "header" row above the variant rows: SKU blank,
# Total variation = COUNT of variant SKUs (not axis count), Variation 1/2 =
# the axis NAMES (Shopee: as typed, e.g. "Color"/"Size"; Lazada: the fixed
# system attribute keys "color_family"/"size"), description filled only
# here, and images = PARENT images only. Variant rows below then carry the
# axis VALUES verbatim (no "Name:" prefix), blank description, and merged
# (parent+variant) images. Single-SKU products get no header row at all —
# same flat single-row behavior as before.
# ---------------------------------------------------------------------------

LAZADA_AXIS1_SYSTEM_NAME = "color_family"
LAZADA_AXIS2_SYSTEM_NAME = "size"


def _shopee_common_fields(row):
    specs = shopee_default_specs(row) + parse_specs(row.get("shopee_item_specifications"))
    out = {
        "Product Name": ensure_brand_prefix(row.get("title"), row.get("brand")),
        "RRP": to_number(row.get("price")),
        "Currency Code": "IDR",
        "SRP": "",
        "Quantity": 0,
        "Category ID": row.get("shopee_category_id"),
        "Shipping Service Details": row.get("shopee_shipping_service"),
        "Weight (Kg)": to_number(row.get("weight_kg")),
        "Package Length(cm)": to_number(row.get("length_cm")),
        "Package Width(cm)": to_number(row.get("width_cm")),
        "Package Height(cm)": to_number(row.get("height_cm")),
        "Size chart Image URL": row.get("shopee_sizechart_url", ""),
    }
    for i, (k, v) in enumerate(specs[:25], start=1):
        out[f"Product Specification {i}"] = f"{k}={v}"
    return out


def _sort_group(group):
    return sorted(
        group,
        key=lambda r: (
            str(r.get("title") or ""),
            str(r.get("variant_value_1") or ""),
            str(r.get("variant_value_2") or ""),
        ),
    )


def build_shopee_group_rows(group):
    group = _sort_group(group)
    if len(group) == 1:
        row = group[0]
        out = _shopee_common_fields(row)
        out.update({
            "Seller SKU": row.get("sku"),
            "Product Description 1": combined_description(row),
            "Total variation": "",
            "Variation 1": "",
            "Variation 2": "",
            "Product Image URL(s)": IMG_SEP.join(merged_images(row)),
        })
        return [[out.get(h, "") for h in SHOPEE_HEADERS]]

    rep = group[0]
    rows_out = []
    header = _shopee_common_fields(rep)
    header.update({
        "Seller SKU": "",
        "Product Description 1": combined_description(rep),
        "Total variation": len(group),
        "Variation 1": rep.get("variant_name_1") or "",
        "Variation 2": rep.get("variant_name_2") or "",
        "Product Image URL(s)": IMG_SEP.join(split_images(rep.get("parent_images"))),
    })
    rows_out.append([header.get(h, "") for h in SHOPEE_HEADERS])

    for row in group:
        child = _shopee_common_fields(row)
        child.update({
            "Seller SKU": row.get("sku"),
            "Product Description 1": "",
            "Total variation": "",
            "Variation 1": row.get("variant_value_1") or "",
            "Variation 2": row.get("variant_value_2") or "",
            "Product Image URL(s)": IMG_SEP.join(merged_images(row)),
        })
        rows_out.append([child.get(h, "") for h in SHOPEE_HEADERS])
    return rows_out


def _lazada_common_fields(row):
    specs = lazada_default_specs(row) + parse_specs(row.get("lazada_item_specifications"))
    title = ensure_brand_prefix(row.get("title"), row.get("brand"))
    out = {
        "Product Name": title,
        "Product Name (English)": title,
        "Short Description": short_description_html(row),
        "SRP": "",
        "RRP": to_number(row.get("price")),
        "Currency Code": "IDR",
        "Quantity": 0,
        "Category ID": row.get("lazada_category_id"),
        "Brand": row.get("brand"),
        "Tax Class": "default",
        "Warranty Type": "No Warranty",
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
    group = _sort_group(group)
    if len(group) == 1:
        row = group[0]
        out = _lazada_common_fields(row)
        desc = script_description(row) + parent_image_html_snippet(row)
        out.update({
            "Seller SKU": row.get("sku"),
            "Product Description 1": desc,
            "Product Description(English) 1": desc,
            "Total variation": "",
            "Variation 1": "",
            "Variation 2": "",
            "Product Image URL(s)": IMG_SEP.join(merged_images(row)),
        })
        return [[out.get(h, "") for h in LAZADA_HEADERS]]

    rep = group[0]
    rows_out = []
    header = _lazada_common_fields(rep)
    header_desc = script_description(rep) + parent_image_html_snippet(rep)
    header.update({
        "Seller SKU": "",
        "Product Description 1": header_desc,
        "Product Description(English) 1": header_desc,
        "Total variation": len(group),
        "Variation 1": LAZADA_AXIS1_SYSTEM_NAME if rep.get("variant_name_1") else "",
        "Variation 2": LAZADA_AXIS2_SYSTEM_NAME if rep.get("variant_name_2") else "",
        "Product Image URL(s)": IMG_SEP.join(split_images(rep.get("parent_images"))),
    })
    rows_out.append([header.get(h, "") for h in LAZADA_HEADERS])

    for row in group:
        child = _lazada_common_fields(row)
        child.update({
            "Seller SKU": row.get("sku"),
            "Product Description 1": "",
            "Product Description(English) 1": "",
            "Total variation": "",
            "Variation 1": row.get("variant_value_1") or "",
            "Variation 2": row.get("variant_value_2") or "",
            "Product Image URL(s)": IMG_SEP.join(merged_images(row)),
        })
        rows_out.append([child.get(h, "") for h in LAZADA_HEADERS])
    return rows_out


# ---------------------------------------------------------------------------
# TikTok Shop
# ---------------------------------------------------------------------------

def build_tiktok_row(row, group):
    axes = variant_label(row)
    parent_imgs = split_images(row.get("parent_images"))
    sizechart = row.get("tiktok_sizechart_url")
    if sizechart:
        parent_imgs = parent_imgs + [sizechart]
    image_slots = (parent_imgs + [""] * 9)[:9]
    variant_imgs = split_images(row.get("variant_images"))
    property_1_image = variant_imgs[0] if variant_imgs else ""

    out = {
        "Kategori": row.get("tiktok_category"),
        "Merek": row.get("brand"),
        "Nama produk": ensure_brand_prefix(row.get("title"), row.get("brand")),
        "Deskripsi produk": combined_description(row),
        "Gambar utama": image_slots[0],
        "Gambar 2": image_slots[1], "Gambar 3": image_slots[2], "Gambar 4": image_slots[3],
        "Gambar 5": image_slots[4], "Gambar 6": image_slots[5], "Gambar 7": image_slots[6],
        "Gambar Produk 8": image_slots[7], "Gambar Produk 9": image_slots[8],
        "Nama varian utama (tema)": axes[0][0] if len(axes) >= 1 else "",
        "Nilai varian utama (opsi)": axes[0][1] if len(axes) >= 1 else "",
        "Gambar varian utama 1": property_1_image,
        "Nama varian sekunder (tema)": axes[1][0] if len(axes) >= 2 else "",
        "Nilai varian sekunder (opsi)": axes[1][1] if len(axes) >= 2 else "",
        "Berat paket(g)": to_number(row.get("weight_kg")) * 1000,
        "Panjang paket(cm)": to_number(row.get("length_cm")),
        "Lebar paket(cm)": to_number(row.get("width_cm")),
        "Tinggi paket(cm)": to_number(row.get("height_cm")),
        "Harga Ritel (Mata Uang Lokal)": to_number(row.get("price")),
        "Kuantitas": 0,
        "SKU Penjual": row.get("sku"),
        "Bahan": "Rubber",
        "Musim": row.get("season"),
    }
    specs = parse_specs(row.get("tiktok_item_specifications"))
    lower_to_col = {c.lower(): c for c in TIKTOK_NAMED_ATTRIBUTE_COLUMNS}
    for k, v in specs:
        col = lower_to_col.get(str(k).strip().lower())
        if col:
            out[col] = v
    return [out.get(h, "") for h in TIKTOK_HEADERS]


# ---------------------------------------------------------------------------
# Zalora
# ---------------------------------------------------------------------------

def zalora_size_variation(row):
    """Zalora's Variation for Toms is just the size NUMBER, with any unit
    prefix stripped — e.g. raw Variant Value 2 'US 5.5' -> '5.5'. Falls back
    to Variant Value 1 (or 'One Size') if there's no size axis at all."""
    size_value = row.get("variant_value_2")
    if size_value:
        stripped = re.sub(r"^[A-Za-z]+[:\s]*", "", str(size_value)).strip()
        return stripped or str(size_value)
    color_value = row.get("variant_value_1")
    if color_value:
        return str(color_value)
    return "One Size"


def build_zalora_row(row, group):
    variation = zalora_size_variation(row)

    imgs = zalora_image_list(row)
    sizechart = row.get("zalora_sizechart_url")
    if sizechart:
        imgs = imgs + [sizechart]
    image_slots = (imgs + [""] * 8)[:8]
    zalora_gender = GENDER_CODE_TO_ZALORA_GENDER.get(_norm(row.get("gender")), "")
    primary_category = row.get("zalora_category")
    subcat_type = lookup_subcat_type(primary_category)
    color_family = row.get("zalora_color_family_resolved", "")

    out = {
        "SkuSupplierConfig": row.get("parent_id"),
        "ParentSku": row.get("parent_id"),
        "SellerSku": row.get("sku"),
        "Brand": row.get("brand"),
        "PrimaryCategory": primary_category,
        "Gender": zalora_gender,
        "SubCatType": subcat_type,
        "Name": ensure_brand_prefix(row.get("title"), row.get("brand")),
        "ColorFamily": color_family,
        "Sizesystembrand": "US",
        "Color": row.get("zalora_color"),
        "Variation": variation,
        "Quantity": 0,
        "Price": to_number(row.get("price")),
        "Description": script_description(row),
        "Year": row.get("year"),
        "Season": row.get("season"),
        "Material": row.get("material"),
        "BoxHeightSimple": to_number(row.get("height_cm")),
        "BoxLengthSimple": to_number(row.get("length_cm")),
        "BoxWidthSimple": to_number(row.get("width_cm")),
        "WeightSimple": to_number(row.get("weight_kg")),
        "MainImage": image_slots[0],
        "Image2": image_slots[1], "Image3": image_slots[2], "Image4": image_slots[3],
        "Image5": image_slots[4], "Image6": image_slots[5], "Image7": image_slots[6],
        "Image8": image_slots[7],
        "ProductGroup": row.get("parent_id"),
    }
    return [out.get(h, "") for h in ZALORA_HEADERS]


GROUPED_BUILDERS = {
    "shopee": (SHOPEE_HEADERS, build_shopee_group_rows),
    "lazada": (LAZADA_HEADERS, build_lazada_group_rows),
}
PER_ROW_BUILDERS = {
    "tiktok": (TIKTOK_HEADERS, build_tiktok_row),
    "zalora": (ZALORA_HEADERS, build_zalora_row),
}


def build_platform_rows(platform, rows):
    groups = group_rows_by_parent(rows)

    if platform in GROUPED_BUILDERS:
        headers, group_builder = GROUPED_BUILDERS[platform]
        out_rows = []
        seen_parents = []
        for r in rows:
            key = str(r.get("parent_id") or r.get("sku"))
            if key not in seen_parents:
                seen_parents.append(key)
        for key in seen_parents:
            out_rows.extend(group_builder(groups[key]))
        return headers, out_rows

    headers, builder = PER_ROW_BUILDERS[platform]
    out_rows = []
    for r in rows:
        group = groups[str(r.get("parent_id") or r.get("sku"))]
        out_rows.append(builder(r, group))
    return headers, out_rows
