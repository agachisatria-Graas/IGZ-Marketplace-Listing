import io
import zipfile

import pandas as pd
import streamlit as st
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

import mapping
import herschel_mapping
import image_combiner

st.set_page_config(page_title="Agachi's Tools", layout="wide")

RAW_COLUMNS = [
    "parent_id", "sku", "title", "main_description", "long_description",
    "description_script_override", "short_description", "brand",
    "variant_name_1", "variant_value_1", "variant_name_2", "variant_value_2",
    "price", "stock", "parent_images", "variant_images", "weight_kg", "length_cm",
    "width_cm", "height_cm", "shopee_shipping_service",
    "shopee_item_specifications", "lazada_item_specifications",
    "tiktok_item_specifications", "zalora_gender",
    "zalora_subcat_type", "zalora_color_family", "zalora_color", "zalora_images",
]

RAW_LABEL_TO_KEY = {
    "Parent SKU": "parent_id", "Seller SKU": "sku", "Title": "title",
    "Main Description": "main_description", "Long Description (optional)": "long_description",
    "Description Script Override (optional)": "description_script_override",
    "Short Description (Lazada)": "short_description", "Brand": "brand",
    "Variant Name 1 (optional)": "variant_name_1", "Variant Value 1 (optional)": "variant_value_1",
    "Variant Name 2 (optional)": "variant_name_2", "Variant Value 2 (optional)": "variant_value_2",
    "Price": "price", "Stock / Quantity": "stock",
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
}

# Category mapping file: one workbook, one sheet per marketplace, each sheet
# with columns (Gender, Keyword in Title, Category ID) — matched positionally
# since the exact header wording differs sheet to sheet (see Agachi's real file).
CATEGORY_SHEET_PLATFORM = {
    "shopee": "shopee", "lazada": "lazada", "tiktok": "tiktok", "zalora": "zalora",
}

PLATFORM_LABELS = {
    "shopee": "Shopee", "lazada": "Lazada", "tiktok": "TikTok Shop", "zalora": "Zalora",
}


def load_raw_file(uploaded_file):
    """Reads the raw_data_template.xlsx layout: row1=labels, row2=notes, row3+=data."""
    df = pd.read_excel(uploaded_file, sheet_name=0, header=0)
    df = df.rename(columns=lambda c: RAW_LABEL_TO_KEY.get(str(c).strip(), str(c).strip()))
    # drop the notes row (row index 0 after header) if present
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
    Sheets are matched to a marketplace by checking if 'shopee'/'lazada'/'tiktok'/
    'zalora' appears anywhere in the sheet name (case-insensitive).
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
            if pd.isna(keyword) or str(keyword).strip() == "":
                continue
            out[platform].append({
                "gender": "" if pd.isna(gender) else gender,
                "keyword": str(keyword).strip(),
                "id": "" if pd.isna(cat_id) else cat_id,
            })
    return out, matched_sheets


