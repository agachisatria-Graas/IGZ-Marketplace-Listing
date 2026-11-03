"""
Mapping logic for the Allbirds Marketplace Listing Tool (footwear — built on
Toms' output rules, fed from the Masterfile).

Allbirds-specific rules, as given by Agachi:
1. Description = "Main Description*" + line break + "Long Description".
   Shopee & TikTok use plain TEXT; Lazada, Shopify & Zalora use HTML SCRIPT.
   Lazada's script description ends with the first parent image snippet.
2. Category: a row in the category file matches when its "Words in Title"
   appears in the item title AND its Gender* equals the item's Gender*.
   The 4th column (Sizechart Images) fills Shopee/Lazada's Size chart Image
   URL and is appended as an extra image for TikTok/Zalora (same as Toms).
3. Lazada Short Description = <ul><li>Brand: Allbirds</li></ul>.
4. Shopee/Lazada grouped variant layout (header row per Parent SKU) is the
   same as Toms. "No Variation" listing-type rows are single listings on
   Shopee / Lazada / Shopify.
5. Quantity 0, SRP blank, Tax Class default, Warranty "No Warranty",
   Zalora Sizesystembrand "US" with Variation = size number (like Toms).
"""
import re

from mapping import (
    IMG_SEP, split_images, merged_images, zalora_image_list, parse_specs,
    group_rows_by_parent, to_number, parent_image_html_snippet, ensure_brand_prefix,
    text_to_html, lazada_short_description, SHOPIFY_HEADERS,
)
from toms_mapping import (
    SHOPEE_HEADERS, LAZADA_HEADERS, TIKTOK_HEADERS, TIKTOK_NAMED_ATTRIBUTE_COLUMNS,
    ZALORA_HEADERS, resolve_color_families, zalora_size_variation,
)

BRAND = "Allbirds"
# Filled into Shopee "Material", Lazada "normal.material_filter", TikTok
# "Bahan" and Zalora "Material": fixed value, same as Toms (the Masterfile's
# own Material column is not used — it holds stray text).
DEFAULT_MATERIAL = "Rubber"

SHOPEE_SHIPPING = ("Reguler (Cashless):18000.00, Hemat:18000.00, Agen Shopee:18000.00, "
                   "Shopee Xpress Point:18000.00, Instant:18000.00")
GENDER_TO_ZALORA = {"WOMENS": "Female", "WOMEN": "Female", "MENS": "Male", "MEN": "Male", "UNISEX": "Unisex"}
LAZADA_AXIS1 = "color_family"
LAZADA_AXIS2 = "size"


def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().upper())


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
        out.append(r2)
    return out


# ---------------------------------------------------------------------------
# Descriptions & specs
# ---------------------------------------------------------------------------

def combined_description(row):
    """Main Description* + line break + Long Description (plain text)."""
    parts = [str(row.get("main_description") or "").strip(),
             str(row.get("long_description") or "").strip()]
    return "\n".join(p for p in parts if p)


def script_description(row):
    return text_to_html(combined_description(row))


def shopee_default_specs(row):
    return [("Brand", BRAND), ("Material", DEFAULT_MATERIAL)]


def lazada_default_specs(row):
    return [("normal.delivery_option_economy", "No"), ("normal.Hazmat", "None"),
            ("normal.material_filter", DEFAULT_MATERIAL)]


def is_single_listing(row):
    return _norm(row.get("listing_type")) == "NO VARIATION"


def _group_key(row, single_aware):
    if single_aware and is_single_listing(row):
        return "SINGLE-" + str(row.get("sku"))
    return str(row.get("parent_id") or row.get("sku"))


# ---------------------------------------------------------------------------
# Shopee / Lazada — grouped variant layout (same as Toms)
# ---------------------------------------------------------------------------

