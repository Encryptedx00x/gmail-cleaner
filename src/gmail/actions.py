"""Ações sobre mensagens Gmail: delete, archive, mark_read, label."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.progress import Progress, SpinnerColumn, TextColumn, MofNCompleteColumn

from src.gmail.client import GmailClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

DELETED_LOG_PATH = Path("./reports/deleted_log.jsonl")


def _append_deleted_log(entry: dict) -> None:
    """Registra email deletado em log imutável JSONL."""
    DELETED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DELETED_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def archive_messages(
    client: GmailClient,
    message_ids: list[str],
    dry_run: bool = True,
) -> dict[str, int]:
    """
    Remove label INBOX das mensagens (arquiva).

    Args:
        client: GmailClient autenticado.
        message_ids: Lista de IDs para arquivar.
        dry_run: Se True, apenas simula sem executar.

    Returns:
        Dict com contadores: success, failed, skipped.
    """
    counters = {"success": 0, "failed": 0, "skipped": 0}
    action = "[DRY-RUN] Arquivar" if dry_run else "Arquivando"

    with Progress(
        SpinnerColumn(),
        TextColumn(f"[cyan]{action}[/cyan] {{task.description}}"),
        MofNCompleteColumn(),
    ) as progress:
        task = progress.add_task("emails...", total=len(message_ids))

        for msg_id in message_ids:
            progress.update(task, advance=1)
            if dry_run:
                counters["skipped"] += 1
                continue
            try:
                client.modify_message(msg_id, remove_label_ids=["INBOX"])
                counters["success"] += 1
            except Exception as exc:
                logger.warning(f"Falha ao arquivar {msg_id}: {exc}")
                counters["failed"] += 1

    return counters


def mark_as_read(
    client: GmailClient,
    message_ids: list[str],
    dry_run: bool = True,
) -> dict[str, int]:
    """
    Remove label UNREAD das mensagens.

    Args:
        client: GmailClient autenticado.
        message_ids: Lista de IDs.
        dry_run: Se True, apenas simula.

    Returns:
        Dict com contadores: success, failed, skipped.
    """
    counters = {"success": 0, "failed": 0, "skipped": 0}
    action = "[DRY-RUN] Marcar como lido" if dry_run else "Marcando como lido"

    with Progress(
        SpinnerColumn(),
        TextColumn(f"[cyan]{action}[/cyan] {{task.description}}"),
        MofNCompleteColumn(),
    ) as progress:
        task = progress.add_task("emails...", total=len(message_ids))

        for msg_id in message_ids:
            progress.update(task, advance=1)
            if dry_run:
                counters["skipped"] += 1
                continue
            try:
                client.modify_message(msg_id, remove_label_ids=["UNREAD"])
                counters["success"] += 1
            except Exception as exc:
                logger.warning(f"Falha ao marcar {msg_id}: {exc}")
                counters["failed"] += 1

    return counters


def label_messages(
    client: GmailClient,
    message_ids: list[str],
    label_name: str,
    dry_run: bool = True,
) -> dict[str, int]:
    """
    Aplica uma label a uma lista de mensagens, criando-a se necessário.

    Args:
        client: GmailClient autenticado.
        message_ids: Lista de IDs.
        label_name: Nome da label (cria se não existir).
        dry_run: Se True, apenas simula.

    Returns:
        Dict com contadores: success, failed, skipped.
    """
    counters = {"success": 0, "failed": 0, "skipped": 0}

    if dry_run:
        logger.info(
            f"[DRY-RUN] Aplicaria label '{label_name}' em {len(message_ids)} emails."
        )
        counters["skipped"] = len(message_ids)
        return counters

    label_id = client.get_or_create_label(label_name)

    with Progress(
        SpinnerColumn(),
        TextColumn(f"[cyan]Aplicando label '{label_name}'[/cyan] {{task.description}}"),
        MofNCompleteColumn(),
    ) as progress:
        task = progress.add_task("emails...", total=len(message_ids))

        for msg_id in message_ids:
            progress.update(task, advance=1)
            try:
                client.modify_message(msg_id, add_label_ids=[label_id])
                counters["success"] += 1
            except Exception as exc:
                logger.warning(f"Falha ao labelar {msg_id}: {exc}")
                counters["failed"] += 1

    return counters


def trash_messages(
    client: GmailClient,
    message_ids: list[str],
    email_metas: list[dict],
    dry_run: bool = True,
    require_confirm: bool = True,
) -> dict[str, int]:
    """
    Move mensagens para lixeira (reversível).

    Requer confirmação interativa se require_confirm=True e não dry_run.

    Args:
        client: GmailClient autenticado.
        message_ids: Lista de IDs.
        email_metas: Metadados dos emails (para log).
        dry_run: Se True, apenas simula.
        require_confirm: Exige digitação de "DELETE" para confirmar.

    Returns:
        Dict com contadores: success, failed, skipped.
    """
    counters = {"success": 0, "failed": 0, "skipped": 0}
    if dry_run:
        logger.info(f"[DRY-RUN] Moveria {len(message_ids)} emails para lixeira.")
        counters["skipped"] = len(message_ids)
        return counters

    if require_confirm:
        print(f"\n[AVISO] Você está prestes a mover {len(message_ids)} emails para a LIXEIRA.")
        print("Esta ação é reversível (lixeira), mas requer confirmação.")
        confirmation = input("Digite DELETE para confirmar: ").strip()
        if confirmation != "DELETE":
            logger.warning("Operação cancelada pelo usuário.")
            counters["skipped"] = len(message_ids)
            return counters

    meta_index = {m["id"]: m for m in email_metas}

    with Progress(
        SpinnerColumn(),
        TextColumn("[red]Movendo para lixeira[/red] {task.description}"),
        MofNCompleteColumn(),
    ) as progress:
        task = progress.add_task("emails...", total=len(message_ids))

        for msg_id in message_ids:
            progress.update(task, advance=1)
            try:
                client.trash_message(msg_id)
                counters["success"] += 1

                # Log imutável
                meta = meta_index.get(msg_id, {"id": msg_id})
                _append_deleted_log(
                    {
                        "action": "trash",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "id": msg_id,
                        "from": meta.get("from", ""),
                        "subject": meta.get("subject", ""),
                        "date": meta.get("date", ""),
                    }
                )
            except Exception as exc:
                logger.warning(f"Falha ao mover {msg_id} para lixeira: {exc}")
                counters["failed"] += 1

    logger.info(
        f"Lixeira: {counters['success']} movidos | {counters['failed']} falhas"
    )
    return counters


def extract_unsubscribe_links(emails: list[dict]) -> list[dict]:
    """
    Extrai links de unsubscribe de emails com header List-Unsubscribe.

    Args:
        emails: Lista de dicts normalizados pelo fetcher.

    Returns:
        Lista de dicts {id, from, subject, unsubscribe_link}.
    """
    results = []
    for email in emails:
        link = email.get("unsubscribe_link", "")
        if link:
            # Limpa formato "<https://...>, <mailto:...>"
            parts = [p.strip().strip("<>") for p in link.split(",")]
            http_link = next((p for p in parts if p.startswith("http")), parts[0])
            results.append(
                {
                    "id": email["id"],
                    "from": email["from"],
                    "subject": email["subject"],
                    "unsubscribe_link": http_link,
                }
            )
    return results
