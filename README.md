# Marketplace Listing Tool

Turns one raw product spreadsheet into ready-to-post files for **Shopee, Lazada,
TikTok Shop, and Zalora Indonesia** — matching the column layouts of your
`IGZ_Hydro_Flask_*_Listing_File.xlsx` templates.

## Files

- `app.py` — the Streamlit app itself, with **three tabs**: the Hydro Flask
  Marketplace Listing Tool, a standalone Image Link Combiner, and the
  Herschel Marketplace Listing Tool (all independent of each other, just
  hosted at the same link).
- `mapping.py` — Hydro Flask's conversion logic (raw row → each
  marketplace's columns)
- `herschel_mapping.py` — Herschel's conversion logic — a fully separate
  module with its own category-matching rule (exact match on Product Type +
  Specific Category + Gender) and material-extraction logic, though it
  reuses Hydro Flask's brand-agnostic helpers (image joining, HTML
  conversion, output header layouts) rather than duplicating them
- `image_combiner.py` — the Image Link Combiner's logic
- `raw_data_template.xlsx` / `category_mapping_template.xlsx` — Hydro
  Flask's input templates
- `herschel_raw_data_template.xlsx` / `herschel_category_mapping_template.xlsx`
  — Herschel's input templates (you can also upload your real
  `IGZ_Herschel_category_sheet.xlsx`-style file directly instead of the blank
  template)
- `build_template.py` / `build_herschel_template.py` — regenerate the
  respective pair of templates if you want to tweak them

## Herschel tool — how it differs from Hydro Flask's

- **Category ID**: resolved by an **exact match** on Product Type + Specific
  Category + Gender\* (e.g. `BAGS` + `BACKPACKS` + `US`) against the category
  mapping file — not a keyword-in-title search. No manual fallback field
  exists; an unmatched combination leaves that platform's Category ID blank.
- **Gender\* code**: also translated into Zalora's own Gender field —
  `US`→`Men`, `WN`→`Women`, `UK`→`Kids`.
- **Material**: type (or paste) a free-text list into the **Material**
  column — the tool scans it line by line and picks out the first
  recognizable material keyword (Polyester, Leather, Nylon, Cotton, Canvas,
  Suede, Wool, Rubber, Silicone, Stainless Steel, etc.) to use in Shopee,
  Lazada, and Zalora's Material fields.
- **Item Specifications order**: Shopee is always `Brand=Herschel` then
  `Material=<extracted>`, with anything in **Shopee Item Specifications**
  appended after. Lazada is always `normal.delivery_option_economy=No`,
  `normal.Hazmat=None`, then `normal.material=<extracted>`, with **Lazada
  Item Specifications** appended after.
- **Description**: combines **three** fields — Main Description, Main
  Description 2, and Measurement — joined with line breaks, instead of
  Hydro Flask's two-field Main+Long combo.
- **Weight**: paste straight from a spec sheet showing both units, e.g.
  `1.10 lb / 0.5` — the tool takes the number **after** the `/` as
  kilograms (that's the kg figure; before the slash is lb). A plain number
  with no slash works fine too.
- **Output headers match Agachi's real Herschel templates exactly** —
  Shopee (50 cols), Lazada (65 cols, including the English/Tax
  Class/Model/Template Attribute fields), TikTok (41 cols, in Indonesian,
  matching the real category-attribute columns like Pola/Gaya/Bahan), and
  Zalora (68 cols). These are separate header layouts from Hydro Flask's,
  defined independently in `herschel_mapping.py`.
- **TikTok Item Specifications**: a `Key=Value` pair whose key matches one
  of TikTok's named attribute columns (Jenis Kulit, Pola, Acara, Gaya,
  Instruksi Mencuci, Tipe Pengencang, Tipe Tas, Fitur, Bahan) — case
  insensitive — lands directly in that column. `Bahan` (Material) is also
  auto-filled from the Material column's extracted keyword unless you
  override it explicitly. Unmatched keys are dropped for TikTok (there's no
  generic overflow column in the real template).
