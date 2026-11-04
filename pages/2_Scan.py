"""Scan — busca e classifica emails do Gmail."""
import threading
import time
from pathlib import Path

import streamlit as st

from ui.helpers import check_env, render_sidebar_status

# Módulo-level: threads não têm ScriptRunContext no Streamlit, então não podem
# escrever em st.session_state diretamente de forma confiável. Usamos dict global.
_SCAN_STATE: dict = {"running": False, "result": None, "error": None}

st.set_page_config(page_title="Scan — Gmail Cleaner", page_icon="🔍", layout="wide")
render_sidebar_status()

st.title("🔍 Scan de Emails")
st.markdown("Busca emails do Gmail e classifica com heurísticas + OpenAI.")

# ── Verifica ambiente ─────────────────────────────────────────────────────────
ok, missing = check_env()
if not ok:
    for m in missing:
        st.error(f"⚠️ {m}")
    st.stop()

# OAuth check
token_path = Path("./credentials/token.json")
if not token_path.exists():
    st.warning(
        "**Token OAuth não encontrado.** Execute no terminal primeiro para autenticar:\n"
        "```\npython main.py scan --limit 1\n```\n"
        "Isso abrirá o browser para login no Google. Depois volte aqui."
    )
    st.stop()

# ── Inicializa session state ──────────────────────────────────────────────────
for key, default in [
    ("scan_running", False),
    ("scan_result", None),
    ("scan_error", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ── Thread de scan ────────────────────────────────────────────────────────────
def _run_scan(limit, query, batch_size, skip_claude, reclassify_unknowns, reclassify_invoice=False):
    _SCAN_STATE["running"] = True
    _SCAN_STATE["result"] = None
    _SCAN_STATE["error"] = None
    try:
        from src.pipeline.scan import run_scan
        result = run_scan(
            limit=limit or None,
            query=query or "",
            batch_size=batch_size,
            skip_claude=skip_claude,
            reclassify_unknowns=reclassify_unknowns,
            reclassify_categories=["invoice"] if reclassify_invoice else [],
        )
        _SCAN_STATE["result"] = result
    except Exception as exc:
        _SCAN_STATE["error"] = str(exc)
    finally:
        _SCAN_STATE["running"] = False


# ── UI ────────────────────────────────────────────────────────────────────────
if st.session_state.scan_running:
    # Verifica se a thread já terminou (via dict global)
    if not _SCAN_STATE["running"]:
        st.session_state.scan_result = _SCAN_STATE["result"]
        st.session_state.scan_error = _SCAN_STATE["error"]
        st.session_state.scan_running = False
        st.rerun()
    else:
        st.info("⏳ Scan em andamento... Isso pode levar alguns minutos.")
        with st.status("Buscando e classificando emails...", expanded=True):
            st.write("• Conectando ao Gmail API")
            st.write("• Baixando metadados dos emails")
            st.write("• Aplicando heurísticas locais")
            st.write("• Enviando ambíguos para OpenAI (se habilitado)")
            st.write("• Salvando resultados no cache SQLite")
            st.markdown("_Acompanhe o progresso detalhado no terminal._")
        time.sleep(2)
        st.rerun()

elif st.session_state.scan_result is not None:
    result = st.session_state.scan_result
    st.success("✅ Scan concluído!")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total processados", f"{result.get('total', 0):,}")
    c2.metric("Já em cache", f"{result.get('already_cached', 0):,}")
    c3.metric("Heurísticas", f"{result.get('heuristic', 0):,}")
    c4.metric("OpenAI", f"{result.get('claude', 0):,}")
    c5.metric("Ambíguos", f"{result.get('ambiguous', 0):,}")

    stats = result.get("stats", {})
    if stats.get("by_category"):
        import pandas as pd
        import plotly.express as px
        from ui.helpers import CATEGORY_COLORS

        df = pd.DataFrame(stats["by_category"])
        color_map = {c: CATEGORY_COLORS.get(c, "#bdc3c7") for c in df["category"]}
        fig = px.bar(
            df.sort_values("cnt", ascending=False),
            x="category", y="cnt",
            color="category",
            color_discrete_map=color_map,
            labels={"category": "Categoria", "cnt": "Emails"},
            title="Resultado do scan por categoria",
        )
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    col1, col2 = st.columns(2)
    if col1.button("🔄 Novo scan"):
        st.session_state.scan_result = None
        st.rerun()
    if col2.button("📋 Ver Staging"):
        st.switch_page("pages/3_Staging.py")

elif st.session_state.scan_error:
    st.error(f"❌ Erro durante o scan:\n\n{st.session_state.scan_error}")
    if st.button("Tentar novamente"):
        st.session_state.scan_error = None
        st.rerun()

else:
    # Formulário de scan
    with st.form("scan_form"):
        st.subheader("Configurações do Scan")

        col1, col2 = st.columns(2)
        with col1:
            limit = st.number_input(
                "Limite de emails",
                min_value=10, max_value=5000, value=200, step=50,
                help="Para o scan completo (5000+), use o CLI: python main.py scan",
            )
            query = st.text_input(
                "Filtro Gmail (opcional)",
                placeholder="is:unread in:inbox",
                help="Sintaxe de busca do Gmail. Vazio = toda a caixa.",
            )
        with col2:
            batch_size = st.number_input(
                "Emails por lote OpenAI",
                min_value=5, max_value=50, value=30, step=5,
                help="Máximo de emails por chamada à API OpenAI.",
            )
            skip_claude = st.checkbox(
                "Usar só heurísticas (sem OpenAI)",
                value=False,
                help="Mais rápido e sem custo de API, mas menos preciso para emails ambíguos.",
            )
            reclassify_unknowns = st.checkbox(
                "Re-classificar unknowns com OpenAI",
                value=False,
                help="Envia emails já marcados como 'unknown' no cache para revisão da OpenAI.",
            )
            reclassify_invoice = st.checkbox(
                "Re-classificar invoice (corrigir declarações de participação)",
                value=False,
                help="Remove emails 'invoice' do cache e re-classifica. Use para corrigir 'declaração de participação' que foram capturadas errado.",
            )

        st.markdown("---")
        submitted = st.form_submit_button("🚀 Iniciar Scan", type="primary", use_container_width=True)

    if submitted:
        st.session_state.scan_running = True
        st.session_state.scan_result = None
        st.session_state.scan_error = None
        _SCAN_STATE["running"] = True
        t = threading.Thread(
            target=_run_scan,
            args=(int(limit), query, int(batch_size), skip_claude, reclassify_unknowns, reclassify_invoice),
            daemon=True,
        )
        t.start()
        st.rerun()

    # Dica
    st.markdown("---")
    st.markdown("**💡 Dica:** Para scans grandes (5000+ emails), use o CLI:")
    st.code("python main.py scan --skip-claude   # rápido, só heurísticas\npython main.py scan           # completo com OpenAI")
