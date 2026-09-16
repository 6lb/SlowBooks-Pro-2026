"""Save PDF / Save backup when the folder is genuinely refused.

The 2.9.0 gate's fallback tests monkeypatch ``_write_unique`` to raise, which
proves the handler but not that a real EPERM from the operating system is
caught where it is raised. On macOS a TCC "Don't Allow" is exactly that: the
syscall returns EPERM and Python raises ``PermissionError`` from inside
``mkdir()`` or ``write_bytes()``, not from a stub. Until now that path was
only ever confirmed by a human clicking the consent prompt (PR #93, macOS
review). These tests take the permission away for real, so both shapes of a
denial run in CI:

  * the first save after a denial -- the Reports folder does not exist yet
    and ``mkdir()`` is refused;
  * a save after the folder was created and access was later revoked --
    ``mkdir(exist_ok=True)`` succeeds and the byte write is refused.

Windows mode bits do not deny the owner and root ignores them, so the module
skips on both.
"""

import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="mode bits do not refuse the owner on Windows, and root ignores them",
)


@pytest.fixture
def mac_home(monkeypatch, tmp_path):
    """A sandbox HOME whose Documents folder we can really refuse, with the
    launcher reporting macOS so the note carries the TCC remedy."""
    import desktop_launcher

    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Downloads").mkdir()
    data = tmp_path / "data"
    monkeypatch.setattr(desktop_launcher.Path, "home", lambda: home)
    monkeypatch.setattr(desktop_launcher, "get_data_dir", lambda: data)
    monkeypatch.setattr(desktop_launcher.sys, "platform", "darwin")
    yield home, data
    # Put the bits back or tmp_path cleanup cannot remove the tree.
    for p in (
        home / "Documents" / "SlowBooks Pro" / "Reports",
        home / "Documents",
        home / "Downloads",
    ):
        if p.exists():
            p.chmod(0o700)


def test_consent_granted_writes_to_documents(mac_home):
    import desktop_launcher

    home, _ = mac_home
    dest, note = desktop_launcher._save_report("Balance Sheet.pdf", b"%PDF-1.7")

    assert note is None
    assert (
        dest == home / "Documents" / "SlowBooks Pro" / "Reports" / "Balance Sheet.pdf"
    )
    assert dest.read_bytes() == b"%PDF-1.7"


def test_denied_on_first_save_falls_back_and_explains(mac_home):
    """Consent refused before the Reports folder exists: mkdir() raises."""
    import desktop_launcher

    home, data = mac_home
    docs = home / "Documents"
    refused = docs / "SlowBooks Pro" / "Reports"
    docs.chmod(0o500)

    dest, note = desktop_launcher._save_report("Balance Sheet.pdf", b"%PDF-1.7")

    assert dest == data / "Reports" / "Balance Sheet.pdf"
    assert dest.read_bytes() == b"%PDF-1.7"
    # The note names the folder that refused, where the file went instead,
    # and the System Settings pane that grants it.
    assert str(refused) in note
    assert str(dest.parent) in note
    assert "Privacy & Security" in note
    assert "Files and Folders" in note
    assert "Documents Folder" in note
    # Nothing was left behind in the refused tree.
    assert not (docs / "SlowBooks Pro").exists()


def test_denied_after_folder_exists_falls_back(mac_home):
    """Consent revoked after a successful save: mkdir(exist_ok=True) passes
    and the byte write is the call that is refused."""
    import desktop_launcher

    home, data = mac_home
    reports = home / "Documents" / "SlowBooks Pro" / "Reports"
    reports.mkdir(parents=True)
    reports.chmod(0o500)

    dest, note = desktop_launcher._save_report("Statement.pdf", b"%PDF-revoked")

    assert dest == data / "Reports" / "Statement.pdf"
    assert dest.read_bytes() == b"%PDF-revoked"
    assert note and str(reports) in note
    assert not (reports / "Statement.pdf").exists()


def test_non_permission_failure_is_not_reported_as_a_denial(mac_home):
    """Only a refusal gets the fallback. Anything else propagates, so a real
    bug is never dressed up as a consent problem."""
    import desktop_launcher

    with pytest.raises(Exception) as exc:
        desktop_launcher._save_report("bad\0name.pdf", b"%PDF-1.7")
    assert not isinstance(exc.value, PermissionError)


def test_backup_answers_a_refused_downloads_folder(mac_home, monkeypatch, tmp_path):
    """Save backup keeps the file it already has and says where it is."""
    import desktop_launcher
    from app.services import backup_service

    home, _ = mac_home
    backups = tmp_path / "backups"
    backups.mkdir()
    src = backups / "slowbooks_20260101_000000.db"
    src.write_bytes(b"company")
    monkeypatch.setattr(backup_service, "BACKUP_DIR", backups)

    (home / "Downloads").chmod(0o500)
    result = desktop_launcher.PickerApi(3001).save_backup_file(src.name)

    assert result["success"] is False
    assert str(home / "Downloads") in result["error"]
    assert str(src) in result["error"]
    assert "Files and Folders" in result["error"]
    assert src.read_bytes() == b"company"
