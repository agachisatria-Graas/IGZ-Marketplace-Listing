import io
import zipfile

import pandas as pd
import streamlit as st

import columbia_mapping as cm
import columbia_masterfile as cmf
from ui_common import CATEGORY_SHEET_PLATFORM, rows_to_xlsx_bytes

OUTPUT_LABELS = {
    "shopee": "Shopee", "lazada": "Lazada", "zalora": "Zalora", "shopify": "Shopify",
    **cm.TIKTOK_OUTPUTS,
}
OUTPUT_FILE_NAMES = {
    "shopee": "Columbia_Shopee_Listing_File.xlsx", "lazada": "Columbia_Lazada_Listing_File.xlsx",
    "zalora": "Columbia_Zalora_Listing_File.xlsx", "shopify": "Columbia_Shopify_Listing_File.xlsx",
    **cm.TIKTOK_FILE_NAMES,
}
PLATFORM_LABELS_SHORT = {"shopee": "Shopee", "lazada": "Lazada", "tiktok": "TikTok", "zalora": "Zalora"}


def load_category_mapping_workbook(uploaded_file):
    """One sheet per marketplace: (Words in Title, Gender*, Category ID [, extra]).
    Optional 4th column: 'Sizechart Images' (any sheet) or 'SubCatType' (Zalora sheet), told apart by its header."""
    sheets = pd.read_excel(uploaded_file, sheet_name=None, header=0)
    out = {p: [] for p in CATEGORY_SHEET_PLATFORM.values()}
    matched = []
    for name, df in sheets.items():
        platform = next((p for k, p in CATEGORY_SHEET_PLATFORM.items() if k in name.lower()), None)
        if not platform or df.shape[1] < 3:
            continue
        matched.append(name)
        extra_is_subcat = df.shape[1] >= 4 and "subcat" in str(df.columns[3]).lower()
        for _, row in df.dropna(how="all").iterrows():
            words, gender, cat_id = row.iloc[0], row.iloc[1], row.iloc[2]
            extra = row.iloc[3] if df.shape[1] >= 4 else None
            extra = "" if extra is None or pd.isna(extra) else extra
            if pd.isna(words) or str(words).strip() == "":
                continue
            e = {"words": str(words).strip(), "gender": "" if pd.isna(gender) else gender,
                 "id": "" if pd.isna(cat_id) else cat_id}
            if extra_is_subcat:
                e["subcat_type"] = extra
            else:
                e["sizechart"] = extra
            out[platform].append(e)
    return out, matched


def _render_preview_and_downloads(rows):
    st.subheader("Preview & download")
    keys = list(OUTPUT_LABELS)
    tabs = st.tabs([OUTPUT_LABELS[k] for k in keys])
    outputs = {}
    for tab, key in zip(tabs, keys):
        with tab:
            headers, out_rows = cm.build_platform_rows(key, rows)
            if not out_rows:
                st.info("No items fall into this output in the uploaded Masterfile.")
                continue
            outputs[key] = (headers, out_rows)
            st.caption(f"{len(out_rows)} row(s)")
            st.dataframe(pd.DataFrame(out_rows, columns=headers), use_container_width=True, height=350)
            st.download_button(
                f"Download {OUTPUT_LABELS[key]} file",
                data=rows_to_xlsx_bytes(key, headers, out_rows),
                file_name=OUTPUT_FILE_NAMES[key],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"columbia_dl_{key}",
            )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for key, (headers, out_rows) in outputs.items():
            zf.writestr(OUTPUT_FILE_NAMES[key], rows_to_xlsx_bytes(key, headers, out_rows))
    buf.seek(0)
    st.divider()
    st.download_button(f"⬇️ Download all {len(outputs)} files as ZIP", data=buf.read(),
                       file_name="columbia_listing_files.zip", mime="application/zip", key="columbia_dl_zip")


def render():
    st.title("Columbia Marketplace Listing Tool")
    st.caption("Shopee · Lazada · Zalora Indonesia · Shopify · TikTok Shop (5 files)")
    st.write(
        "Upload your Columbia Masterfile, the combined-images file (from the **Image Link "
        "Combiner** — 'Lazada Images', 'Zalora Images' and 'Shopify Images' sheets) and the "
        "Columbia category mapping file."
    )
    st.caption(
        "Parent SKUs with several SKUs become variant listings (Color and/or Size, whichever "
        "actually varies); a Parent SKU with one SKU, or Listing Type 'No Variation', is a single "
        "listing. TikTok is split into Men's Apparels, Women's Apparels, Footwears, Bags and "
        "Accessories based on the item title and gender."
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        master = st.file_uploader("Upload Masterfile (.xlsx)", type=["xlsx"], key="columbia_master")
    with c2:
        images = st.file_uploader("Upload combined images file (.xlsx)", type=["xlsx"], key="columbia_images")
    with c3:
        cat_file = st.file_uploader("Upload category mapping file (.xlsx)", type=["xlsx"], key="columbia_cat")

    if master is None:
        st.info("Upload your Masterfile to get started.")
        return
    try:
        rows = cmf.parse_masterfile(master)
    except Exception as e:
        st.error(f"Couldn't read the Masterfile: {e}")
        st.stop()
    if not rows:
        st.warning("No data rows found. Make sure Inventory Sku is filled in.")
        st.stop()

    if images is not None:
        try:
            lz, zl, sh = cmf.load_images_workbook(images)
        except Exception as e:
            st.error(f"Couldn't read the images file: {e}")
            st.stop()
        rows, unmatched = cmf.merge_images_into_rows(rows, lz, zl, sh)
        st.success(f"Matched images for {len(rows) - len(unmatched)} / {len(rows)} SKU(s) "
                   "(all sizes of a colour share that colour's images).")
        if unmatched:
            with st.expander(f"⚠️ No images matched for {len(unmatched)} SKU(s)"):
                for s in unmatched:
                    st.write(s)
    else:
        st.warning("No images file uploaded — Product Image URL(s) will be blank in every output.")

    if cat_file is not None:
        try:
            sheets, matched = load_category_mapping_workbook(cat_file)
        except Exception as e:
            st.error(f"Couldn't read the category mapping file: {e}")
            st.stop()
        st.success(f"Loaded {sum(len(v) for v in sheets.values())} category row(s) from: " + ", ".join(matched))
        rows = cm.apply_category_mapping(rows, sheets)
        for p, field in cm.PLATFORM_CATEGORY_FIELD.items():
            missing = sorted({r["title"] for r in rows if not r.get(field)})
            if missing:
                st.warning(f"No {PLATFORM_LABELS_SHORT[p]} category match (Words in Title + Gender) — Category ID left "
                           f"empty for {len(missing)} item(s): " + "; ".join(missing))
    else:
        st.warning("No category mapping file uploaded — every Category ID will be blank.")

    with st.spinner("Classifying Zalora color families..."):
        rows, unresolved = cm.resolve_color_families(rows)
    if unresolved:
        with st.expander(f"⚠️ Zalora ColorFamily couldn't be determined for {len(unresolved)} SKU(s) — left blank"):
            for s in unresolved:
                st.write(s)

    groups = cm.build_groups(rows)
    singles = sum(1 for g in groups if not cm.group_axes(g))
    st.success(f"Loaded {len(rows)} SKU row(s): {len(groups) - singles} variant listing(s) and {singles} single-SKU listing(s).")
    _render_preview_and_downloads(rows)
