import io
import zipfile

import pandas as pd
import streamlit as st

import mapping
import hydro_flask_masterfile as hfm
from ui_common import PLATFORM_LABELS, CATEGORY_SHEET_PLATFORM, file_bytes, rows_to_xlsx_bytes

RAW_COLUMNS = [
    "parent_id", "sku", "title", "main_description", "long_description",
    "description_script_override", "short_description", "brand",
    "variant_name_1", "variant_value_1", "variant_name_2", "variant_value_2",
    "price", "parent_images", "variant_images", "weight_kg", "length_cm",
    "width_cm", "height_cm", "shopee_shipping_service",
    "shopee_item_specifications", "lazada_item_specifications",
    "tiktok_item_specifications", "zalora_gender",
    "zalora_subcat_type", "zalora_color_family", "zalora_color", "zalora_images",
    "season", "year",
]

RAW_LABEL_TO_KEY = {
    "Parent SKU": "parent_id", "Seller SKU": "sku", "Title": "title",
    "Main Description": "main_description", "Long Description (optional)": "long_description",
    "Description Script Override (optional)": "description_script_override",
    "Short Description (Lazada)": "short_description", "Brand": "brand",
    "Variant Name 1 (optional)": "variant_name_1", "Variant Value 1 (optional)": "variant_value_1",
    "Variant Name 2 (optional)": "variant_name_2", "Variant Value 2 (optional)": "variant_value_2",
    "Price": "price",
    "Parent Images": "parent_images", "Variant Images (optional)": "variant_images",
    "Weight (kg)": "weight_kg", "Length (cm)": "length_cm", "Width (cm)": "width_cm",
    "Height (cm)": "height_cm",
    "Shopee Shipping Service": "shopee_shipping_service",
    "Shopee Item Specifications": "shopee_item_specifications",
    "Lazada Item Specifications": "lazada_item_specifications",
    "TikTok Item Specifications": "tiktok_item_specifications",
    "Zalora Gender": "zalora_gender", "Zalora Sub Cat Type": "zalora_subcat_type",
    "Zalora Color Family": "zalora_color_family", "Zalora Color": "zalora_color",
    "Zalora Images": "zalora_images",
    "Season": "season", "Year": "year",
}


def load_raw_file(uploaded_file):
    """Reads the raw_data_template.xlsx layout: row1=labels, row2=notes, row3+=data."""
    df = pd.read_excel(uploaded_file, sheet_name=0, header=0)
    df = df.rename(columns=lambda c: RAW_LABEL_TO_KEY.get(str(c).strip(), str(c).strip()))
    if len(df) and str(df.iloc[0].get("title", "")).strip().startswith(("Product title", "Plain text")):
        df = df.iloc[1:]
    df = df.dropna(how="all")
    df = df[df["sku"].notna()] if "sku" in df.columns else df
    for col in RAW_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df.fillna("")
    return df[RAW_COLUMNS].to_dict(orient="records")


