"""Pipeline de scan: orquestra fetch + classificação em camadas."""
from typing import Optional

from src.auth.oauth import get_gmail_service
from src.classifier.cache import EmailCache
from src.classifier.claude_classifier import classify_all
from src.classifier.heuristics import quick_classify
from src.classifier.policy import normalize_classification, PROTECTED_CATEGORIES
from src.gmail.client import GmailClient
from src.gmail.fetcher import fetch_all_messages
from src.utils.config import get_settings
from src.utils.logger import get_logger, console

logger = get_logger(__name__)


def run_scan(
    limit: Optional[int] = None,
    query: str = "",
    batch_size: Optional[int] = None,
    skip_claude: bool = False,
    reclassify_unknowns: bool = False,
    reclassify_categories: Optional[list] = None,
) -> dict:
    """
    Executa o scan completo da caixa de entrada.

    Fluxo:
    1. Autentica Gmail
    2. Busca emails com paginação
    3. Verifica cache por email_id e sender
    4. Aplica heurísticas locais (rápido, sem API)
    5. Envia ambíguos para Claude em lotes
    6. Salva tudo no cache SQLite

    Args:
        limit: Limitar a N emails (None = todos).
        query: Filtro Gmail (ex: "is:unread", "in:inbox").
        batch_size: Tamanho do lote Claude (default: MAX_BATCH_SIZE).
        skip_claude: Se True, pula Claude (apenas heurísticas).

    Returns:
        Dict com estatísticas: total, cached, heuristic, claude, ambiguous.
    """
    settings = get_settings()
    cache = EmailCache()

    console.rule("[bold cyan]SCAN — Gmail Cleaner[/bold cyan]")

    # ── 1. Autenticação ───────────────────────────────────────────────────────
    logger.info("Autenticando Gmail...")
    service = get_gmail_service()
    client = GmailClient(service)

    profile = client.get_profile()
    email_addr = profile.get("emailAddress", "desconhecido")
    cache.ensure_account(email_addr, bind_empty=True)
    total_msgs = profile.get("messagesTotal", 0)
    console.print(f"  Conta: [bold]{email_addr}[/bold]")
    console.print(f"  Total na caixa: [bold]{total_msgs:,}[/bold] mensagens")
    if limit:
        console.print(f"  Limite do scan: [bold]{limit:,}[/bold]")

    # ── 2. Fetch com paginação ────────────────────────────────────────────────
    logger.info("Iniciando fetch de mensagens...")
    all_emails: list[dict] = list(
        fetch_all_messages(client, query=query, limit=limit)
    )
    logger.info(f"Fetch concluído: {len(all_emails):,} emails carregados")

    if not all_emails:
        console.print("[yellow]Nenhum email encontrado.[/yellow]")
        return _empty_stats()

    # ── 3. Separar já classificados (email_cache + sender_cache) ─────────────
    all_ids = [e["id"] for e in all_emails]
    unclassified_ids = set(cache.get_unclassified_ids(all_ids))
    local_results = {email['id']: quick_classify(email) for email in all_emails}
    unclassified_ids.update(email_id for email_id, local in local_results.items()
                            if local and local['category'] in PROTECTED_CATEGORIES | {'certificate', 'course_access'})

    # Re-classificar unknowns: remove-os do cache para forçar re-envio ao Claude
    if reclassify_unknowns and not skip_claude:
        unknown_ids = set(cache.get_ids_by_category("unknown"))
        to_reclassify = unknown_ids & (set(all_ids) - unclassified_ids)
        if to_reclassify:
            cache.delete_by_ids(list(to_reclassify))
            unclassified_ids |= to_reclassify
            logger.info(f"Re-classificando {len(to_reclassify):,} unknowns com Claude")

    # Re-classificar categorias específicas (ex: invoice que eram declarações de participação)
    for cat in (reclassify_categories or []):
        cat_ids = set(cache.get_ids_by_category(cat))
        to_reclassify = cat_ids & (set(all_ids) - unclassified_ids)
        if to_reclassify:
            cache.delete_by_ids(list(to_reclassify))
            unclassified_ids |= to_reclassify
            logger.info(f"Re-classificando {len(to_reclassify):,} emails de '{cat}'")

    already_cached = len(all_ids) - len(unclassified_ids)
    logger.info(f"Já em cache: {already_cached:,} | A classificar: {len(unclassified_ids):,}")

    to_classify = [e for e in all_emails if e["id"] in unclassified_ids]

    # ── 4. Heurísticas locais ─────────────────────────────────────────────────
    heuristic_results: list[dict] = []
    needs_claude: list[dict] = []
    sender_cache_hits = 0

    for email in to_classify:
        result = local_results[email['id']]
        if result:
            heuristic_results.append({**result, 'email_id': email['id'], 'source': 'heuristic'})
            continue
        # 4a. Sender cache (se remetente já classificado 3+ vezes)
        sender_hit = cache.get_sender_classification(email["from"])
        if sender_hit:
            heuristic_results.append({
                "email_id": email["id"],
                "category": sender_hit["category"],
                "confidence": sender_hit["confidence"],
                "recommended_action": 'review',
                "safe_to_automate": False,
                "reason": f"Cache de remetente ({sender_hit['count']} classificações anteriores)",
                "has_unsubscribe": bool(email.get("unsubscribe_link")),
                "source": "sender_cache",
            })
            sender_cache_hits += 1
            continue

        # 4b. Heurísticas locais
        needs_claude.append(email)

    logger.info(
        f"Heurísticas: {len(heuristic_results):,} classificados "
        f"({sender_cache_hits} sender cache) | "
        f"Ambíguos para Claude: {len(needs_claude):,}"
    )

    # ── 5. Claude API para ambíguos ───────────────────────────────────────────
    claude_results: list[dict] = []
    if needs_claude and not skip_claude:
        logger.info(f"Enviando {len(needs_claude):,} emails para Claude...")
        claude_raw = classify_all(needs_claude, batch_size=batch_size)
        for r in claude_raw:
            r["source"] = "claude"
        claude_results = claude_raw
    elif needs_claude and skip_claude:
        logger.warning(
            f"skip_claude=True: {len(needs_claude)} emails marcados como 'unknown'"
        )
        for email in needs_claude:
            claude_results.append({
                "email_id": email["id"],
                "category": "unknown",
                "confidence": 0.0,
                "recommended_action": "review",
                "safe_to_automate": False,
                "reason": "Classificação pulada (--skip-claude)",
                "has_unsubscribe": bool(email.get("unsubscribe_link")),
                "source": "skipped",
            })

    # ── 6. Persistir no cache ─────────────────────────────────────────────────
    all_new_classifications = heuristic_results + claude_results
    email_index = {email['id']: email for email in to_classify}
    all_new_classifications = [normalize_classification(row, email_index.get(row['email_id']), settings.confidence_threshold) for row in all_new_classifications]
    if all_new_classifications:
        saved = cache.save_emails_batch(all_new_classifications)
        logger.info(f"Cache: {saved:,} novas classificações salvas")

        # Atualiza sender cache com novos dados
        email_index = {e["id"]: e for e in to_classify}
        for cls in all_new_classifications:
            email = email_index.get(cls["email_id"])
            if email and cls["confidence"] > 0 and cls.get('source') != 'sender_cache':
                cache.update_sender(
                    email["from"], cls["category"], cls["confidence"]
                )

    # ── 7. Resumo ─────────────────────────────────────────────────────────────
    stats = cache.get_stats()
    console.rule("[bold green]Scan concluído[/bold green]")
    console.print(f"  Total processados: [bold]{len(all_emails):,}[/bold]")
    console.print(f"  Já em cache: [bold]{already_cached:,}[/bold]")
    console.print(f"  Heurísticas: [bold]{len(heuristic_results):,}[/bold]")
    console.print(f"  Claude API: [bold]{len(claude_results):,}[/bold]")
    console.print("\n  [bold]Por categoria:[/bold]")
    for row in stats["by_category"]:
        console.print(f"    {row['category']:<25} {row['cnt']:>6,}")

    return {
        "total": len(all_emails),
        "already_cached": already_cached,
        "heuristic": len(heuristic_results),
        "claude": len(claude_results),
        "ambiguous": len(needs_claude),
        "stats": stats,
    }


def _default_action(category: str) -> str:
    """Retorna ação padrão para uma categoria."""
    defaults = {
        "marketing": "delete",
        "spam": "delete",
        "newsletter": "archive",
        "transactional": "archive",
        "certificate": "label",
        "course_access": "label",
        "important": "keep",
        "invoice": "keep",
        "platform_credential": "keep",
        "action_required": "keep",
        "unknown": "review",
    }
    return defaults.get(category, "review")


def _empty_stats() -> dict:
    return {
        "total": 0,
        "already_cached": 0,
        "heuristic": 0,
        "claude": 0,
        "ambiguous": 0,
        "stats": {},
    }
