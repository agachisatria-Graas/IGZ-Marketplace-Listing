"""
Mapping logic for the Herschel Marketplace Listing Tool.

Kept as its own module (separate from mapping.py, which is Hydro Flask's) per
Agachi's request for a fully separate tool — but reuses Hydro Flask's
brand-agnostic low-level helpers (image splitting/joining, HTML conversion,
spec parsing, variant/grouping logic, output header layouts) rather than
duplicating them.

Herschel-specific rules, as given by Agachi:
1. Category ID is resolved by an EXACT match on (Product Type, Specific
   Category, Gender code) — not by keyword-in-title like Hydro Flask.
2. Material is auto-extracted from a free-text bullet list: the first
   recognizable material keyword found, scanning line by line.
3. Shopee specs: 1=Brand=Herschel, 2=Material=<extracted>, then whatever's
   manually typed. Lazada specs: 1-2 same fixed pair as Hydro Flask
   (delivery_option_economy, Hazmat), 3=normal.material=<extracted>, then
   manually typed ones.
4. Description = Main Description + Main Description 2 + Measurement,
   joined with line breaks.
"""
import re

from mapping import (
    split_images, merged_images, zalora_image_list, text_to_html,
    lines_to_bullets_html, parse_specs, variant_label, group_rows_by_parent,
    to_number, SHOPEE_HEADERS, LAZADA_HEADERS, TIKTOK_HEADERS, ZALORA_HEADERS,
)

IMG_SEP = " ; "

# Gender* code (as used in the raw file / category mapping file) -> the
# literal value Zalora's own "Gender" column expects.
GENDER_CODE_TO_ZALORA_GENDER = {"US": "Men", "WN": "Women", "UK": "Kids"}

# Scanned in this order, against each line of the Material column, in turn.
# The first line containing any of these wins; the keyword itself (not the
# surrounding text) is what gets returned.
MATERIAL_KEYWORDS = [
    "Stainless Steel", "Vegan Leather", "Faux Leather", "PU Leather",
    "Genuine Leather", "Leather", "Polyester", "Nylon", "Cotton", "Canvas",
    "Suede", "Wool", "Rubber", "Silicone", "Neoprene", "Spandex", "Denim",
    "Polypropylene", "Polyurethane", "Aluminum", "Metal",
]


def extract_material(raw_text):
    """Scans `raw_text` line by line (splitting on newlines and bullet
    markers) and returns the first recognized material keyword found.
    E.g. '• Custom print...\\n• 100% recycled 600D polyester, excluding trims'
    -> 'Polyester'. Returns '' if nothing recognizable is found."""
    if not raw_text or not str(raw_text).strip():
        return ""
    lines = re.split(r"[\n•]+", str(raw_text))
    for line in lines:
        line_l = line.lower()
        for kw in MATERIAL_KEYWORDS:
            if kw.lower() in line_l:
                return kw
    return ""


def combined_description(row):
    """Main Description + Main Description 2 + Measurement, joined with line
    breaks — skipping any of the three that are blank."""
    parts = [
        str(row.get("main_description") or "").strip(),
        str(row.get("main_description_2") or "").strip(),
        str(row.get("measurement") or "").strip(),
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
    return [("Brand", "Herschel"), ("Material", extract_material(row.get("material")))]


def lazada_default_specs(row):
    return [
        ("normal.delivery_option_economy", "No"),
        ("normal.Hazmat", "None"),
        ("normal.material", extract_material(row.get("material"))),
    ]


# ---------------------------------------------------------------------------
# Category matching: exact (Product Type, Specific Category, Gender) match
# ---------------------------------------------------------------------------

PLATFORM_CATEGORY_FIELD = {
    "shopee": "shopee_category_id",
    "lazada": "lazada_category_id",
    "tiktok": "tiktok_category",
    "zalora": "zalora_category",
}


def _norm(v):
    return str(v or "").strip().upper()


def match_exact_category(product_type, specific_category, gender, entries):
    """entries: list of {'product_type':.., 'specific_category':.., 'gender':..,
    'id':..}. Returns the id of the first entry whose three fields exactly
    match (case-insensitive), or None."""
    pt, sc, g = _norm(product_type), _norm(specific_category), _norm(gender)
    for e in entries:
        if _norm(e.get("product_type")) == pt and _norm(e.get("specific_category")) == sc and _norm(e.get("gender")) == g:
            return e.get("id")
    return None


def apply_exact_category_mapping(rows, category_sheets):
    """category_sheets: {'shopee': [...], 'lazada': [...], 'tiktok': [...], 'zalora': [...]}
    Fills each row's per-platform category field via an exact tuple match on
    (Product Type, Specific Category, Gender). No manual fallback field exists
    in the Herschel raw data — unmatched rows are left blank.
    Returns a new list of rows (does not mutate the input)."""
    if not category_sheets:
        return rows
    out = []
    for r in rows:
        r2 = dict(r)
        for platform, field in PLATFORM_CATEGORY_FIELD.items():
            entries = category_sheets.get(platform) or []
            matched = match_exact_category(
                r.get("product_type"), r.get("specific_category"), r.get("gender"), entries
            )
            if matched not in (None, ""):
                r2[field] = matched
        out.append(r2)
    return out


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
        "quantity": to_number(row.get("stock")),
        "seller_sku": row.get("sku"),
    }
    specs = parse_specs(row.get("tiktok_item_specifications"))
    for i, (k, v) in enumerate(specs[:10], start=1):
        out[f"Specification {i}"] = f"{k}={v}"
    return [out.get(h, "") for h in TIKTOK_HEADERS]


# ---------------------------------------------------------------------------
# Zalora
# ---------------------------------------------------------------------------

def build_zalora_row(row, group):
    axes = variant_label(row)
    variation = ", ".join(v for _, v in axes) if axes else "One Size"

    imgs = zalora_image_list(row)
    image_slots = (imgs + [""] * 8)[:8]
    material = extract_material(row.get("material"))
    zalora_gender = GENDER_CODE_TO_ZALORA_GENDER.get(_norm(row.get("gender")), "")

    out = {
        "SkuSupplierConfig": row.get("parent_id"),
        "ParentSku": row.get("parent_id"),
        "SellerSku": row.get("sku"),
        "Brand": row.get("brand"),
        "PrimaryCategory": row.get("zalora_category"),
        "Gender": zalora_gender,
        "SubCatType": row.get("zalora_subcat_type"),
        "Name": row.get("title"),
        "ColorFamily": row.get("zalora_color_family"),
        "Color": row.get("zalora_color"),
        "Variation": variation,
        "Quantity": to_number(row.get("stock")),
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
