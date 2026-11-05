import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
import httplib2
import pytest
from typer.testing import CliRunner

import main
from src.auth import oauth
from src.gmail.client import GmailClient
from src.gmail.fetcher import fetch_all_messages
from src.gmail.actions import trash_messages
from src.pipeline.executor import execute_plan, prepare_execution_plan
from test_final_audit import seed_cache, save

SCOPE = 'https://www.googleapis.com/auth/gmail.modify'


def credentials(scopes=(SCOPE,), valid=True):
    return SimpleNamespace(scopes=list(scopes), valid=valid, expired=not valid, refresh_token='synthetic',
                           refresh=Mock(), to_json=lambda: '{"token":"synthetic-new"}')


def setup_token(settings):
    settings.gmail_credentials_path.write_text('{}')
    settings.gmail_token_path.write_text('{"token":"synthetic-old"}')


def test_read_only_auth_never_refreshes_or_rewrites_token(isolated_settings_and_no_network):
    settings = isolated_settings_and_no_network
    setup_token(settings)
    old = settings.gmail_token_path.read_bytes()
    creds = credentials()
    with patch.object(oauth.Credentials, 'from_authorized_user_file', return_value=creds) as loader, patch.object(oauth.InstalledAppFlow, 'from_client_secrets_file') as flow:
        assert oauth.get_credentials(allow_token_updates=False) is creds
    loader.assert_called_once_with(str(settings.gmail_token_path))
    creds.refresh.assert_not_called()
    flow.assert_not_called()
    assert settings.gmail_token_path.read_bytes() == old


@pytest.mark.parametrize('scopes,valid', [([SCOPE], False), (['https://mail.google.com/'], True), (['https://www.googleapis.com/auth/gmail.readonly'], True)])
def test_read_only_auth_rejects_expired_or_wrong_scope_without_updates(isolated_settings_and_no_network, scopes, valid):
    settings = isolated_settings_and_no_network
    setup_token(settings)
    old = settings.gmail_token_path.read_bytes()
    creds = credentials(scopes, valid)
    with patch.object(oauth.Credentials, 'from_authorized_user_file', return_value=creds), patch.object(oauth.InstalledAppFlow, 'from_client_secrets_file') as flow:
        with pytest.raises(RuntimeError):
            oauth.get_credentials(allow_token_updates=False)
    creds.refresh.assert_not_called()
    flow.assert_not_called()
    assert settings.gmail_token_path.read_bytes() == old


def test_token_replacement_failure_preserves_old_file(isolated_settings_and_no_network):
    settings = isolated_settings_and_no_network
    setup_token(settings)
    old = settings.gmail_token_path.read_bytes()
    with patch.object(oauth.os, 'replace', side_effect=OSError('synthetic disk failure')):
        with pytest.raises(OSError):
            oauth._save_token(settings.gmail_token_path, '{"new":true}')
    assert settings.gmail_token_path.read_bytes() == old
    assert not list(settings.gmail_token_path.parent.glob('.token-*.json'))


def test_wrong_scope_requires_new_explicit_flow(isolated_settings_and_no_network):
    settings = isolated_settings_and_no_network
    setup_token(settings)
    updated = credentials()
    with patch.object(oauth.Credentials, 'from_authorized_user_file', return_value=credentials(['https://mail.google.com/'])), patch.object(oauth.InstalledAppFlow, 'from_client_secrets_file') as factory:
        factory.return_value.run_local_server.return_value = updated
        assert oauth.get_credentials() is updated
    factory.return_value.run_local_server.assert_called_once_with(host='127.0.0.1', port=0)


def test_preview_service_cannot_refresh_behind_the_read_only_auth_gate():
    original = Credentials(token='synthetic', refresh_token='synthetic-refresh', token_uri='https://oauth.example.test', client_id='synthetic', client_secret='synthetic', scopes=[SCOPE])
    with patch.object(oauth, 'get_credentials', return_value=original), patch.object(oauth, 'build') as build:
        oauth.get_gmail_service(allow_token_updates=False)
    credentials_used = build.call_args.kwargs['credentials']
    assert credentials_used.token == 'synthetic'
    assert credentials_used.refresh_token is None


