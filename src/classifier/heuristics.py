"""Classificação local por heurísticas — sem chamada de API, instantânea."""
import re
from typing import Optional


# ── Listas de padrões ─────────────────────────────────────────────────────────

MARKETING_SENDER_PATTERNS: list[str] = [
    "noreply@", "no-reply@", "newsletter@", "marketing@",
    "promo@", "offers@", "deals@", "news@", "updates@",
    "info@", "hello@", "hi@", "team@", "donotreply@",
    "do-not-reply@", "notify@", "notifications@", "automated@",
]

MARKETING_SUBJECT_KEYWORDS: list[str] = [
    "promoção", "oferta", "desconto", "% off", "black friday",
    "cyber monday", "sale", "deal", "exclusive", "limited time",
    "last chance", "don't miss", "não perca", "aproveite",
    "frete grátis", "cupom", "voucher", "economize", "save",
    "newsletter", "unsubscribe", "descadastre", "cancelar inscrição",
    "semana do cliente", "liquidação", "mega sale", "flash sale",
]

MARKETING_BODY_KEYWORDS: list[str] = [
    "unsubscribe", "descadastre-se", "cancelar inscrição",
    "opt out", "opt-out", "remove from list", "sair da lista",
]

IMPORTANT_KEYWORDS: list[str] = [
    "nota fiscal", "nfe", "nf-e", "boleto", "contrato",
    "certificado digital", "cpf", "cnpj",
    "comprovante", "recibo", "pedido confirmado", "confirmação de pedido",
    "fatura", "invoice", "pagamento recebido", "pagamento confirmado",
    "imposto de renda", "ir 2024", "ir 2025", "receita federal",
    "declaração de imposto", "declaração ir",
]

CERTIFICATE_KEYWORDS: list[str] = [
    "certificado de conclusão", "certificate of completion",
    "certificado de participação", "declaração de participação",
    "certificado", "certificate", "diploma",
    "você concluiu", "you completed", "curso concluído",
]

SPAM_PATTERNS: list[str] = [
    r"you've? (won|been selected)", r"lottery", r"prize",
    r"nigerian", r"inheritance", r"million dollars?",
    r"click here to claim", r"verify your account immediately",
    r"urgent.{0,20}(wire|transfer|bitcoin)",
]

COURSE_ACCESS_KEYWORDS: list[str] = [
    "acesso liberado", "sua senha", "login liberado", "bem-vindo ao curso",
    "welcome to", "your account", "account created", "account activated",
    "activate your account", "confirm your email", "verify your email",
    "udemy", "coursera", "alura", "hotmart", "kiwify", "eduzz",
]

PLATFORM_KEYWORDS: list[str] = [
    "senha temporária", "temporary password", "reset password",
    "redefinição de senha", "recuperação de senha", "password reset",
    "two-factor", "2fa", "authentication code", "código de acesso",
]

# Remetentes que NUNCA devem ser deletados automaticamente
PROTECTED_SENDER_PATTERNS: list[str] = [
    "receita.fazenda.gov.br", "fazenda.gov.br", "caixa.gov.br",
    "correios.com.br", "detran", "prefeitura", ".gov.br",
]


# ── Funções auxiliares ────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase sem acentos para matching."""
    import unicodedata
    text = text.lower()
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _contains_any(text: str, keywords: list[str]) -> bool:
    norm = _normalize(text)
    return any(_normalize(kw) in norm for kw in keywords)


def _matches_any_pattern(text: str, patterns: list[str]) -> bool:
    norm = _normalize(text)
    return any(re.search(p, norm, re.IGNORECASE) for p in patterns)


def _extract_sender_address(from_field: str) -> str:
    """Extrai apenas o email de 'Nome <email@domain.com>'."""
    match = re.search(r"<([^>]+)>", from_field)
    return match.group(1).lower() if match else from_field.lower().strip()


# ── Classificador principal ───────────────────────────────────────────────────

