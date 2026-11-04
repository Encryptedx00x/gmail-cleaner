"""Staging — plano de ação antes de executar."""
import pandas as pd
import plotly.express as px
import streamlit as st

from ui.helpers import (
    CATEGORY_COLORS,
    ACTION_COLORS,
    DESTRUCTIVE_ACTIONS,
    PROTECTED_CATEGORIES,
    classifications_to_df,
    render_sidebar_status,
)

st.set_page_config(page_title="Staging — Gmail Cleaner", page_icon="📋", layout="wide")
render_sidebar_status()

st.title("📋 Staging — Plano de Ação")
st.markdown("Visualize o que será feito antes de executar qualquer ação.")

# ── Filtros ───────────────────────────────────────────────────────────────────
with st.expander("⚙️ Filtros", expanded=False):
    col1, col2, col3 = st.columns(3)
    with col1:
        action_filter = st.selectbox(
            "Filtrar por ação",
            ["todas", "delete", "archive", "label", "keep", "review"],
        )
    with col2:
        category_filter = st.selectbox(
            "Filtrar por categoria",
            ["todas", "marketing", "spam", "newsletter", "transactional",
             "certificate", "course_access", "important", "invoice",
             "platform_credential", "action_required", "unknown"],
        )
    with col3:
        safe_only = st.checkbox("Apenas safe_to_automate=True", value=True)
    preview_count = st.slider("Exemplos de preview por grupo", 5, 30, 10)

generate_btn = st.button("🔄 Gerar Plano", type="primary")

if generate_btn or "staging_result" in st.session_state:
    if generate_btn:
        with st.spinner("Gerando plano de ação..."):
            try:
                from src.pipeline.staging import generate_staging_plan
                result = generate_staging_plan(
                    action_filter=None if action_filter == "todas" else action_filter,
                    category_filter=None if category_filter == "todas" else category_filter,
                    safe_only=safe_only,
                    preview_count=preview_count,
                )
                st.session_state.staging_result = result
            except Exception as exc:
                st.error(f"Erro ao gerar plano: {exc}")
                st.stop()

    result = st.session_state.get("staging_result", {})
    plan = result.get("plan", {})
    summary = result.get("summary", {})
    classifications = result.get("classifications", [])
    total = result.get("total", 0)

    if total == 0:
        st.warning("Nenhum email no plano. Execute o **Scan** primeiro.")
        st.stop()

    # ── Resumo ────────────────────────────────────────────────────────────────
    st.success(f"Plano gerado: **{total:,}** emails em {len(plan)} ações")

    # Métricas por ação
    action_counts = {a: len(ids) for a, ids in plan.items()}
    cols = st.columns(len(action_counts) or 1)
    for i, (action, count) in enumerate(sorted(action_counts.items())):
        emoji = "🗑️" if action in DESTRUCTIVE_ACTIONS else "📂" if action == "archive" else "🏷️" if action == "label" else "✅"
        cols[i].metric(f"{emoji} {action}", f"{count:,}")

    # Gráfico de barras do plano
    if summary:
        df_summary = pd.DataFrame([
            {"Ação": v["action"], "Categoria": v["category"], "Qtd": v["count"]}
            for v in summary.values()
        ])
        fig = px.bar(
            df_summary,
            x="Categoria", y="Qtd", color="Ação",
            color_discrete_map=ACTION_COLORS,
            barmode="group",
            title="Distribuição do plano",
        )
        fig.update_layout(margin=dict(t=40, b=0))
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # ── Aviso para ações destrutivas ──────────────────────────────────────────
    if any(a in plan for a in DESTRUCTIVE_ACTIONS):
        st.warning(
            "⚠️ **O plano contém ações destrutivas (delete/trash).**  \n"
            "Use **dry-run** na página Execute antes de confirmar."
        )

    # ── Preview por grupo de ação/categoria ──────────────────────────────────
    st.subheader("Preview por grupo")

    df_all = classifications_to_df(classifications)

    for key, info in sorted(summary.items(), key=lambda x: (x[1]["action"], x[1]["category"])):
        action = info["action"]
        category = info["category"]
        count = info["count"]
        is_destructive = action in DESTRUCTIVE_ACTIONS

        icon = "🗑️" if is_destructive else "📂" if action == "archive" else "🏷️" if action == "label" else "👁️"
        label = f"{icon} **{action.upper()}** — {category} ({count:,} emails)"

        with st.expander(label, expanded=is_destructive):
            subset = df_all[
                (df_all["action"] == action) & (df_all["category"] == category)
            ].head(preview_count)

            if not subset.empty:
                display_cols = [c for c in ["id_short", "confidence", "conf_pct", "safe_to_automate", "reason", "source", "has_unsubscribe"] if c in subset.columns]
                st.dataframe(
                    subset[display_cols].rename(columns={
                        "id_short": "ID",
                        "conf_pct": "Confiança",
                        "safe_to_automate": "Auto?",
                        "has_unsubscribe": "Unsub?",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )
            if is_destructive:
                st.info(f"Mostrados {min(preview_count, count)} de {count} emails que seriam movidos para lixeira.")

    st.markdown("---")
    if st.button("⚡ Ir para Execute", type="primary"):
        st.switch_page("pages/4_Execute.py")