def rows_to_xlsx_bytes(platform, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    header_font = Font(name="Arial", bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2F5496")
    for i, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=i, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for r_idx, row in enumerate(rows, start=2):
        for c_idx, val in enumerate(row, start=1):
            ws.cell(row=r_idx, column=c_idx, value=val)
    for i in range(1, len(headers) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 22
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def file_bytes(path):
    with open(path, "rb") as f:
        return f.read()


# ===========================================================================
# Herschel tool — fully separate raw data format, category matching rule,
# and mapping logic (herschel_mapping.py). Kept apart from the Hydro Flask
# constants/functions above on purpose.
# ===========================================================================

HERSCHEL_RAW_COLUMNS = [
    "parent_id", "sku", "title", "main_description", "main_description_2",
    "measurement", "description_script_override", "short_description", "brand",
    "variant_name_1", "variant_value_1", "variant_name_2", "variant_value_2",
    "price", "stock", "parent_images", "variant_images", "weight_kg", "length_cm",
    "width_cm", "height_cm", "product_type", "specific_category", "gender",
    "material", "shopee_shipping_service", "shopee_item_specifications",
    "lazada_item_specifications", "tiktok_item_specifications", "season", "year",
    "zalora_color", "zalora_images",
]

HERSCHEL_RAW_LABEL_TO_KEY = {
    "Parent SKU": "parent_id", "Seller SKU": "sku", "Title": "title",
    "Main Description": "main_description", "Main Description 2": "main_description_2",
    "Measurement": "measurement",
    "Description Script Override (optional)": "description_script_override",
    "Short Description (Lazada)": "short_description", "Brand": "brand",
    "Variant Name 1 (optional)": "variant_name_1", "Variant Value 1 (optional)": "variant_value_1",
    "Variant Name 2 (optional)": "variant_name_2", "Variant Value 2 (optional)": "variant_value_2",
    "Price": "price", "Stock / Quantity": "stock",
    "Parent Images": "parent_images", "Variant Images (optional)": "variant_images",
    "Weight (kg)": "weight_kg", "Length (cm)": "length_cm", "Width (cm)": "width_cm",
    "Height (cm)": "height_cm", "Product Type": "product_type",
    "Specific Category": "specific_category", "Gender*": "gender", "Material": "material",
    "Shopee Shipping Service": "shopee_shipping_service",
    "Shopee Item Specifications": "shopee_item_specifications",
    "Lazada Item Specifications": "lazada_item_specifications",
    "TikTok Item Specifications": "tiktok_item_specifications",
    "Season": "season", "Year": "year", "Zalora Color": "zalora_color",
    "Zalora Images": "zalora_images",
}


def load_herschel_raw_file(uploaded_file):
    """Reads the herschel_raw_data_template.xlsx layout: row1=labels, row2=notes, row3+=data."""
    df = pd.read_excel(uploaded_file, sheet_name=0, header=0)
    df = df.rename(columns=lambda c: HERSCHEL_RAW_LABEL_TO_KEY.get(str(c).strip(), str(c).strip()))
    if len(df) and str(df.iloc[0].get("title", "")).strip().startswith(("Product title", "Plain text")):
        df = df.iloc[1:]
    df = df.dropna(how="all")
    df = df[df["sku"].notna()] if "sku" in df.columns else df
    for col in HERSCHEL_RAW_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df.fillna("")
    return df[HERSCHEL_RAW_COLUMNS].to_dict(orient="records")


def load_herschel_category_mapping_workbook(uploaded_file):
    """Reads a category mapping workbook: one sheet per marketplace, each with
    (Product Type, Specific Category, Gender*, Category ID) columns — matched
    positionally. Sheets are matched to a marketplace by checking if
    'shopee'/'lazada'/'tiktok'/'zalora' appears in the sheet name.
    Returns ({'shopee': [{'product_type':..,'specific_category':..,'gender':..,'id':..}, ...], ...}, matched_sheet_names)
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
            pt, sc, gender, cat_id = row.iloc[0], row.iloc[1], row.iloc[2], row.iloc[3]
            if pd.isna(pt) or str(pt).strip() == "":
                continue
            out[platform].append({
                "product_type": pt, "specific_category": sc,
                "gender": "" if pd.isna(gender) else gender,
                "id": "" if pd.isna(cat_id) else cat_id,
            })
    return out, matched_sheets


def render_herschel_listing_tool():
    st.title("Herschel Marketplace Listing Tool")
    st.caption("Shopee · Lazada · TikTok Shop · Zalora Indonesia")
    st.write(
        "Separate from the Hydro Flask tool — its own raw data format, its own "
        "category mapping rules. Upload one raw data file with all your items "
        "and variants, and get back ready-to-post files formatted for each "
        "marketplace."
    )

    with st.expander("📋 First time here? Get the templates", expanded=False):
        st.write(
            "1. Download **herschel_raw_data_template.xlsx**, fill in one row "
            "per SKU (each variant of a product is its own row, sharing the "
            "same **Parent SKU**)."
        )
        st.download_button(
            "Download herschel_raw_data_template.xlsx",
            data=file_bytes("herschel_raw_data_template.xlsx"),
            file_name="herschel_raw_data_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_herschel_raw_template",
        )
        st.write(
            "2. Download **herschel_category_mapping_template.xlsx** — category "
            "IDs come exclusively from this file, matched by an EXACT match on "
            "**Product Type** + **Specific Category** + **Gender\\*** (not a "
            "keyword search like the Hydro Flask tool). You can also upload "
            "your own existing category sheet directly, as long as it follows "
            "this same 4-column layout, one sheet per marketplace."
        )
        st.download_button(
            "Download herschel_category_mapping_template.xlsx",
            data=file_bytes("herschel_category_mapping_template.xlsx"),
            file_name="herschel_category_mapping_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_herschel_category_template",
        )
        st.write(
            "Zalora's **ColorFamily** and **SubCatType** are fully automatic — "
            "no field to fill in for either. ColorFamily is classified from "
            "the first Zalora image's dominant color (falling back to a "
            "keyword match on the Color name if no image is available), and "
            "SubCatType is looked up from the PrimaryCategory your category "
            "mapping resolves to. **Weight** can be pasted straight from a "
            "spec sheet showing both units, e.g. `1.10 lb / 0.5` — the tool "
            "automatically takes the number after the `/` as kilograms."
        )

    col1, col2 = st.columns(2)
    with col1:
        uploaded = st.file_uploader(
            "Upload your filled-in raw data file (.xlsx)", type=["xlsx"], key="herschel_raw_upload"
        )
    with col2:
        uploaded_map = st.file_uploader(
            "Upload category mapping file (.xlsx) — needed to fill in Category IDs",
            type=["xlsx"], key="herschel_category_upload",
        )

    if uploaded is None:
        st.info("Upload a filled-in raw data file to get started.")
        return

    try:
        raw_rows = load_herschel_raw_file(uploaded)
    except Exception as e:
        st.error(f"Couldn't read the raw data file: {e}")
        return

    if not raw_rows:
        st.warning("No data rows found. Make sure Seller SKUs are filled in.")
        return

    if uploaded_map is not None:
        try:
            category_sheets, matched_sheets = load_herschel_category_mapping_workbook(uploaded_map)
        except Exception as e:
            st.error(f"Couldn't read the category mapping file: {e}")
            return
        total_entries = sum(len(v) for v in category_sheets.values())
        st.success(
            f"Loaded {total_entries} category mapping row(s) from sheet(s): "
            + ", ".join(matched_sheets)
        )
        raw_rows = herschel_mapping.apply_exact_category_mapping(raw_rows, category_sheets)
        unmatched = {p: [] for p in herschel_mapping.PLATFORM_CATEGORY_FIELD}
        for r in raw_rows:
            for p, f in herschel_mapping.PLATFORM_CATEGORY_FIELD.items():
                if not r.get(f):
                    unmatched[p].append(r.get("sku"))
        for p, skus in unmatched.items():
            if skus:
                st.warning(
                    f"No {PLATFORM_LABELS[p]} category match (Product Type + "
                    f"Specific Category + Gender didn't exactly match any row in "
                    f"the category mapping file) for: " + ", ".join(str(s) for s in skus)
                )
    else:
        st.warning(
            "No category mapping file uploaded — every Category ID field will "
            "be blank in the output. Upload one to auto-fill them."
        )

    st.success(f"Loaded {len(raw_rows)} SKU row(s) across "
               f"{len(herschel_mapping.group_rows_by_parent(raw_rows))} parent product(s).")

    with st.spinner("Classifying Zalora color families from images..."):
        raw_rows, unresolved_colors = herschel_mapping.resolve_color_families(raw_rows)
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
            headers, out_rows = herschel_mapping.build_platform_rows(platform, raw_rows)
            df_out = pd.DataFrame(out_rows, columns=headers)
            outputs[platform] = (headers, out_rows)
            st.dataframe(df_out, use_container_width=True, height=350)
            file_data = rows_to_xlsx_bytes(platform, headers, out_rows)
            st.download_button(
                f"Download {PLATFORM_LABELS[platform]} file",
                data=file_data,
                file_name=f"Herschel_{PLATFORM_LABELS[platform].replace(' ', '_')}_Listing_File.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_herschel_{platform}",
            )

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        for platform, (headers, out_rows) in outputs.items():
            zf.writestr(
                f"Herschel_{PLATFORM_LABELS[platform].replace(' ', '_')}_Listing_File.xlsx",
                rows_to_xlsx_bytes(platform, headers, out_rows),
            )
    zip_buf.seek(0)
    st.divider()
    st.download_button(
        "⬇️ Download all 4 files as ZIP",
        data=zip_buf.read(),
        file_name="herschel_marketplace_listing_files.zip",
        mime="application/zip",
        key="dl_herschel_zip",
    )


def render_listing_tool():
    st.title("Hydro Flask Marketplace Listing Tool")
    st.caption("Shopee · Lazada · TikTok Shop · Zalora Indonesia")
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

    col1, col2 = st.columns(2)
    with col1:
        uploaded = st.file_uploader("Upload your filled-in raw data file (.xlsx)", type=["xlsx"])
    with col2:
        uploaded_map = st.file_uploader(
            "Upload category mapping file (.xlsx) — needed to fill in Category IDs",
            type=["xlsx"],
        )

    if uploaded is not None:
        try:
            raw_rows = load_raw_file(uploaded)
        except Exception as e:
            st.error(f"Couldn't read the raw data file: {e}")
            st.stop()

        if not raw_rows:
            st.warning("No data rows found. Make sure Seller SKUs are filled in.")
            st.stop()

        if uploaded_map is not None:
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
                        f"category_mapping_template.xlsx matched these Titles, so their "
                        f"Category ID is blank): " + ", ".join(str(s) for s in skus)
                    )
        else:
            st.warning(
                "No category mapping file uploaded — every Category ID field will be "
                "blank in the output. Upload one to auto-fill them."
            )

        st.success(f"Loaded {len(raw_rows)} SKU row(s) across "
                   f"{len(mapping.group_rows_by_parent(raw_rows))} parent product(s).")

        st.subheader("Preview & download")
        tabs = st.tabs([PLATFORM_LABELS[p] for p in ["shopee", "lazada", "tiktok", "zalora"]])
        outputs = {}

        for tab, platform in zip(tabs, ["shopee", "lazada", "tiktok", "zalora"]):
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
                    key=f"dl_{platform}",
                )

        # bundle all four into one zip too
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
            "⬇️ Download all 4 files as ZIP",
            data=zip_buf.read(),
            file_name="marketplace_listing_files.zip",
            mime="application/zip",
        )
    else:
        st.info("Upload a filled-in raw data file to get started.")


def render_image_combiner():
    st.title("Image Link Combiner")
    st.caption("Not connected to the marketplace listing tool — just a standalone helper.")
    st.write(
        "Combines images into one `\" ; \"`-separated string per SKU, in the "
        "right order — ready to paste into the listing tool's image columns."
    )

    mode = st.radio(
        "What do you want to do?",
        [
            "Combine Lazada/Shopee images + Zalora images (two files → one output with 2 tabs)",
            "Combine a single file (File Name + Image URL columns)",
            "Paste a list of URLs where the SKU_N pattern is IN the URL itself",
        ],
    )

    if mode.startswith("Combine Lazada/Shopee"):
        st.write(
            "Zalora needs images at a different dimension (**762 × 1100**) than "
            "Lazada/Shopee, so it needs its own separate source file. Upload "
            "both below — each is combined independently, and the result comes "
            "back as one Excel file with a **Lazada Images** tab and a "
            "**Zalora Images** tab."
        )
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Lazada / Shopee Images")
            lazada_combined, lazada_unmatched = _media_file_uploader("lazada")
        with col2:
            st.subheader("Zalora Images (762 × 1100)")
            zalora_combined, zalora_unmatched = _media_file_uploader("zalora")

        if lazada_combined or zalora_combined:
            st.divider()
            st.subheader("Combined output")
            buf = io.BytesIO()
            wb = openpyxl.Workbook()
            wb.remove(wb.active)
            _write_combined_sheet(wb, "Lazada Images", lazada_combined or {})
            _write_combined_sheet(wb, "Zalora Images", zalora_combined or {})
            wb.save(buf)
            buf.seek(0)
            st.success(
                f"Lazada: {len(lazada_combined or {})} SKU(s) combined. "
                f"Zalora: {len(zalora_combined or {})} SKU(s) combined."
            )
            st.download_button(
                "Download lazada_zalora_images.xlsx",
                data=buf.read(),
                file_name="lazada_zalora_images.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            if lazada_unmatched:
                with st.expander(f"⚠️ Lazada: {len(lazada_unmatched)} File Name(s) didn't match SKU_N.ext"):
                    for u in lazada_unmatched:
                        st.write(u)
            if zalora_unmatched:
                with st.expander(f"⚠️ Zalora: {len(zalora_unmatched)} File Name(s) didn't match SKU_N.ext"):
                    for u in zalora_unmatched:
                        st.write(u)
        else:
            st.info("Upload at least one of the two files above to get started.")

    elif mode.startswith("Combine a single file"):
        st.write(
            "The order comes from the number in **File Name** (e.g. `_1`, `_2`, "
            "`_3`) — the **Image URL** column can be anything, even random "
            "upload links with no SKU in them at all."
        )
        combined, unmatched = _media_file_uploader("single")
        if combined is not None:
            _render_combined_result(combined, unmatched, unmatched_label="File Name(s)")
        else:
            st.info("Upload a file to get started.")

    else:
        pasted = st.text_area(
            "Paste image URLs, one per line",
            height=220,
            placeholder="https://example.com/DSM427123Y-M_1.jpg\n"
                        "https://example.com/DSM427123Y-M_2.jpg\n"
                        "https://example.com/HF-TUM-BUR_1.jpg",
        )
        if pasted.strip():
            urls = [line for line in pasted.splitlines() if line.strip()]
            combined, unmatched = image_combiner.group_and_combine(urls)
            _render_combined_result(combined, unmatched, unmatched_label="URL(s)")
        else:
            st.info("Paste some URLs to get started.")


def _media_file_uploader(key_prefix):
    """Renders a file uploader + column pickers, returns (combined, unmatched)
    or (None, None) if nothing's been uploaded yet."""
    uploaded_media_file = st.file_uploader(
        "Upload file (.xlsx or .csv)", type=["xlsx", "csv"], key=f"upload_{key_prefix}"
    )
    if uploaded_media_file is None:
        return None, None
    try:
        if uploaded_media_file.name.lower().endswith(".csv"):
            df_media = pd.read_csv(uploaded_media_file)
        else:
            df_media = pd.read_excel(uploaded_media_file)
    except Exception as e:
        st.error(f"Couldn't read that file: {e}")
        return None, None

    cols = list(df_media.columns)

    def guess(keywords, exclude=()):
        for c in cols:
            cl = str(c).lower()
            if any(k in cl for k in keywords) and not any(x in cl for x in exclude):
                return c
        return cols[0]

    guessed_filename_col = guess(["file", "name"], exclude=["date"])
    guessed_url_col = guess(["image", "url", "link"])

    filename_col = st.selectbox(
        "File Name column", cols, index=cols.index(guessed_filename_col),
        key=f"fname_{key_prefix}",
    )
    url_col = st.selectbox(
        "Image URL column", cols, index=cols.index(guessed_url_col),
        key=f"url_{key_prefix}",
    )
    st.dataframe(df_media[[filename_col, url_col]].head(5), use_container_width=True)

    pairs = list(zip(df_media[filename_col], df_media[url_col]))
    return image_combiner.group_and_combine_from_pairs(pairs)


def _write_combined_sheet(wb, sheet_title, combined):
    ws = wb.create_sheet(sheet_title)
    ws.cell(row=1, column=1, value="SKU").font = Font(bold=True)
    ws.cell(row=1, column=2, value="Combined Images").font = Font(bold=True)
    for i, (sku, joined) in enumerate(combined.items(), start=2):
        ws.cell(row=i, column=1, value=sku)
        ws.cell(row=i, column=2, value=joined)
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 80
    return ws


def _render_combined_result(combined, unmatched, unmatched_label="item(s)"):
    if combined:
        st.success(f"Combined images for {len(combined)} SKU(s).")
        df_result = pd.DataFrame(
            [{"SKU": sku, "Combined Images": joined} for sku, joined in combined.items()]
        )
        st.dataframe(df_result, use_container_width=True, height=300)

        buf = io.BytesIO()
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        _write_combined_sheet(wb, "Combined Images", combined)
        wb.save(buf)
        buf.seek(0)
        st.download_button(
            "Download combined_images.xlsx",
            data=buf.read(),
            file_name="combined_images.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.warning("None of the rows matched the expected `SKU_N.ext` pattern.")

    if unmatched:
        with st.expander(f"⚠️ {len(unmatched)} {unmatched_label} didn't match the SKU_N.ext pattern"):
            for u in unmatched:
                st.write(u)


tool_tab1, tool_tab2, tool_tab3 = st.tabs([
    "📦 Hydro Flask Marketplace Listing Tool",
    "🖼️ Image Link Combiner",
    "🎒 Herschel Marketplace Listing Tool",
])
with tool_tab1:
    render_listing_tool()
with tool_tab2:
    render_image_combiner()
with tool_tab3:
    render_herschel_listing_tool()
