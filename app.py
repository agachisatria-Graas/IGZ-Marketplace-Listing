import streamlit as st

import hydro_flask_ui
import image_combiner_ui
import herschel_ui
import toms_ui

st.set_page_config(page_title="Agachi's Tools", layout="wide")

tool_tab1, tool_tab2, tool_tab3, tool_tab4 = st.tabs([
    "📦 Hydro Flask Marketplace Listing Tool",
    "🖼️ Image Link Combiner",
    "🎒 Herschel Marketplace Listing Tool",
    "👟 Toms Marketplace Listing Tool",
])
with tool_tab1:
    hydro_flask_ui.render()
with tool_tab2:
    image_combiner_ui.render()
with tool_tab3:
    herschel_ui.render()
with tool_tab4:
    toms_ui.render()

# To add a new brand: create <brand>_ui.py with its own raw-file loader,
# category-mapping loader, and a render() function (copy an existing
# *_ui.py as a starting template), then add one import line and one
# tab here. Nothing else in this file needs to change.
