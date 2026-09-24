"""
Imports Agachi's native "Masterfile" format directly (the internal ops
spreadsheet, not the tool's simplified raw_data_template.xlsx), merges in
images from a combined-images lookup file (the same format the Image Link
Combiner tool produces), and hands back rows in the tool's internal raw
schema — ready to feed straight into mapping.py's category matching and
build_platform_rows().

This is an alternative INPUT path only. Nothing about the output logic
(mapping.py) changes — a masterfile-imported row looks identical, from
mapping.py's point of view, to one typed into the simple template.
"""
import re

import pandas as pd

# Masterfile column labels (row 2 of the real file) -> our internal field.
# Matched by label text so column reordering in future exports doesn't break
# this, as long as the label wording stays the same.
MASTERFILE_LABEL_TO_KEY = {
    "Item Name": "title",
    "Inventory Sku*": "sku",
    "Parent Sku": "parent_id",
    "Main Description*": "main_description",
    "Long Description (*If for Lazada & Dotcom listing)": "long_description",
    "Size*": "variant_value_2",
    "Package Length (cm)*": "length_cm",
    "Package Width (cm)*": "width_cm",
    "Package Height (cm)*": "height_cm",
    "Package Weight (kg)*": "weight_kg",
    "Color": "variant_value_1",
    "Color family*": "zalora_color_family",
    "Gender*": "zalora_gender",
    "Original SRP*": "price",
    "Shopee/Zalora /Shopify Description": "description_script_override",
    "Season (*If for Zalora listing)": "season",
    "Year (*If for Zalora listing)": "year",
}


def _parse_decimal(v):
    """Handles the masterfile's occasional comma-as-decimal values, e.g.
    '18,11' -> 18.11. Passes plain numbers through untouched."""
    if v is None or str(v).strip() == "":
        return ""
    text = str(v).strip()
    if re.match(r"^\d+,\d+$", text):
        text = text.replace(",", ".")
    return text


def parse_masterfile(uploaded_file):
    """Reads the Masterfile (row1=field descriptions, row2=labels, row3+=data)
    and returns rows in the tool's internal raw schema. Parent SKU / variant
    grouping is taken from the file's own Parent Sku column, so genuinely
    grouped products (multiple rows sharing a Parent Sku) will group
    automatically downstream, same as a hand-filled raw_data_template."""
    df = pd.read_excel(uploaded_file, sheet_name=0, header=1)  # row2 = header
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
            "long_description": r.get("long_description"),
            "description_script_override": r.get("description_script_override"),
            "short_description": "",
            "brand": "Hydro Flask",
            "variant_name_1": "Color" if r.get("variant_value_1") else "",
            "variant_value_1": r.get("variant_value_1"),
            "variant_name_2": "Size" if r.get("variant_value_2") else "",
            "variant_value_2": r.get("variant_value_2"),
            "price": r.get("price"),
            "parent_images": "",  # filled in by merge_images_into_rows()
            "variant_images": "",
            "weight_kg": _parse_decimal(r.get("weight_kg")),
            "length_cm": _parse_decimal(r.get("length_cm")),
            "width_cm": _parse_decimal(r.get("width_cm")),
            "height_cm": _parse_decimal(r.get("height_cm")),
            "shopee_shipping_service": "",
            "shopee_item_specifications": "",
            "lazada_item_specifications": "",
            "tiktok_item_specifications": "",
            "zalora_gender": str(r.get("zalora_gender") or "").title(),
            "zalora_subcat_type": "",
            "zalora_color_family": str(r.get("zalora_color_family") or "").title(),
            "zalora_color": r.get("variant_value_1"),
            "zalora_images": "",  # filled in by merge_images_into_rows()
            "season": r.get("season"),
            "year": r.get("year"),
        }
        rows.append(row)
    return rows


def load_images_workbook(uploaded_file):
    """Reads a combined-images file (same format the Image Link Combiner tool
    produces): a 'Lazada Images' sheet and/or 'Zalora Images' sheet, each with
    SKU + Combined Images columns. Returns (lazada_images, zalora_images),
    each a {sku_as_string: combined_images_string} dict."""
    sheets = pd.read_excel(uploaded_file, sheet_name=None, header=0)
    lazada_images, zalora_images = {}, {}
    for sheet_name, df in sheets.items():
        if df.shape[1] < 2:
            continue
        target = None
        if "lazada" in sheet_name.lower():
            target = lazada_images
        elif "zalora" in sheet_name.lower():
            target = zalora_images
        if target is None:
            continue
        for _, row in df.iterrows():
            sku, imgs = row.iloc[0], row.iloc[1]
            if pd.isna(sku):
                continue
            target[str(sku).strip()] = "" if pd.isna(imgs) else str(imgs)
    return lazada_images, zalora_images


def merge_images_into_rows(rows, lazada_images, zalora_images):
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
        if not parent_imgs and not zalora_imgs:
            unmatched.append(sku)
        out.append(r2)
    return out, sorted(unmatched)
