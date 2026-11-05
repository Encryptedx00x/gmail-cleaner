"""Gmail Cleaner — CLI principal."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table
from rich.panel import Panel

from src.utils.logger import console, get_logger

logger = get_logger(__name__)
app = typer.Typer(
    name="gmail-cleaner",
    help="Organiza sua caixa do Gmail com heurísticas + OpenAI API.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

show_app = typer.Typer(help="Exibe emails por categoria ou prioridade.")
app.add_typer(show_app, name="show")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _check_env() -> None:
    """Verifica variáveis obrigatórias antes de qualquer comando."""
    from dotenv import load_dotenv
    load_dotenv()
    import os
    missing = []
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if not Path(os.getenv("GMAIL_CREDENTIALS_PATH", "./credentials/credentials.json")).exists():
        missing.append("GMAIL_CREDENTIALS_PATH (arquivo não encontrado)")
    if missing:
        console.print(
            Panel(
                f"[red]Variáveis obrigatórias ausentes:[/red]\n" +
                "\n".join(f"  • {m}" for m in missing) +
                "\n\nCopie [bold].env.example[/bold] para [bold].env[/bold] e preencha.",
                title="Erro de configuração",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)


# ── Comandos ──────────────────────────────────────────────────────────────────

@app.command()
def scan(
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limite de emails a escanear"),
    query: str = typer.Option("", "--query", "-q", help="Filtro Gmail (ex: 'is:unread in:inbox')"),
    batch_size: Optional[int] = typer.Option(None, "--batch-size", "-b", help="Emails por lote OpenAI"),
    skip_claude: bool = typer.Option(False, "--skip-claude", help="Pular classificação OpenAI (só heurísticas)"),
) -> None:
    """Escaneia a caixa do Gmail e classifica todos os emails."""
    _check_env()
    from src.pipeline.scan import run_scan
    run_scan(limit=limit, query=query, batch_size=batch_size, skip_claude=skip_claude)


@app.command()
def classify(
    batch_size: int = typer.Option(30, "--batch-size", "-b", help="Emails por lote"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Máximo de emails a classificar"),
) -> None:
    """Classifica emails ainda não classificados (incrementa sobre scan existente)."""
    _check_env()
    from src.classifier.cache import EmailCache
    from src.classifier.claude_classifier import classify_all
    from src.classifier.heuristics import quick_classify

    cache = EmailCache()

    # Busca emails sem classificação
    with cache._connect() as conn:
        # Emails no cache com category=unknown ou sem classificação ainda
        unknown = conn.execute(
            "SELECT email_id FROM email_cache WHERE category = 'unknown' LIMIT ?",
            (limit or 10000,),
        ).fetchall()

    ids = [r["email_id"] for r in unknown]

    if not ids:
        console.print("[green]Todos os emails já estão classificados.[/green]")
        return

    console.print(f"[cyan]Reclassificando {len(ids)} emails 'unknown'...[/cyan]")

    # Re-usa scan para só reprocessar esses IDs
    from src.auth.oauth import get_gmail_service
    from src.gmail.client import GmailClient
    service = get_gmail_service()
    client = GmailClient(service)

    emails = client.batch_get_messages(ids[:limit or len(ids)])
    from src.gmail.fetcher import parse_message
    parsed = [parse_message(e) for e in emails]

    results = classify_all(parsed, batch_size=batch_size)
    saved = cache.save_emails_batch(results)
    console.print(f"[green]Classificados e salvos: {saved}[/green]")


@app.command()
def staging(
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Filtrar por categoria"),
    action: Optional[str] = typer.Option(None, "--action", "-a", help="Filtrar por ação"),
    preview: int = typer.Option(10, "--preview", "-p", help="Número de exemplos a exibir"),
    include_unsafe: bool = typer.Option(False, "--include-unsafe", help="Incluir emails com safe_to_automate=False"),
) -> None:
    """Exibe plano de ação detalhado (dry-run obrigatório antes de execute)."""
    from src.pipeline.staging import generate_staging_plan
    generate_staging_plan(
        action_filter=action,
        category_filter=category,
        safe_only=not include_unsafe,
        preview_count=preview,
    )


@app.command()
def execute(
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Executar só esta categoria"),
    action: Optional[str] = typer.Option(None, "--action", "-a", help="Executar só esta ação"),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Simular (padrão) ou executar alterações"),
    confirm: bool = typer.Option(False, "--confirm", help="Confirmar execução sem prompt interativo"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Máximo de emails"),
) -> None:
    """Executa as ações planejadas no staging."""
    _check_env()

    if not dry_run and not confirm:
        console.print(
            "[yellow]Você está prestes a executar ações na sua caixa de email.[/yellow]\n"
            "Use [bold]--dry-run[/bold] para simular primeiro.\n"
            "Use [bold]--confirm[/bold] para confirmar execução real."
        )
        confirmed = typer.confirm("Deseja continuar?")
        if not confirmed:
            raise typer.Exit()

    from src.pipeline.executor import execute_plan
    execute_plan(
        category=category,
        action=action,
        dry_run=dry_run,
        confirm=confirm,
        limit=limit,
    )


@app.command(name="delete-sender")
def delete_sender(
    sender_email: str = typer.Argument(..., help="Endereço de email do remetente"),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Simular (padrão) ou mover para lixeira"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l"),
) -> None:
    """Move para a lixeira mensagens de um remetente (simulação por padrão)."""
    _check_env()
    from src.classifier.cache import EmailCache
    from src.auth.oauth import get_gmail_service
    from src.gmail.client import GmailClient
    from src.gmail.actions import trash_messages
    from src.gmail.fetcher import fetch_all_messages, parse_message

    cache = EmailCache()
    service = get_gmail_service()
    client = GmailClient(service)

    query = f"from:{sender_email}"
    console.print(f"Buscando emails de: [bold]{sender_email}[/bold]")
    emails = list(fetch_all_messages(client, query=query, limit=limit))

    if not emails:
        console.print("[yellow]Nenhum email encontrado.[/yellow]")
        return

    console.print(f"Encontrados: [bold]{len(emails)}[/bold] emails")

    ids = [e["id"] for e in emails]
    mode = "[DRY-RUN]" if dry_run else ""
    console.print(f"{mode} Mover {len(ids)} emails para lixeira...")

    if not dry_run:
        counters = trash_messages(client, ids, emails, dry_run=False, require_confirm=True)
        console.print(f"Resultado: {counters}")
    else:
        console.print(f"[DRY-RUN] Moveria {len(ids)} emails para lixeira.")


@app.command(name="mark-read")
def mark_read(
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Filtrar por categoria"),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Simular (padrão) ou marcar como lido"),
) -> None:
    """Marca emails como lidos por categoria."""
    _check_env()
    from src.classifier.cache import EmailCache
    from src.auth.oauth import get_gmail_service
    from src.gmail.client import GmailClient
    from src.gmail.actions import mark_as_read

    cache = EmailCache()

    with cache._connect() as conn:
        query = "SELECT email_id FROM email_cache WHERE 1=1"
        params: list = []
        if category:
            query += " AND category = ?"
            params.append(category)
        rows = conn.execute(query, params).fetchall()

    ids = [r["email_id"] for r in rows]
    console.print(f"Marcando {len(ids)} emails como lidos...")

    if ids:
        if not dry_run and not typer.confirm(
            f"Marcar {len(ids)} emails como lidos no Gmail?", default=False
        ):
            console.print("Operação cancelada.")
            return
        service = get_gmail_service()
        client = GmailClient(service)
        counters = mark_as_read(client, ids, dry_run=dry_run)
        console.print(f"Resultado: {counters}")


@app.command()
def report(
    output_format: str = typer.Option("html", "--format", "-f", help="Formato: html, json"),
    output_path: Optional[Path] = typer.Option(None, "--output", "-o", help="Caminho de saída"),
) -> None:
    """Gera relatório de classificações em HTML ou JSON."""
    from src.classifier.cache import EmailCache

    cache = EmailCache()
    stats = cache.get_stats()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    reports_dir = Path("./reports")
    reports_dir.mkdir(exist_ok=True)

    if output_format == "json":
        out = output_path or reports_dir / f"report_{timestamp}.json"
        out.write_text(json.dumps(stats, indent=2, ensure_ascii=False))
        console.print(f"Relatório JSON salvo em: [bold]{out}[/bold]")

    else:  # html
        out = output_path or reports_dir / f"report_{timestamp}.html"
        html = _generate_html_report(stats, timestamp)
        out.write_text(html, encoding="utf-8")
        console.print(f"Relatório HTML salvo em: [bold]{out}[/bold]")


@app.command()
def unsubscribe(
    dry_run: bool = typer.Option(True, "--dry-run", help="Listar links sem abrir (padrão)"),
    limit: int = typer.Option(50, "--limit", "-l", help="Máximo de links"),
) -> None:
    """Detecta e lista links de unsubscribe de newsletters."""
    _check_env()
    from src.classifier.cache import EmailCache
    from src.auth.oauth import get_gmail_service
    from src.gmail.client import GmailClient
    from src.gmail.actions import extract_unsubscribe_links

    cache = EmailCache()
    service = get_gmail_service()
    client = GmailClient(service)

    with cache._connect() as conn:
        rows = conn.execute(
            "SELECT email_id FROM email_cache WHERE has_unsubscribe = 1 LIMIT ?",
            (limit,),
        ).fetchall()

    ids = [r["email_id"] for r in rows]
    if not ids:
        console.print("[yellow]Nenhum email com unsubscribe detectado.[/yellow]")
        return

    raw_emails = client.batch_get_messages(ids)
    from src.gmail.fetcher import parse_message
    parsed = [parse_message(e) for e in raw_emails]
    links = extract_unsubscribe_links(parsed)

    table = Table(title=f"Links de Unsubscribe ({len(links)})")
    table.add_column("Remetente", max_width=40)
    table.add_column("Link", max_width=60)

    for item in links:
        table.add_row(item["from"][:40], item["unsubscribe_link"][:60])

    console.print(table)

    if not dry_run:
        console.print("[yellow]Abertura automática de links não implementada por segurança.[/yellow]")
        console.print("Acesse os links manualmente na tabela acima.")


# ── show subcommands ──────────────────────────────────────────────────────────

@show_app.command(name="priority")
def show_priority() -> None:
    """Lista emails que exigem ação manual (action_required, review)."""
    from src.classifier.cache import EmailCache

    cache = EmailCache()
    with cache._connect() as conn:
        rows = conn.execute(
            """SELECT * FROM email_cache
               WHERE action IN ('review', 'keep')
               AND category IN ('action_required', 'unknown', 'important')
               ORDER BY classified_at DESC
               LIMIT 50""",
        ).fetchall()

    items = [dict(r) for r in rows]
    if not items:
        console.print("[green]Nenhum email prioritário pendente.[/green]")
        return

    table = Table(title=f"Emails Prioritários ({len(items)})")
    table.add_column("ID", max_width=16)
    table.add_column("Categoria")
    table.add_column("Confiança", justify="right")
    table.add_column("Motivo", max_width=60)

    for item in items:
        table.add_row(
            item["email_id"][:16],
            item["category"],
            f"{item['confidence']:.2f}",
            item.get("reason", "")[:60],
        )
    console.print(table)


@show_app.command(name="category")
def show_category(
    category_name: str = typer.Argument(..., help="Nome da categoria"),
    limit: int = typer.Option(20, "--limit", "-l"),
) -> None:
    """Lista emails de uma categoria específica."""
    from src.classifier.cache import EmailCache

    cache = EmailCache()
    items = cache.get_by_category(category_name)[:limit]

    if not items:
        console.print(f"[yellow]Nenhum email na categoria '{category_name}'.[/yellow]")
        return

    table = Table(title=f"Categoria: {category_name} ({len(items)} emails)")
    table.add_column("ID", max_width=16)
    table.add_column("Ação")
    table.add_column("Confiança", justify="right")
    table.add_column("Auto?", justify="center")
    table.add_column("Fonte")

    for item in items:
        table.add_row(
            item["email_id"][:16],
            item["action"],
            f"{item['confidence']:.2f}",
            "✓" if item["safe_to_automate"] else "✗",
            item.get("source", ""),
        )
    console.print(table)


# ── Gerador de HTML ───────────────────────────────────────────────────────────

def _generate_html_report(stats: dict, timestamp: str) -> str:
    """Gera HTML simples para o relatório."""
    rows_category = "".join(
        f"<tr><td>{r['category']}</td><td>{r['cnt']}</td></tr>"
        for r in stats.get("by_category", [])
    )
    rows_action = "".join(
        f"<tr><td>{r['action']}</td><td>{r['cnt']}</td></tr>"
        for r in stats.get("by_action", [])
    )
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>Gmail Cleaner — Relatório {timestamp}</title>
  <style>
    body {{ font-family: sans-serif; max-width: 900px; margin: 2rem auto; color: #333; }}
    h1 {{ color: #1a73e8; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
    th {{ background: #f8f9fa; }}
    tr:hover {{ background: #f1f3f4; }}
    .stat {{ font-size: 2rem; font-weight: bold; color: #1a73e8; }}
  </style>
</head>
<body>
  <h1>Gmail Cleaner — Relatório</h1>
  <p>Gerado em: {timestamp}</p>
  <p>Total de emails no cache: <span class="stat">{stats.get("total_emails", 0):,}</span></p>
  <p>Remetentes únicos: <span class="stat">{stats.get("total_senders", 0):,}</span></p>

  <h2>Por Categoria</h2>
  <table>
    <tr><th>Categoria</th><th>Quantidade</th></tr>
    {rows_category}
  </table>

  <h2>Por Ação Recomendada</h2>
  <table>
    <tr><th>Ação</th><th>Quantidade</th></tr>
    {rows_action}
  </table>
</body>
</html>"""


if __name__ == "__main__":
    app()
