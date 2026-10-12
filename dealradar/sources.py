"""Where emails come from.

`FolderSource`       - reads .eml files (and simple .json exports) dropped in a folder. This is what
                       the tests and the live trial in this build use.
`ImapSource`         - the real thing for deployment: logs into Mason's own inbox over IMAP with an
                       app-specific password (Settings > "Connect your email"). No Google Cloud
                       project or OAuth consent screen needed -- works with Gmail, Outlook/Office365,
                       Yahoo, iCloud and most other providers that support IMAP + app passwords.
                       Verified end-to-end against a real Gmail inbox (see tests/test_sources.py).
`SettingsBackedSource` - what the server actually uses: reads the current Settings every scan and
                       picks IMAP (once an email + app password are saved) or falls back to the
                       `inbox/` folder, so saving credentials in Settings takes effect on the very
                       next scheduled scan -- no redeploy, no restart.
`GmailApiSource`     - superseded by ImapSource. Full Gmail API OAuth needs a browser and a listening
                       localhost port on the SAME machine as the server, which a headless deployment
                       (Railway, Render, ...) doesn't have. Left here untouched in case a future
                       version wants real Gmail API features IMAP can't do; not wired into the app.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path


@dataclass
class RawMessage:
    msg_id: str
    subject: str
    sender: str
    date: datetime | None
    text: str  # the text/plain body, decoded
    html: str | None = None  # the text/html twin, when the message has one (used for photos)


def _from_eml_bytes(raw: bytes) -> RawMessage:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    body = msg.get_body(preferencelist=("plain",))
    html_part = msg.get_body(preferencelist=("html",))
    try:
        date = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else None
    except (TypeError, ValueError):
        date = None
    return RawMessage(
        msg_id=(msg["Message-ID"] or "").strip("<> ") or f"noid-{hash(raw)}",
        subject=str(msg["Subject"] or ""),
        sender=str(msg["From"] or ""),
        date=date,
        text=body.get_content() if body is not None else "",
        html=html_part.get_content() if html_part is not None else None,
    )


class FolderSource:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)

    def fetch(self, since: datetime | None = None) -> list[RawMessage]:
        out: list[RawMessage] = []
        for p in sorted(self.folder.glob("*")):
            try:
                if p.suffix.lower() == ".eml":
                    m = _from_eml_bytes(p.read_bytes())
                elif p.suffix.lower() == ".json":
                    j = json.loads(p.read_text())
                    m = RawMessage(str(j["id"]), j.get("subject", ""), j.get("from", ""),
                                   datetime.fromisoformat(j["date"]) if j.get("date") else None, j["text"],
                                   html=j.get("html"))  # most JSON exports won't have this -> no photos, that's fine
                else:
                    continue
            except Exception as e:  # a bad file must not stop the scan
                print(f"[sources] skipped {p.name}: {e}")
                continue
            if since and m.date and m.date.astimezone(timezone.utc) < since:
                continue
            out.append(m)
        return out


IMAP_HOSTS = {  # guessed from the email domain when no host is set explicitly in Settings
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com", "office365.com": "outlook.office365.com",
    "yahoo.com": "imap.mail.yahoo.com", "ymail.com": "imap.mail.yahoo.com",
    "icloud.com": "imap.mail.me.com", "me.com": "imap.mail.me.com", "mac.com": "imap.mail.me.com",
    "aol.com": "imap.aol.com",
}


def guess_imap_host(email_address: str) -> str | None:
    domain = (email_address or "").rsplit("@", 1)[-1].lower()
    return IMAP_HOSTS.get(domain)


class ImapError(Exception):
    """Login or search failed -- message is safe to show in the UI (no password in it)."""


class ImapSource:
    """Logs into one inbox over IMAP (read-only) with an app-specific password and pulls messages
    whose subject matches the wholesaler blast. Works with any IMAP provider; `host` can be left
    unset for Gmail/Outlook/Yahoo/iCloud/AOL (guessed from the email's domain, see IMAP_HOSTS).
    """

    def __init__(self, email_address: str, app_password: str, host: str | None = None,
                 folder: str = "INBOX", subject_contains: str = "Wholesale", port: int = 993):
        self.email_address, self.app_password = email_address, app_password
        self.host = host or guess_imap_host(email_address)
        self.folder, self.subject_contains, self.port = folder, subject_contains, port

    def _connect(self):
        import imaplib

        if not self.host:
            raise ImapError(
                f"Don't know the IMAP server for {self.email_address.rsplit('@', 1)[-1] if '@' in self.email_address else 'that address'} "
                "-- set it manually in Settings.")
        try:
            conn = imaplib.IMAP4_SSL(self.host, self.port)
            conn.login(self.email_address, self.app_password)
        except imaplib.IMAP4.error as e:
            raise ImapError(f"Could not sign in to {self.email_address}: {e}") from e
        except OSError as e:
            raise ImapError(f"Could not reach {self.host}:{self.port}: {e}") from e
        return conn

    def test_connection(self) -> None:
        """Just proves the login + folder work; raises ImapError with a UI-safe message on failure."""
        conn = self._connect()
        try:
            typ, _ = conn.select(self.folder, readonly=True)
            if typ != "OK":
                raise ImapError(f"Signed in, but couldn't open the \"{self.folder}\" folder.")
        finally:
            self._close(conn)

    def _close(self, conn) -> None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - already disconnected, or nothing was selected
            pass
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass

    def fetch(self, since: datetime | None = None) -> list[RawMessage]:
        conn = self._connect()
        out: list[RawMessage] = []
        try:
            typ, _ = conn.select(self.folder, readonly=True)
            if typ != "OK":
                raise ImapError(f"Signed in, but couldn't open the \"{self.folder}\" folder.")
            crit = ["SUBJECT", f'"{self.subject_contains}"']
            if since:
                crit += ["SINCE", since.astimezone(timezone.utc).strftime("%d-%b-%Y")]
            typ, data = conn.search(None, *crit)
            if typ != "OK" or not data or not data[0]:
                return out
            for num in data[0].split():
                typ, msg_data = conn.fetch(num, "(RFC822)")
                if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                try:
                    m = _from_eml_bytes(msg_data[0][1])
                except Exception as e:  # noqa: BLE001 - one bad message must not sink the scan
                    print(f"[sources] skipped IMAP message {num}: {e}")
                    continue
                if since and m.date and m.date.astimezone(timezone.utc) < since:
                    continue
                out.append(m)
        finally:
            self._close(conn)
        return out


class SettingsBackedSource:
    """What the server passes to the scanner: picks IMAP when Mason has saved an email + app
    password in Settings, otherwise reads the (normally empty, except in tests/the demo) folder.
    Reading Settings fresh on every call means saving credentials takes effect on the very next
    scheduled scan -- no redeploy, no restart.
    """

    def __init__(self, store, fallback_folder: str | Path = "inbox"):
        self.store = store
        self.fallback = FolderSource(fallback_folder)

    def fetch(self, since: datetime | None = None) -> list[RawMessage]:
        s = self.store.get_settings()
        if s.get("email_address") and s.get("email_app_password"):
            src = ImapSource(s["email_address"], s["email_app_password"], host=s.get("imap_host") or None,
                              folder=s.get("imap_folder") or "INBOX")
            try:
                return src.fetch(since)
            except ImapError as e:
                print(f"[sources] IMAP fetch failed, falling back to folder: {e}")
        return self.fallback.fetch(since)


class GmailApiSource:  # pragma: no cover - needs Google credentials
    """Reads matching messages with the Gmail API (scope: gmail.readonly).

    Setup: create an OAuth "Desktop app" client in Google Cloud, enable the Gmail API, download
    credentials.json, run once interactively to create token.json.
    """

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    def __init__(self, credentials_file="credentials.json", token_file="token.json",
                 query='subject:("Wholesale Updates") newer_than:3d'):
        self.credentials_file, self.token_file, self.query = credentials_file, token_file, query

    def _service(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(self.token_file, self.SCOPES) if Path(self.token_file).exists() else None
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                creds = InstalledAppFlow.from_client_secrets_file(self.credentials_file, self.SCOPES).run_local_server(port=0)
            Path(self.token_file).write_text(creds.to_json())
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def fetch(self, since: datetime | None = None) -> list[RawMessage]:
        svc = self._service()
        ids = svc.users().messages().list(userId="me", q=self.query, maxResults=20).execute().get("messages", [])
        out = []
        for i in ids:
            raw = svc.users().messages().get(userId="me", id=i["id"], format="raw").execute()["raw"]
            out.append(_from_eml_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))))
        return out
