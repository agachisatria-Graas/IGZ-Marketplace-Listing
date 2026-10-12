"""
Imports Agachi's Hedgren Masterfile (header row = row 1, auto-detected) and
merges in the combined-images file (Lazada / Zalora / optional Shopify
sheets) — same mechanism as the Hydro Flask and Herschel tools.
"""
import pandas as pd

import hydro_flask_masterfile as _hfm

MASTERFILE_LABEL_TO_KEY = {
    "Item Name": "title",
    "Inventory Sku*": "sku",
    "Parent Sku": "parent_id",
    "Product Type": "product_type",
    "Long Description (*If for Lazada & Dotcom listing)": "main_description",
    "Size*": "variant_value_2",
    "Package Length (cm)*": "length_cm",
    "Package Width (cm)*": "width_cm",
    "Package Height (cm)*": "height_cm",
    "Package Weight (kg)*": "weight_kg",
    "Color": "variant_value_1",
    "Gender*": "gender",
    "Original SRP*": "price",
    "Material": "material",
    "Season (*If for Zalora listing)": "season",
    "Year (*If for Zalora listing)": "year",
    "CareLabel (*If for Zalora listing)": "care_label",
}


def parse_masterfile(uploaded_file):
    preview = pd.read_excel(uploaded_file, sheet_name=0, header=None, nrows=10)
    header_row = 0
    for i in range(len(preview)):
        if any(str(v).strip() == "Inventory Sku*" for v in preview.iloc[i].tolist()):
            header_row = i
            break
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file, sheet_name=0, header=header_row)
    df = df.rename(columns=lambda c: MASTERFILE_LABEL_TO_KEY.get(str(c).strip(), str(c).strip()))
    df = df.dropna(how="all")
    df = df[df["sku"].notna()] if "sku" in df.columns else df
    df = df.fillna("")

    def clean_sku(v):
        s = str(v).strip()
        return s[:-2] if s.endswith(".0") else s

    rows = []
    for _, r in df.iterrows():
        sku = clean_sku(r.get("sku"))
        parent = clean_sku(r.get("parent_id")) or sku
        rows.append({
            "parent_id": parent, "sku": sku, "title": r.get("title"),
            "main_description": r.get("main_description"), "long_description": "",
            "description_script_override": "", "short_description": "",
            "brand": "Hedgren",
            "variant_name_1": "Color" if r.get("variant_value_1") else "",
            "variant_value_1": r.get("variant_value_1"),
            "variant_name_2": "Size" if r.get("variant_value_2") else "",
            "variant_value_2": r.get("variant_value_2"),
            "price": r.get("price"), "parent_images": "", "variant_images": "",
            "weight_kg": _hfm._parse_decimal(r.get("weight_kg")),
            "length_cm": _hfm._parse_decimal(r.get("length_cm")),
            "width_cm": _hfm._parse_decimal(r.get("width_cm")),
            "height_cm": _hfm._parse_decimal(r.get("height_cm")),
            "product_type": r.get("product_type"), "gender": r.get("gender"),
            "material": r.get("material"), "care_label": r.get("care_label"),
            "shopee_item_specifications": "", "lazada_item_specifications": "",
            "tiktok_item_specifications": "",
            "zalora_color": r.get("variant_value_1"), "zalora_images": "",
            "shopify_images": "", "shopify_tags": "", "shopify_category_id": "",
            "season": r.get("season"), "year": r.get("year"),
        })
    return rows


def load_images_workbook(uploaded_file):
    """-> (lazada_images, zalora_images, shopify_images), each {sku: combined string}."""
    sheets = pd.read_excel(uploaded_file, sheet_name=None, header=0)
    lazada, zalora, shopify = {}, {}, {}
    for name, df in sheets.items():
        if df.shape[1] < 2:
            continue
        n = name.lower()
        target = lazada if "lazada" in n else zalora if "zalora" in n else shopify if "shopify" in n else None
        if target is None:
            continue
        for _, row in df.iterrows():
            sku, imgs = row.iloc[0], row.iloc[1]
            if pd.isna(sku):
                continue
            target[str(sku).strip()] = "" if pd.isna(imgs) else str(imgs)
    return lazada, zalora, shopify


def merge_images_into_rows(rows, lazada, zalora, shopify=None):
    out, unmatched = [], []
    for r in rows:
        r2 = dict(r)
        sku = str(r.get("sku") or "").strip()
        r2["parent_images"] = lazada.get(sku, "")
        r2["zalora_images"] = zalora.get(sku, "")
        r2["shopify_images"] = (shopify or {}).get(sku, "")
        if not (r2["parent_images"] or r2["zalora_images"] or r2["shopify_images"]):
            unmatched.append(sku)
        out.append(r2)
    return out, sorted(unmatched)