def test_dry_run_preserves_existing_cache_tokens_and_logs(isolated_settings_and_no_network):
    settings = isolated_settings_and_no_network
    cache = seed_cache()
    save(cache, 'synthetic')
    setup_token(settings)
    def snapshot():
        return {str(p.relative_to(settings.db_path.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in settings.db_path.parent.rglob('*') if p.is_file()}
    before = snapshot()
    with patch('src.pipeline.executor.get_gmail_service') as auth:
        execute_plan()
    auth.assert_not_called()
    assert snapshot() == before


def test_changed_plan_requires_new_approval():
    cache = seed_cache()
    save(cache, 'one')
    approved = prepare_execution_plan()
    save(cache, 'two')
    with patch('src.pipeline.executor.get_gmail_service') as auth:
        with pytest.raises(RuntimeError, match='plano mudou'):
            execute_plan(dry_run=False, confirm=True, approved_plan=approved)
    auth.assert_not_called()


def test_trash_log_failure_does_not_count_already_moved_message_twice():
    client = Mock()
    with patch('src.gmail.actions._append_deleted_log', side_effect=OSError('synthetic full disk')):
        result = trash_messages(client, ['synthetic'], [{'id':'synthetic'}], dry_run=False, require_confirm=False)
    assert result == {'success':1, 'failed':0, 'skipped':0}
    client.trash_message.assert_called_once_with('synthetic')


def test_permanent_deletion_requires_explicit_confirmation():
    service = Mock()
    with pytest.raises(ValueError):
        GmailClient(service).delete_message('synthetic')
    service.users.assert_not_called()


def test_pagination_deduplicates_before_applying_limit():
    client = Mock()
    client.get_profile.return_value = {'messagesTotal':3}
    client.list_messages.side_effect = [{'messages':[{'id':'a'},{'id':'a'}], 'nextPageToken':'next'}, {'messages':[{'id':'b'}]}]
    client.get_message.side_effect = lambda id, **kwargs: {'id':id}
    assert [row['id'] for row in fetch_all_messages(client, limit=2)] == ['a','b']


def test_repeated_pagination_token_is_not_an_infinite_or_clean_result():
    client = Mock()
    client.get_profile.return_value = {'messagesTotal':3}
    client.list_messages.side_effect = [{'messages':[{'id':'a'}], 'nextPageToken':'repeat'}, {'messages':[], 'nextPageToken':'repeat'}]
    client.get_message.return_value = {'id':'a'}
    with pytest.raises(RuntimeError, match='pagina|página'):
        list(fetch_all_messages(client))


def error(status, reason='rateLimitExceeded', retry_after=None):
    response = httplib2.Response({'status':str(status), **({'retry-after':str(retry_after)} if retry_after is not None else {})})
    return HttpError(response, json.dumps({'error': {'errors':[{'reason':reason}]}}).encode())


def test_quota_403_and_retry_after_are_respected():
    request = Mock()
    request.execute.side_effect = [error(403, retry_after=7), {'ok':True}]
    with patch('src.gmail.client.time.sleep') as sleep:
        assert GmailClient(Mock())._execute_with_retry(request) == {'ok':True}
    sleep.assert_called_once_with(7)


def test_permissions_403_are_not_retried():
    request = Mock()
    request.execute.side_effect = error(403, 'insufficientPermissions')
    with patch('src.gmail.client.time.sleep') as sleep, pytest.raises(HttpError):
        GmailClient(Mock())._execute_with_retry(request)
    sleep.assert_not_called()
    assert request.execute.call_count == 1


def test_retry_exhaustion_does_not_sleep_after_final_attempt(isolated_settings_and_no_network):
    isolated_settings_and_no_network.max_retries = 2
    request = Mock()
    request.execute.side_effect = error(500)
    with patch('src.gmail.client.time.sleep') as sleep, pytest.raises(HttpError):
        GmailClient(Mock())._execute_with_retry(request)
    assert sleep.call_count == 1


def test_offline_execute_does_not_require_ai_or_google_credentials(isolated_settings_and_no_network):
    isolated_settings_and_no_network.openai_api_key = ''
    result = CliRunner().invoke(main.app, ['execute'])
    assert result.exit_code == 0, result.output


def test_empty_unsubscribe_preview_does_not_create_cache_or_authenticate(isolated_settings_and_no_network):
    with patch('src.auth.oauth.get_gmail_service') as auth:
        result = CliRunner().invoke(main.app, ['unsubscribe'])
    assert result.exit_code == 0, result.output
    auth.assert_not_called()
    assert not isolated_settings_and_no_network.db_path.exists()
