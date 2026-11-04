"""Gmail Cleaner — Web UI (entry point Streamlit)."""
import streamlit as st

st.set_page_config(
    page_title="Gmail Cleaner",
    page_icon="📧",
    layout="wide",
    initial_sidebar_state="expanded",
)

from ui.helpers import render_sidebar_status, load_stats, CATEGORY_COLORS  # noqa: E402

render_sidebar_status()

st.title("📧 Gmail Cleaner")
st.markdown("Organize sua caixa do Gmail com heurísticas + OpenAI. Use o menu lateral para navegar.")

# Visão rápida
stats = load_stats()
if stats.get("total_emails", 0) == 0:
    st.info(
        "Nenhum email escaneado ainda. Vá para **Scan** para começar, "
        "ou execute `python main.py scan --limit 100` no terminal."
    )
else:
    c1, c2, c3 = st.columns(3)
    c1.metric("Emails no cache", f"{stats['total_emails']:,}")
    c2.metric("Remetentes únicos", f"{stats['total_senders']:,}")
    by_action = {r["action"]: r["cnt"] for r in stats.get("by_action", [])}
    c3.metric("A deletar / arquivar",
              f"{by_action.get('delete', 0) + by_action.get('trash', 0):,} / "
              f"{by_action.get('archive', 0):,}")

    st.markdown("---")
    st.markdown("**Navegação rápida:**")
    cols = st.columns(4)
    cols[0].page_link("pages/1_Dashboard.py", label="📊 Dashboard", icon="📊")
    cols[1].page_link("pages/2_Scan.py", label="🔍 Scan", icon="🔍")
    cols[2].page_link("pages/3_Staging.py", label="📋 Staging", icon="📋")
    cols[3].page_link("pages/4_Execute.py", label="⚡ Execute", icon="⚡")
