import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

import main
from src.classifier.cache import EmailCache
from src.classifier.claude_classifier import classify_batch, classify_all
from src.pipeline.executor import execute_plan
from src.pipeline.scan import run_scan
from src.pipeline.staging import generate_staging_plan


def seed_cache():
    cache = EmailCache()
    with cache._connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS cache_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute("INSERT OR REPLACE INTO cache_metadata VALUES ('account_email','account-a@example.test')")
    return cache


def save(cache, name, category='marketing', action='delete', confidence=.99, safe=True):
    cache.save_email(name, category, action, confidence, safe, 'synthetic', False, source='manual')


def completion(arguments):
    tool = SimpleNamespace(function=SimpleNamespace(name='classify_email', arguments=json.dumps(arguments)))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[tool]), finish_reason='tool_calls')])))))
    return client


def classification(**updates):
    return {'email_index': 1, 'category': 'marketing', 'confidence': .99,
            'recommended_action': 'delete', 'safe_to_automate': True,
            'reason': 'synthetic', 'has_unsubscribe': False, **updates}


def test_model_output_cannot_delete_protected_message():
    result = classify_batch([{'id':'synthetic', 'from':'shop@example.test', 'subject':'Nota fiscal', 'snippet':''}], client=completion(classification()))[0]
    assert result['recommended_action'] == 'keep'
    assert result['safe_to_automate'] is False


def test_model_output_with_invalid_types_fails_closed():
    result = classify_batch([{'id':'synthetic'}], client=completion(classification(confidence='NaN', safe_to_automate='false')))[0]
    assert result['recommended_action'] == 'review'
    assert result['safe_to_automate'] is False


def test_api_error_text_is_not_persisted_as_classification_reason(capsys):
    client = completion(classification())
    client.chat.completions.create.side_effect = RuntimeError('synthetic-api-secret')
    result = classify_all([{'id':'synthetic'}], client=client)[0]
    assert 'synthetic-api-secret' not in result['reason']
    assert 'synthetic-api-secret' not in capsys.readouterr().out
    assert result['safe_to_automate'] is False


def test_html_report_escapes_legacy_category_content():
    report = main._generate_html_report({'by_category':[{'category':'<script>synthetic()</script>', 'cnt':1}], 'by_action':[]}, 'synthetic')
    assert '<script>synthetic()</script>' not in report
    assert '&lt;script&gt;synthetic()&lt;/script&gt;' in report


@pytest.mark.parametrize('category,confidence', [('spam', .89), ('marketing', .84)])
def test_destructive_categories_enforce_their_minimum_confidence(category, confidence):
    result = classify_batch([{'id':'synthetic'}], client=completion(classification(category=category, confidence=confidence)))[0]
    assert result['safe_to_automate'] is False


@pytest.mark.parametrize('updates', [{'category': []}, {'email_index': True}, {'email_index': 999}, {'recommended_action': {}}, {'reason': {}}])
def test_malformed_model_fields_require_review(updates):
    result = classify_batch([{'id':'synthetic'}], client=completion(classification(**updates)))[0]
    assert result['safe_to_automate'] is False


def test_credentials_protection_wins_over_spam_keywords():
    result = classify_batch([{'id':'synthetic', 'from':'account@example.test', 'subject':'Redefinição de senha', 'snippet':"You've won a prize"}], client=completion(classification()))[0]
    assert result['recommended_action'] == 'keep'
    assert result['safe_to_automate'] is False


def test_certificate_protection_wins_over_spam_keywords():
    result = classify_batch([{'id':'synthetic', 'from':'school@example.test', 'subject':'Certificate of completion', 'snippet':"You've won a prize"}], client=completion(classification()))[0]
    assert result['recommended_action'] == 'label'


def test_staging_rejects_low_confidence_and_certificate_deletion():
    cache = seed_cache()
    save(cache, 'low', confidence=.01)
    save(cache, 'certificate', category='certificate')
    assert generate_staging_plan()['total'] == 0


