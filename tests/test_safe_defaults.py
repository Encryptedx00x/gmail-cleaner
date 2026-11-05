from unittest.mock import patch

from typer.testing import CliRunner

import main
from src.gmail.actions import trash_messages


runner = CliRunner()


def test_execute_defaults_to_dry_run():
    with patch("main._check_env"), patch("src.pipeline.executor.execute_plan") as execute_plan:
        result = runner.invoke(main.app, ["execute"])

    assert result.exit_code == 0, result.output
    assert execute_plan.call_args.kwargs["dry_run"] is True


def test_execute_live_requires_confirmation_by_default():
    with patch("main._check_env"), patch("src.pipeline.executor.execute_plan") as execute_plan:
        result = runner.invoke(main.app, ["execute", "--live"], input="n\n")

    assert result.exit_code == 0, result.output
    execute_plan.assert_not_called()


def test_delete_sender_defaults_to_dry_run():
    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
         patch("main._check_env"), \
         patch("src.classifier.cache.EmailCache"), \
         patch("src.auth.oauth.get_gmail_service"), \
         patch("src.gmail.fetcher.fetch_all_messages", return_value=[]):
        result = runner.invoke(main.app, ["delete-sender", "news@example.test"])

    assert result.exit_code == 0, result.output


def test_mark_read_defaults_to_dry_run():
    with patch("main._check_env"), \
         patch("src.classifier.cache.EmailCache") as cache_cls, \
         patch("src.auth.oauth.get_gmail_service") as get_service:
        cache_cls.return_value._connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
        result = runner.invoke(main.app, ["mark-read"])

    assert result.exit_code == 0, result.output
    get_service.assert_not_called()


def test_trash_cancelled_without_exact_confirmation():
    class Client:
        def __init__(self):
            self.trashed = []

        def trash_message(self, message_id):
            self.trashed.append(message_id)

    client = Client()
    with patch("builtins.input", return_value="no"):
        result = trash_messages(
            client,
            ["synthetic-message"],
            [{"id": "synthetic-message"}],
            dry_run=False,
            require_confirm=True,
        )

    assert result == {"success": 0, "failed": 0, "skipped": 1}
    assert client.trashed == []
