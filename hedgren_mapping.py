"""
Mapping logic for the Hedgren Marketplace Listing Tool.

Built on Hydro Flask's output rules (mapping.py), with Hedgren differences:
1. Category match: EXACT on (Product Type, Gender) — no Specific Category.
2. Material comes from the Masterfile's own Material column (as written).
3. Variants: Color is always a variation; Size is added as a second one
   only when sizes differ within the same Parent SKU.
4. Zalora SubCatType: optional 4th column of the category file's Zalora
   sheet, else a lookup on the Zalora category ID.
5. Shopify: Category ID / tags from an optional Shopify sheet in the
   category file; Product Type falls back to the Masterfile's Product Type.
Everything else (Quantity 0, SRP blank, Tax Class default, Lazada image
snippet, brand prefix, Shopee shipping text, TikTok N/Wajib ...) is the
same as Hydro Flask.
"""
import re

from mapping import (
    IMG_SEP, split_images, merged_images, zalora_image_list, parse_specs,
    group_rows_by_parent, to_number, parent_image_html_snippet, ensure_brand_prefix,
    script_description, short_description_html, combined_description,
    resolve_color_families, SHOPEE_HEADERS, LAZADA_HEADERS, TIKTOK_HEADERS,
    TIKTOK_NAMED_ATTRIBUTE_COLUMNS, ZALORA_HEADERS, SHOPIFY_HEADERS,
)

BRAND = "Hedgren"
SHOPEE_SHIPPING = ("Reguler (Cashless):18000.00, Hemat:18000.00, Agen Shopee:18000.00, "
                   "Shopee Xpress Point:18000.00, Instant:18000.00")

GENDER_TO_ZALORA = {"WOMENS": "Women", "WOMEN": "Women", "MENS": "Men", "MEN": "Men", "UNISEX": "Unisex"}

# Zalora category ID -> SubCatType (used only when the category file has no
# SubCatType column). IDs 266/499/269 follow Agachi's real Herschel file;
# 510 and 15250 are best guesses — add a SubCatType column to the category
# file's Zalora sheet to override.
ZALORA_ID_TO_SUBCAT = {266: "Shoulder Bags", 499: "Top-handles", 269: "Backpacks", 510: "Top-handles"}


def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().upper())


def _leading_id(text):
    m = re.match(r"\s*(\d+)\s*-", str(text or ""))
    return int(m.group(1)) if m else None


PLATFORM_CATEGORY_FIELD = {
    "shopee": "shopee_category_id", "lazada": "lazada_category_id",
    "tiktok": "tiktok_category", "zalora": "zalora_category", "shopify": "shopify_category_id",
}


def match_category_entry(product_type, gender, entries):
    pt, g = _norm(product_type), _norm(gender)
    for e in entries:
        if _norm(e.get("product_type")) == pt and _norm(e.get("gender")) == g:
            return e
    return None


def apply_category_mapping(rows, category_sheets):
    """Exact (Product Type, Gender) match per platform. Zalora entries may
    carry 'subcat_type'; Shopify entries may carry 'tags'."""
    if not category_sheets:
        return rows
    out = []
    for r in rows:
        r2 = dict(r)
        for platform, field in PLATFORM_CATEGORY_FIELD.items():
            e = match_category_entry(r.get("product_type"), r.get("gender"), category_sheets.get(platform) or [])
            if not e:
                continue
            if e.get("id") not in (None, ""):
                r2[field] = e["id"]
            if platform == "zalora" and e.get("subcat_type"):
                r2["zalora_subcat_type_resolved"] = e["subcat_type"]
            if platform == "shopify" and e.get("tags"):
                r2["shopify_tags"] = e["tags"]
        out.append(r2)
    return out


def resolve_zalora_subcat(row):
    explicit = row.get("zalora_subcat_type_resolved")
    if explicit:
        return explicit
    return ZALORA_ID_TO_SUBCAT.get(_leading_id(row.get("zalora_category")), "")


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------

def hedgren_axes(row, group):
    axes = []
    if row.get("variant_value_1"):
        axes.append(("Color", str(row["variant_value_1"]).strip()))
    sizes = {str(g.get("variant_value_2") or "").strip() for g in group}
    if len(sizes) > 1 and row.get("variant_value_2"):
        axes.append(("Size", str(row["variant_value_2"]).strip()))
    return axes