def _shopee_common(row):
    specs = shopee_default_specs(row) + parse_specs(row.get("shopee_item_specifications"))
    out = {
        "Product Name": ensure_brand_prefix(row.get("title"), row.get("brand")),
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
    if len(group) == 1:
        row = group[0]
        out = _shopee_common(row)
        out.update({"Seller SKU": row.get("sku"), "Product Description 1": combined_description(row),
                    "Total variation": "", "Variation 1": "", "Variation 2": "",
                    "Product Image URL(s)": IMG_SEP.join(merged_images(row))})
        return [[out.get(h, "") for h in SHOPEE_HEADERS]]
    rep = group[0]
    header = _shopee_common(rep)
    header.update({"Seller SKU": "", "Product Description 1": combined_description(rep),
                   "Total variation": len(group),
                   "Variation 1": rep.get("variant_name_1") or "", "Variation 2": rep.get("variant_name_2") or "",
                   "Product Image URL(s)": IMG_SEP.join(split_images(rep.get("parent_images")))})
    rows_out = [[header.get(h, "") for h in SHOPEE_HEADERS]]
    for row in group:
        c = _shopee_common(row)
        c.update({"Seller SKU": row.get("sku"), "Product Description 1": "", "Total variation": "",
                  "Variation 1": row.get("variant_value_1") or "", "Variation 2": row.get("variant_value_2") or "",
                  "Product Image URL(s)": IMG_SEP.join(merged_images(row))})
        rows_out.append([c.get(h, "") for h in SHOPEE_HEADERS])
    return rows_out


def _lazada_common(row):
    specs = lazada_default_specs(row) + parse_specs(row.get("lazada_item_specifications"))
    title = ensure_brand_prefix(row.get("title"), row.get("brand"))
    out = {
        "Product Name": title, "Product Name (English)": title,
        "Short Description": lazada_short_description(row),
        "SRP": "", "RRP": to_number(row.get("price")), "Currency Code": "IDR", "Quantity": 0,
        "Category ID": row.get("lazada_category_id"), "Brand": row.get("brand"),
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
    if len(group) == 1:
        row = group[0]
        out = _lazada_common(row)
        desc = script_description(row) + parent_image_html_snippet(row)
        out.update({"Seller SKU": row.get("sku"), "Product Description 1": desc,
                    "Product Description(English) 1": desc, "Total variation": "",
                    "Variation 1": "", "Variation 2": "",
                    "Product Image URL(s)": IMG_SEP.join(merged_images(row))})
        return [[out.get(h, "") for h in LAZADA_HEADERS]]
    rep = group[0]
    header = _lazada_common(rep)
    hd = script_description(rep) + parent_image_html_snippet(rep)
    header.update({"Seller SKU": "", "Product Description 1": hd, "Product Description(English) 1": hd,
                   "Total variation": len(group),
                   "Variation 1": LAZADA_AXIS1 if rep.get("variant_name_1") else "",
                   "Variation 2": LAZADA_AXIS2 if rep.get("variant_name_2") else "",
                   "Product Image URL(s)": IMG_SEP.join(split_images(rep.get("parent_images")))})
    rows_out = [[header.get(h, "") for h in LAZADA_HEADERS]]
    for row in group:
        c = _lazada_common(row)
        c.update({"Seller SKU": row.get("sku"), "Product Description 1": "",
                  "Product Description(English) 1": "", "Total variation": "",
                  "Variation 1": row.get("variant_value_1") or "", "Variation 2": row.get("variant_value_2") or "",
                  "Product Image URL(s)": IMG_SEP.join(merged_images(row))})
        rows_out.append([c.get(h, "") for h in LAZADA_HEADERS])
    return rows_out


# ---------------------------------------------------------------------------
# TikTok / Zalora / Shopify — one row per SKU
# ---------------------------------------------------------------------------

def _axes(row):
    axes = []
    if row.get("variant_name_1") and row.get("variant_value_1"):
        axes.append((row["variant_name_1"], str(row["variant_value_1"]).strip()))
    if row.get("variant_name_2") and row.get("variant_value_2"):
        axes.append((row["variant_name_2"], str(row["variant_value_2"]).strip()))
    return axes


def build_tiktok_row(row):
    axes = _axes(row)
    imgs = split_images(row.get("parent_images"))
    if row.get("tiktok_sizechart_url"):
        imgs = imgs + [row["tiktok_sizechart_url"]]
    slots = (imgs + [""] * 9)[:9]
    out = {
        "Kategori": row.get("tiktok_category"), "Merek": row.get("brand"),
        "Nama produk": ensure_brand_prefix(row.get("title"), row.get("brand")),
        "Deskripsi produk": combined_description(row),
        "Gambar utama": slots[0], "Gambar 2": slots[1], "Gambar 3": slots[2], "Gambar 4": slots[3],
        "Gambar 5": slots[4], "Gambar 6": slots[5], "Gambar 7": slots[6],
        "Gambar Produk 8": slots[7], "Gambar Produk 9": slots[8],
        "Nama varian utama (tema)": axes[0][0] if len(axes) >= 1 else "",
        "Nilai varian utama (opsi)": axes[0][1] if len(axes) >= 1 else "",
        "Nama varian sekunder (tema)": axes[1][0] if len(axes) >= 2 else "",
        "Nilai varian sekunder (opsi)": axes[1][1] if len(axes) >= 2 else "",
        "Berat paket(g)": to_number(row.get("weight_kg")) * 1000,
        "Panjang paket(cm)": to_number(row.get("length_cm")),
        "Lebar paket(cm)": to_number(row.get("width_cm")),
        "Tinggi paket(cm)": to_number(row.get("height_cm")),
        "Harga Ritel (Mata Uang Lokal)": to_number(row.get("price")),
        "Kuantitas": 0, "SKU Penjual": row.get("sku"),
        "Bahan": DEFAULT_MATERIAL, "Musim": row.get("season"),
    }
    lower_to_col = {c.lower(): c for c in TIKTOK_NAMED_ATTRIBUTE_COLUMNS}
    for k, v in parse_specs(row.get("tiktok_item_specifications")):
        col = lower_to_col.get(str(k).strip().lower())
        if col:
            out[col] = v
    return [out.get(h, "") for h in TIKTOK_HEADERS]


def build_zalora_row(row):
    imgs = zalora_image_list(row)
    if row.get("zalora_sizechart_url"):
        imgs = imgs + [row["zalora_sizechart_url"]]
    slots = (imgs + [""] * 8)[:8]
    out = {
        "SkuSupplierConfig": row.get("parent_id"), "ParentSku": row.get("parent_id"),
        "SellerSku": row.get("sku"), "Brand": row.get("brand"),
        "PrimaryCategory": row.get("zalora_category"),
        "Gender": GENDER_TO_ZALORA.get(_norm(row.get("gender")), ""),
        "Name": ensure_brand_prefix(row.get("title"), row.get("brand")),
        "ColorFamily": row.get("zalora_color_family_resolved", ""),
        "Sizesystembrand": "US", "Color": row.get("zalora_color"),
        "Variation": zalora_size_variation(row), "Quantity": 0,
        "Price": to_number(row.get("price")), "Description": script_description(row),
        "Year": row.get("year"), "Season": row.get("season"), "Material": DEFAULT_MATERIAL,
        "BoxHeightSimple": to_number(row.get("height_cm")),
        "BoxLengthSimple": to_number(row.get("length_cm")),
        "BoxWidthSimple": to_number(row.get("width_cm")),
        "WeightSimple": to_number(row.get("weight_kg")),
        "MainImage": slots[0], "Image2": slots[1], "Image3": slots[2], "Image4": slots[3],
        "Image5": slots[4], "Image6": slots[5], "Image7": slots[6], "Image8": slots[7],
        "ProductGroup": row.get("parent_id"),
    }
    return [out.get(h, "") for h in ZALORA_HEADERS]


def build_shopify_row(row, group):
    single = is_single_listing(row) or len(group) <= 1
    axes = [] if single else _axes(row)
    imgs = split_images(row.get("shopify_images")) or merged_images(row)
    out = {
        "Seller SKU": row.get("sku"),
        "Product Name": str(row.get("title") or "").strip(),  # Shopify: no brand prefix added
        "Product Description 1": script_description(row),
        "Total variation": len(axes) or "",
        "Variation 1": f"{axes[0][0]}:{axes[0][1]}" if len(axes) >= 1 else "",
        "Variation 2": f"{axes[1][0]}:{axes[1][1]}" if len(axes) >= 2 else "",
        "RRP": to_number(row.get("price")), "Currency Code": "IDR", "Quantity": 0,
        "Category ID": row.get("shopify_category_id"),
        "Product Image URL(s)": IMG_SEP.join(imgs),
        "Weight (Kg)": to_number(row.get("weight_kg")),
        "Vendor": row.get("brand"),
        "Product Type": row.get("shopify_product_type", ""),
        "tags": row.get("shopify_tags", ""),
    }
    return [out.get(h, "") for h in SHOPIFY_HEADERS]


HEADERS = {"shopee": SHOPEE_HEADERS, "lazada": LAZADA_HEADERS, "tiktok": TIKTOK_HEADERS,
           "zalora": ZALORA_HEADERS, "shopify": SHOPIFY_HEADERS}


def build_platform_rows(platform, rows):
    headers = HEADERS[platform]
    if platform in ("shopee", "lazada"):
        builder = build_shopee_group_rows if platform == "shopee" else build_lazada_group_rows
        groups, order = {}, []
        for r in rows:
            k = _group_key(r, True)
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(r)
        out = []
        for k in order:
            out.extend(builder(groups[k]))
        return headers, out
    if platform == "shopify":
        groups = {}
        for r in rows:
            groups.setdefault(_group_key(r, True), []).append(r)
        return headers, [build_shopify_row(r, groups[_group_key(r, True)]) for r in rows]
    builder = build_tiktok_row if platform == "tiktok" else build_zalora_row
    return headers, [builder(r) for r in rows]
