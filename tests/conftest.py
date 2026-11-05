import socket

import pytest

from src.utils import config


@pytest.fixture(autouse=True)
def isolated_settings_and_no_network(tmp_path, monkeypatch):
    settings = config.Settings(
        _env_file=None, openai_api_key='synthetic-key', db_path=tmp_path / 'cache.db',
        gmail_credentials_path=tmp_path / 'credentials.json', gmail_token_path=tmp_path / 'token.json',
        rate_limit_delay=0,
    )
    monkeypatch.setattr(config, '_settings', settings)

    def forbidden(*args, **kwargs):
        raise AssertionError('Real network access is forbidden in Gmail tests')

    original_connect = socket.socket.connect
    def guarded_connect(sock, address):
        # Windows asyncio implements its internal socketpair over loopback.
        if isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1'):
            return original_connect(sock, address)
        return forbidden()
    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    return settings
