import io
import zipfile

import pandas as pd
import streamlit as st

import toms_mapping
from ui_common import PLATFORM_LABELS, CATEGORY_SHEET_PLATFORM, file_bytes, rows_to_xlsx_bytes

TOMS_RAW_COLUMNS = [
    "parent_id", "sku", "title", "main_description", "main_description_2",
    "description_script_override", "short_description", "brand",
    "variant_name_1", "variant_value_1", "variant_name_2", "variant_value_2",
    "price", "parent_images", "variant_images", "weight_kg", "length_cm",
    "width_cm", "height_cm", "specific_category", "gender", "material",
    "shopee_shipping_service", "shopee_item_specifications",
    "lazada_item_specifications", "tiktok_item_specifications", "season", "year",
    "zalora_color", "zalora_images",
]

TOMS_RAW_LABEL_TO_KEY = {
    "Parent SKU": "parent_id", "Seller SKU": "sku", "Title": "title",
    "Main Description": "main_description", "Main Description 2": "main_description_2",
    "Description Script Override (optional)": "description_script_override",
    "Short Description (Lazada)": "short_description", "Brand": "brand",
    "Variant Name 1 (optional)": "variant_name_1", "Variant Value 1 (optional)": "variant_value_1",
    "Variant Name 2 (optional)": "variant_name_2", "Variant Value 2 (optional)": "variant_value_2",
    "Price": "price",
    "Parent Images": "parent_images", "Variant Images (optional)": "variant_images",
    "Weight (kg)": "weight_kg", "Length (cm)": "length_cm", "Width (cm)": "width_cm",
    "Height (cm)": "height_cm", "Specific Category": "specific_category",
    "Gender*": "gender", "Material (optional)": "material",
    "Shopee Shipping Service": "shopee_shipping_service",
    "Shopee Item Specifications": "shopee_item_specifications",
    "Lazada Item Specifications": "lazada_item_specifications",
    "TikTok Item Specifications": "tiktok_item_specifications",
    "Season": "season", "Year": "year", "Zalora Color": "zalora_color",
    "Zalora Images": "zalora_images",
}


def load_toms_raw_file(uploaded_file):
    """Reads the toms_raw_data_template.xlsx layout: row1=labels, row2=notes, row3+=data."""
    df = pd.read_excel(uploaded_file, sheet_name=0, header=0)
    df = df.rename(columns=lambda c: TOMS_RAW_LABEL_TO_KEY.get(str(c).strip(), str(c).strip()))
    if len(df) and str(df.iloc[0].get("title", "")).strip().startswith(("Product title", "Plain text")):
        df = df.iloc[1:]
    df = df.dropna(how="all")
    df = df[df["sku"].notna()] if "sku" in df.columns else df
    for col in TOMS_RAW_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df.fillna("")
    return df[TOMS_RAW_COLUMNS].to_dict(orient="records")


def load_toms_category_mapping_workbook(uploaded_file):
    """Reads a category mapping workbook: one sheet per marketplace, each with
    (Gender in Title, Words in Title, Specific Category, Category ID, and
    optionally a 5th Sizechart Images column) — matched positionally.
    Returns ({'shopee': [{'gender_word':..,'words':..,'specific_category':..,'id':..,'sizechart':..}, ...], ...}, matched_sheet_names)
    """
    sheets = pd.read_excel(uploaded_file, sheet_name=None, header=0)
    out = {p: [] for p in CATEGORY_SHEET_PLATFORM.values()}
    matched_sheets = []
    for sheet_name, df in sheets.items():
        platform = None
        for key, p in CATEGORY_SHEET_PLATFORM.items():
            if key in sheet_name.lower():
                platform = p
                break
        if not platform or df.shape[1] < 4:
            continue
        matched_sheets.append(sheet_name)
        df = df.dropna(how="all")
        for _, row in df.iterrows():
            gender_word, words, sc, cat_id = row.iloc[0], row.iloc[1], row.iloc[2], row.iloc[3]
            sizechart = row.iloc[4] if df.shape[1] >= 5 else None
            if pd.isna(sc) or str(sc).strip() == "":
                continue
            out[platform].append({
                "gender_word": "" if pd.isna(gender_word) else gender_word,
                "words": "" if pd.isna(words) else words,
                "specific_category": sc,
                "id": "" if pd.isna(cat_id) else cat_id,
                "sizechart": "" if pd.isna(sizechart) else sizechart,
            })
    return out, matched_sheets


