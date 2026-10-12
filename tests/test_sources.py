"""IMAP email source, mocked -- no real network. Nothing here touches a real mailbox; the live
Gmail test for this feature was run by hand against a real inbox outside the test suite."""
import imaplib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dealradar.sources import FolderSource, ImapError, ImapSource, SettingsBackedSource, guess_imap_host

FIXTURE = Path(__file__).parent / "fixtures" / "sample.eml"


def test_guess_imap_host():
    assert guess_imap_host("mason@gmail.com") == "imap.gmail.com"
    assert guess_imap_host("mason@outlook.com") == "outlook.office365.com"
    assert guess_imap_host("mason@some-wholesaler-crm.net") is None


class FakeImap:
    """Stands in for imaplib.IMAP4_SSL. `script` controls what login/select/search/fetch return."""

    instances = []

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.logged_in = False
        self.closed = self.logged_out = False
        FakeImap.instances.append(self)

    def login(self, user, pw):
        if pw != "correct-app-password":
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        self.logged_in = True

    def select(self, folder, readonly=True):
        if folder == "NoSuchFolder":
            return "NO", [b"folder not found"]
        return "OK", [b"1"]

    def search(self, charset, *crit):
        return "OK", [b"1"]

    def fetch(self, num, parts):
        return "OK", [(b"1 (RFC822 {n})", FIXTURE.read_bytes())]

    def close(self):
        self.closed = True

    def logout(self):
        self.logged_out = True


@pytest.fixture(autouse=True)
def fake_imap(monkeypatch):
    FakeImap.instances.clear()
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap)
    yield FakeImap.instances


def test_fetch_success_closes_connection(fake_imap):
    src = ImapSource("mason@gmail.com", "correct-app-password")
    msgs = src.fetch()
    assert len(msgs) == 1 and msgs[0].subject and "Wholesale" in msgs[0].subject
    assert fake_imap[0].closed and fake_imap[0].logged_out  # connection always cleaned up


def test_wrong_password_raises_imap_error_without_leaking_it(fake_imap):
    src = ImapSource("mason@gmail.com", "wrong-password")
    with pytest.raises(ImapError) as e:
        src.fetch()
    assert "wrong-password" not in str(e.value)  # the password itself never ends up in the error text


def test_unknown_domain_without_explicit_host_raises(fake_imap):
    src = ImapSource("mason@some-wholesaler-crm.net", "correct-app-password")
    with pytest.raises(ImapError, match="Settings"):
        src.fetch()


def test_explicit_host_overrides_guess(fake_imap):
    src = ImapSource("mason@some-wholesaler-crm.net", "correct-app-password", host="imap.example.com")
    msgs = src.fetch()
    assert len(msgs) == 1 and fake_imap[0].host == "imap.example.com"


def test_bad_folder_raises(fake_imap):
    src = ImapSource("mason@gmail.com", "correct-app-password", folder="NoSuchFolder")
    with pytest.raises(ImapError, match="NoSuchFolder"):
        src.fetch()


def test_test_connection_does_not_fetch_messages(fake_imap):
    ImapSource("mason@gmail.com", "correct-app-password").test_connection()  # must not raise
    with pytest.raises(ImapError):
        ImapSource("mason@gmail.com", "wrong-password").test_connection()


class FakeStore:
    def __init__(self, settings):
        self._settings = settings

    def get_settings(self):
        return self._settings


def test_settings_backed_source_falls_back_without_credentials(tmp_path, fake_imap):
    (tmp_path / "early.json").write_text('{"id":"x","subject":"s","from":"f","date":"2026-01-01T00:00:00","text":"hi"}')
    store = FakeStore({"email_address": "", "email_app_password": ""})
    src = SettingsBackedSource(store, tmp_path)
    msgs = src.fetch()
    assert len(msgs) == 1 and not fake_imap  # IMAP was never even attempted


def test_settings_backed_source_uses_imap_once_credentials_saved(tmp_path, fake_imap):
    store = FakeStore({"email_address": "mason@gmail.com", "email_app_password": "correct-app-password",
                        "imap_host": "", "imap_folder": "INBOX"})
    src = SettingsBackedSource(store, tmp_path)
    msgs = src.fetch()
    assert len(msgs) == 1 and fake_imap  # IMAP was actually used this time


def test_settings_backed_source_falls_back_on_bad_credentials(tmp_path, fake_imap):
    store = FakeStore({"email_address": "mason@gmail.com", "email_app_password": "wrong-password",
                        "imap_host": "", "imap_folder": "INBOX"})
    (tmp_path / "early.json").write_text('{"id":"x","subject":"s","from":"f","date":"2026-01-01T00:00:00","text":"hi"}')
    src = SettingsBackedSource(store, tmp_path)
    msgs = src.fetch()  # bad IMAP creds must not crash the scan -- falls back to the folder
    assert len(msgs) == 1 and msgs[0].msg_id == "x"
