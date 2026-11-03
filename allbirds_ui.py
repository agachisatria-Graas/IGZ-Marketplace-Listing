import io
import zipfile

import pandas as pd
import streamlit as st

import allbirds_mapping
import allbirds_masterfile as amf
from toms_mapping import resolve_color_families
from ui_common import PLATFORM_LABELS, CATEGORY_SHEET_PLATFORM, rows_to_xlsx_bytes

PLATFORMS = ["shopee", "lazada", "tiktok", "zalora", "shopify"]


def load_category_mapping_workbook(uploaded_file):
    """One sheet per marketplace: (Words in Title, Gender*, Category ID, optional Sizechart Images)."""
    sheets = pd.read_excel(uploaded_file, sheet_name=None, header=0)
    out = {p: [] for p in CATEGORY_SHEET_PLATFORM.values()}
    matched = []
    for name, df in sheets.items():
        platform = next((p for k, p in CATEGORY_SHEET_PLATFORM.items() if k in name.lower()), None)
        if not platform or df.shape[1] < 3:
            continue
        matched.append(name)
        for _, row in df.dropna(how="all").iterrows():
            words, gender, cat_id = row.iloc[0], row.iloc[1], row.iloc[2]
            sizechart = row.iloc[3] if df.shape[1] >= 4 else None
            if pd.isna(words) or str(words).strip() == "":
                continue
            out[platform].append({
                "words": str(words).strip(), "gender": "" if pd.isna(gender) else gender,
                "id": "" if pd.isna(cat_id) else cat_id,
                "sizechart": "" if sizechart is None or pd.isna(sizechart) else sizechart,
            })
    return out, matched


def _render_preview_and_downloads(raw_rows, zip_filename):
    st.subheader("Preview & download")
    tabs = st.tabs([PLATFORM_LABELS[p] for p in PLATFORMS])
    outputs = {}
    for tab, platform in zip(tabs, PLATFORMS):
        with tab:
            headers, out_rows = allbirds_mapping.build_platform_rows(platform, raw_rows)
            outputs[platform] = (headers, out_rows)
            st.dataframe(pd.DataFrame(out_rows, columns=headers), use_container_width=True, height=350)
            st.download_button(
                f"Download {PLATFORM_LABELS[platform]} file",
                data=rows_to_xlsx_bytes(platform, headers, out_rows),
                file_name=f"Allbirds_{PLATFORM_LABELS[platform].replace(' ', '_')}_Listing_File.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"allbirds_dl_{platform}",
            )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for platform, (headers, out_rows) in outputs.items():
            zf.writestr(f"Allbirds_{PLATFORM_LABELS[platform].replace(' ', '_')}_Listing_File.xlsx",
                        rows_to_xlsx_bytes(platform, headers, out_rows))
    buf.seek(0)
    st.divider()
    st.download_button("⬇️ Download all 5 files as ZIP", data=buf.read(), file_name=zip_filename,
                       mime="application/zip", key="allbirds_dl_zip")


def render():
    st.title("Allbirds Marketplace Listing Tool")
    st.caption("Shopee · Lazada · TikTok Shop · Zalora Indonesia · Shopify")
    st.write(
        "Upload your Allbirds Masterfile, the combined-images file (from the **Image Link "
        "Combiner** — 'Lazada Images', 'Zalora Images' and 'Shopify Images' sheets) and the "
        "Allbirds category mapping file."
    )
    st.caption(
        "Categories match when the sheet's **Words in Title** appears in the item title AND its "
        "**Gender*** equals the item's Gender*. The 4th column (Sizechart Images) fills Shopee/Lazada's "
        "size chart column and is added as an extra image for TikTok/Zalora."
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        master = st.file_uploader("Upload Masterfile (.xlsx)", type=["xlsx"], key="allbirds_master")
    with c2:
        images = st.file_uploader("Upload combined images file (.xlsx)", type=["xlsx"], key="allbirds_images")
    with c3:
        cat_file = st.file_uploader("Upload category mapping file (.xlsx)", type=["xlsx"], key="allbirds_cat")

    if master is None:
        st.info("Upload your Masterfile to get started.")
        return
    try:
        rows = amf.parse_masterfile(master)
    except Exception as e:
        st.error(f"Couldn't read the Masterfile: {e}")
        st.stop()
    if not rows:
        st.warning("No data rows found. Make sure Inventory Sku is filled in.")
        st.stop()

    if images is not None:
        try:
            lz, zl, sh = amf.load_images_workbook(images)
        except Exception as e:
            st.error(f"Couldn't read the images file: {e}")
            st.stop()
        rows, unmatched = amf.merge_images_into_rows(rows, lz, zl, sh)
        st.success(f"Matched images for {len(rows) - len(unmatched)} / {len(rows)} SKU(s) "
                   "(all sizes of a colour share that colour's images).")
        if not zl:
            st.info("The 'Zalora Images' sheet is empty — Zalora image columns will be blank.")
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
        rows = allbirds_mapping.apply_category_mapping(rows, sheets)
        for p, field in allbirds_mapping.PLATFORM_CATEGORY_FIELD.items():
            missing = [r["sku"] for r in rows if not r.get(field)]
            if missing:
                st.warning(f"No {PLATFORM_LABELS[p]} category match (Words in Title + Gender) for "
                           f"{len(missing)} SKU(s): " + ", ".join(missing[:15]) + (" …" if len(missing) > 15 else ""))
    else:
        st.warning("No category mapping file uploaded — every Category ID will be blank.")

    with st.spinner("Classifying Zalora color families..."):
        rows, unresolved = resolve_color_families(rows)
    if unresolved:
        with st.expander(f"⚠️ Zalora ColorFamily couldn't be determined for {len(unresolved)} SKU(s) — left blank"):
            for s in unresolved:
                st.write(s)

    st.success(f"Loaded {len(rows)} SKU row(s) across "
               f"{len(allbirds_mapping.group_rows_by_parent(rows))} parent product(s).")
    _render_preview_and_downloads(rows, "allbirds_listing_files.zip")
