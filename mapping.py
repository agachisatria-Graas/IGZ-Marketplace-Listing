"""
Core mapping logic for the marketplace listing tool.
Converts one row of raw item/variant data into the column layout each
marketplace's bulk-upload template expects.

Kept separate from the Streamlit UI (app.py) so it can be unit-tested
on its own.
"""
import html
import io
import re
from collections import OrderedDict, defaultdict

import requests
from PIL import Image

IMG_SEP = " ; "


# ---------------------------------------------------------------------------
# Zalora ColorFamily: image-based classification first, keyword fallback
# (same approach as the Herschel/Toms tools — same 18 Zalora-accepted
# families).
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
    ("olive", "green"), ("sage", "green"), ("mint", "green"), ("forest", "green"),
    ("mermaid", "green"), ("palmer", "green"), ("agave", "green"), ("green", "green"),
    ("denim", "blue"), ("cobalt", "blue"), ("sky", "blue"), ("surf", "blue"), ("blue", "blue"),
    ("violet", "purple"), ("plum", "purple"), ("purple", "purple"),
    ("bronze", "bronze"), ("silver", "silver"),
    ("camel", "beige"), ("stone", "beige"), ("nude", "beige"), ("khaki", "beige"),
    ("sand", "beige"), ("tan", "beige"), ("cobblestone", "beige"), ("natural", "beige"),
    ("birch", "beige"), ("oat", "beige"), ("beige", "beige"),
    ("gold", "gold"),
    ("chocolate", "brown"), ("coffee", "brown"), ("espresso", "brown"),
    ("mocha", "brown"), ("chestnut", "brown"), ("brown", "brown"),
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
    """Fetches `url`, averages the center ~30% of the image (to avoid
    whitespace product-shot borders), and returns the nearest color family.
    Returns None on any failure (bad URL, network error, unreadable image)."""
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
    """Adds 'zalora_color_family_resolved' to each row: tries a keyword
    match on the Color name FIRST (reliable for descriptive names, and
    immune to the multi-tone-averaging problem image classification has),
    falling back to the first Zalora image's dominant color only when the
    name itself gives no clue. Returns (new_rows, unresolved_skus).
    Does not mutate the input."""
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
# small helpers
# ---------------------------------------------------------------------------

def split_images(raw):
    """'a.jpg ; b.jpg' -> ['a.jpg', 'b.jpg']. Tolerates plain ';' too."""
    if not raw or not str(raw).strip():
        return []
    text = str(raw)
    parts = re.split(r"\s*;\s*", text)
    return [p.strip() for p in parts if p.strip()]


def merged_images(row, dedupe=True):
    """Parent images first, then this variant's own images."""
    imgs = split_images(row.get("parent_images")) + split_images(row.get("variant_images"))
    if dedupe:
        seen = OrderedDict()
        for u in imgs:
            seen[u] = None
        imgs = list(seen.keys())
    return imgs


def text_to_html(text):
    """Plain text (one point per line, blank line = new paragraph) -> simple HTML."""
    if not text or not str(text).strip():
        return ""
    paragraphs = re.split(r"\n\s*\n", str(text).strip())
    html_parts = []
    for para in paragraphs:
        lines = [html.escape(l.strip()) for l in para.split("\n") if l.strip()]
        html_parts.append("<p>" + "<br>".join(lines) + "</p>")
    return "".join(html_parts)


def lines_to_bullets_html(text):
    if not text or not str(text).strip():
        return ""
    lines = [html.escape(l.strip()) for l in str(text).split("\n") if l.strip()]
    return "<ul>" + "".join(f"<li>{l}</li>" for l in lines) + "</ul>"


def parse_specs(raw):
    """'Brand=Hydro Flask ; Material=Steel' -> [('Brand','Hydro Flask'), ...]"""
    out = []
    for part in split_images(raw):  # same ' ; ' separator convention
        if "=" in part:
            k, v = part.split("=", 1)
            out.append((k.strip(), v.strip()))
    return out