def test_legacy_malformed_cache_is_reviewable_without_unsafe_action():
    cache = seed_cache()
    save(cache, 'legacy')
    with cache._connect() as db:
        db.execute("UPDATE email_cache SET confidence='invalid' WHERE email_id='legacy'")
    preview = generate_staging_plan(safe_only=False)
    assert preview['plan'] == {'review':['legacy']}
    assert preview['classifications'][0]['confidence'] == 0


def test_sender_history_does_not_override_invoice_protection():
    cache = seed_cache()
    for _ in range(3):
        cache.update_sender('shop@example.test', 'marketing', .99)
    message = {'id':'synthetic', 'from':'shop@example.test', 'subject':'Nota fiscal', 'snippet':'Comprovante', 'label_ids':[]}
    with patch('src.pipeline.scan.get_gmail_service'), patch('src.pipeline.scan.GmailClient') as factory, patch('src.pipeline.scan.fetch_all_messages', return_value=[message]):
        factory.return_value.get_profile.return_value = {'emailAddress':'account-a@example.test'}
        run_scan(skip_claude=True)
    row = cache.get_email('synthetic')
    assert row['action'] == 'keep'
    assert row['safe_to_automate'] == 0


def test_sender_history_alone_cannot_authorize_writes():
    cache = seed_cache()
    for _ in range(3):
        cache.update_sender('person@example.test', 'marketing', .99)
    message = {'id':'synthetic', 'from':'person@example.test', 'subject':'Uma mensagem', 'snippet':'Conversamos amanhã', 'label_ids':[]}
    with patch('src.pipeline.scan.get_gmail_service'), patch('src.pipeline.scan.GmailClient') as factory, patch('src.pipeline.scan.fetch_all_messages', return_value=[message]):
        factory.return_value.get_profile.return_value = {'emailAddress':'account-a@example.test'}
        run_scan(skip_claude=True)
    assert cache.get_email('synthetic')['safe_to_automate'] == 0


def test_legacy_nonempty_cache_cannot_silently_bind_to_a_new_account():
    cache = EmailCache()
    save(cache, 'legacy')
    with pytest.raises(RuntimeError, match='Cache sem vínculo'):
        cache.ensure_account('account-a@example.test', bind_empty=True)
    assert cache.get_email('legacy') is not None


def test_execute_dry_run_does_not_create_cache_or_authenticate(isolated_settings_and_no_network):
    settings = isolated_settings_and_no_network
    with patch('src.pipeline.executor.get_gmail_service') as auth:
        assert execute_plan() == {}
    auth.assert_not_called()
    assert not settings.db_path.exists()


def test_mark_read_dry_run_does_not_authenticate():
    cache = seed_cache()
    save(cache, 'synthetic')
    with patch('main._check_env'), patch('src.auth.oauth.get_gmail_service') as auth:
        result = CliRunner().invoke(main.app, ['mark-read'])
    assert result.exit_code == 0, result.output
    auth.assert_not_called()


def test_delete_sender_rejects_query_injection_before_auth():
    with patch('main._check_env'), patch('src.auth.oauth.get_gmail_service') as auth, patch('src.gmail.fetcher.fetch_all_messages', return_value=[]):
        result = CliRunner().invoke(main.app, ['delete-sender', 'a@example.test OR in:anywhere'])
    assert result.exit_code != 0
    auth.assert_not_called()


def test_limit_applies_to_entire_plan():
    cache = seed_cache()
    save(cache, 'trash')
    save(cache, 'archive', category='newsletter', action='archive')
    result = execute_plan(limit=1)
    assert sum(r['skipped'] for r in result.values()) == 1


def test_live_plan_cannot_apply_to_different_account():
    cache = seed_cache()
    save(cache, 'synthetic')
    with patch('src.pipeline.executor.get_gmail_service'), patch('src.pipeline.executor.GmailClient') as factory, patch('src.pipeline.executor.trash_messages') as trash:
        factory.return_value.get_profile.return_value = {'emailAddress':'account-b@example.test'}
        with pytest.raises(RuntimeError, match='conta|account'):
            execute_plan(dry_run=False, confirm=True)
    trash.assert_not_called()
