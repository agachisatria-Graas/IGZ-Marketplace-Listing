"""
Imports Agachi's native Herschel "Masterfile" format directly (instead of
the tool's simplified herschel_raw_data_template.xlsx), and merges in images
from a combined-images lookup file (same format the Image Link Combiner
tool produces). Mirrors hydro_flask_masterfile.py's approach.

This is an alternative INPUT path only — nothing about herschel_mapping.py's
output logic changes.
"""
import pandas as pd

# Masterfile column labels (row 3 of the real file) -> our internal field.
MASTERFILE_LABEL_TO_KEY = {
    "Generic Item Name": "title",
    "Inventory Sku*": "sku",
    "Seller SKU": "sku",  # newer Masterfile exports use this label for the same column
    "Parent Sku": "parent_id",
    "Specific Category": "specific_category",
    "Product Type": "product_type",
    "Main Description*": "main_description",
    "Measurement": "measurement",
    # Newer Masterfile layout (header in row 1): Description 1/2/3
    "Description 1": "main_description",
    "Description 2": "main_description_2",
    "Description 3": "measurement",
    "Lazada Category ID & Name": "mf_lazada_category",
    "Shopee Category ID & Name": "mf_shopee_category",
    "Zalora category name": "mf_zalora_category",
    "Size*": "variant_value_2",
    "Package Length (cm)*": "length_cm",
    "Package Width (cm)*": "width_cm",
    "Package Height (cm)*": "height_cm",
    "Package Weight (kg)*": "weight_kg",
    "Color": "variant_value_1",
    "Gender*": "gender",
    "SRP": "price",
    "Material": "material",
    "Season (*If for Zalora listing)": "season",
    "Year (*If for Zalora listing)": "year",
    "Tags (*if for Shopify Listing)": "shopify_tags",
}


def parse_masterfile(uploaded_file):
    """Reads the Masterfile (row1=field descriptions, row2=blank, row3=labels,
    row4+=data) and returns rows in the Herschel tool's internal raw schema.

    Note: the Masterfile's own "Main Description 2" column (right after Main
    Description*) has no label in row 3 in the real file, so it isn't
    captured here — paste that content into Main Description* itself if
    needed, or fill it in afterward in the downloaded output."""
    # Header row is auto-detected (row 3 in the older layout, row 1 in the
    # newer one) by looking for the 'Inventory Sku*' label.
    preview = pd.read_excel(uploaded_file, sheet_name=0, header=None, nrows=10)
    header_row = 2
    for i in range(len(preview)):
        if any(str(v).strip() in ("Inventory Sku*", "Seller SKU") for v in preview.iloc[i].tolist()):
            header_row = i
            break
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file, sheet_name=0, header=header_row)
    df = df.rename(columns=lambda c: MASTERFILE_LABEL_TO_KEY.get(str(c).strip(), str(c).strip()))
    df = df.dropna(how="all")
    df = df[df["sku"].notna()] if "sku" in df.columns else df
    df = df.fillna("")

    rows = []
    for _, r in df.iterrows():
        row = {
            "parent_id": r.get("parent_id") or r.get("sku"),
            "sku": r.get("sku"),
            "title": r.get("title"),
            "main_description": r.get("main_description"),
            "main_description_2": r.get("main_description_2", ""),
            "measurement": r.get("measurement"),
            "description_script_override": "",
            "short_description": "",
            "brand": "Herschel",
            "variant_name_1": "Color" if r.get("variant_value_1") else "",
            "variant_value_1": r.get("variant_value_1"),
            "variant_name_2": "Size" if r.get("variant_value_2") else "",
            "variant_value_2": r.get("variant_value_2"),
            "price": r.get("price"),
            "parent_images": "",  # filled in by merge_images_into_rows()
            "variant_images": "",
            "weight_kg": r.get("weight_kg"),
            "length_cm": r.get("length_cm"),
            "width_cm": r.get("width_cm"),
            "height_cm": r.get("height_cm"),
            "product_type": r.get("product_type"),
            "specific_category": r.get("specific_category"),
            "gender": r.get("gender"),
            "material": r.get("material"),
            "shopee_shipping_service": "",
            "shopee_item_specifications": "",
            "lazada_item_specifications": "",
            "tiktok_item_specifications": "",
            "season": r.get("season"),
            "year": r.get("year"),
            "zalora_color": r.get("variant_value_1"),
            "zalora_images": "",  # filled in by merge_images_into_rows()
            "shopify_tags": r.get("shopify_tags"),
            "shopify_category_id": "",
            "shopify_images": "",  # filled in by merge_images_into_rows()
            "mf_lazada_category": r.get("mf_lazada_category", ""),
            "mf_shopee_category": r.get("mf_shopee_category", ""),
            "mf_zalora_category": r.get("mf_zalora_category", ""),
        }
        rows.append(row)
    return rows