def load_category_mapping_workbook(uploaded_file):
    """Reads a category mapping workbook: one sheet per marketplace, each with
    (Gender, Keyword in Title, Category ID) columns — matched positionally.
    The Zalora sheet may have an optional 4th column, SubCatType, captured
    as 'subcat_type' on each entry.
    Returns {'shopee': [{'gender':..,'keyword':..,'id':..}, ...], 'lazada': [...], ...}
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
        if not platform or df.shape[1] < 3:
            continue
        matched_sheets.append(sheet_name)
        df = df.dropna(how="all")
        for _, row in df.iterrows():
            gender, keyword, cat_id = row.iloc[0], row.iloc[1], row.iloc[2]
            subcat_type = row.iloc[3] if df.shape[1] >= 4 else None
            if pd.isna(keyword) or str(keyword).strip() == "":
                continue
            out[platform].append({
                "gender": "" if pd.isna(gender) else gender,
                "keyword": str(keyword).strip(),
                "id": "" if pd.isna(cat_id) else cat_id,
                "subcat_type": "" if pd.isna(subcat_type) else subcat_type,
            })
    return out, matched_sheets


def _render_preview_and_downloads(raw_rows, platforms, zip_filename, key_prefix=""):
    st.subheader("Preview & download")
    tabs = st.tabs([PLATFORM_LABELS[p] for p in platforms])
    outputs = {}

    for tab, platform in zip(tabs, platforms):
        with tab:
            headers, out_rows = mapping.build_platform_rows(platform, raw_rows)
            df_out = pd.DataFrame(out_rows, columns=headers)
            outputs[platform] = (headers, out_rows)
            st.dataframe(df_out, use_container_width=True, height=350)
            file_data = rows_to_xlsx_bytes(platform, headers, out_rows)
            st.download_button(
                f"Download {PLATFORM_LABELS[platform]} file",
                data=file_data,
                file_name=f"{PLATFORM_LABELS[platform].replace(' ', '_')}_Listing_File.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_{key_prefix}_{platform}",
            )

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        for platform, (headers, out_rows) in outputs.items():
            zf.writestr(
                f"{PLATFORM_LABELS[platform].replace(' ', '_')}_Listing_File.xlsx",
                rows_to_xlsx_bytes(platform, headers, out_rows),
            )
    zip_buf.seek(0)
    st.divider()
    st.download_button(
        f"⬇️ Download all {len(platforms)} files as ZIP",
        data=zip_buf.read(),
        file_name=zip_filename,
        mime="application/zip",
        key=f"dl_{key_prefix}_zip",
    )


def _apply_category_mapping_with_warnings(raw_rows, uploaded_map, platforms):
    """Shared by both modes: loads the category mapping file, applies it,
    and shows the standard success/warning messages. Returns raw_rows
    (possibly unchanged if no file was uploaded)."""
    if uploaded_map is None:
        st.warning(
            "No category mapping file uploaded — every Category ID field will be "
            "blank in the output. Upload one to auto-fill them."
        )
        return raw_rows

    try:
        category_sheets, matched_sheets = load_category_mapping_workbook(uploaded_map)
    except Exception as e:
        st.error(f"Couldn't read the category mapping file: {e}")
        st.stop()
    total_keywords = sum(len(v) for v in category_sheets.values())
    st.success(
        f"Loaded {total_keywords} keyword mapping(s) from sheet(s): "
        + ", ".join(matched_sheets)
    )
    raw_rows = mapping.apply_title_category_mapping(raw_rows, category_sheets)
    unmatched = {p: [] for p in mapping.PLATFORM_CATEGORY_FIELD}
    for r in raw_rows:
        for p, f in mapping.PLATFORM_CATEGORY_FIELD.items():
            if not r.get(f):
                unmatched[p].append(r.get("sku"))
    for p, skus in unmatched.items():
        if skus:
            st.warning(
                f"No {PLATFORM_LABELS[p]} category match (no keyword in "
                f"the category mapping file matched these Titles, so their "
                f"Category ID is blank): " + ", ".join(str(s) for s in skus)
            )
    return raw_rows


def _resolve_colors_with_warning(raw_rows):
    """Shared by both modes: runs Zalora ColorFamily classification (keyword
    match on Color first, image-based fallback), and flags any SKU it
    couldn't resolve. Returns the updated raw_rows."""
    with st.spinner("Classifying Zalora color families..."):
        raw_rows, unresolved_colors = mapping.resolve_color_families(raw_rows)
    if unresolved_colors:
        with st.expander(f"⚠️ Zalora ColorFamily couldn't be determined for {len(unresolved_colors)} SKU(s) — left blank"):
            st.write(
                "The Color name didn't match any recognizable keyword, and "
                "no Zalora image could be fetched/classified either. Fill "
                "these in by hand in the downloaded file if needed:"
            )
            for s in unresolved_colors:
                st.write(s)
    return raw_rows


def render():
    st.title("Hydro Flask Marketplace Listing Tool")
    st.caption("Shopee · Lazada · TikTok Shop · Zalora Indonesia · Shopify")

    mode = st.radio(
        "How do you want to provide your item data?",
        [
            "Fill in the simple raw data template",
            "Import my own Masterfile + Images + Category files",
        ],
    )

    if mode.startswith("Fill in"):
        _render_simple_template_mode()
    else:
        _render_masterfile_import_mode()


def _render_simple_template_mode():
    st.write(
        "Upload one raw data file with all your items and variants, and get back "
        "ready-to-post files formatted for each marketplace."
    )

    with st.expander("📋 First time here? Get the templates", expanded=False):
        st.write(
            "1. Download **raw_data_template.xlsx**, fill in one row per SKU (each "
            "variant of a product is its own row, sharing the same **Parent SKU**)."
        )
        st.download_button(
            "Download raw_data_template.xlsx",
            data=file_bytes("raw_data_template.xlsx"),
            file_name="raw_data_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.write(
            "2. Download **category_mapping_template.xlsx** — this is how category IDs "
            "get filled in; there's no manual category ID field in the raw data file "
            "anymore. It has one sheet per marketplace — add a row per keyword: if that "
            "keyword appears anywhere in an item's Title (and Gender matches, if given), "
            "that category ID gets filled in automatically. You can also upload your own "
            "existing category mapping file directly, as long as it follows this same "
            "Gender / Keyword in Title / Category ID layout, one sheet per marketplace."
        )
        st.download_button(
            "Download category_mapping_template.xlsx",
            data=file_bytes("category_mapping_template.xlsx"),
            file_name="category_mapping_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.write(
            "**Quantity is always output as 0** across all four marketplaces. "
            "Lazada's description also always ends with an HTML image tag for "
            "the first Parent Image, in addition to the regular description."
        )

    col1, col2 = st.columns(2)
    with col1:
        uploaded = st.file_uploader(
            "Upload your filled-in raw data file (.xlsx)", type=["xlsx"], key="simple_raw_upload"
        )
    with col2:
        uploaded_map = st.file_uploader(
            "Upload category mapping file (.xlsx) — needed to fill in Category IDs",
            type=["xlsx"], key="simple_category_upload",
        )

    if uploaded is None:
        st.info("Upload a filled-in raw data file to get started.")
        return

    try:
        raw_rows = load_raw_file(uploaded)
    except Exception as e:
        st.error(f"Couldn't read the raw data file: {e}")
        st.stop()

    if not raw_rows:
        st.warning("No data rows found. Make sure Seller SKUs are filled in.")
        st.stop()

    raw_rows = _apply_category_mapping_with_warnings(
        raw_rows, uploaded_map, ["shopee", "lazada", "tiktok", "zalora"]
    )

    raw_rows = _resolve_colors_with_warning(raw_rows)

    st.success(f"Loaded {len(raw_rows)} SKU row(s) across "
               f"{len(mapping.group_rows_by_parent(raw_rows))} parent product(s).")

    _render_preview_and_downloads(
        raw_rows, ["shopee", "lazada", "tiktok", "zalora"],
        "marketplace_listing_files.zip", key_prefix="simple",
    )


