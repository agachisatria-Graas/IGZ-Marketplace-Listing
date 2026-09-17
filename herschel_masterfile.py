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
    "Parent Sku": "parent_id",
    "Specific Category": "specific_category",
    "Product Type": "product_type",
    "Main Description*": "main_description",
    "Measurement": "measurement",
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
    df = pd.read_excel(uploaded_file, sheet_name=0, header=2)  # row3 = header
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
            "main_description_2": "",
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
