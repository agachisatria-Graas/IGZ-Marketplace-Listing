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
   (delivery_option_economy, Hazmat), 3=normal.material=Rubber (fixed), then
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
    to_number,
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
    (new_rows, unresolved_skus). Does not mutate the input."""
    out = []
    unresolved = []
    for r in rows:
        r2 = dict(r)
        imgs = zalora_image_list(r)
        family = None
        if imgs:
            family = classify_color_by_image(imgs[0])
        if not family:
            family = classify_color_by_keyword(r.get("zalora_color"))
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


def match_toms_category(title, specific_category, entries):
    """entries: list of {'gender_word':.., 'words':.., 'specific_category':.., 'id':..}.
    Returns the id of the first entry where both gender_word and words are
    found (case-insensitive substring) in `title`, AND specific_category
    exactly matches (case-insensitive). Returns None if nothing matches."""
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
        return e.get("id")
    return None


def apply_toms_category_mapping(rows, category_sheets):
    if not category_sheets:
        return rows
    out = []
    for r in rows:
        r2 = dict(r)
        for platform, field in PLATFORM_CATEGORY_FIELD.items():
            entries = category_sheets.get(platform) or []
            matched = match_toms_category(r.get("title"), r.get("specific_category"), entries)
            if matched not in (None, ""):
                r2[field] = matched
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
        ("normal.material", "Rubber"),
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
# Shopee
# ---------------------------------------------------------------------------

def build_shopee_row(row, group):
    axes = variant_label(row)
    total_variation = len(axes) if len(group) > 1 else 0
    var1 = f"{axes[0][0]}:{axes[0][1]}" if len(axes) >= 1 and total_variation else ""
    var2 = f"{axes[1][0]}:{axes[1][1]}" if len(axes) >= 2 and total_variation else ""

    imgs = merged_images(row)
    specs = shopee_default_specs(row) + parse_specs(row.get("shopee_item_specifications"))

    out = {
        "Seller SKU": row.get("sku"),
        "Product Name": row.get("title"),
        "Product Description 1": combined_description(row),
        "Total variation": total_variation or "",
        "Variation 1": var1,
        "Variation 2": var2,
        "RRP": to_number(row.get("price")),
        "Currency Code": "IDR",
        "SRP": to_number(row.get("price")),
        "Quantity": to_number(row.get("stock")),
        "Product Image URL(s)": IMG_SEP.join(imgs),
        "Category ID": row.get("shopee_category_id"),
        "Shipping Service Details": row.get("shopee_shipping_service"),
        "Weight (Kg)": to_number(row.get("weight_kg")),
        "Package Length(cm)": to_number(row.get("length_cm")),
        "Package Width(cm)": to_number(row.get("width_cm")),
        "Package Height(cm)": to_number(row.get("height_cm")),
    }
    for i, (k, v) in enumerate(specs[:25], start=1):
        out[f"Product Specification {i}"] = f"{k}={v}"
    return [out.get(h, "") for h in SHOPEE_HEADERS]


# ---------------------------------------------------------------------------
# Lazada
# ---------------------------------------------------------------------------

def build_lazada_row(row, group):
    axes = variant_label(row)
    total_variation = len(axes) if len(group) > 1 else 0
    var1 = f"{axes[0][0]}:{axes[0][1]}" if len(axes) >= 1 and total_variation else ""
    var2 = f"{axes[1][0]}:{axes[1][1]}" if len(axes) >= 2 and total_variation else ""

    imgs = merged_images(row)
    specs = lazada_default_specs(row) + parse_specs(row.get("lazada_item_specifications"))
    title = row.get("title") or ""

    out = {
        "Seller SKU": row.get("sku"),
        "Product Name": title,
        "Product Name (English)": title,
        "Product Description 1": script_description(row),
        "Total variation": total_variation or "",
        "Variation 1": var1,
        "Variation 2": var2,
        "Short Description": short_description_html(row),
        "SRP": to_number(row.get("price")),
        "RRP": to_number(row.get("price")),
        "Currency Code": "IDR",
        "Quantity": to_number(row.get("stock")),
        "Product Image URL(s)": IMG_SEP.join(imgs),
        "Category ID": row.get("lazada_category_id"),
        "Brand": row.get("brand"),
        "Package Weight (kg)": to_number(row.get("weight_kg")),
        "Package Height(cm)": to_number(row.get("height_cm")),
        "Package Length(cm)": to_number(row.get("length_cm")),
        "Package Width(cm)": to_number(row.get("width_cm")),
        "What's in the Box": f"1 x {title}",
    }
    for i, (k, v) in enumerate(specs[:25], start=1):
        out[f"Product Specification {i}"] = f"{k}={v}"
    return [out.get(h, "") for h in LAZADA_HEADERS]


# ---------------------------------------------------------------------------
# TikTok Shop
# ---------------------------------------------------------------------------

def build_tiktok_row(row, group):
    axes = variant_label(row)
    parent_imgs = split_images(row.get("parent_images"))
    image_slots = (parent_imgs + [""] * 9)[:9]
    variant_imgs = split_images(row.get("variant_images"))
    property_1_image = variant_imgs[0] if variant_imgs else ""

    out = {
        "Kategori": row.get("tiktok_category"),
        "Merek": row.get("brand"),
        "Nama produk": row.get("title"),
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
        "Kuantitas": to_number(row.get("stock")),
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

def build_zalora_row(row, group):
    axes = variant_label(row)
    variation = ", ".join(v for _, v in axes) if axes else "One Size"

    imgs = zalora_image_list(row)
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
        "Name": row.get("title"),
        "ColorFamily": color_family,
        "Color": row.get("zalora_color"),
        "Variation": variation,
        "Quantity": to_number(row.get("stock")),
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


BUILDERS = {
    "shopee": (SHOPEE_HEADERS, build_shopee_row),
    "lazada": (LAZADA_HEADERS, build_lazada_row),
    "tiktok": (TIKTOK_HEADERS, build_tiktok_row),
    "zalora": (ZALORA_HEADERS, build_zalora_row),
}


def build_platform_rows(platform, rows):
    headers, builder = BUILDERS[platform]
    groups = group_rows_by_parent(rows)
    out_rows = []
    for r in rows:
        group = groups[str(r.get("parent_id") or r.get("sku"))]
        out_rows.append(builder(r, group))
    return headers, out_rows
