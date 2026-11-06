"""Local safety rules; model output and sender history cannot authorize writes."""
import math

PROTECTED_CATEGORIES = {'invoice', 'important', 'platform_credential', 'action_required'}
ALLOWED_ACTIONS = {
    'marketing': {'delete', 'trash', 'archive', 'label', 'keep', 'review'},
    'spam': {'delete', 'trash', 'archive', 'label', 'keep', 'review'},
    'newsletter': {'archive', 'label', 'keep', 'review'},
    'transactional': {'archive', 'label', 'keep', 'review'},
    'certificate': {'label', 'keep', 'review'},
    'course_access': {'label', 'keep', 'review'},
    **{category: {'keep', 'review'} for category in PROTECTED_CATEGORIES},
    'unknown': {'review', 'keep'},
}


def review_classification(email_id: str, reason: str) -> dict:
    return {'email_id': email_id, 'category': 'unknown', 'confidence': 0.0,
            'recommended_action': 'review', 'safe_to_automate': False,
            'reason': reason, 'has_unsubscribe': False}


def normalize_classification(result: dict, email: dict | None = None, threshold: float = .8) -> dict:
    email_id = (email or {}).get('id', result.get('email_id', ''))
    if email:
        from src.classifier.heuristics import quick_classify
        local = quick_classify(email)
        if local and local['category'] in PROTECTED_CATEGORIES | {'certificate', 'course_access'}:
            result = {**result, **local, 'email_id': email_id}
    category = result.get('category')
    action = result.get('recommended_action')
    confidence = result.get('confidence')
    safe = result.get('safe_to_automate')
    if (not isinstance(category, str) or not isinstance(action, str) or category not in ALLOWED_ACTIONS or not isinstance(confidence, (int, float))
            or isinstance(confidence, bool) or not 0 <= confidence <= 1 or not math.isfinite(confidence) or type(safe) is not bool
            or not isinstance(result.get('reason', ''), str)
            or type(result.get('has_unsubscribe', False)) is not bool):
        return review_classification(email_id, 'Classificação inválida; revisão manual necessária')
    normalized = {**result, 'email_id': email_id}
    destructive_threshold = .90 if category == 'spam' else .85
    if action in {'delete', 'trash'} and confidence < max(threshold, destructive_threshold):
        normalized['safe_to_automate'] = False
    if category in PROTECTED_CATEGORIES:
        normalized.update(recommended_action='keep', safe_to_automate=False)
    elif action not in ALLOWED_ACTIONS[category]:
        normalized.update(recommended_action='review', safe_to_automate=False)
    elif category == 'unknown' or confidence < threshold or result.get('source') == 'sender_cache':
        normalized['safe_to_automate'] = False
    return normalized
