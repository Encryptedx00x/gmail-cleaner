"""Dashboard — visão geral das classificações."""
import pandas as pd
import plotly.express as px
import streamlit as st

from ui.helpers import (
    CATEGORY_COLORS,
    ACTION_COLORS,
    load_stats,
    load_last_scan_time,
    render_sidebar_status,
)

st.set_page_config(page_title="Dashboard — Gmail Cleaner", page_icon="📊", layout="wide")
render_sidebar_status()

st.title("📊 Dashboard")

if st.button("🔄 Atualizar"):
    st.cache_data.clear()
    st.rerun()

stats = load_stats()

if "error" in stats:
    st.error(f"Erro ao carregar cache: {stats['error']}")
    st.stop()

if stats["total_emails"] == 0:
    st.warning("Nenhum email escaneado. Vá para **Scan** para começar.")
    st.stop()

# ── Métricas ──────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total no cache", f"{stats['total_emails']:,}")
c2.metric("Remetentes únicos", f"{stats['total_senders']:,}")

by_action = {r["action"]: r["cnt"] for r in stats.get("by_action", [])}
c3.metric("A deletar/mover lixeira",
          f"{by_action.get('delete', 0) + by_action.get('trash', 0):,}")
c4.metric("A arquivar", f"{by_action.get('archive', 0):,}")

last = load_last_scan_time()
if last:
    st.caption(f"Último scan: {last[:19].replace('T', ' ')} UTC")

st.markdown("---")

# ── Gráficos ──────────────────────────────────────────────────────────────────
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("Por categoria")
    df_cat = pd.DataFrame(stats["by_category"])
    if not df_cat.empty:
        color_map = {cat: CATEGORY_COLORS.get(cat, "#bdc3c7") for cat in df_cat["category"]}
        fig = px.pie(
            df_cat,
            names="category",
            values="cnt",
            color="category",
            color_discrete_map=color_map,
            hole=0.35,
        )
        fig.update_layout(margin=dict(t=0, b=0, l=0, r=0), legend_title="Categoria")
        st.plotly_chart(fig, use_container_width=True)

with col_right:
    st.subheader("Por ação recomendada")
    df_act = pd.DataFrame(stats["by_action"])
    if not df_act.empty:
        color_map_a = {a: ACTION_COLORS.get(a, "#bdc3c7") for a in df_act["action"]}
        fig2 = px.bar(
            df_act.sort_values("cnt", ascending=False),
            x="action",
            y="cnt",
            color="action",
            color_discrete_map=color_map_a,
            labels={"action": "Ação", "cnt": "Quantidade"},
        )
        fig2.update_layout(showlegend=False, margin=dict(t=0, b=0))
        st.plotly_chart(fig2, use_container_width=True)

# ── Tabela detalhada ──────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Detalhamento por categoria")

df_cat2 = pd.DataFrame(stats["by_category"])
df_act2 = pd.DataFrame(stats["by_action"])

tab1, tab2 = st.tabs(["Por categoria", "Por ação"])
with tab1:
    if not df_cat2.empty:
        df_cat2.columns = ["Categoria", "Quantidade"]
        df_cat2 = df_cat2.sort_values("Quantidade", ascending=False)
        st.dataframe(df_cat2, use_container_width=True, hide_index=True)
with tab2:
    if not df_act2.empty:
        df_act2.columns = ["Ação", "Quantidade"]
        df_act2 = df_act2.sort_values("Quantidade", ascending=False)
        st.dataframe(df_act2, use_container_width=True, hide_index=True)

# ── Categorias protegidas ─────────────────────────────────────────────────────
st.markdown("---")
st.info(
    "🔒 **Categorias protegidas** (nunca são deletadas automaticamente): "
    "`invoice`, `important`, `platform_credential`, `action_required`"
)