def _variation_fields(row, group):
    axes = hedgren_axes(row, group)
    total = len(axes) if len(group) > 1 else 0
    v1 = f"{axes[0][0]}:{axes[0][1]}" if total >= 1 else ""
    v2 = f"{axes[1][0]}:{axes[1][1]}" if total >= 2 else ""
    return axes, total, v1, v2


def _material(row):
    return str(row.get("material") or "").strip()


def shopee_default_specs(row):
    return [("Brand", BRAND), ("Material", _material(row))]


def lazada_default_specs(row):
    return [("normal.delivery_option_economy", "No"), ("normal.Hazmat", "None"),
            ("normal.material", _material(row))]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_shopee_row(row, group):
    axes, total, v1, v2 = _variation_fields(row, group)
    specs = shopee_default_specs(row) + parse_specs(row.get("shopee_item_specifications"))
    out = {
        "Seller SKU": row.get("sku"),
        "Product Name": ensure_brand_prefix(row.get("title"), row.get("brand")),
        "Product Description 1": combined_description(row),
        "Total variation": total or "", "Variation 1": v1, "Variation 2": v2,
        "RRP": to_number(row.get("price")), "Currency Code": "IDR", "SRP": "", "Quantity": 0,
        "Product Image URL(s)": IMG_SEP.join(merged_images(row)),
        "Category ID": row.get("shopee_category_id"),
        "Shipping Service Details": SHOPEE_SHIPPING,
        "Weight (Kg)": to_number(row.get("weight_kg")),
        "Package Length(cm)": to_number(row.get("length_cm")),
        "Package Width(cm)": to_number(row.get("width_cm")),
        "Package Height(cm)": to_number(row.get("height_cm")),
    }
    for i, (k, v) in enumerate(specs[:25], start=1):
        out[f"Product Specification {i}"] = f"{k}={v}"
    return [out.get(h, "") for h in SHOPEE_HEADERS]


def build_lazada_row(row, group):
    axes, total, v1, v2 = _variation_fields(row, group)
    specs = lazada_default_specs(row) + parse_specs(row.get("lazada_item_specifications"))
    title = ensure_brand_prefix(row.get("title"), row.get("brand"))
    desc = script_description(row) + parent_image_html_snippet(row)
    out = {
        "Seller SKU": row.get("sku"), "Product Name": title, "Product Name (English)": title,
        "Product Description 1": desc, "Product Description(English) 1": desc,
        "Total variation": total or "", "Variation 1": v1, "Variation 2": v2,
        "Short Description": short_description_html(row),
        "SRP": "", "RRP": to_number(row.get("price")), "Currency Code": "IDR", "Quantity": 0,
        "Product Image URL(s)": IMG_SEP.join(merged_images(row)),
        "Category ID": row.get("lazada_category_id"),
        "Brand": row.get("brand"), "Tax Class": "default", "Warranty Type": "No Warranty",
        "Package Weight (kg)": to_number(row.get("weight_kg")),
        "Package Height(cm)": to_number(row.get("height_cm")),
        "Package Length(cm)": to_number(row.get("length_cm")),
        "Package Width(cm)": to_number(row.get("width_cm")),
        "What's in the Box": f"1 x {title}",
    }
    for i, (k, v) in enumerate(specs[:25], start=1):
        out[f"Product Specification {i}"] = f"{k}={v}"
    return [out.get(h, "") for h in LAZADA_HEADERS]


