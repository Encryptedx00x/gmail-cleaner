"""Executor: aplica ações aprovadas após confirmação manual."""
from typing import Optional

from rich.panel import Panel

from src.auth.oauth import get_gmail_service
from src.classifier.cache import EmailCache
from src.gmail.actions import (
    archive_messages,
    label_messages,
    mark_as_read,
    trash_messages,
)
from src.gmail.client import GmailClient
from src.pipeline.staging import PROTECTED_CATEGORIES, generate_staging_plan
from src.utils.config import get_settings
from src.utils.logger import get_logger, console

logger = get_logger(__name__)

# Mapa de categoria para nome da label
CATEGORY_LABEL_MAP: dict[str, str] = {
    "certificate": "Certificados",
    "course_access": "Cursos",
    "newsletter": "Newsletters",
    "transactional": "Transacionais",
    "marketing": "Marketing",
    "spam": "Spam",
}


def execute_plan(
    category: Optional[str] = None,
    action: Optional[str] = None,
    dry_run: bool = True,
    confirm: bool = False,
    limit: Optional[int] = None,
) -> dict:
    """
    Executa o plano de ação gerado pelo staging.

    NUNCA executa deleções sem confirmação explícita ou confirm=True.
    Sempre exibe staging antes de executar.

    Args:
        category: Filtrar execução por categoria.
        action: Filtrar por tipo de ação ('delete', 'archive', etc.).
        dry_run: Se True, simula sem executar.
        confirm: Se True, pula prompt interativo (para automação).
        limit: Limitar número de emails processados.

    Returns:
        Dict com contadores: success, failed, skipped por ação.
    """
    settings = get_settings()
    cache = EmailCache()

    mode = "[DRY-RUN]" if dry_run else "[LIVE]"
    console.rule(f"[bold {'yellow' if dry_run else 'red'}]EXECUTE {mode}[/bold {'yellow' if dry_run else 'red'}]")

    # Gera plano
    staging = generate_staging_plan(
        action_filter=action,
        category_filter=category,
        safe_only=True,
    )

    plan = staging.get("plan", {})
    classifications = staging.get("classifications", [])

    if not plan:
        console.print("[yellow]Nenhum email no plano. Execute 'scan' primeiro.[/yellow]")
        return {}

    # Confirmação para ações não-dry-run
    if not dry_run and not confirm:
        total = staging.get("total", 0)
        console.print(
            Panel(
                f"[bold]Total de emails a processar:[/bold] {total}\n"
                f"[bold]Categoria:[/bold] {category or 'todas'}\n"
                f"[bold]Ação:[/bold] {action or 'todas'}\n\n"
                "Para confirmar, digite [bold]EXECUTE[/bold]:",
                title="Confirmação necessária",
                border_style="yellow",
            )
        )
        user_input = input(">>> ").strip()
        if user_input != "EXECUTE":
            logger.warning("Execução cancelada pelo usuário.")
            return {"cancelled": True}

    # Autentica Gmail (apenas se não for dry-run)
    client: Optional[GmailClient] = None
    if not dry_run:
        service = get_gmail_service()
        client = GmailClient(service)

    # Monta índice de metadados para o log
    cls_index = {c["email_id"]: c for c in classifications}

    results: dict[str, dict] = {}

    # ── Processa cada ação ────────────────────────────────────────────────────

    for current_action, email_ids in plan.items():
        if limit:
            email_ids = email_ids[:limit]

        if not email_ids:
            continue

        logger.info(f"Processando ação '{current_action}': {len(email_ids)} emails")
        metas = [{"id": eid, **cls_index.get(eid, {})} for eid in email_ids]

        if current_action in ("delete", "trash"):
            if dry_run:
                console.print(
                    f"[DRY-RUN] Moveria {len(email_ids)} emails para lixeira (trash)."
                )
                results[current_action] = {
                    "success": 0, "failed": 0, "skipped": len(email_ids)
                }
            else:
                counters = trash_messages(
                    client,  # type: ignore
                    email_ids,
                    metas,
                    dry_run=False,
                    require_confirm=(not confirm),
                )
                results[current_action] = counters

        elif current_action == "archive":
            counters = archive_messages(
                client,  # type: ignore
                email_ids,
                dry_run=dry_run,
            )
            results[current_action] = counters

        elif current_action == "label":
            # Agrupa por categoria para usar label correta
            by_category: dict[str, list[str]] = {}
            for eid in email_ids:
                cat = cls_index.get(eid, {}).get("category", "outros")
                by_category.setdefault(cat, []).append(eid)

            results[current_action] = {"success": 0, "failed": 0, "skipped": 0}
            for cat, cat_ids in by_category.items():
                label_name = CATEGORY_LABEL_MAP.get(cat, cat.capitalize())
                counters = label_messages(
                    client,  # type: ignore
                    cat_ids,
                    label_name,
                    dry_run=dry_run,
                )
                for k in results[current_action]:
                    results[current_action][k] += counters.get(k, 0)

        elif current_action == "keep":
            logger.info(f"'keep': {len(email_ids)} emails preservados (sem ação).")
            results[current_action] = {
                "success": len(email_ids), "failed": 0, "skipped": 0
            }

        elif current_action == "review":
            if not dry_run:
                # Aplica label "Revisar"
                counters = label_messages(
                    client,  # type: ignore
                    email_ids,
                    "Revisar",
                    dry_run=dry_run,
                )
                results[current_action] = counters
            else:
                results[current_action] = {
                    "success": 0, "failed": 0, "skipped": len(email_ids)
                }

    # ── Resumo final ──────────────────────────────────────────────────────────
    console.rule(f"[bold green]Execução {mode} concluída[/bold green]")
    for act, counters in results.items():
        console.print(
            f"  [bold]{act}[/bold]: "
            f"{counters.get('success', 0)} ok | "
            f"{counters.get('failed', 0)} falhas | "
            f"{counters.get('skipped', 0)} pulados"
        )

    return results