def combined_description(row):
    """Main Description + Long Description, joined with a line break.

    Long Description is optional (mainly used for Lazada/Dotcom-style listings) —
    if it's blank, this just returns Main Description as-is.
    """
    main = str(row.get("main_description") or "").strip()
    long_ = str(row.get("long_description") or "").strip()
    if main and long_:
        return f"{main}\n{long_}"
    return main or long_


def ensure_brand_prefix(title, brand):
    """Makes sure `brand` appears at the very front of `title`. If the brand
    already appears somewhere else in the title, it's moved to the front
    rather than duplicated. If the title already starts with the brand
    (case-insensitive), it's left as-is."""
    title = str(title or "").strip()
    brand = str(brand or "").strip()
    if not brand:
        return title
    if not title:
        return brand
    if title.lower().startswith(brand.lower()):
        return title
    if brand.lower() in title.lower():
        pattern = re.compile(re.escape(brand), re.IGNORECASE)
        remainder = pattern.sub("", title, count=1)
        remainder = re.sub(r"\s+", " ", remainder).strip(" -")
        return f"{brand} {remainder}".strip()
    return f"{brand} {title}"


def parent_image_html_snippet(row):
    """Appended to the very end of Lazada's item description: an HTML image
    tag for the first Parent Image, in the exact format Agachi specified."""
    imgs = split_images(row.get("parent_images"))
    if not imgs:
        return ""
    return f'<p style="text-align:center"><img src="{imgs[0]}"100%"/></p>'


def script_description(row):
    """Lazada/Zalora HTML description: explicit override wins, else auto-convert."""
    override = row.get("description_script_override")
    if override and str(override).strip():
        return str(override)
    return text_to_html(combined_description(row))


def short_description_html(row):
    sd = row.get("short_description")
    if sd and str(sd).strip():
        return lines_to_bullets_html(sd)
    # fall back to Lazada's own item specifications as bullets, e.g. "Brand: X"
    specs = parse_specs(row.get("lazada_item_specifications"))
    if specs:
        return "<ul>" + "".join(f"<li>{k}: {v}</li>" for k, v in specs) + "</ul>"
    return ""


def zalora_image_list(row):
    """Zalora uses its own dedicated image set (different crop/dimensions than
    the other marketplaces) — sourced only from the 'Zalora Images' column,
    not merged with Parent/Variant Images."""
    return split_images(row.get("zalora_images"))


def variant_label(row):
    """[('Color','Burgundy'), ('Size','M')] for whichever axes are filled in."""
    axes = []
    if row.get("variant_name_1") and row.get("variant_value_1"):
        axes.append((str(row["variant_name_1"]).strip(), str(row["variant_value_1"]).strip()))
    if row.get("variant_name_2") and row.get("variant_value_2"):
        axes.append((str(row["variant_name_2"]).strip(), str(row["variant_value_2"]).strip()))
    return axes


