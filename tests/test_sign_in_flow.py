"""Sign in, sign out, and getting back to the company picker.

Owner, 2026-09-13: signing out only reloaded the same company's password
prompt; to open another company, or even to see who the users were, you
closed SlowBooks and opened it again — and the Companies page said so.
Now: sign out in the desktop window goes back to the picker (the launcher
stops the company's server and reloads the picker page); the sign-in
screen on a multi-user install lists the users; the Companies page has a
Switch company button; and the sign-in screen itself offers a way to a
different company on the desktop.
"""

import re
import threading
from pathlib import Path

import desktop_launcher as dl

ROOT = Path(__file__).resolve().parents[1]
# Fixture password for the second user, satisfying create_user's minimum length.
# Kept away from the "username" key: GitGuardian's pair detector fires on a
# username and password literal adjacent in one object (2.14.0 gate, #157).
SECOND_USER_PW = "long-enough-pw"
JS = {
    name: (ROOT / "app/static/js" / name).read_text(encoding="utf-8")
    for name in ("auth.js", "bootstrap.js", "companies.js")
}


class _Window:
    def __init__(self):
        self.loaded = []

    def load_html(self, html):
        self.loaded.append(html)


class _Proc:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_show_picker_stops_the_company_and_reloads_the_picker_page(monkeypatch):
    api = dl.PickerApi(3001)
    api._window = _Window()
    api._server = proc = _Proc()
    done = threading.Event()
    real_thread = threading.Thread

    class _Sync(real_thread):
        """run the scheduled load on this thread so the test can see it"""

        def start(self):
            self.run()
            done.set()

    monkeypatch.setattr(threading, "Thread", _Sync)
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    assert api.show_picker() == {"success": True}
    assert proc.terminated, "the open company's server must be stopped"
    assert api._server is None
    assert api._window.loaded == [dl.PICKER_HTML]


def test_show_picker_without_a_window_still_stops_the_server(monkeypatch):
    api = dl.PickerApi(3001)
    api._server = proc = _Proc()
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    assert api.show_picker()["success"] is True
    assert proc.terminated


def test_the_launcher_keeps_the_window_it_creates():
    src = (ROOT / "desktop_launcher.py").read_text(encoding="utf-8")
    assert re.search(r"api\._window = webview\.create_window\(", src)


def test_status_lists_users_only_on_a_multi_user_install_and_only_before_sign_in(
    client, unauthed_client
):
    # single user: no list, even signed out
    assert "usernames" not in unauthed_client.get("/api/auth/status").json()
    r = client.post(
        "/api/users",
        json={
            "username": "bookkeeper",
            "password": SECOND_USER_PW,
            "role": "bookkeeper",
        },
    )
    assert r.status_code in (200, 201), r.text
    signed_out = unauthed_client.get("/api/auth/status").json()
    assert signed_out["multi_user"] is True
    assert "bookkeeper" in signed_out["usernames"] and len(signed_out["usernames"]) >= 2
    assert all(
        isinstance(u, str) for u in signed_out["usernames"]
    )  # names only, never roles
    signed_in = client.get("/api/auth/status").json()
    assert "usernames" not in signed_in


def test_the_page_sends_sign_out_back_to_the_picker_on_the_desktop():
    boot = JS["bootstrap.js"]
    body = boot[boot.index("logout-btn") :]
    assert "shell.show_picker()" in body and "window.location.reload()" in body
    assert body.index("show_picker") < body.index("window.location.reload()")


def test_the_sign_in_screen_lists_users_and_offers_another_company():
    auth = JS["auth.js"]
    assert 'select id="auth-username"' in auth
    assert "status.usernames" in auth
    assert (
        "auth-switch-company" in auth and "window.pywebview.api.show_picker()" in auth
    )


def test_the_companies_page_no_longer_tells_people_to_close_the_app():
    comp = JS["companies.js"]
    assert "close SlowBooks Pro and open it again" not in comp
    assert "switchCompany" in comp and "show_picker" in comp


def test_the_picker_opens_the_last_company_on_first_load_only(monkeypatch):
    """Owner: with companies on disk the app should land in the last one,
    with the picker one click away — not on the picker every time. But a
    picker reached by Sign out or Switch company must stay put, or you
    could never leave."""
    from app.services import company_service

    monkeypatch.setattr(
        company_service,
        "manifest_list_companies",
        lambda: [{"name": "A", "file": "a.db"}],
    )
    monkeypatch.setattr(company_service, "get_last_opened", lambda: "a.db")
    api = dl.PickerApi(3001)
    assert api.list_companies()["auto_open"] is True
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    api.show_picker()
    assert api.list_companies()["auto_open"] is False
    # and the page honours it: opens only when auto_open is set and the file is listed
    assert "c.file === info.last_opened" in dl.PICKER_HTML
    assert "if (info.auto_open && last)" in dl.PICKER_HTML
    assert "openCompany(last.file, last.name)" in dl.PICKER_HTML
    # and says which company it is opening while the list is greyed out
    assert "setStatus('Opening ' + (name || 'company')" in dl.PICKER_HTML