def quick_classify(email: dict) -> Optional[dict]:
    """
    Tenta classificar um email com alta confiança usando apenas heurísticas locais.

    Retorna None se o email for ambíguo e precisar do Claude.

    Args:
        email: Dict com campos from, subject, snippet, label_ids.

    Returns:
        Dict de classificação com campos:
            category, confidence, recommended_action,
            safe_to_automate, reason, has_unsubscribe
        Ou None se não conseguir classificar com confiança >= 0.85.
    """
    sender = email.get("from", "")
    subject = email.get("subject", "")
    snippet = email.get("snippet", "")
    label_ids = email.get("label_ids", [])
    combined = f"{sender} {subject} {snippet}"

    sender_addr = _extract_sender_address(sender)
    has_unsubscribe = bool(email.get("unsubscribe_link", ""))

    # ── 1. PROTEÇÃO: nunca tocar nestas categorias ─────────────────────────
    if _contains_any(combined, IMPORTANT_KEYWORDS):
        return {
            "category": "invoice",
            "confidence": 0.95,
            "recommended_action": "keep",
            "safe_to_automate": False,
            "reason": "Contém palavras-chave de documentos financeiros/fiscais",
            "has_unsubscribe": has_unsubscribe,
        }

    # Remetentes governamentais/protegidos
    if any(p in sender_addr for p in PROTECTED_SENDER_PATTERNS):
        return {
            "category": "important",
            "confidence": 0.95,
            "recommended_action": "keep",
            "safe_to_automate": False,
            "reason": "Remetente governamental ou instituição protegida",
            "has_unsubscribe": has_unsubscribe,
        }

    # Credenciais têm precedência mesmo se também houver termos de spam.
    if _contains_any(combined, PLATFORM_KEYWORDS):
        return {
            'category': 'platform_credential', 'confidence': .95,
            'recommended_action': 'keep', 'safe_to_automate': False,
            'reason': 'Conteúdo de credencial/recuperação de acesso protegido',
            'has_unsubscribe': has_unsubscribe,
        }

    if _contains_any(subject, CERTIFICATE_KEYWORDS):
        return {
            'category': 'certificate', 'confidence': .90,
            'recommended_action': 'label', 'safe_to_automate': True,
            'reason': 'Assunto de certificado protegido contra exclusão',
            'has_unsubscribe': has_unsubscribe,
        }

    # ── 2. SPAM evidente ───────────────────────────────────────────────────
    if _matches_any_pattern(combined, SPAM_PATTERNS):
        return {
            "category": "spam",
            "confidence": 0.92,
            "recommended_action": "delete",
            "safe_to_automate": True,
            "reason": "Padrão de spam/golpe detectado",
            "has_unsubscribe": has_unsubscribe,
        }

    # ── 3. CERTIFICADOS ────────────────────────────────────────────────────
    if _contains_any(subject, CERTIFICATE_KEYWORDS):
        return {
            "category": "certificate",
            "confidence": 0.90,
            "recommended_action": "label",
            "safe_to_automate": True,
            "reason": "Assunto indica certificado de conclusão",
            "has_unsubscribe": has_unsubscribe,
        }

    # ── 4. ACESSO A PLATAFORMAS / CREDENCIAIS ──────────────────────────────
    if _contains_any(combined, PLATFORM_KEYWORDS):
        return {
            "category": "platform_credential",
            "confidence": 0.88,
            "recommended_action": "keep",
            "safe_to_automate": False,
            "reason": "Email de redefinição/recuperação de senha ou 2FA",
            "has_unsubscribe": has_unsubscribe,
        }

    if _contains_any(combined, COURSE_ACCESS_KEYWORDS):
        return {
            "category": "course_access",
            "confidence": 0.85,
            "recommended_action": "label",
            "safe_to_automate": True,
            "reason": "Email de acesso a curso ou plataforma de aprendizado",
            "has_unsubscribe": has_unsubscribe,
        }

    # ── 5. MARKETING — alta confiança ──────────────────────────────────────
    marketing_signals = 0

    if any(sender_addr.startswith(p) or f"@{p}" in sender_addr
           for p in MARKETING_SENDER_PATTERNS):
        marketing_signals += 2

    if _contains_any(subject, MARKETING_SUBJECT_KEYWORDS):
        marketing_signals += 2

    if _contains_any(snippet, MARKETING_BODY_KEYWORDS):
        marketing_signals += 1

    if has_unsubscribe:
        marketing_signals += 1

    if "CATEGORY_PROMOTIONS" in label_ids:
        marketing_signals += 2

    if marketing_signals >= 4:
        confidence = min(0.70 + (marketing_signals * 0.04), 0.96)
        return {
            "category": "marketing",
            "confidence": round(confidence, 2),
            "recommended_action": "delete",
            "safe_to_automate": confidence >= 0.85,
            "reason": f"Marketing detectado ({marketing_signals} sinais: remetente, assunto, unsubscribe)",
            "has_unsubscribe": has_unsubscribe,
        }

    # ── 6. NEWSLETTER ──────────────────────────────────────────────────────
    if has_unsubscribe and marketing_signals >= 1:
        return {
            "category": "newsletter",
            "confidence": 0.82,
            "recommended_action": "archive",
            "safe_to_automate": True,
            "reason": "Newsletter com link de descadastro",
            "has_unsubscribe": True,
        }

    # Ambíguo — precisa do Claude
    return None


def is_safe_to_delete(email: dict, classification: dict) -> bool:
    """
    Verifica se um email pode ser deletado automaticamente com segurança.

    Nunca retorna True para emails com conteúdo financeiro/fiscal.

    Args:
        email: Dict normalizado do email.
        classification: Dict retornado por quick_classify ou Claude.

    Returns:
        True se pode ser deletado automaticamente.
    """
    from src.utils.config import get_settings

    settings = get_settings()
    combined = f"{email.get('from', '')} {email.get('subject', '')} {email.get('snippet', '')}"

    # Proteções absolutas
    if _contains_any(combined, IMPORTANT_KEYWORDS):
        return False

    if classification.get("category") in ("invoice", "certificate", "important",
                                           "platform_credential", "action_required"):
        return False

    confidence = classification.get("confidence", 0)
    if confidence < settings.confidence_threshold:
        return False

    return classification.get("safe_to_automate", False)
