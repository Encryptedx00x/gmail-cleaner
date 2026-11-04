"""Paginação completa de mensagens Gmail com progress bar."""
from typing import Generator, Optional

from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    MofNCompleteColumn,
)

from src.gmail.client import GmailClient
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _extract_header(headers: list[dict], name: str) -> str:
    """Extrai valor de um header por nome (case-insensitive)."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def parse_message(raw: dict) -> dict:
    """
    Normaliza uma mensagem bruta da Gmail API para dict padronizado.

    Args:
        raw: Resposta crua do messages.get().

    Returns:
        Dict com campos: id, thread_id, from, subject, date, snippet,
        label_ids, unsubscribe_link.
    """
    headers = raw.get("payload", {}).get("headers", [])
    return {
        "id": raw.get("id", ""),
        "thread_id": raw.get("threadId", ""),
        "from": _extract_header(headers, "From"),
        "subject": _extract_header(headers, "Subject"),
        "date": _extract_header(headers, "Date"),
        "snippet": raw.get("snippet", ""),
        "label_ids": raw.get("labelIds", []),
        "unsubscribe_link": _extract_header(headers, "List-Unsubscribe"),
        "size_estimate": raw.get("sizeEstimate", 0),
    }


def fetch_all_messages(
    client: GmailClient,
    query: str = "",
    label_ids: Optional[list[str]] = None,
    limit: Optional[int] = None,
    chunk_size: int = 100,
) -> Generator[dict, None, None]:
    """
    Gerador que percorre toda a caixa de entrada com paginação.

    Busca IDs em páginas de chunk_size, depois resolve metadados de cada
    mensagem individualmente com throttling.

    Args:
        client: Instância do GmailClient autenticado.
        query: Filtro de busca Gmail (ex: "is:unread", "from:noreply@").
        label_ids: Lista de labels para filtrar (ex: ["INBOX"]).
        limit: Limite total de mensagens (None = todas).
        chunk_size: Tamanho da página de IDs (máx 500).

    Yields:
        Dicts normalizados por parse_message().
    """
    fetched = 0
    page_token: Optional[str] = None

    # Primeiro: conta total aproximado (apenas para progress bar)
    total_estimate = _estimate_total(client, query, label_ids)
    max_fetch = min(limit, total_estimate) if limit else total_estimate
    logger.info(f"Total estimado de mensagens: {total_estimate:,}")
    if limit:
        logger.info(f"Limite definido: {limit:,}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task("Buscando emails...", total=max_fetch)

        while True:
            if limit and fetched >= limit:
                break

            remaining = (limit - fetched) if limit else chunk_size
            page_size = min(chunk_size, remaining)

            # Busca página de IDs
            response = client.list_messages(
                query=query,
                label_ids=label_ids,
                page_token=page_token,
                max_results=page_size,
            )

            message_refs = response.get("messages", [])
            if not message_refs:
                break

            ids = [m["id"] for m in message_refs]

            # Resolve metadados de cada ID
            for msg_id in ids:
                if limit and fetched >= limit:
                    break
                try:
                    raw = client.get_message(msg_id, format="metadata")
                    yield parse_message(raw)
                    fetched += 1
                    progress.update(task, advance=1)
                except Exception as exc:
                    logger.warning(f"Erro ao buscar msg {msg_id}: {exc}")

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    logger.info(f"Total de mensagens carregadas: {fetched:,}")


def _estimate_total(
    client: GmailClient,
    query: str,
    label_ids: Optional[list[str]],
) -> int:
    """Estima total de mensagens via profile ou primeira página."""
    try:
        if not query and not label_ids:
            profile = client.get_profile()
            return profile.get("messagesTotal", 0)
        # Para queries, usa primeira página para estimar
        resp = client.list_messages(query=query, label_ids=label_ids, max_results=1)
        return resp.get("resultSizeEstimate", 0)
    except Exception:
        return 0
