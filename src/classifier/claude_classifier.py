"""Classificação de emails via OpenAI API usando function calling (tool_use)."""
import json
import time
from typing import Optional

from openai import OpenAI

from src.utils.config import get_settings
from src.utils.logger import get_logger
from src.classifier.policy import normalize_classification

logger = get_logger(__name__)

# ── Tool definition (OpenAI function calling format) ─────────────────────────

CLASSIFY_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "classify_email",
        "description": (
            "Classifica um email e define a ação recomendada. "
            "Use para cada email individualmente dentro de um lote."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "email_index": {
                    "type": "integer",
                    "description": "Índice do email no lote (começa em 1)",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "marketing",
                        "spam",
                        "important",
                        "certificate",
                        "invoice",
                        "course_access",
                        "platform_credential",
                        "action_required",
                        "newsletter",
                        "transactional",
                        "unknown",
                    ],
                    "description": "Categoria do email",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confiança da classificação de 0.0 a 1.0",
                },
                "recommended_action": {
                    "type": "string",
                    "enum": ["delete", "archive", "label", "keep", "review"],
                    "description": "Ação recomendada para o email",
                },
                "safe_to_automate": {
                    "type": "boolean",
                    "description": (
                        "Se é seguro executar a ação automaticamente sem revisão humana. "
                        "False para emails com documentos importantes, senhas, cobranças."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Explicação concisa da classificação (1-2 frases)",
                },
                "has_unsubscribe": {
                    "type": "boolean",
                    "description": "Se o email parece ter opção de descadastro",
                },
            },
            "required": [
                "email_index",
                "category",
                "confidence",
                "recommended_action",
                "safe_to_automate",
                "reason",
                "has_unsubscribe",
            ],
        },
    },
}

SYSTEM_PROMPT = """Você é um assistente especializado em classificar emails em português e inglês.

Regras ABSOLUTAS (nunca viole):
- Emails com nota fiscal, NF-e, boleto, contrato, certificado digital, CPF, CNPJ → categoria "invoice" ou "important", action "keep", safe_to_automate=false
- Emails de redefinição de senha, 2FA, código de acesso → "platform_credential", action "keep", safe_to_automate=false
- Remetentes governamentais (.gov.br, receita federal) → "important", action "keep", safe_to_automate=false
- Quando em dúvida, prefira "keep" com safe_to_automate=false

Para marketing/newsletter: confidence >= 0.85 apenas se o padrão for óbvio (noreply de loja, promoção clara).
Para spam: confidence >= 0.90 apenas se for claramente malicioso ou phishing.

Classifique TODOS os emails numerados no lote, um por um, chamando a função classify_email para cada um."""