def _render_masterfile_import_mode():
    st.write(
        "Upload your own internal Masterfile, a combined-images file (the "
        "same format the **Image Link Combiner** tool produces — a "
        "'Lazada Images' sheet and a 'Zalora Images' sheet, each with SKU + "
        "Combined Images columns), and a category mapping file — no need to "
        "manually re-type anything into the simple template. This mode also "
        "produces a **5th file: Shopify**."
    )
    st.caption(
        "Matched by column label, not position, so a reordered Masterfile "
        "export still works as long as the column headers (row 2) are the "
        "same. Images and category IDs are matched by Seller SKU / Title, "
        "same as the simple template mode."
    )

    with st.expander("⚠️ What this mode does NOT pull from your Masterfile", expanded=False):
        st.write(
            "- **Item Specifications (Shopee/Lazada/TikTok)** — not present "
            "in the Masterfile format, left blank beyond the automatic "
            "Brand + Material defaults. Shopee's Shipping Service Details "
            "and Zalora's SubCatType are fixed hardcoded values (not from "
            "the Masterfile), same as the simple template mode. Add any "
            "other specs afterward in the downloaded files if needed.\n"
            "- **Shopify Category ID / tags / Product Type** — now resolved "
            "from the category mapping file's own **Shopify Category** "
            "sheet (keyword-in-title match, same as the other 4 platforms), "
            "not borrowed from Shopee's category anymore.\n"
            "- **Zalora Color Family** — the Masterfile's own Color Family "
            "column is ignored; it's auto-classified the same way as the "
            "Herschel/Toms tools (keyword match on Color first, image-based "
            "fallback), so it's always one of Zalora's 18 accepted values."
        )

    col1, col2, col3 = st.columns(3)
    with col1:
        uploaded_master = st.file_uploader(
            "Upload your Masterfile (.xlsx)", type=["xlsx"], key="master_upload"
        )
    with col2:
        uploaded_images = st.file_uploader(
            "Upload combined images file (.xlsx)", type=["xlsx"], key="master_images_upload"
        )
    with col3:
        uploaded_map = st.file_uploader(
            "Upload category mapping file (.xlsx)", type=["xlsx"], key="master_category_upload"
        )

    if uploaded_master is None:
        st.info("Upload your Masterfile to get started (images and category files are optional, but recommended).")
        return

    try:
        raw_rows = hfm.parse_masterfile(uploaded_master)
    except Exception as e:
        st.error(f"Couldn't read the Masterfile: {e}")
        st.stop()

    if not raw_rows:
        st.warning("No data rows found. Make sure Inventory Sku is filled in.")
        st.stop()

    if uploaded_images is not None:
        try:
            lazada_images, zalora_images = hfm.load_images_workbook(uploaded_images)
        except Exception as e:
            st.error(f"Couldn't read the images file: {e}")
            st.stop()
        raw_rows, unmatched_imgs = hfm.merge_images_into_rows(raw_rows, lazada_images, zalora_images)
        st.success(f"Matched images for {len(raw_rows) - len(unmatched_imgs)} / {len(raw_rows)} SKU(s).")
        if unmatched_imgs:
            with st.expander(f"⚠️ No images matched for {len(unmatched_imgs)} SKU(s)"):
                for s in unmatched_imgs:
                    st.write(s)
    else:
        st.warning("No images file uploaded — Product Image URL(s) will be blank in every output.")

    raw_rows = _apply_category_mapping_with_warnings(
        raw_rows, uploaded_map, ["shopee", "lazada", "tiktok", "zalora", "shopify"]
    )

    raw_rows = _resolve_colors_with_warning(raw_rows)

    st.success(f"Loaded {len(raw_rows)} SKU row(s) across "
               f"{len(mapping.group_rows_by_parent(raw_rows))} parent product(s).")

    _render_preview_and_downloads(
        raw_rows, ["shopee", "lazada", "tiktok", "zalora", "shopify"],
        "hydro_flask_masterfile_listing_files.zip", key_prefix="master",
    )
