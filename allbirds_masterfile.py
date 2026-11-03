"""Reads the Allbirds Masterfile and merges in the combined-images file
(Lazada / Zalora / Shopify sheets). Images in that file are keyed by ONE SKU
per colour (the first size's SKU of each Parent SKU + Color), so every size
of a colour picks up its colour's images."""
import pandas as pd

import hydro_flask_masterfile as _hfm

MASTERFILE_LABEL_TO_KEY = {
    "Fixed Item Name": "title", "Item Name": "title",
    "Inventory Sku*": "sku", "Seller SKU": "sku", "Parent Sku": "parent_id",
    "Listing Type": "listing_type",
    "Main Description*": "main_description",
    "Long Description (*If for Lazada & Dotcom listing)": "long_description",
    "Size*": "variant_value_2", "Color": "variant_value_1",
    "Package Length (cm)*": "length_cm", "Package Width (cm)*": "width_cm",
    "Package Height (cm)*": "height_cm", "Package Weight (kg)*": "weight_kg",
    "Gender*": "gender", "Original SRP*": "price",
    "Season (*If for Zalora listing)": "season", "Year (*If for Zalora listing)": "year",
}


def _clean_id(v):
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") else s


def _clean_year(v):
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") else s


def parse_masterfile(uploaded_file):
    preview = pd.read_excel(uploaded_file, sheet_name=0, header=None, nrows=10)
    header_row = 0
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
        sku = _clean_id(r.get("sku"))
        rows.append({
            "parent_id": _clean_id(r.get("parent_id")) or sku, "sku": sku,
            "title": r.get("title"), "listing_type": r.get("listing_type", ""),
            "main_description": r.get("main_description"),
            "long_description": r.get("long_description"),
            "description_script_override": "", "short_description": "", "brand": "Allbirds",
            "variant_name_1": "Color" if r.get("variant_value_1") else "",
            "variant_value_1": r.get("variant_value_1"),
            "variant_name_2": "Size" if r.get("variant_value_2") else "",
            "variant_value_2": r.get("variant_value_2"),
            "price": r.get("price"), "parent_images": "", "variant_images": "",
            "weight_kg": _hfm._parse_decimal(r.get("weight_kg")),
            "length_cm": _hfm._parse_decimal(r.get("length_cm")),
            "width_cm": _hfm._parse_decimal(r.get("width_cm")),
            "height_cm": _hfm._parse_decimal(r.get("height_cm")),
            "gender": r.get("gender"),
            "shopee_item_specifications": "", "lazada_item_specifications": "",
            "tiktok_item_specifications": "",
            "zalora_color": r.get("variant_value_1"), "zalora_images": "", "shopify_images": "",
            "season": r.get("season"), "year": _clean_year(r.get("year")),
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
            target[_clean_id(sku)] = "" if pd.isna(imgs) else str(imgs)
    return lazada, zalora, shopify


def merge_images_into_rows(rows, lazada, zalora, shopify=None):
    """Looks up each row's own SKU first, then the first SKU seen for its
    (Parent SKU, Color) — so all sizes of a colour share that colour's images."""
    rep = {}
    for r in rows:
        rep.setdefault((str(r.get("parent_id")), str(r.get("variant_value_1") or "").strip().lower()), r["sku"])
    out, unmatched = [], []
    shopify = shopify or {}
    for r in rows:
        r2 = dict(r)
        key = rep[(str(r.get("parent_id")), str(r.get("variant_value_1") or "").strip().lower())]
        pick = lambda d: d.get(r["sku"]) or d.get(key) or ""
        r2["parent_images"], r2["zalora_images"], r2["shopify_images"] = pick(lazada), pick(zalora), pick(shopify)
        if not (r2["parent_images"] or r2["zalora_images"] or r2["shopify_images"]):
            unmatched.append(r["sku"])
        out.append(r2)
    return out, sorted(unmatched)