def load_images_workbook(uploaded_file):
    """Reads a combined-images file (same format the Image Link Combiner tool
    produces): a 'Lazada Images' sheet and/or 'Zalora Images' sheet, each with
    SKU + Combined Images columns. Returns (lazada_images, zalora_images),
    Returns (lazada_images, zalora_images, shopify_images), each a
    {sku_as_string: combined_images_string} dict. A 'Shopify Images' sheet is
    optional (shopify_images is {} if absent)."""
    sheets = pd.read_excel(uploaded_file, sheet_name=None, header=0)
    lazada_images, zalora_images, shopify_images = {}, {}, {}
    for sheet_name, df in sheets.items():
        if df.shape[1] < 2:
            continue
        target = None
        if "lazada" in sheet_name.lower():
            target = lazada_images
        elif "zalora" in sheet_name.lower():
            target = zalora_images
        elif "shopify" in sheet_name.lower():
            target = shopify_images
        if target is None:
            continue
        for _, row in df.iterrows():
            sku, imgs = row.iloc[0], row.iloc[1]
            if pd.isna(sku):
                continue
            target[str(sku).strip()] = "" if pd.isna(imgs) else str(imgs)
    return lazada_images, zalora_images, shopify_images


def merge_images_into_rows(rows, lazada_images, zalora_images, shopify_images=None):
    """Fills parent_images (used by Shopee/Lazada/TikTok/Shopify) and
    zalora_images on each row, matched by SKU. Returns a new list of rows and
    the sorted list of SKUs that had no image match in either file (does not
    mutate the input)."""
    out = []
    unmatched = []
    for r in rows:
        r2 = dict(r)
        sku = str(r.get("sku") or "").strip()
        parent_imgs = lazada_images.get(sku, "")
        zalora_imgs = zalora_images.get(sku, "")
        r2["parent_images"] = parent_imgs
        r2["zalora_images"] = zalora_imgs
        shopify_imgs = (shopify_images or {}).get(sku, "")
        r2["shopify_images"] = shopify_imgs
        if not parent_imgs and not zalora_imgs and not shopify_imgs:
            unmatched.append(sku)
        out.append(r2)
    return out, sorted(unmatched)


# ---------------------------------------------------------------------------
# Category fallbacks (used only where the category mapping file's exact
# Product Type + Specific Category + Gender match found nothing)
# ---------------------------------------------------------------------------
import re


def _leading_id(text):
    m = re.match(r"\s*(\d+)\s*-", str(text or ""))
    return int(m.group(1)) if m else None


def _sc_key(v):
    """Plural/spacing-insensitive Specific Category key: 'WAIST BAGS' == 'waist bag'."""
    t = re.sub(r"\s+", " ", str(v or "").strip().upper())
    return t[:-1] if t.endswith("S") else t


def apply_category_fallbacks(rows, category_sheets):
    """For each platform field still blank after exact mapping:
      - Shopee/Lazada: the numeric ID from the Masterfile's own category column.
      - Zalora: the Masterfile's category ID, translated to the category
        mapping file's Zalora wording when that ID exists there (so
        SubCatType lookup works); otherwise the Masterfile's own text.
      - TikTok: Specific Category (plural-insensitive) + Gender match against
        the category mapping file, ignoring Product Type.
    Returns (new_rows, notes: {platform: [skus filled by fallback]})."""
    category_sheets = category_sheets or {}
    zalora_by_id = {}
    for e in category_sheets.get("zalora") or []:
        i = _leading_id(e.get("id"))
        if i is not None:
            zalora_by_id.setdefault(i, e.get("id"))
    out, notes = [], {"shopee": [], "lazada": [], "tiktok": [], "zalora": []}
    for r in rows:
        r2 = dict(r)
        sku = r.get("sku")
        if not r2.get("shopee_category_id") and _leading_id(r.get("mf_shopee_category")) is not None:
            r2["shopee_category_id"] = _leading_id(r["mf_shopee_category"]); notes["shopee"].append(sku)
        if not r2.get("lazada_category_id") and _leading_id(r.get("mf_lazada_category")) is not None:
            r2["lazada_category_id"] = _leading_id(r["mf_lazada_category"]); notes["lazada"].append(sku)
        if not r2.get("zalora_category") and r.get("mf_zalora_category"):
            i = _leading_id(r["mf_zalora_category"])
            r2["zalora_category"] = zalora_by_id.get(i, r["mf_zalora_category"]); notes["zalora"].append(sku)
        if not r2.get("tiktok_category"):
            g = str(r.get("gender") or "").strip().upper()
            for e in category_sheets.get("tiktok") or []:
                if _sc_key(e.get("specific_category")) == _sc_key(r.get("specific_category")) \
                        and str(e.get("gender") or "").strip().upper() == g and e.get("id") not in (None, ""):
                    r2["tiktok_category"] = e["id"]; notes["tiktok"].append(sku)
                    break
        out.append(r2)
    return out, notes