def classify_batch(
    emails: list[dict],
    client: Optional[OpenAI] = None,
) -> list[dict]:
    """
    Classifica um lote de emails via OpenAI API usando function calling.

    Processa até 30 emails por chamada. Cada email gera uma chamada
    à função classify_email. Emails sem resposta recebem classificação
    padrão "unknown" com confidence 0.0.

    Args:
        emails: Lista de dicts com campos id, from, subject, snippet.
                Máximo recomendado: 30 emails por chamada.
        client: Instância OpenAI (usa OPENAI_API_KEY se None).

    Returns:
        Lista de dicts de classificação alinhada com a lista de entrada.

    Raises:
        openai.APIError: Em caso de falha na API após retries.
    """
    settings = get_settings()

    if not emails:
        return []

    if client is None:
        if not settings.openai_api_key:
            raise ValueError('Configure OPENAI_API_KEY para classificação remota.')
        client = OpenAI(api_key=settings.openai_api_key)

    # Monta prompt com os emails numerados
    prompt_lines = [
        "Classifique cada email abaixo. Para cada um, chame a função classify_email com o email_index correspondente.\n"
    ]
    for i, email in enumerate(emails, start=1):
        from_field = email.get("from", "")[:80]
        subject = email.get("subject", "")[:120]
        snippet = email.get("snippet", "")[:200]
        prompt_lines.append(
            f"[{i}] De: {from_field}\n"
            f"    Assunto: {subject}\n"
            f"    Preview: {snippet}\n"
        )

    prompt = "\n".join(prompt_lines)

    logger.debug(f"Enviando lote de {len(emails)} emails para OpenAI...")

    response = client.chat.completions.create(
        model=settings.openai_model,
        max_tokens=settings.openai_max_tokens,
        tools=[CLASSIFY_TOOL],
        tool_choice="auto",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )

    # Extrai resultados das tool calls
    tool_results: dict[int, dict] = {}
    message = response.choices[0].message
    if message.tool_calls:
        for tool_call in message.tool_calls:
            if tool_call.function.name == "classify_email":
                try:
                    args = json.loads(tool_call.function.arguments)
                    idx = args.get("email_index", 0) if isinstance(args, dict) else 0
                    if type(idx) is int and 1 <= idx <= len(emails) and idx not in tool_results:
                        tool_results[idx] = args
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning(f"Falha ao decodificar tool call: {exc}")

    # Alinha resultados com emails de entrada (por índice 1-based)
    aligned: list[dict] = []
    for i, email in enumerate(emails, start=1):
        if i in tool_results:
            result = dict(tool_results[i])
            result["email_id"] = email["id"]
            result.pop("email_index", None)
            aligned.append(normalize_classification(result, email, settings.confidence_threshold))
        else:
            logger.warning(
                f"OpenAI não classificou email [{i}]: {email.get('subject', '')[:60]}"
            )
            aligned.append(
                {
                    "email_id": email["id"],
                    "category": "unknown",
                    "confidence": 0.0,
                    "recommended_action": "review",
                    "safe_to_automate": False,
                    "reason": "OpenAI não retornou classificação para este email",
                    "has_unsubscribe": False,
                }
            )

    finish_reason = response.choices[0].finish_reason
    logger.info(
        f"OpenAI classificou {len(tool_results)}/{len(emails)} emails "
        f"(finish_reason: {finish_reason})"
    )
    return aligned


def classify_all(
    emails: list[dict],
    batch_size: Optional[int] = None,
    client: Optional[OpenAI] = None,
) -> list[dict]:
    """
    Classifica uma lista arbitrária de emails em lotes.

    Args:
        emails: Lista completa de emails para classificar.
        batch_size: Tamanho do lote (usa MAX_BATCH_SIZE do .env se None).
        client: Instância OpenAI.

    Returns:
        Lista de classificações na mesma ordem da entrada.
    """
    from rich.progress import Progress, SpinnerColumn, TextColumn, MofNCompleteColumn

    settings = get_settings()
    batch_size = settings.max_batch_size if batch_size is None else batch_size
    if batch_size < 1 or batch_size > 100:
        raise ValueError('batch_size deve estar entre 1 e 100.')
    all_results: list[dict] = []

    if client is None:
        if not settings.openai_api_key:
            raise ValueError('Configure OPENAI_API_KEY para classificação remota.')
        client = OpenAI(api_key=settings.openai_api_key)

    batches = [emails[i: i + batch_size] for i in range(0, len(emails), batch_size)]
    logger.info(
        f"Classificando {len(emails)} emails em {len(batches)} lotes de até {batch_size}"
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]OpenAI classificando[/cyan] {task.description}"),
        MofNCompleteColumn(),
    ) as progress:
        task = progress.add_task("lotes...", total=len(batches))

        for batch_num, batch in enumerate(batches, start=1):
            progress.update(
                task,
                description=f"lote {batch_num}/{len(batches)}",
                advance=1,
            )
            try:
                results = classify_batch(batch, client=client)
                all_results.extend(results)
            except Exception as exc:
                logger.error(f"Erro no lote {batch_num}: {type(exc).__name__}")
                for email in batch:
                    all_results.append(
                        {
                            "email_id": email["id"],
                            "category": "unknown",
                            "confidence": 0.0,
                            "recommended_action": "review",
                            "safe_to_automate": False,
                            "reason": f"Erro na API OpenAI ({type(exc).__name__}); revisão manual necessária",
                            "has_unsubscribe": False,
                        }
                    )

            if batch_num < len(batches):
                time.sleep(0.5)

    return all_results
