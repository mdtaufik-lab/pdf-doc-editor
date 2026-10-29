"""Local web page: paste Google Drive invoice links -> Download & check -> Edit -> Download ZIP.

Run with `.venv/bin/streamlit run app.py`. All logic lives in invoice_agent/; this file only
lays out the page.
"""
import pandas as pd
import streamlit as st

from invoice_agent import drive, workflow

st.set_page_config(page_title="Invoice Description Editor", layout="wide")
st.title("Invoice Description Editor")
st.caption("Changes the line-item description on contractor invoices to **Consultancy Fees** and "
           "leaves everything else exactly as it was. Files on Google Drive are never changed.")

if drive.connected():
    st.success("Google Drive is connected.")
else:
    st.error("Google Drive is not connected on this Mac (rclone remote `gdrive:` not found).")


def progress_bar():
    bar = st.progress(0.0, text="Starting…")

    def update(stage: str, done: int, total: int) -> None:
        bar.progress(done / total if total else 1.0, text=f"{stage}: {done} of {total}")
    return bar, update


# --- 1. links ---------------------------------------------------------------------------------
st.subheader("1. Paste Google Drive links")
links = st.text_area("One link per line", height=180,
                     placeholder="https://drive.google.com/open?id=1ZBE33pJ7n2Plk1lcTj4kbUYljun8ZD2s")
left, right = st.columns([1, 5])
if left.button("Download & check", type="primary", disabled=not links.strip()):
    bar, update = progress_bar()
    st.caption("Keep this tab open while it runs.")
    st.session_state.batch = workflow.check(links, update)
    bar.empty()
if right.button("Start over"):
    st.session_state.pop("batch", None)
    st.rerun()

batch = st.session_state.get("batch")
if not batch:
    st.stop()

for col, (label, value) in zip(st.columns(7), workflow.check_counts(batch).items()):
    col.metric(label, value)

rows = pd.DataFrame(workflow.table(batch))
show = st.radio("Show", ["All", "Ready to edit / edited", "Needs attention"], horizontal=True)
if show == "Ready to edit / edited":
    rows = rows[rows["Status"].isin(["ready_to_edit", "edited"])]
elif show == "Needs attention":
    rows = rows[rows["Status"].isin(["needs_review", "failed", "download_failed", "invalid_link",
                                     "not_in_relook_list"])]
st.dataframe(rows, hide_index=True, width="stretch")

# --- 2. edit ----------------------------------------------------------------------------------
st.subheader("2. Edit")
if not batch.edit_run:
    ready = len(batch.ready)
    if st.button(f"Edit {ready} invoice(s)", type="primary", disabled=ready == 0):
        bar, update = progress_bar()
        st.caption("Keep this tab open while it runs.")
        workflow.edit(batch, update)
        st.rerun()
    st.stop()

for col, (label, value) in zip(st.columns(3), workflow.edit_counts(batch).items()):
    col.metric(label, value)

edited = [i for i in batch.items if i.status == "edited"]
if edited:
    names = [i.file.name for i in edited]
    pick = st.selectbox("Before / after preview", names)
    st.image([str(p) for p in workflow.previews(edited[names.index(pick)])], width="stretch")

# --- 3. download ------------------------------------------------------------------------------
st.subheader("3. Download")
st.download_button("Download edited invoices (ZIP)", data=batch.zip_path.read_bytes(),
                   file_name=batch.zip_path.name, mime="application/zip", type="primary",
                   disabled=not edited)
st.caption(f"The edited files are also in the `Output` folder. Run details: `{batch.edit_run}`")
