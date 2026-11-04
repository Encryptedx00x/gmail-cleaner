"""Execute — aplica ações após confirmação."""
import threading
import time

import pandas as pd
import streamlit as st

from ui.helpers import (
    DESTRUCTIVE_ACTIONS,
    render_sidebar_status,
)

# Módulo-level: threads não têm ScriptRunContext no Streamlit.
_EXEC_STATE: dict = {"running": False, "result": None, "error": None}

st.set_page_config(page_title="Execute — Gmail Cleaner", page_icon="⚡", layout="wide")
render_sidebar_status()

st.title("⚡ Execute")
st.markdown("Aplique as ações planejadas no Staging.")

# ── Inicializa session state ──────────────────────────────────────────────────
for key, default in [
    ("execute_running", False),
    ("execute_result", None),
    ("execute_error", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ── Thread de execução ────────────────────────────────────────────────────────
def _run_execute(category, action, dry_run, limit):
    _EXEC_STATE["running"] = True
    _EXEC_STATE["result"] = None
    _EXEC_STATE["error"] = None
    try:
        from src.pipeline.executor import execute_plan
        result = execute_plan(
            category=category or None,
            action=action or None,
            dry_run=dry_run,
            confirm=True,   # bypassa input() — confirmação feita pela UI
            limit=limit or None,
        )
        _EXEC_STATE["result"] = result
    except Exception as exc:
        _EXEC_STATE["error"] = str(exc)
    finally:
        _EXEC_STATE["running"] = False


# ── Running state ─────────────────────────────────────────────────────────────
if st.session_state.execute_running:
    # Verifica se a thread já terminou (via dict global)
    if not _EXEC_STATE["running"]:
        st.session_state.execute_result = _EXEC_STATE["result"]
        st.session_state.execute_error = _EXEC_STATE["error"]
        st.session_state.execute_running = False
        st.rerun()
    else:
        mode = "DRY-RUN" if st.session_state.get("_dry_run_flag", True) else "LIVE"
        st.info(f"⏳ Execução em andamento ({mode})...")
        with st.status(f"Executando ações ({mode})...", expanded=True):
            st.write("• Autenticando Gmail API")
            st.write("• Aplicando ações em lotes")
            st.write("• Registrando log em reports/deleted_log.jsonl")
            st.markdown("_Acompanhe o progresso no terminal._")
        time.sleep(2)
        st.rerun()

elif st.session_state.execute_result is not None:
    result = st.session_state.execute_result

    if result.get("cancelled"):
        st.warning("Execução cancelada.")
    elif not result:
        st.info("Nenhuma ação para executar. Verifique o Staging.")
    else:
        mode_label = "DRY-RUN" if st.session_state.get("_dry_run_flag", True) else "LIVE"
        st.success(f"✅ Execução **{mode_label}** concluída!")

        rows = []
        for action, counters in result.items():
            if isinstance(counters, dict):
                rows.append({
                    "Ação": action,
                    "✅ Sucesso": counters.get("success", 0),
                    "❌ Falhas": counters.get("failed", 0),
                    "⏭️ Pulados": counters.get("skipped", 0),
                })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        if not st.session_state.get("_dry_run_flag", True):
            st.info("📄 Log de deletados salvo em `reports/deleted_log.jsonl`")

    col1, col2 = st.columns(2)
    if col1.button("🔄 Nova execução"):
        st.session_state.execute_result = None
        st.rerun()
    if col2.button("📊 Ver Dashboard"):
        st.switch_page("pages/1_Dashboard.py")

elif st.session_state.execute_error:
    st.error(f"❌ Erro:\n\n{st.session_state.execute_error}")
    if st.button("Tentar novamente"):
        st.session_state.execute_error = None
        st.rerun()

else:
    # ── Formulário de execução ────────────────────────────────────────────────
    # Modo dry-run
    dry_run = st.toggle("🧪 Dry-Run (simulação)", value=True)

    if not dry_run:
        st.error(
            "⚠️ **MODO LIVE ATIVADO** — As ações serão aplicadas no seu Gmail real.  \n"
            "Emails serão movidos para a **lixeira** (reversível).  \n"
            "Certifique-se de ter revisado o **Staging** antes de continuar."
        )
    else:
        st.success("✅ Modo Dry-Run: nenhuma alteração será feita no Gmail.")

    st.markdown("---")

    # Filtros de execução
    st.subheader("Configurações")
    col1, col2, col3 = st.columns(3)
    with col1:
        category_opt = st.selectbox(
            "Categoria (opcional)",
            ["todas", "marketing", "spam", "newsletter", "transactional",
             "certificate", "course_access", "unknown"],
            help="Executa apenas esta categoria. 'todas' = sem filtro.",
        )
    with col2:
        action_opt = st.selectbox(
            "Ação (opcional)",
            ["todas", "delete", "archive", "label", "keep", "review"],
            help="Executa apenas esta ação. 'todas' = sem filtro.",
        )
    with col3:
        limit_exec = st.number_input(
            "Limite de emails",
            min_value=0, max_value=10000, value=0, step=50,
            help="0 = sem limite. Use para testar com poucos emails primeiro.",
        )

    st.markdown("---")

    # Confirmações de segurança
    st.subheader("Confirmações de segurança")

    needs_destructive_confirm = (
        action_opt in ("todas", "delete", "trash") and not dry_run
    )
    confirmed_destructive = True

    if needs_destructive_confirm:
        confirmed_destructive = st.checkbox(
            "✅ Confirmo que quero mover emails para a **lixeira** (reversível via Gmail → Lixeira)",
            value=False,
        )

    confirmed_live = True
    if not dry_run:
        confirmed_live = st.checkbox(
            "✅ Confirmo que o Staging foi revisado e quero executar em modo LIVE",
            value=False,
        )

    can_execute = confirmed_destructive and confirmed_live

    st.markdown("---")

    col_exec, col_staging = st.columns(2)

    with col_exec:
        if st.button(
            f"{'🧪 Simular' if dry_run else '⚡ EXECUTAR'} ações",
            type="primary",
            disabled=not can_execute,
            use_container_width=True,
        ):
            st.session_state._dry_run_flag = dry_run
            st.session_state.execute_running = True
            st.session_state.execute_result = None
            st.session_state.execute_error = None
            _EXEC_STATE["running"] = True
            t = threading.Thread(
                target=_run_execute,
                args=(
                    None if category_opt == "todas" else category_opt,
                    None if action_opt == "todas" else action_opt,
                    dry_run,
                    int(limit_exec) if limit_exec > 0 else None,
                ),
                daemon=True,
            )
            t.start()
            st.rerun()

    with col_staging:
        if st.button("📋 Revisar Staging primeiro", use_container_width=True):
            st.switch_page("pages/3_Staging.py")

    # Dica de fluxo seguro
    st.markdown("---")
    st.markdown(
        "**Fluxo seguro recomendado:**\n"
        "1. Execute em **Dry-Run** → confirme os resultados\n"
        "2. Ative **LIVE** → marque as confirmações → execute\n"
        "3. Verifique `reports/deleted_log.jsonl` após execução"
    )
