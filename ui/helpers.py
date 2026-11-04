"""Utilitários compartilhados entre as páginas Streamlit."""
import os
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

# Garante que o projeto está no path
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Muda working directory para a raiz do projeto (necessário para paths relativos do .env)
os.chdir(ROOT)


# ── Cores por categoria ───────────────────────────────────────────────────────

CATEGORY_COLORS: dict[str, str] = {
    "marketing": "#e74c3c",
    "spam": "#c0392b",
    "newsletter": "#e67e22",
    "transactional": "#f39c12",
    "certificate": "#27ae60",
    "course_access": "#2ecc71",
    "important": "#2980b9",
    "invoice": "#1a5276",
    "platform_credential": "#6c3483",
    "action_required": "#d35400",
    "unknown": "#95a5a6",
}

ACTION_COLORS: dict[str, str] = {
    "delete": "#e74c3c",
    "trash": "#c0392b",
    "archive": "#e67e22",
    "label": "#27ae60",
    "keep": "#2980b9",
    "review": "#95a5a6",
}

PROTECTED_CATEGORIES = {"invoice", "important", "platform_credential", "action_required"}
DESTRUCTIVE_ACTIONS = {"delete", "trash"}


# ── Verificação de ambiente ───────────────────────────────────────────────────

def check_env() -> tuple[bool, list[str]]:
    """Verifica se as variáveis de ambiente obrigatórias estão configuradas."""
    from dotenv import load_dotenv
    load_dotenv()

    missing = []
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    creds = os.getenv("GMAIL_CREDENTIALS_PATH", "./credentials/credentials.json")
    if not Path(creds).exists():
        missing.append(f"credentials.json não encontrado em: {creds}")
    token = os.getenv("GMAIL_TOKEN_PATH", "./credentials/token.json")
    if not Path(token).exists():
        missing.append("token.json ausente (execute 'python main.py scan --limit 1' no terminal para autenticar)")

    return len(missing) == 0, missing


# ── Cache helpers ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def load_stats() -> dict:
    """Carrega estatísticas do cache SQLite (cache de 30s)."""
    try:
        from src.classifier.cache import EmailCache
        return EmailCache().get_stats()
    except Exception as e:
        return {
            "total_emails": 0,
            "total_senders": 0,
            "by_category": [],
            "by_action": [],
            "error": str(e),
        }


@st.cache_data(ttl=30)
def load_category_emails(category: str) -> list[dict]:
    """Carrega emails de uma categoria específica."""
    try:
        from src.classifier.cache import EmailCache
        return EmailCache().get_by_category(category)
    except Exception:
        return []


def load_categories() -> list[str]:
    """Lista de categorias com emails no cache."""
    stats = load_stats()
    return [r["category"] for r in stats.get("by_category", [])]


def load_last_scan_time() -> Optional[str]:
    """Retorna o timestamp da última classificação."""
    try:
        from src.classifier.cache import EmailCache
        cache = EmailCache()
        with cache._connect() as conn:
            row = conn.execute(
                "SELECT MAX(classified_at) as last FROM email_cache"
            ).fetchone()
        return row["last"] if row else None
    except Exception:
        return None


# ── Formatação ────────────────────────────────────────────────────────────────

def confidence_badge(v: float) -> str:
    """Retorna emoji de confiança."""
    if v >= 0.90:
        return "🟢"
    if v >= 0.80:
        return "🟡"
    return "🔴"


def classifications_to_df(classifications: list[dict]) -> pd.DataFrame:
    """Converte lista de classificações para DataFrame formatado."""
    if not classifications:
        return pd.DataFrame()
    df = pd.DataFrame(classifications)
    if "confidence" in df.columns:
        df["conf_pct"] = (df["confidence"] * 100).round(1).astype(str) + "%"
    if "safe_to_automate" in df.columns:
        df["safe_to_automate"] = df["safe_to_automate"].map({1: "✓", 0: "✗", True: "✓", False: "✗"})
    if "has_unsubscribe" in df.columns:
        df["has_unsubscribe"] = df["has_unsubscribe"].map({1: "✓", 0: "✗", True: "✓", False: "✗"})
    # Encurta email_id para exibição
    if "email_id" in df.columns:
        df["id_short"] = df["email_id"].str[:16]
    return df


# ── Sidebar padrão ────────────────────────────────────────────────────────────

def render_sidebar_status() -> None:
    """Renderiza status do ambiente na sidebar."""
    ok, missing = check_env()
    stats = load_stats()

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Status**")

    if ok:
        st.sidebar.success("Ambiente configurado")
    else:
        for m in missing:
            st.sidebar.error(m)

    total = stats.get("total_emails", 0)
    st.sidebar.metric("Emails no cache", f"{total:,}")

    last = load_last_scan_time()
    if last:
        st.sidebar.caption(f"Último scan: {last[:19].replace('T', ' ')}")

    st.sidebar.markdown("---")
    st.sidebar.caption("Gmail Cleaner — web UI")
