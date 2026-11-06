from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from test_final_audit import seed_cache, save

ROOT = Path(__file__).resolve().parents[1]


def test_sidebar_readers_do_not_create_missing_cache(isolated_settings_and_no_network):
    from ui.helpers import load_stats, load_category_emails, load_last_scan_time
    load_stats.clear()
    load_category_emails.clear()
    assert load_stats()['total_emails'] == 0
    assert load_category_emails('marketing') == []
    assert load_last_scan_time() is None
    assert not isolated_settings_and_no_network.db_path.exists()


def test_execution_worker_result_survives_streamlit_rerun():
    expected = {'archive': {'success':0, 'failed':0, 'skipped':1}}
    with patch('ui.helpers.render_sidebar_status'):
        app = AppTest.from_file(str(ROOT / 'pages/4_Execute.py')).run()
        app.session_state['execute_running'] = True
        app.session_state['_execute_worker_state'] = {'running':False, 'result':expected, 'error':None}
        app.run()
    assert not app.exception
    assert app.session_state['execute_result'] == expected


def test_execute_shows_plan_and_resets_approval_when_scope_changes():
    cache = seed_cache()
    save(cache, 'one')
    with patch('ui.helpers.render_sidebar_status'):
        app = AppTest.from_file(str(ROOT / 'pages/4_Execute.py')).run()
        assert not app.exception
        assert len(app.dataframe) > 0
        app.toggle[0].set_value(False).run()
        assert len(app.checkbox) == 2
        for box in app.checkbox:
            box.set_value(True)
        app.run()
        assert all(box.value for box in app.checkbox)
        save(cache, 'two')
        app.run()
        assert all(not box.value for box in app.checkbox)


def test_scan_worker_survives_rerun_and_uses_configured_token_path(isolated_settings_and_no_network):
    settings = isolated_settings_and_no_network
    settings.gmail_token_path.write_text('{}')
    expected = {'total':1, 'already_cached':0, 'heuristic':1, 'claude':0, 'ambiguous':0, 'stats':{}}
    with patch('ui.helpers.render_sidebar_status'), patch('ui.helpers.check_env', return_value=(True, [])):
        app = AppTest.from_file(str(ROOT / 'pages/2_Scan.py')).run()
        app.session_state['scan_running'] = True
        app.session_state['_scan_worker_state'] = {'running':False, 'result':expected, 'error':None}
        app.run()
    assert not app.exception
    assert app.session_state['scan_result'] == expected