def group_rows_by_parent(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[str(r.get("parent_id") or r.get("sku"))].append(r)
    return groups


def to_number(v, default=0):
    try:
        if v is None or str(v).strip() == "":
            return default
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


def normalize_category_key(v):
    return str(v or "").strip().lower()


PLATFORM_CATEGORY_FIELD = {
    "shopee": "shopee_category_id",
    "lazada": "lazada_category_id",
    "tiktok": "tiktok_category",
    "zalora": "zalora_category",
    "shopify": "shopify_category_id",
}


def match_keyword_category(title, gender, entries):
    """entries: list of {'gender':.., 'keyword':.., 'id':..}. Returns the matched
    category id, or None if no keyword from `entries` appears in `title`.

    If multiple keywords match, the first one (in sheet row order) whose Gender
    matches the item's gender wins; otherwise the first match with a blank/'Any'/
    'Unisex' gender wins; otherwise the first match overall wins.
    """
    title_l = str(title or "").lower()
    gender_l = str(gender or "").strip().lower()
    candidates = [e for e in entries if e.get("keyword") and str(e["keyword"]).lower() in title_l]
    if not candidates:
        return None
    if gender_l:
        for e in candidates:
            if str(e.get("gender") or "").strip().lower() == gender_l:
                return e["id"]
    for e in candidates:
        g = str(e.get("gender") or "").strip().lower()
        if not g or g in ("any", "unisex"):
            return e["id"]
    return candidates[0]["id"]


def apply_title_category_mapping(rows, category_sheets):
    """category_sheets: {'shopee': [...], 'lazada': [...], 'tiktok': [...], 'zalora': [...]},
    each a list of {'gender':.., 'keyword':.., 'id':..} loaded from the category
    mapping file. Auto-fills each row's per-platform category field by searching
    for a keyword inside the item's Title (using zalora_gender as the item's
    gender for matching, since that's the schema's one gender field). A row's own
    manually-filled category field is kept as a fallback when nothing matches.
    Returns a new list of rows (does not mutate the input).
    """
    if not category_sheets:
        return rows
    out = []
    for r in rows:
        r2 = dict(r)
        gender = r.get("zalora_gender")
        for platform, field in PLATFORM_CATEGORY_FIELD.items():
            entries = category_sheets.get(platform) or []
            matched = match_keyword_category(r.get("title"), gender, entries)
            if matched not in (None, ""):
                r2[field] = matched
        out.append(r2)
    return out


# Fixed specification entries that always come first, in this order, before
# whatever the person types into the raw data's Item Specifications field.
def default_material(title):
    """'Boot' anywhere in the title -> Silicone, otherwise Stainless Steel."""
    return "Silicone" if "boot" in str(title or "").lower() else "Stainless Steel"


def shopee_default_specs(row):
    return [("Brand", "Hydro Flask"), ("Material", default_material(row.get("title")))]


def lazada_default_specs(row):
    return [
        ("normal.delivery_option_economy", "No"),
        ("normal.Hazmat", "None"),
        ("normal.material", default_material(row.get("title"))),
    ]


# ---------------------------------------------------------------------------
# Shopee
# ---------------------------------------------------------------------------

SHOPEE_HEADERS = [
    "Seller SKU", "Product Name", "Product Description 1", "Product Description 2",
    "Product Description 3", "Total variation", "Variation 1", "Variation 2", "Variation 3",
    "tags", "RRP", "Currency Code", "SRP", "Sale Start Date", "Sale End Date", "Quantity",
    "Product Image URL(s)", "Category ID", "Shipping Service Details", "Weight (Kg)",
    "Package Length(cm)", "Package Width(cm)", "Package Height(cm)", "Size chart Image URL",
    "Shipping Duration",
] + [f"Product Specification {i}" for i in range(1, 26)]


def build_shopee_row(row, group):
    axes = variant_label(row)
    total_variation = len(axes) if len(group) > 1 else 0
    var1 = f"{axes[0][0]}:{axes[0][1]}" if len(axes) >= 1 and total_variation else ""
    var2 = f"{axes[1][0]}:{axes[1][1]}" if len(axes) >= 2 and total_variation else ""

    imgs = merged_images(row)
    specs = shopee_default_specs(row) + parse_specs(row.get("shopee_item_specifications"))

    out = {
        "Seller SKU": row.get("sku"),
        "Product Name": ensure_brand_prefix(row.get("title"), row.get("brand")),
        "Product Description 1": combined_description(row),
        "Total variation": total_variation or "",
        "Variation 1": var1,
        "Variation 2": var2,
        "RRP": to_number(row.get("price")),
        "Currency Code": "IDR",
        "SRP": "",
        "Quantity": 0,
        "Product Image URL(s)": IMG_SEP.join(imgs),
        "Category ID": row.get("shopee_category_id"),
        "Shipping Service Details": "Reguler (Cashless):18000.00, Hemat:18000.00, Agen Shopee:18000.00, Shopee Xpress Point:18000.00, Instant:18000.00",
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


def build_lazada_row(row, group):
    axes = variant_label(row)
    total_variation = len(axes) if len(group) > 1 else 0
    var1 = f"{axes[0][0]}:{axes[0][1]}" if len(axes) >= 1 and total_variation else ""
    var2 = f"{axes[1][0]}:{axes[1][1]}" if len(axes) >= 2 and total_variation else ""

    imgs = merged_images(row)
    specs = lazada_default_specs(row) + parse_specs(row.get("lazada_item_specifications"))
    title = ensure_brand_prefix(row.get("title"), row.get("brand"))

    out = {
        "Seller SKU": row.get("sku"),
        "Product Name": title,
        "Product Name (English)": title,
        "Product Description 1": script_description(row) + parent_image_html_snippet(row),
        "Product Description(English) 1": script_description(row) + parent_image_html_snippet(row),
        "Total variation": total_variation or "",
        "Variation 1": var1,
        "Variation 2": var2,
        "Short Description": short_description_html(row),
        "SRP": "",
        "RRP": to_number(row.get("price")),
        "Currency Code": "IDR",
        "Quantity": 0,
        "Product Image URL(s)": IMG_SEP.join(imgs),
        "Category ID": row.get("lazada_category_id"),
        "Brand": row.get("brand"),
        "Tax Class": "default",
        "Warranty Type": "No Warranty",
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
    "Produk lelang", "Tawaran awal",
    "Pola", "Volume", "Gaya", "Fitur", "Bahan", "Magnet", "Teknik Produksi",
    "Usia", "Baterai", "Bebas BPA",
]
TIKTOK_NAMED_ATTRIBUTE_COLUMNS = [
    "Pola", "Volume", "Gaya", "Fitur", "Bahan", "Magnet", "Teknik Produksi",
    "Usia", "Baterai", "Bebas BPA",
]


def build_tiktok_row(row, group):
    axes = variant_label(row)
    parent_imgs = split_images(row.get("parent_images"))
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
        "Nilai varian utama (opsi)": str(axes[0][1]).title() if len(axes) >= 1 else "",
        "Gambar varian utama 1": property_1_image,
        "Nama varian sekunder (tema)": "",
        "Nilai varian sekunder (opsi)": "",
        "Berat paket(g)": to_number(row.get("weight_kg")) * 1000,  # kg -> g
        "Panjang paket(cm)": to_number(row.get("length_cm")),
        "Lebar paket(cm)": to_number(row.get("width_cm")),
        "Tinggi paket(cm)": to_number(row.get("height_cm")),
        "Harga Ritel (Mata Uang Lokal)": to_number(row.get("price")),
        "Kuantitas": 0,
        "SKU Penjual": row.get("sku"),
        "Bahan": default_material(row.get("title")),
    }
    # A manually-typed spec whose key matches one of the named attribute
    # columns above (case-insensitive) lands directly in that column —
    # overriding the automatic "Bahan" value above if explicitly given.
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

ZALORA_HEADERS = [
    "SkuSupplierConfig", "ParentSku", "SellerSku", "Brand", "PrimaryCategory", "Gender",
    "SubCatType", "BrowseNodes", "Name", "ColorFamily", "Color", "IsSample", "Sizesystembrand",
    "Variation", "AgeGroup", "Quantity", "Price", "SalePrice", "SaleStartDate", "SaleEndDate",
    "Model", "Description", "CareLabel", "Measurements", "Modeiswearing",
    "Modelbodymeasurements", "Year", "Season", "Material", "Range", "FrameColor",
    "FrameShape", "LensColor", "UpperMaterial", "InnerMaterial", "SoleMaterial",
    "InnerSoleMaterial", "VideoLink", "TechnicalFeatures", "Care", "LeatherType",
    "HexColor", "SkincareIngredients", "SkinFaceFormula", "SkinType", "SkincareConcerns",
    "HairType", "FaceFinish", "FaceCoverage", "EarthEditTag", "EarthEditCriteria",
    "EarthEditProof", "Condition", "BoxHeightSimple", "BoxLengthSimple", "BoxWidthSimple",
    "WeightSimple", "Activity", "PickedByEditor", "MainImage", "Image2", "Image3", "Image4",
    "Image5", "Image6", "Image7", "Image8", "ProductGroup",
]


def build_zalora_row(row, group):
    imgs = zalora_image_list(row)
    image_slots = (imgs + [""] * 8)[:8]
    all_specs = (
        parse_specs(row.get("lazada_item_specifications"))
        + parse_specs(row.get("shopee_item_specifications"))
        + parse_specs(row.get("tiktok_item_specifications"))
    )
    material = next((v for k, v in all_specs if k.lower() == "material"), "")

    out = {
        "SkuSupplierConfig": row.get("parent_id"),
        "ParentSku": row.get("parent_id"),
        "SellerSku": row.get("sku"),
        "Brand": row.get("brand"),
        "PrimaryCategory": row.get("zalora_category"),
        "Gender": row.get("zalora_gender"),
        "SubCatType": "Sports Metal Water Bottles",
        "Name": ensure_brand_prefix(row.get("title"), row.get("brand")),
        "ColorFamily": row.get("zalora_color_family_resolved", ""),
        "Sizesystembrand": "International",
        "Color": row.get("zalora_color"),
        "Variation": "One Size",
        "Quantity": 0,
        "Price": to_number(row.get("price")),
        "Description": script_description(row),
        "Material": material,
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


# ---------------------------------------------------------------------------
# top-level entry point
# ---------------------------------------------------------------------------

SHOPIFY_HEADERS = [
    "Graas SKU", "Status", "Remarks", "Seller SKU", "Product Name",
    "Product Description 1", "Product Description 2", "Product Description 3",
    "Total variation", "Variation 1", "Variation 2", "Variation 3",
    "RRP", "compare At Price", "Currency Code", "Quantity", "Category ID",
    "Product Image URL(s)", "Weight (Kg)", "Vendor", "Product Type", "Taxable", "tags",
]


def build_shopify_row(row, group):
    axes = variant_label(row)
    total_variation = len(axes) if len(group) > 1 else 0
    var1 = f"{axes[0][0]}:{axes[0][1]}" if len(axes) >= 1 and total_variation else ""
    var2 = f"{axes[1][0]}:{axes[1][1]}" if len(axes) >= 2 and total_variation else ""

    imgs = merged_images(row)

    out = {
        "Seller SKU": row.get("sku"),
        "Product Name": ensure_brand_prefix(row.get("title"), row.get("brand")),
        "Product Description 1": script_description(row),
        "Total variation": total_variation or "",
        "Variation 1": var1,
        "Variation 2": var2,
        "RRP": to_number(row.get("price")),
        "Currency Code": "IDR",
        "Quantity": 0,
        "Category ID": row.get("shopify_category_id"),
        "Product Image URL(s)": IMG_SEP.join(imgs),
        "Weight (Kg)": to_number(row.get("weight_kg")),
        "Vendor": row.get("brand"),
        "tags": row.get("shopify_category_id"),
    }
    out["Product Type"] = out.get("tags", "")
    return [out.get(h, "") for h in SHOPIFY_HEADERS]


BUILDERS = {
    "shopee": (SHOPEE_HEADERS, build_shopee_row),
    "lazada": (LAZADA_HEADERS, build_lazada_row),
    "tiktok": (TIKTOK_HEADERS, build_tiktok_row),
    "zalora": (ZALORA_HEADERS, build_zalora_row),
    "shopify": (SHOPIFY_HEADERS, build_shopify_row),
}


def build_platform_rows(platform, rows):
    """rows: list of dicts (raw data rows). Returns (headers, list_of_output_rows)."""
    headers, builder = BUILDERS[platform]
    groups = group_rows_by_parent(rows)
    out_rows = []
    for r in rows:
        group = groups[str(r.get("parent_id") or r.get("sku"))]
        out_rows.append(builder(r, group))
    return headers, out_rows
