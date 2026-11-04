"""Staging: gera e exibe plano de ação antes de qualquer execução."""
from typing import Optional

from rich.table import Table
from rich.panel import Panel

from src.classifier.cache import EmailCache
from src.utils.config import get_settings
from src.utils.logger import get_logger, console

logger = get_logger(__name__)

# Ações que são destrutivas e exigem atenção extra
DESTRUCTIVE_ACTIONS = {"delete", "trash"}

# Categorias protegidas — nunca incluídas em planos automáticos
PROTECTED_CATEGORIES = {
    "invoice", "important", "platform_credential", "action_required"
}


def generate_staging_plan(
    action_filter: Optional[str] = None,
    category_filter: Optional[str] = None,
    safe_only: bool = True,
    preview_count: int = 10,
) -> dict:
    """
    Gera plano de ação a partir do cache de classificações.

    Filtra emails classificados, mostra preview de exemplos e retorna
    o plano completo para ser aprovado pelo usuário antes de executar.

    Args:
        action_filter: Filtrar por ação ('delete', 'archive', 'label', etc.).
        category_filter: Filtrar por categoria.
        safe_only: Se True, inclui apenas emails com safe_to_automate=True.
        preview_count: Número de exemplos a exibir no preview.

    Returns:
        Dict com: plan (lista de email_ids por ação), summary, preview_shown.
    """
    settings = get_settings()
    cache = EmailCache()

    console.rule("[bold yellow]STAGING — Plano de Ação[/bold yellow]")

    # Busca classificações do cache
    with cache._connect() as conn:
        query_parts = ["SELECT * FROM email_cache WHERE 1=1"]
        params: list = []

        if safe_only:
            query_parts.append("AND safe_to_automate = 1")

        if action_filter:
            query_parts.append("AND action = ?")
            params.append(action_filter)

        if category_filter:
            query_parts.append("AND category = ?")
            params.append(category_filter)

        # Exclui categorias protegidas
        protected_placeholders = ",".join("?" * len(PROTECTED_CATEGORIES))
        query_parts.append(f"AND category NOT IN ({protected_placeholders})")
        params.extend(PROTECTED_CATEGORIES)

        query_parts.append("ORDER BY category, action")
        rows = conn.execute(" ".join(query_parts), params).fetchall()

    classifications = [dict(r) for r in rows]

    if not classifications:
        console.print("[yellow]Nenhum email no staging. Execute 'scan' primeiro.[/yellow]")
        return {"plan": {}, "summary": {}, "total": 0}

    # Agrupa por ação
    plan: dict[str, list[str]] = {}
    summary: dict[str, dict] = {}

    for cls in classifications:
        action = cls["action"]
        category = cls["category"]

        if action not in plan:
            plan[action] = []
        plan[action].append(cls["email_id"])

        key = f"{action}/{category}"
        if key not in summary:
            summary[key] = {"action": action, "category": category, "count": 0}
        summary[key]["count"] += 1

    # ── Exibe tabela resumo ───────────────────────────────────────────────────
    table = Table(title="Resumo do Plano", show_header=True, header_style="bold magenta")
    table.add_column("Ação", style="bold")
    table.add_column("Categoria")
    table.add_column("Qtd", justify="right")
    table.add_column("Risco", justify="center")

    total = 0
    for key, info in sorted(summary.items()):
        action = info["action"]
        risk = "[red]ALTO[/red]" if action in DESTRUCTIVE_ACTIONS else "[green]baixo[/green]"
        table.add_row(action, info["category"], str(info["count"]), risk)
        total += info["count"]

    table.add_row("[bold]TOTAL[/bold]", "", f"[bold]{total}[/bold]", "")
    console.print(table)

    # ── Preview de exemplos por ação destrutiva ───────────────────────────────
    _show_preview(classifications, plan, preview_count)

    # ── Aviso de segurança ────────────────────────────────────────────────────
    if any(a in plan for a in DESTRUCTIVE_ACTIONS):
        console.print(
            Panel(
                "[red bold]ATENÇÃO:[/red bold] O plano contém ações destrutivas (delete/trash).\n"
                "Execute com [bold]--dry-run[/bold] primeiro para verificar, depois confirme com [bold]--confirm[/bold].\n"
                f"Safe mode está: [bold]{'ATIVADO' if settings.safe_mode else 'DESATIVADO'}[/bold]",
                title="Aviso de Segurança",
                border_style="red",
            )
        )

    return {
        "plan": plan,
        "summary": summary,
        "total": total,
        "classifications": classifications,
    }


def _show_preview(
    classifications: list[dict],
    plan: dict[str, list[str]],
    count: int,
) -> None:
    """Exibe preview de N exemplos para ações destrutivas."""
    id_set = set()
    for action in DESTRUCTIVE_ACTIONS:
        if action in plan:
            id_set.update(plan[action][:count])

    if not id_set:
        return

    examples = [c for c in classifications if c["email_id"] in id_set][:count]

    table = Table(
        title=f"Preview — {count} exemplos de ações destrutivas",
        show_header=True,
        header_style="bold red",
    )
    table.add_column("ID", style="dim", max_width=16)
    table.add_column("Ação", style="red")
    table.add_column("Categoria")
    table.add_column("Confiança", justify="right")
    table.add_column("Motivo", max_width=50)

    for ex in examples:
        conf = ex.get("confidence", 0)
        conf_style = "green" if conf >= 0.90 else "yellow" if conf >= 0.80 else "red"
        table.add_row(
            ex["email_id"][:16],
            ex["action"],
            ex["category"],
            f"[{conf_style}]{conf:.2f}[/{conf_style}]",
            ex.get("reason", "")[:60],
        )

    console.print(table)
