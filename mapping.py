"""
Core mapping logic for the marketplace listing tool.
Converts one row of raw item/variant data into the column layout each
marketplace's bulk-upload template expects.

Kept separate from the Streamlit UI (app.py) so it can be unit-tested
on its own.
"""
import html
import re
from collections import OrderedDict, defaultdict

IMG_SEP = " ; "


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
        "Product Name": row.get("title"),
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
    title = row.get("title") or ""

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
    "category", "brand", "product_name", "product_description",
    "main_image", "image_2", "image_3", "image_4", "image_5", "image_6", "image_7",
    "image_8", "image_9",
    "property_name_1", "property_value_1", "property_1_image",
    "property_name_2", "property_value_2",
    "parcel_weight", "parcel_length", "parcel_width", "parcel_height",
    "price", "quantity", "seller_sku",
] + [f"Specification {i}" for i in range(1, 11)]


def build_tiktok_row(row, group):
    axes = variant_label(row)
    all_imgs = merged_images(row)
    # main + up to 8 more parent-level images
    parent_imgs = split_images(row.get("parent_images"))
    image_slots = (parent_imgs + [""] * 9)[:9]
    variant_imgs = split_images(row.get("variant_images"))
    property_1_image = variant_imgs[0] if variant_imgs else ""

    out = {
        "category": row.get("tiktok_category"),
        "brand": row.get("brand"),
        "product_name": row.get("title"),
        "product_description": combined_description(row),
        "main_image": image_slots[0],
        "image_2": image_slots[1], "image_3": image_slots[2], "image_4": image_slots[3],
        "image_5": image_slots[4], "image_6": image_slots[5], "image_7": image_slots[6],
        "image_8": image_slots[7], "image_9": image_slots[8],
        "property_name_1": axes[0][0] if len(axes) >= 1 else "",
        "property_value_1": axes[0][1] if len(axes) >= 1 else "",
        "property_1_image": property_1_image,
        "property_name_2": axes[1][0] if len(axes) >= 2 else "",
        "property_value_2": axes[1][1] if len(axes) >= 2 else "",
        "parcel_weight": to_number(row.get("weight_kg")) * 1000,  # kg -> g
        "parcel_length": to_number(row.get("length_cm")),
        "parcel_width": to_number(row.get("width_cm")),
        "parcel_height": to_number(row.get("height_cm")),
        "price": to_number(row.get("price")),
        "quantity": 0,
        "seller_sku": row.get("sku"),
    }
    # NOTE: TikTok's real bulk template names these columns "product_property/<id>",
    # a numeric attribute ID that's specific to the chosen category and only known
    # inside TikTok Shop Seller Centre. We can't reliably guess those IDs, so specs
    # are written to generic "Specification N" columns instead — copy the values
    # into the correct product_property/<id> columns in the official template
    # before uploading.
    specs = parse_specs(row.get("tiktok_item_specifications"))
    for i, (k, v) in enumerate(specs[:10], start=1):
        out[f"Specification {i}"] = f"{k}={v}"
    return [out.get(h, "") for h in TIKTOK_HEADERS]


# ---------------------------------------------------------------------------
# Zalora
# ---------------------------------------------------------------------------

ZALORA_HEADERS = [
    "SkuSupplierConfig", "ParentSku", "SellerSku", "Brand", "PrimaryCategory", "Gender",
    "SubCatType", "Name", "ColorFamily", "Color", "Variation", "Quantity", "Price",
    "Description", "CareLabel", "Material",
    "BoxHeightSimple", "BoxLengthSimple", "BoxWidthSimple", "WeightSimple",
    "MainImage", "Image2", "Image3", "Image4", "Image5", "Image6", "Image7", "Image8",
    "ProductGroup",
]


def build_zalora_row(row, group):
    axes = variant_label(row)
    variation = ", ".join(f"{v}" for _, v in axes) if axes else "One Size"

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
        "SubCatType": row.get("zalora_subcat_type"),
        "Name": row.get("title"),
        "ColorFamily": row.get("zalora_color_family"),
        "Color": row.get("zalora_color"),
        "Variation": variation,
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

BUILDERS = {
    "shopee": (SHOPEE_HEADERS, build_shopee_row),
    "lazada": (LAZADA_HEADERS, build_lazada_row),
    "tiktok": (TIKTOK_HEADERS, build_tiktok_row),
    "zalora": (ZALORA_HEADERS, build_zalora_row),
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
