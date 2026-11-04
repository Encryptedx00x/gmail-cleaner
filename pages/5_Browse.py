"""Browse — navega emails classificados por categoria."""
import pandas as pd
import streamlit as st

from ui.helpers import (
    CATEGORY_COLORS,
    ACTION_COLORS,
    PROTECTED_CATEGORIES,
    DESTRUCTIVE_ACTIONS,
    classifications_to_df,
    render_sidebar_status,
)

st.set_page_config(page_title="Browse — Gmail Cleaner", page_icon="🗂️", layout="wide")
render_sidebar_status()

st.title("🗂️ Browse Emails")
st.markdown("Explore os emails classificados, filtre por categoria e exporte para CSV.")

# ── Carrega dados ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def load_all_classifications():
    try:
        from src.classifier.cache import ClassifierCache
        cache = ClassifierCache()
        return cache.get_all_classifications()
    except Exception as exc:
        return {"error": str(exc)}


data = load_all_classifications()

if isinstance(data, dict) and "error" in data:
    st.error(f"Erro ao carregar cache: {data['error']}")
    st.stop()

if not data:
    st.warning("Nenhum email classificado. Vá para **Scan** primeiro.")
    st.stop()

df = classifications_to_df(data)

if df.empty:
    st.warning("Nenhum dado disponível.")
    st.stop()

# ── Filtros ───────────────────────────────────────────────────────────────────
with st.expander("⚙️ Filtros", expanded=True):
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        categories = ["todas"] + sorted(df["category"].unique().tolist())
        cat_filter = st.selectbox("Categoria", categories)

    with col2:
        actions = ["todas"] + sorted(df["action"].unique().tolist())
        act_filter = st.selectbox("Ação recomendada", actions)

    with col3:
        min_conf = st.slider(
            "Confiança mínima",
            min_value=0.0, max_value=1.0, value=0.0, step=0.05,
            format="%.2f",
        )

    with col4:
        safe_filter = st.selectbox(
            "Safe to automate",
            ["todos", "sim", "não"],
        )

    search_term = st.text_input(
        "Buscar (sender/subject/reason)",
        placeholder="ex: newsletter, amazon, promoção...",
    )

# ── Aplica filtros ────────────────────────────────────────────────────────────
filtered = df.copy()

if cat_filter != "todas":
    filtered = filtered[filtered["category"] == cat_filter]

if act_filter != "todas":
    filtered = filtered[filtered["action"] == act_filter]

if min_conf > 0.0:
    filtered = filtered[filtered["confidence"] >= min_conf]

if safe_filter == "sim":
    filtered = filtered[filtered["safe_to_automate"] == True]
elif safe_filter == "não":
    filtered = filtered[filtered["safe_to_automate"] == False]

if search_term:
    mask = (
        filtered.get("sender", pd.Series(dtype=str)).str.contains(search_term, case=False, na=False)
        | filtered.get("subject", pd.Series(dtype=str)).str.contains(search_term, case=False, na=False)
        | filtered.get("reason", pd.Series(dtype=str)).str.contains(search_term, case=False, na=False)
    )
    filtered = filtered[mask]

# ── Métricas resumo ───────────────────────────────────────────────────────────
total_all = len(df)
total_filtered = len(filtered)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total no cache", f"{total_all:,}")
c2.metric("Exibindo", f"{total_filtered:,}")
c3.metric(
    "Safe to automate",
    f"{filtered['safe_to_automate'].sum():,}" if "safe_to_automate" in filtered.columns else "—",
)
c4.metric(
    "Confiança média",
    f"{filtered['confidence'].mean():.0%}" if not filtered.empty else "—",
)

st.markdown("---")

# ── Tabela principal ──────────────────────────────────────────────────────────
if filtered.empty:
    st.info("Nenhum email encontrado com os filtros selecionados.")
else:
    # Paginação
    PAGE_SIZE = 100
    total_pages = max(1, (total_filtered + PAGE_SIZE - 1) // PAGE_SIZE)
    col_pag, col_export = st.columns([3, 1])

    with col_pag:
        page = st.number_input(
            f"Página (de {total_pages})",
            min_value=1, max_value=total_pages, value=1, step=1,
        )

    with col_export:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        csv_data = filtered.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Exportar CSV",
            data=csv_data,
            file_name="gmail_cleaner_export.csv",
            mime="text/csv",
            use_container_width=True,
        )

    start = (page - 1) * PAGE_SIZE
    page_df = filtered.iloc[start : start + PAGE_SIZE]

    # Colunas a exibir
    display_cols = [c for c in [
        "id_short", "category", "action", "confidence", "conf_pct",
        "safe_to_automate", "source", "reason", "has_unsubscribe",
    ] if c in page_df.columns]

    rename_map = {
        "id_short": "ID",
        "category": "Categoria",
        "action": "Ação",
        "conf_pct": "Confiança",
        "safe_to_automate": "Auto?",
        "source": "Fonte",
        "reason": "Motivo",
        "has_unsubscribe": "Unsub?",
    }

    st.dataframe(
        page_df[display_cols].rename(columns=rename_map),
        use_container_width=True,
        hide_index=True,
        height=600,
    )

    st.caption(f"Mostrando {start + 1}–{min(start + PAGE_SIZE, total_filtered)} de {total_filtered:,} emails")

# ── Distribuição dos filtrados ────────────────────────────────────────────────
if not filtered.empty and total_filtered > 1:
    st.markdown("---")
    st.subheader("Distribuição dos emails filtrados")

    col_l, col_r = st.columns(2)

    with col_l:
        cat_counts = filtered["category"].value_counts().reset_index()
        cat_counts.columns = ["Categoria", "Qtd"]
        import plotly.express as px
        color_map = {c: CATEGORY_COLORS.get(c, "#bdc3c7") for c in cat_counts["Categoria"]}
        fig = px.pie(
            cat_counts,
            names="Categoria",
            values="Qtd",
            color="Categoria",
            color_discrete_map=color_map,
            hole=0.35,
            title="Por categoria",
        )
        fig.update_layout(margin=dict(t=40, b=0, l=0, r=0))
        st.plotly_chart(fig, use_container_width=True)

    with col_r:
        act_counts = filtered["action"].value_counts().reset_index()
        act_counts.columns = ["Ação", "Qtd"]
        color_map_a = {a: ACTION_COLORS.get(a, "#bdc3c7") for a in act_counts["Ação"]}
        fig2 = px.bar(
            act_counts,
            x="Ação",
            y="Qtd",
            color="Ação",
            color_discrete_map=color_map_a,
            title="Por ação",
        )
        fig2.update_layout(showlegend=False, margin=dict(t=40, b=0))
        st.plotly_chart(fig2, use_container_width=True)

# ── Navegação ─────────────────────────────────────────────────────────────────
st.markdown("---")
col1, col2 = st.columns(2)
if col1.button("🔍 Ir para Scan"):
    st.switch_page("pages/2_Scan.py")
if col2.button("⚡ Ir para Execute"):
    st.switch_page("pages/4_Execute.py")