def build_tiktok_row(row, group):
    axes, total, v1, v2 = _variation_fields(row, group)
    slots = (split_images(row.get("parent_images")) + [""] * 9)[:9]
    variant_imgs = split_images(row.get("variant_images"))
    out = {
        "Kategori": row.get("tiktok_category"), "Merek": row.get("brand"),
        "Nama produk": ensure_brand_prefix(row.get("title"), row.get("brand")),
        "Deskripsi produk": combined_description(row),
        "Gambar utama": slots[0], "Gambar 2": slots[1], "Gambar 3": slots[2], "Gambar 4": slots[3],
        "Gambar 5": slots[4], "Gambar 6": slots[5], "Gambar 7": slots[6],
        "Gambar Produk 8": slots[7], "Gambar Produk 9": slots[8],
        "Nama varian utama (tema)": "Color" if row.get("variant_value_1") else "",
        "Nilai varian utama (opsi)": str(row.get("variant_value_1") or "").title(),
        "Gambar varian utama 1": variant_imgs[0] if variant_imgs else "",
        "Nama varian sekunder (tema)": "", "Nilai varian sekunder (opsi)": "",
        "Berat paket(g)": to_number(row.get("weight_kg")) * 1000,
        "Panjang paket(cm)": to_number(row.get("length_cm")),
        "Lebar paket(cm)": to_number(row.get("width_cm")),
        "Tinggi paket(cm)": to_number(row.get("height_cm")),
        "Harga Ritel (Mata Uang Lokal)": to_number(row.get("price")),
        "Kuantitas": 0, "SKU Penjual": row.get("sku"), "Bahan": _material(row),
        "Pilih apakah akan mendukung pembayaran di tempat.": "N",
        "Asuransi pengiriman": "Wajib",
    }
    lower_to_col = {c.lower(): c for c in TIKTOK_NAMED_ATTRIBUTE_COLUMNS}
    for k, v in parse_specs(row.get("tiktok_item_specifications")):
        col = lower_to_col.get(str(k).strip().lower())
        if col:
            out[col] = v
    return [out.get(h, "") for h in TIKTOK_HEADERS]


def build_zalora_row(row, group):
    slots = (zalora_image_list(row) + [""] * 8)[:8]
    out = {
        "SkuSupplierConfig": row.get("parent_id"), "ParentSku": row.get("parent_id"),
        "SellerSku": row.get("sku"), "Brand": row.get("brand"),
        "PrimaryCategory": row.get("zalora_category"),
        "Gender": GENDER_TO_ZALORA.get(_norm(row.get("gender")), str(row.get("gender") or "").title()),
        "SubCatType": resolve_zalora_subcat(row),
        "Name": ensure_brand_prefix(row.get("title"), row.get("brand")),
        "ColorFamily": row.get("zalora_color_family_resolved", ""),
        "Sizesystembrand": "International",
        "Color": str(row.get("zalora_color") or "").title(),
        "Variation": "One Size", "Quantity": 0, "Price": to_number(row.get("price")),
        "Description": script_description(row),
        "CareLabel": row.get("care_label"),
        "Year": row.get("year"), "Season": row.get("season"), "Material": _material(row),
        "BoxHeightSimple": to_number(row.get("height_cm")),
        "BoxLengthSimple": to_number(row.get("length_cm")),
        "BoxWidthSimple": to_number(row.get("width_cm")),
        "WeightSimple": to_number(row.get("weight_kg")),
        "MainImage": slots[0], "Image2": slots[1], "Image3": slots[2], "Image4": slots[3],
        "Image5": slots[4], "Image6": slots[5], "Image7": slots[6], "Image8": slots[7],
        "ProductGroup": "",
    }
    return [out.get(h, "") for h in ZALORA_HEADERS]


def build_shopify_row(row, group):
    axes, total, v1, v2 = _variation_fields(row, group)
    shopify_imgs = split_images(row.get("shopify_images"))
    imgs = shopify_imgs or merged_images(row)
    tags = row.get("shopify_tags") or ""
    out = {
        "Seller SKU": row.get("sku"),
        "Product Name": ensure_brand_prefix(row.get("title"), row.get("brand")),
        "Product Description 1": script_description(row),
        "Total variation": total or "", "Variation 1": v1, "Variation 2": v2,
        "RRP": to_number(row.get("price")), "Currency Code": "IDR", "Quantity": 0,
        "Category ID": row.get("shopify_category_id"),
        "Product Image URL(s)": IMG_SEP.join(imgs),
        "Weight (Kg)": to_number(row.get("weight_kg")),
        "Vendor": row.get("brand"),
        "Product Type": tags or str(row.get("product_type") or "").title(),
        "tags": tags,
    }
    return [out.get(h, "") for h in SHOPIFY_HEADERS]


BUILDERS = {
    "shopee": (SHOPEE_HEADERS, build_shopee_row),
    "lazada": (LAZADA_HEADERS, build_lazada_row),
    "tiktok": (TIKTOK_HEADERS, build_tiktok_row),
    "zalora": (ZALORA_HEADERS, build_zalora_row),
    "shopify": (SHOPIFY_HEADERS, build_shopify_row),
}


def build_platform_rows(platform, rows):
    headers, builder = BUILDERS[platform]
    groups = group_rows_by_parent(rows)
    return headers, [builder(r, groups[str(r.get("parent_id") or r.get("sku"))]) for r in rows]