- **Zalora ColorFamily**: fully automatic, no field to fill in. The tool
  fetches the **first Zalora Image** and classifies its dominant color
  against Zalora's 18 valid families (black, grey, white, red, pink,
  orange, yellow, green, blue, purple, turquoise, bronze, lilac purple,
  silver, beige, gold, navy, brown) by nearest color match. If no image is
  available or the fetch fails, it falls back to a keyword match on the
  **Zalora Color** name (e.g. "Navy" → navy). This matters because many
  real color names are non-literal (e.g. "Enderman", "Creeper", "Pink
  Sheep") — keyword matching alone only catches about half of those
  correctly, so the image is the primary signal. Any SKU where neither
  method resolves a family is flagged in the app and left blank for manual
  entry.
- **Zalora SubCatType**: also fully automatic, looked up from a fixed
  PrimaryCategory → SubCatType table (`PRIMARY_CATEGORY_TO_SUBCAT` in
  `herschel_mapping.py`) extracted from Agachi's real Zalora listing file.
  If a new PrimaryCategory shows up that isn't in that table yet, SubCatType
  comes back blank — extend the dict with the new pair when that happens.
- **Season / Year**: simple pass-through raw columns, used only by Zalora's
  output.

## Setup (one time)

You need Python 3.9+ installed. Then, in this folder:

```bash
pip install streamlit pandas openpyxl requests Pillow
```

## Running the tool

```bash
streamlit run app.py
```

This opens the tool in your browser at `http://localhost:8501`. Keep it running
while you use it; close the terminal to stop it.

## How to use it

1. **Fill in `raw_data_template.xlsx`** — one row per SKU. If a product has
   variants (e.g. 3 colors), give each color its own row, but use the **same
   Parent SKU** for all of them — that's how the tool knows to group them and
   build the "Variation" columns correctly. **Seller SKU** (right after Parent
   SKU) must be unique per row.
   - **Parent Images**: URLs shared by the whole product, separated by ` ; `
     (space, semicolon, space). Used for Shopee, Lazada, and TikTok.
   - **Variant Images**: URLs for that one specific variant only, same
     separator. Leave blank if a variant has no images of its own. Also used
     for Shopee, Lazada, and TikTok (merged after Parent Images).
   - **Zalora Images**: a completely separate image set, same ` ; `
     separator. Zalora needs different image dimensions/crops than the other
     three marketplaces, so it doesn't reuse Parent/Variant Images at all —
     fill this in independently with Zalora-ready images.
   - **Main Description** + **Long Description**: plain text, one
     bullet/point per line each. The tool joins them with a line break
     (Long Description is optional — leave it blank if you only need one
     block of text). This combined text is used as-is for Shopee & TikTok,
     and auto-converted into a simple HTML `<p>/<br>` script for Lazada &
     Zalora. If you already have a polished HTML script for Lazada/Zalora,
     paste it into **Description Script Override** instead and it'll be
     used verbatim, ignoring Main/Long Description.
   - **Item Specifications**: each marketplace now has its **own** field —
     **Shopee Item Specifications**, **Lazada Item Specifications**, and
     **TikTok Item Specifications** — so you can give each platform different
     spec keys/values if needed. Format is `Key=Value` pairs separated by
     ` ; `, e.g. `Brand=Hydro Flask ; Material=Stainless Steel`. Lazada's specs
     also become its bullet-point Short Description if that field is left
     blank. ⚠️ TikTok's real bulk template needs specs under specific
     `product_property/<numeric-id>` columns that are category-specific — this
     tool can't know those IDs automatically, so TikTok specs land in generic
     "Specification 1, 2, 3…" columns in the output. Copy those values into
     the correct attribute columns in TikTok Seller Centre before uploading.
   - There's **no manual category ID field in the raw data file anymore** —
     category IDs are filled in exclusively from the Category Mapping file
     (step 2 below), matched against each item's Title.
2. **Fill in `category_mapping_template.xlsx`** — this is now the only way
   category IDs get filled in. It has one sheet per marketplace (Shopee/
   Lazada/TikTok/Zalora), each with **Gender**, **Keyword in Title**, and
   **Category ID** columns — this matches the format of your own
   `IGZ_Hydro_Flask_category_sheet.xlsx`, so you can also upload that file (or
   an extended version of it) directly instead of the blank template.
   - For each raw data row, the tool checks whether any **Keyword in Title**
     appears anywhere inside that row's **Title** (case-insensitive), and — if
     Gender is filled in — whether it matches the row's **Zalora Gender**
     field (which doubles as the general "gender" used for this lookup across
     all 4 platforms). The first matching row wins, so put more specific
     keywords above more general ones if you expect overlaps.
   - If a row's Title matches no keyword for a given platform, that
     platform's Category ID is left **blank** in the output — there's no
     manual fallback field anymore, so make sure your keyword list covers
     every product line you sell (or fill in the ID by hand afterward for the
     rare item that doesn't match).
3. **Upload both files** — the raw data file and the category mapping file —
   into the tool.
4. **Preview** each marketplace's tab to sanity-check the output — watch for
   any "No category match" warnings.
5. **Download** each file individually, or grab the ZIP with all four.
6. Copy the rows into the marketplace's official bulk-upload template if it
   needs to go through a specific sheet/cell range (Lazada and Zalora's real
   templates ship with extra reference sheets for dropdowns — this tool
   outputs a clean data-only file with the right headers, ready to paste in).

## Notes on the mapping logic

- **Weight**: enter in kg. The tool converts to grams automatically for
  TikTok's `parcel_weight` field; Shopee/Lazada/Zalora stay in kg.
- **Variants**: currently supports up to 2 variant axes (e.g. Color + Size).
  If a Parent SKU only has one row, it's treated as a no-variation product and
  the "Total variation" / "Variation" fields are left blank.
- **Shopee Shipping Service**: pass this through exactly as you'd want it to
  appear in Shopee's "Shipping Service Details" column (e.g.
  `Reguler (Cashless):18000.00, Instant:18000.00`).
- Everything is editable in `mapping.py` if a column mapping needs
  adjusting as your templates evolve — each marketplace has its own
  `build_<platform>_row()` function, and category-keyword matching lives in
  `match_keyword_category()` / `apply_title_category_mapping()`.