def render():
    st.title("Toms Marketplace Listing Tool")
    st.caption("Shopee · Lazada · TikTok Shop · Zalora Indonesia")
    st.write(
        "Separate from the Hydro Flask and Herschel tools — its own raw data "
        "format, its own category mapping rule. Upload one raw data file with "
        "all your items and variants, and get back ready-to-post files "
        "formatted for each marketplace."
    )

    with st.expander("📋 First time here? Get the templates", expanded=False):
        st.write(
            "1. Download **toms_raw_data_template.xlsx**, fill in one row "
            "per SKU (each variant of a product is its own row, sharing the "
            "same **Parent SKU**)."
        )
        st.download_button(
            "Download toms_raw_data_template.xlsx",
            data=file_bytes("toms_raw_data_template.xlsx"),
            file_name="toms_raw_data_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_toms_raw_template",
        )
        st.write(
            "2. Download **toms_category_mapping_template.xlsx** — category "
            "IDs come exclusively from this file. A row matches when its "
            "**Gender in Title** AND **Words in Title** both appear somewhere "
            "in the item's **Title**, AND its **Specific Category** exactly "
            "matches the raw row's own Specific Category field. You can also "
            "upload your own existing category sheet directly, as long as it "
            "follows this same 4-column layout, one sheet per marketplace."
        )
        st.download_button(
            "Download toms_category_mapping_template.xlsx",
            data=file_bytes("toms_category_mapping_template.xlsx"),
            file_name="toms_category_mapping_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_toms_category_template",
        )
        st.write(
            "Zalora's **ColorFamily** and **SubCatType** are fully automatic "
            "— no field to fill in for either, same approach as the Herschel "
            "tool. ColorFamily is classified from the first Zalora image's "
            "dominant color (falling back to a keyword match on the Color "
            "name if no image is available), and SubCatType is looked up "
            "from the PrimaryCategory your category mapping resolves to."
        )
        st.write(
            "The category mapping file also has a **5th column, Sizechart "
            "Images** — the tool fills Shopee/Lazada's own Size chart Image "
            "URL column from it, and appends it as an extra image (after "
            "your real product images) for TikTok and Zalora, based on "
            "whichever category row matched. **Quantity is always output as "
            "0** across all four marketplaces, regardless of any stock info."
        )

    col1, col2 = st.columns(2)
    with col1:
        uploaded = st.file_uploader(
            "Upload your filled-in raw data file (.xlsx)", type=["xlsx"], key="toms_raw_upload"
        )
    with col2:
        uploaded_map = st.file_uploader(
            "Upload category mapping file (.xlsx) — needed to fill in Category IDs",
            type=["xlsx"], key="toms_category_upload",
        )

    if uploaded is None:
        st.info("Upload a filled-in raw data file to get started.")
        return

    try:
        raw_rows = load_toms_raw_file(uploaded)
    except Exception as e:
        st.error(f"Couldn't read the raw data file: {e}")
        return

    if not raw_rows:
        st.warning("No data rows found. Make sure Seller SKUs are filled in.")
        return

    if uploaded_map is not None:
        try:
            category_sheets, matched_sheets = load_toms_category_mapping_workbook(uploaded_map)
        except Exception as e:
            st.error(f"Couldn't read the category mapping file: {e}")
            return
        total_entries = sum(len(v) for v in category_sheets.values())
        st.success(
            f"Loaded {total_entries} category mapping row(s) from sheet(s): "
            + ", ".join(matched_sheets)
        )
        raw_rows = toms_mapping.apply_toms_category_mapping(raw_rows, category_sheets)
        unmatched = {p: [] for p in toms_mapping.PLATFORM_CATEGORY_FIELD}
        for r in raw_rows:
            for p, f in toms_mapping.PLATFORM_CATEGORY_FIELD.items():
                if not r.get(f):
                    unmatched[p].append(r.get("sku"))
        for p, skus in unmatched.items():
            if skus:
                st.warning(
                    f"No {PLATFORM_LABELS[p]} category match (Title didn't "
                    f"contain both keywords, or Specific Category didn't "
                    f"exactly match) for: " + ", ".join(str(s) for s in skus)
                )
        missing_subcats = toms_mapping.find_missing_subcat_categories(raw_rows)
        if missing_subcats:
            with st.expander(f"⚠️ Zalora SubCatType has no lookup entry for {len(missing_subcats)} PrimaryCategory value(s) — left blank"):
                st.write(
                    "Add these to `PRIMARY_CATEGORY_TO_SUBCAT` in "
                    "`toms_mapping.py` once you know the right SubCatType for each:"
                )
                for pc in missing_subcats:
                    st.write(pc)
    else:
        st.warning(
            "No category mapping file uploaded — every Category ID field will "
            "be blank in the output. Upload one to auto-fill them."
        )

    st.success(f"Loaded {len(raw_rows)} SKU row(s) across "
               f"{len(toms_mapping.group_rows_by_parent(raw_rows))} parent product(s).")

    with st.spinner("Classifying Zalora color families from images..."):
        raw_rows, unresolved_colors = toms_mapping.resolve_color_families(raw_rows)
    if unresolved_colors:
        with st.expander(f"⚠️ Zalora ColorFamily couldn't be determined for {len(unresolved_colors)} SKU(s) — left blank"):
            st.write(
                "No Zalora image could be fetched/classified, and the Color "
                "name didn't match any recognizable keyword either. Fill "
                "these in by hand in the downloaded file if needed:"
            )
            for s in unresolved_colors:
                st.write(s)

    st.subheader("Preview & download")
    tabs = st.tabs([PLATFORM_LABELS[p] for p in ["shopee", "lazada", "tiktok", "zalora"]])
    outputs = {}

    for tab, platform in zip(tabs, ["shopee", "lazada", "tiktok", "zalora"]):
        with tab:
            headers, out_rows = toms_mapping.build_platform_rows(platform, raw_rows)
            df_out = pd.DataFrame(out_rows, columns=headers)
            outputs[platform] = (headers, out_rows)
            st.dataframe(df_out, use_container_width=True, height=350)
            file_data = rows_to_xlsx_bytes(platform, headers, out_rows)
            st.download_button(
                f"Download {PLATFORM_LABELS[platform]} file",
                data=file_data,
                file_name=f"Toms_{PLATFORM_LABELS[platform].replace(' ', '_')}_Listing_File.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_toms_{platform}",
            )

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        for platform, (headers, out_rows) in outputs.items():
            zf.writestr(
                f"Toms_{PLATFORM_LABELS[platform].replace(' ', '_')}_Listing_File.xlsx",
                rows_to_xlsx_bytes(platform, headers, out_rows),
            )
    zip_buf.seek(0)
    st.divider()
    st.download_button(
        "⬇️ Download all 4 files as ZIP",
        data=zip_buf.read(),
        file_name="toms_marketplace_listing_files.zip",
        mime="application/zip",
        key="dl_toms_zip",
    )
