# PDU Config Backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users save a snapshot of a Raritan PDU's actual on-device configuration into NetBox (`Device.local_context_data`) and, optionally, a local git repo, via a manual button and a periodic job.

**Architecture:** `RaritanPDUClient.get_full_config()` fetches raw vendor JSON (PDU/outlet/inlet settings) over the existing JSON-RPC backend. `config_backup.py` writes it to `Device.local_context_data` (always) and to a git repo (only if `config_backup_path` is configured in `PLUGINS_CONFIG`). A view and a `system_job`-based periodic job both call the same `save_config_backup()` function, mirroring the existing `sync_managed_pdu()` / `ManagedPDUSyncView` / `PDUSyncJob` pattern already in this codebase.

**Tech Stack:** Django 5 (via NetBox 4.6), Python 3.12+, `requests` (existing dep only — no new dependencies), stdlib `subprocess` for git, `pytest` for backend-only unit tests, Django `TestCase` (via `manage.py test`) for model/view tests.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-pdu-config-backup-design.md` (already committed on `release/0.5.0`)
- All work happens on host `netbox-dev.test1` (ssh), repo at `/opt/netbox/netbox-pdu-control`, already on branch `release/0.5.0`
- Backend-only tests (no Django): `cd /opt/netbox/netbox-pdu-control && python3 -m pytest netbox_pdu_control/tests/test_backends_raritan.py -v` (works outside Docker per `conftest.py` mock injection)
- Model/view/job tests (need Django/DB): `cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests.<module> -v2`
- No new pip dependency — `git` CLI is already installed on the host; use stdlib `subprocess`
- UniFi backend is explicitly **out of scope** — do not touch `backends/unifi.py`
- Every commit is local only (`git commit`, no `git push`) — this repo tracks `origin/release/0.5.0` and pushing is a separate, explicit decision for the user
- Follow existing code style: `ruff` line-length 120, target py312. Run `ruff check .` before each commit if convenient (existing CI will catch it either way)

---

### Task 1: `get_full_config()` on the Raritan backend

**Files:**
- Modify: `netbox_pdu_control/backends/base.py` (add method to `BasePDUClient`, after `get_inlet_thresholds`, i.e. at end of class)
- Modify: `netbox_pdu_control/backends/raritan.py` (add method to `RaritanPDUClient`, near `get_pdu_info`)
- Test: `netbox_pdu_control/tests/test_backends_raritan.py`

**Interfaces:**
- Produces: `BasePDUClient.get_full_config(self) -> dict` (default raises `PDUClientError`), overridden in `RaritanPDUClient.get_full_config(self) -> dict` returning `{"pdu": dict, "network": dict, "outlets": list[dict], "inlets": list[dict]}`. Later tasks call this via `client.get_full_config()`.

- [ ] **Step 1: Write the failing tests**

Append to `netbox_pdu_control/tests/test_backends_raritan.py`:

```python
class TestGetFullConfig(unittest.TestCase):
    """Tests for get_full_config()."""

    def setUp(self):
        self.client = _make_client()

    def test_combines_pdu_outlet_inlet_settings(self):
        """get_full_config() aggregates PDU/outlet/inlet settings into one dict."""

        def fake_rpc(path, method, params=None):
            if path == "/model/pdu/0" and method == "getSettings":
                return {"name": "pdu01", "cycleDelay": 10}
            if path == "/model/pdu/0" and method == "getOutlets":
                return [{"rid": "/outlet/1"}]
            if path == "/model/pdu/0" and method == "getInlets":
                return [{"rid": "/inlet/1"}]
            if path == "/outlet/1" and method == "getSettings":
                return {"name": "server-a", "startupState": 3}
            if path == "/inlet/1" and method == "getSettings":
                return {"name": "feed-1"}
            raise AssertionError(f"unexpected rpc call: {path} {method}")

        with patch.object(self.client, "_rpc", side_effect=fake_rpc), patch.object(
            self.client, "get_pdu_info"
        ) as mock_info:
            mock_info.return_value = {
                "model": "PX3-5138JR",
                "serial_number": "ABC123",
                "device_time_epoch": 1784930535,
            }
            config = self.client.get_full_config()

        self.assertEqual(config["pdu"], {"name": "pdu01", "cycleDelay": 10})
        self.assertEqual(config["network"], {"model": "PX3-5138JR", "serial_number": "ABC123"})
        self.assertEqual(config["outlets"], [{"outlet_number": 1, "settings": {"name": "server-a", "startupState": 3}}])
        self.assertEqual(config["inlets"], [{"inlet_number": 1, "settings": {"name": "feed-1"}}])

    def test_excludes_device_time_epoch_from_network(self):
        """device_time_epoch is dropped since it changes on every call."""
        with patch.object(self.client, "_rpc", return_value={}), patch.object(
            self.client, "get_pdu_info", return_value={"model": "X", "device_time_epoch": 123}
        ):
            config = self.client.get_full_config()

        self.assertNotIn("device_time_epoch", config["network"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/netbox/netbox-pdu-control && python3 -m pytest netbox_pdu_control/tests/test_backends_raritan.py::TestGetFullConfig -v`
Expected: FAIL with `AttributeError: 'RaritanPDUClient' object has no attribute 'get_full_config'`

- [ ] **Step 3: Add the default method to `BasePDUClient`**

In `netbox_pdu_control/backends/base.py`, add after the `get_inlet_thresholds` method (end of class body):

```python
    def get_full_config(self) -> dict:
        """
        Return the PDU's on-device configuration as a JSON-serializable dict, for
        backup/history purposes. Shape is vendor-specific (raw settings passthrough).

        Default implementation raises PDUClientError (vendor not supported).
        Override in backends that support it.
        """
        raise PDUClientError("This backend does not support config backup")
```

- [ ] **Step 4: Implement it in `RaritanPDUClient`**

In `netbox_pdu_control/backends/raritan.py`, add near `get_pdu_info` (e.g. directly after it):

```python
    def get_full_config(self) -> dict:
        pdu_settings = self._rpc("/model/pdu/0", "getSettings") or {}

        info = self.get_pdu_info()
        network = {k: v for k, v in info.items() if k != "device_time_epoch"}

        outlets = []
        for i, rid in enumerate(self._get_outlet_rids()):
            settings = self._rpc(rid, "getSettings") or {}
            outlets.append({"outlet_number": i + 1, "settings": settings})

        inlets = []
        for i, rid in enumerate(self._get_inlet_rids()):
            settings = self._rpc(rid, "getSettings") or {}
            inlets.append({"inlet_number": i + 1, "settings": settings})

        return {"pdu": pdu_settings, "network": network, "outlets": outlets, "inlets": inlets}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /opt/netbox/netbox-pdu-control && python3 -m pytest netbox_pdu_control/tests/test_backends_raritan.py -v`
Expected: all PASS, including the two new tests

- [ ] **Step 6: Commit**

```bash
ssh netbox-dev.test1 'cd /opt/netbox/netbox-pdu-control && git add netbox_pdu_control/backends/base.py netbox_pdu_control/backends/raritan.py netbox_pdu_control/tests/test_backends_raritan.py && git commit -m "Add get_full_config() to PDU backend interface and Raritan backend"'
```

---

### Task 2: `ManagedPDU` model fields for config backup

**Files:**
- Modify: `netbox_pdu_control/models.py:18-27` (`clone_fields`), `netbox_pdu_control/models.py:96-104` (after `metrics_enabled` field)
- Create: `netbox_pdu_control/migrations/0011_managedpdu_config_backup.py`
- Test: `netbox_pdu_control/tests/test_models.py`

**Interfaces:**
- Produces: `ManagedPDU.last_config_saved` (`DateTimeField`, null/blank), `ManagedPDU.config_backup_enabled` (`BooleanField`, default `True`). Task 3/4/6 read and write these.

- [ ] **Step 1: Write the failing test**

Add to `netbox_pdu_control/tests/test_models.py`, inside `ManagedPDUModelTest` (or as a new small test appended near other field-default tests — check the file for the existing `ManagedPDUModelTest` class and add there):

```python
    def test_config_backup_defaults(self):
        """New ManagedPDU has config_backup_enabled=True and last_config_saved=None."""
        pdu = create_test_pdu()
        self.assertTrue(pdu.config_backup_enabled)
        self.assertIsNone(pdu.last_config_saved)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_models.ManagedPDUModelTest.test_config_backup_defaults -v2'`
Expected: FAIL with `AttributeError` or `TypeError: ... unexpected keyword argument` style error (field doesn't exist yet — actually since the test doesn't pass the field explicitly, expect `AttributeError: 'ManagedPDU' object has no attribute 'config_backup_enabled'`)

- [ ] **Step 3: Add the fields to the model**

In `netbox_pdu_control/models.py`, add `"config_backup_enabled"` to `clone_fields` (after `"metrics_enabled"`):

```python
    clone_fields = [
        "vendor",
        "api_url",
        "api_username",
        "api_password",
        "verify_ssl",
        "sync_enabled",
        "metrics_enabled",
        "config_backup_enabled",
        "comments",
    ]
```

Then add the two new fields directly after the existing `metrics_enabled` field block:

```python
    metrics_enabled = models.BooleanField(
        default=True,
        verbose_name=_("Metrics Enabled"),
        help_text=_("Include this PDU in periodic metrics polling jobs"),
    )
    last_config_saved = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Last Config Saved"),
    )
    config_backup_enabled = models.BooleanField(
        default=True,
        verbose_name=_("Config Backup Enabled"),
        help_text=_("Include this PDU in periodic config backup jobs"),
    )
```

- [ ] **Step 4: Generate and inspect the migration**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py makemigrations netbox_pdu_control'`

This should create `netbox_pdu_control/migrations/0011_managedpdu_config_backup_and_more.py` (or similar auto-generated name). Rename it to `0011_managedpdu_config_backup.py` and verify its contents match:

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_pdu_control", "0010_managedpdu_ip_address"),
    ]

    operations = [
        migrations.AddField(
            model_name="managedpdu",
            name="last_config_saved",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Last Config Saved"),
        ),
        migrations.AddField(
            model_name="managedpdu",
            name="config_backup_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Include this PDU in periodic config backup jobs",
                verbose_name="Config Backup Enabled",
            ),
        ),
    ]
```

If the auto-generated file differs cosmetically (field order, etc.) that's fine — what matters is both `AddField` operations are present with these exact field definitions.

- [ ] **Step 5: Apply the migration and run the test**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py migrate netbox_pdu_control'`
Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_models.ManagedPDUModelTest.test_config_backup_defaults -v2'`
Expected: PASS

- [ ] **Step 6: Run the full model test suite to check nothing broke**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_models -v2'`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
ssh netbox-dev.test1 'cd /opt/netbox/netbox-pdu-control && git add netbox_pdu_control/models.py netbox_pdu_control/migrations/0011_managedpdu_config_backup.py netbox_pdu_control/tests/test_models.py && git commit -m "Add last_config_saved and config_backup_enabled fields to ManagedPDU"'
```

---

### Task 3: `config_backup.py` — the save logic

**Files:**
- Create: `netbox_pdu_control/config_backup.py`
- Test: `netbox_pdu_control/tests/test_config_backup.py` (new file)

**Interfaces:**
- Consumes: `get_pdu_client(managed_pdu, request=None)` from `netbox_pdu_control.backends` (existing), `client.get_full_config() -> dict` (Task 1), `ManagedPDU.last_config_saved` / `.device.local_context_data` (Task 2 + NetBox core `Device`)
- Produces: `ConfigBackupResult` dataclass (`git_committed: bool | None`, `git_error: str | None`), `save_config_backup(managed_pdu, request=None) -> ConfigBackupResult`, raises `PDUClientError` on fetch failure. Task 4 and Task 6 both call `save_config_backup`.

- [ ] **Step 1: Write the failing tests**

Create `netbox_pdu_control/tests/test_config_backup.py`:

```python
"""
Tests for config_backup.py.

Run inside Docker:
  docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_config_backup -v2
"""

import subprocess
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from ..config_backup import save_config_backup
from .test_models import create_test_pdu

SAMPLE_CONFIG = {"pdu": {"name": "pdu01"}, "network": {}, "outlets": [], "inlets": []}


class SaveConfigBackupTest(TestCase):
    def setUp(self):
        self.pdu = create_test_pdu()

    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_always_saves_to_local_context_data(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client

        result = save_config_backup(self.pdu)

        self.pdu.device.refresh_from_db()
        self.assertEqual(self.pdu.device.local_context_data, SAMPLE_CONFIG)
        self.assertIsNone(result.git_committed)
        self.assertIsNone(result.git_error)

    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_updates_last_config_saved(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client

        self.assertIsNone(self.pdu.last_config_saved)
        save_config_backup(self.pdu)
        self.pdu.refresh_from_db()
        self.assertIsNotNone(self.pdu.last_config_saved)

    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_without_config_backup_path_skips_git(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client

        with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {}}):
            with patch("netbox_pdu_control.config_backup.subprocess.run") as mock_run:
                result = save_config_backup(self.pdu)

        mock_run.assert_not_called()
        self.assertIsNone(result.git_committed)

    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_with_config_backup_path_commits_to_git(self, mock_get_client, tmp_path=None):
        import tempfile

        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": tmpdir}}):
                result = save_config_backup(self.pdu)

            self.assertTrue(result.git_committed)
            self.assertIsNone(result.git_error)

            log = subprocess.run(
                ["git", "-C", tmpdir, "log", "--oneline"], capture_output=True, text=True
            )
            self.assertEqual(len(log.stdout.strip().splitlines()), 1)

    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_second_call_with_same_config_does_not_recommit(self, mock_get_client):
        import tempfile

        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": tmpdir}}):
                first = save_config_backup(self.pdu)
                second = save_config_backup(self.pdu)

            self.assertTrue(first.git_committed)
            self.assertFalse(second.git_committed)

            log = subprocess.run(
                ["git", "-C", tmpdir, "log", "--oneline"], capture_output=True, text=True
            )
            self.assertEqual(len(log.stdout.strip().splitlines()), 1)

    @patch("netbox_pdu_control.config_backup._commit_to_git")
    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_git_failure_does_not_raise_and_still_saves_context(self, mock_get_client, mock_commit):
        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client
        mock_commit.side_effect = RuntimeError("git commit failed: no permission")

        with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": "/some/path"}}):
            result = save_config_backup(self.pdu)

        self.pdu.device.refresh_from_db()
        self.assertEqual(self.pdu.device.local_context_data, SAMPLE_CONFIG)
        self.assertFalse(result.git_committed)
        self.assertIn("no permission", result.git_error)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_config_backup -v2'`
Expected: FAIL with `ModuleNotFoundError: No module named 'netbox_pdu_control.config_backup'`

- [ ] **Step 3: Implement `config_backup.py`**

Create `netbox_pdu_control/config_backup.py`:

```python
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

from .backends import get_pdu_client

logger = logging.getLogger(__name__)


@dataclass
class ConfigBackupResult:
    """Outcome of save_config_backup(). git_committed is None when git backup is not configured."""

    git_committed: bool | None
    git_error: str | None = None


def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _commit_to_git(repo_path: str, filename: str, config: dict) -> bool:
    """Write the snapshot into a local git repo and commit if changed. Returns True if committed."""
    repo_path = Path(repo_path)
    repo_path.mkdir(parents=True, exist_ok=True)

    if not (repo_path / ".git").exists():
        result = _run_git(repo_path, "init")
        if result.returncode != 0:
            raise RuntimeError(f"git init failed: {result.stderr}")
        _run_git(repo_path, "config", "user.email", "netbox-pdu-control@localhost")
        _run_git(repo_path, "config", "user.name", "netbox-pdu-control")

    file_path = repo_path / filename
    file_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")

    _run_git(repo_path, "add", filename)
    status = _run_git(repo_path, "status", "--porcelain", "--", filename)
    if not status.stdout.strip():
        return False

    commit = _run_git(repo_path, "commit", "-m", f"Update config for {filename}")
    if commit.returncode != 0:
        raise RuntimeError(f"git commit failed: {commit.stderr}")
    return True


def save_config_backup(managed_pdu, request=None) -> ConfigBackupResult:
    """
    Fetch the PDU's full config and save it.

    Always writes to `managed_pdu.device.local_context_data` (visible in NetBox's
    Config Context tab; history via NetBox's own Change Log). Additionally commits
    to a local git repo if `config_backup_path` is set in PLUGINS_CONFIG.

    Raises PDUClientError on fetch failure (backend not supported, connection error).
    Git failures are captured in the returned result rather than raised, since the
    primary (local_context_data) save already succeeded by that point.
    """
    client = get_pdu_client(managed_pdu, request=request)
    config = client.get_full_config()

    managed_pdu.device.local_context_data = config
    managed_pdu.device.save(update_fields=["local_context_data"])

    plugin_config = settings.PLUGINS_CONFIG.get("netbox_pdu_control", {})
    repo_path = plugin_config.get("config_backup_path")

    result = ConfigBackupResult(git_committed=None)
    if repo_path:
        filename = f"{slugify(managed_pdu.device.name)}.json"
        try:
            result.git_committed = _commit_to_git(repo_path, filename, config)
        except (RuntimeError, OSError) as e:
            result.git_committed = False
            result.git_error = str(e)
            logger.error("Git config backup failed [%s]: %s", managed_pdu, e)

    managed_pdu.last_config_saved = timezone.now()
    managed_pdu.save(update_fields=["last_config_saved"])

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_config_backup -v2'`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
ssh netbox-dev.test1 'cd /opt/netbox/netbox-pdu-control && git add netbox_pdu_control/config_backup.py netbox_pdu_control/tests/test_config_backup.py && git commit -m "Add save_config_backup(): local_context_data + optional git backup"'
```

---

### Task 4: `ManagedPDUSaveConfigView` + URL route

**Files:**
- Modify: `netbox_pdu_control/views.py` (add view after `ManagedPDUGetMetricsView`, ~line 247)
- Modify: `netbox_pdu_control/urls.py` (add route after `managedpdu_get_metrics`)
- Test: `netbox_pdu_control/tests/test_views.py`

**Interfaces:**
- Consumes: `save_config_backup(managed_pdu, request=None) -> ConfigBackupResult` (Task 3)
- Produces: URL name `plugins:netbox_pdu_control:managedpdu_save_config` (used by Task 5's template button)

- [ ] **Step 1: Write the failing tests**

Add to `netbox_pdu_control/tests/test_views.py` (append as a new class; check the top of the file for existing imports of `create_test_pdu`, `PluginViewTestCase`, `reverse`, `patch`, `MagicMock` and reuse them):

```python
class ManagedPDUSaveConfigViewTest(PluginViewTestCase):
    """Tests for ManagedPDUSaveConfigView."""

    @classmethod
    def setUpTestData(cls):
        cls.pdu = create_test_pdu()

    def _url(self):
        return reverse("plugins:netbox_pdu_control:managedpdu_save_config", kwargs={"pk": self.pdu.pk})

    def test_without_permission_redirects(self):
        response = self.client.post(self._url())
        self.assertHttpStatus(response, 302)

    def test_without_permission_does_not_call_backend(self):
        with patch("netbox_pdu_control.views.save_config_backup") as mock_save:
            self.client.post(self._url())
            mock_save.assert_not_called()

    @patch("netbox_pdu_control.views.save_config_backup")
    def test_success_saves_and_redirects(self, mock_save):
        from netbox_pdu_control.config_backup import ConfigBackupResult

        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_save.return_value = ConfigBackupResult(git_committed=None)

        response = self.client.post(self._url(), follow=True)

        self.assertHttpStatus(response, 200)
        mock_save.assert_called_once()
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("saved to NetBox" in m for m in messages))

    @patch("netbox_pdu_control.views.save_config_backup")
    def test_git_committed_shows_git_in_message(self, mock_save):
        from netbox_pdu_control.config_backup import ConfigBackupResult

        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_save.return_value = ConfigBackupResult(git_committed=True)

        response = self.client.post(self._url(), follow=True)

        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("git commit" in m for m in messages))

    @patch("netbox_pdu_control.views.save_config_backup")
    def test_git_error_shows_warning(self, mock_save):
        from netbox_pdu_control.config_backup import ConfigBackupResult

        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_save.return_value = ConfigBackupResult(git_committed=False, git_error="git commit failed: boom")

        response = self.client.post(self._url(), follow=True)

        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("git backup failed" in m for m in messages))

    @patch("netbox_pdu_control.views.save_config_backup")
    def test_fetch_error_shows_error_message(self, mock_save):
        from netbox_pdu_control.backends.base import PDUClientError

        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_save.side_effect = PDUClientError("connection refused")

        response = self.client.post(self._url(), follow=True)

        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("connection refused" in m for m in messages))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_views.ManagedPDUSaveConfigViewTest -v2'`
Expected: FAIL — `NoReverseMatch` (URL name doesn't exist yet)

- [ ] **Step 3: Add the view**

In `netbox_pdu_control/views.py`, add after `ManagedPDUGetMetricsView` (check the top of the file for its existing imports of `logger`, `messages`, `redirect`, `get_object_or_404`, `View`, `_`, and reuse them — do not re-import):

```python
@register_model_view(models.ManagedPDU, name="save_config")
class ManagedPDUSaveConfigView(View):
    """
    View that saves the PDU's on-device config to NetBox (Device.local_context_data)
    and, if configured, a local git backup repo. Accepts POST requests only.
    """

    def post(self, request, pk):
        managed_pdu = get_object_or_404(models.ManagedPDU, pk=pk)

        if not request.user.has_perm("netbox_pdu_control.change_managedpdu"):
            messages.error(request, _("You do not have permission to save config for this PDU."))
            return redirect(managed_pdu.get_absolute_url())

        try:
            result = save_config_backup(managed_pdu, request=request)
            if result.git_error:
                messages.warning(request, f"Config saved to NetBox, but git backup failed: {result.git_error}")
            elif result.git_committed:
                messages.success(request, "Config saved (NetBox + git commit).")
            else:
                messages.success(request, "Config saved to NetBox.")
            logger.info("Config save succeeded [%s]: git_committed=%s", managed_pdu, result.git_committed)
        except PDUClientError as e:
            messages.error(request, f"Config save error: {e}")
            logger.error("Config save failed [%s]: %s", managed_pdu, e)

        return redirect(managed_pdu.get_absolute_url())
```

Add the import at the top of `views.py` (near the other imports from `.config_backup` / local modules — add alongside existing `from .jobs import ...`-style imports):

```python
from .config_backup import save_config_backup
```

- [ ] **Step 4: Add the URL route**

In `netbox_pdu_control/urls.py`, add after the `managedpdu_get_metrics` path:

```python
    path(
        "managed-pdus/<int:pk>/save-config/",
        views.ManagedPDUSaveConfigView.as_view(),
        name="managedpdu_save_config",
    ),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_views.ManagedPDUSaveConfigViewTest -v2'`
Expected: all PASS

- [ ] **Step 6: Run the full view test suite to check nothing broke**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_views -v2'`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
ssh netbox-dev.test1 'cd /opt/netbox/netbox-pdu-control && git add netbox_pdu_control/views.py netbox_pdu_control/urls.py netbox_pdu_control/tests/test_views.py && git commit -m "Add ManagedPDUSaveConfigView and save-config URL route"'
```

---

### Task 5: "Save Config" button and "Last Config Saved" row in the template

**Files:**
- Modify: `netbox_pdu_control/templates/netbox_pdu_control/managedpdu.html` (button near line 156, status row near line 174)
- Test: `netbox_pdu_control/tests/test_views.py` (extend `ManagedPDUViewTest.test_detail_view`)

**Interfaces:**
- Consumes: URL name `managedpdu_save_config` (Task 4), `object.last_config_saved` (Task 2)

- [ ] **Step 1: Write the failing test**

In `netbox_pdu_control/tests/test_views.py`, find `ManagedPDUViewTest.test_detail_view` and add a new test method in the same class:

```python
    def test_detail_view_shows_save_config_button(self):
        self.add_permissions("netbox_pdu_control.view_managedpdu")
        response = self.client.get(reverse("plugins:netbox_pdu_control:managedpdu", kwargs={"pk": self.pdu.pk}))
        self.assertContains(response, "managedpdu_save_config")
        self.assertContains(response, "Save Config")
```

(Check the existing `test_detail_view` method in this class for the exact `self.pdu` attribute name and URL-reverse pattern already used in `setUpTestData` — reuse it rather than redefining.)

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_views.ManagedPDUViewTest.test_detail_view_shows_save_config_button -v2'`
Expected: FAIL — response does not contain "Save Config"

- [ ] **Step 3: Add the button to the template**

In `netbox_pdu_control/templates/netbox_pdu_control/managedpdu.html`, locate the existing block around line 150-160:

```html
            <form method="post" action="{% url 'plugins:netbox_pdu_control:managedpdu_sync' pk=object.pk %}" class="d-inline">
              {% csrf_token %}
              <button type="submit" class="btn btn-primary btn-sm">
                <i class="mdi mdi-refresh"></i> Sync PDU
              </button>
            </form>
            <form method="post" action="{% url 'plugins:netbox_pdu_control:managedpdu_get_metrics' pk=object.pk %}" class="d-inline">
              {% csrf_token %}
              <button type="submit" class="btn btn-outline-primary btn-sm">
```

Add immediately after the `Get Metrics` form's closing `</form>` tag:

```html
            <form method="post" action="{% url 'plugins:netbox_pdu_control:managedpdu_save_config' pk=object.pk %}" class="d-inline">
              {% csrf_token %}
              <button type="submit" class="btn btn-outline-secondary btn-sm">
                <i class="mdi mdi-content-save"></i> Save Config
              </button>
            </form>
```

Then locate the "Last Synced" row (around line 173-174):

```html
          <tr>
            <th scope="row">Last Synced</th>
            <td>{{ object.last_synced|placeholder }}</td>
          </tr>
```

Add a new row immediately after it:

```html
          <tr>
            <th scope="row">Last Config Saved</th>
            <td>{{ object.last_config_saved|placeholder }}</td>
          </tr>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_views.ManagedPDUViewTest -v2'`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
ssh netbox-dev.test1 'cd /opt/netbox/netbox-pdu-control && git add netbox_pdu_control/templates/netbox_pdu_control/managedpdu.html netbox_pdu_control/tests/test_views.py && git commit -m "Add Save Config button and Last Config Saved row to PDU detail page"'
```

---

### Task 6: Periodic `PDUConfigBackupJob` + README documentation

**Files:**
- Modify: `netbox_pdu_control/jobs.py` (add job after `PDUSyncJob`, at end of file)
- Modify: `README.md` (document new `PLUGINS_CONFIG` key, in the existing `PLUGINS_CONFIG` example block)

**Interfaces:**
- Consumes: `save_config_backup(managed_pdu) -> ConfigBackupResult` (Task 3), `ManagedPDU.objects.filter(config_backup_enabled=True)` (Task 2)
- Produces: `PDUConfigBackupJob` class, registered via `@system_job(interval=...)` only when `config_backup_poll_interval > 0` — same convention as `PDUSyncJob`/`PDUGetMetricsJob`, which also have no dedicated unit tests in this codebase (see `Global Constraints`), so no new test file is added here; correctness is covered by Task 3's tests of `save_config_backup` itself.

- [ ] **Step 1: Add the job to `jobs.py`**

At the end of `netbox_pdu_control/jobs.py`, after the existing `_sync_interval = ...` line and before the `if _metrics_interval > 0 or _sync_interval > 0:` block, add:

```python
_config_backup_interval = _plugin_config.get("config_backup_poll_interval", 0)
```

Change the existing conditional import line from:

```python
if _metrics_interval > 0 or _sync_interval > 0:
    from netbox.jobs import JobFailed, JobRunner, system_job
```

to:

```python
if _metrics_interval > 0 or _sync_interval > 0 or _config_backup_interval > 0:
    from netbox.jobs import JobFailed, JobRunner, system_job
```

Then append at the very end of the file (after the existing `PDUSyncJob` class):

```python
if _config_backup_interval > 0:

    @system_job(interval=_config_backup_interval)
    class PDUConfigBackupJob(JobRunner):
        class Meta:
            name = "PDU Config Backup"

        def run(self, *args, **kwargs):
            from . import models
            from .config_backup import save_config_backup

            pdus = models.ManagedPDU.objects.filter(config_backup_enabled=True)
            success, failed = 0, 0
            for pdu in pdus:
                try:
                    result = save_config_backup(pdu)
                    self.logger.info(f"Config backup [{pdu}]: git_committed={result.git_committed}")
                    success += 1
                except Exception as e:
                    self.logger.error(f"Config backup failed [{pdu}]: {e}")
                    failed += 1
            self.logger.info(f"Periodic config backup complete: {success} OK, {failed} failed")
            if failed > 0 and success == 0:
                raise JobFailed(f"All {failed} PDU(s) failed config backup")
            elif failed > 0:
                raise Exception(f"{failed} PDU(s) failed config backup ({success} succeeded)")
```

- [ ] **Step 2: Verify the module still imports cleanly**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_config_backup netbox_pdu_control.tests.test_models netbox_pdu_control.tests.test_views -v2'`
Expected: all PASS (this indirectly confirms `jobs.py` still imports without error, since `apps.py`/plugin loading pulls it in)

- [ ] **Step 3: Update README.md**

In `README.md`, find the existing `PLUGINS_CONFIG` example block (in the `Install` section, shown earlier in this session):

```python
PLUGINS_CONFIG = {
    "netbox_pdu_control": {
        # Interval in minutes for automatic Prometheus metrics fetch.
        # Set to 0 or remove to disable periodic fetching.
        "metrics_poll_interval": 5,
        # Interval in minutes for automatic full PDU sync (hardware info, outlets, inlets).
        # Set to 0 or remove to disable periodic syncing.
        "sync_poll_interval": 60,
        # Only needed if netbox-secrets is installed and used for PDU credentials —
        # lets background/system jobs decrypt secrets without an HTTP session.
        # "service_account": "pdu-sync",
        # "service_private_key_path": "/opt/netbox/pdu-sync.pem",
    }
}
```

Replace it with (adding the two new keys before the closing brace):

```python
PLUGINS_CONFIG = {
    "netbox_pdu_control": {
        # Interval in minutes for automatic Prometheus metrics fetch.
        # Set to 0 or remove to disable periodic fetching.
        "metrics_poll_interval": 5,
        # Interval in minutes for automatic full PDU sync (hardware info, outlets, inlets).
        # Set to 0 or remove to disable periodic syncing.
        "sync_poll_interval": 60,
        # Optional: directory for an additional local git-backed PDU config backup,
        # for unlimited-retention history beyond NetBox's own Change Log (which
        # defaults to 90 days via the CHANGELOG_RETENTION admin setting). The plugin
        # runs `git init` here automatically on first use. Config is always saved to
        # the PDU's Device > Config Context regardless of this setting.
        # "config_backup_path": "/opt/netbox/pdu-config-backups",
        # Interval in minutes for automatic config backup. Set to 0 or remove to disable.
        "config_backup_poll_interval": 0,
        # Only needed if netbox-secrets is installed and used for PDU credentials —
        # lets background/system jobs decrypt secrets without an HTTP session.
        # "service_account": "pdu-sync",
        # "service_private_key_path": "/opt/netbox/pdu-sync.pem",
    }
}
```

Also add a bullet to the `## Features` list (after the existing "Background jobs" bullet):

```markdown
- **Config backup** — save the PDU's on-device configuration to NetBox (Device Config Context) and, optionally, a local git repo, via a manual button or periodic job (Raritan only)
```

- [ ] **Step 4: Commit**

```bash
ssh netbox-dev.test1 'cd /opt/netbox/netbox-pdu-control && git add netbox_pdu_control/jobs.py README.md && git commit -m "Add PDUConfigBackupJob periodic job and document config_backup_path"'
```

---

### Task 7: Full test suite + ruff, and final review

**Files:** none (verification only)

- [ ] **Step 1: Run the entire plugin test suite**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-docker && docker compose exec netbox python manage.py test netbox_pdu_control.tests -v2'`
Expected: all PASS, zero failures/errors

- [ ] **Step 2: Run the standalone (non-Django) backend tests**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-pdu-control && python3 -m pytest netbox_pdu_control/tests/test_backends_raritan.py netbox_pdu_control/tests/test_factory.py -v'`
Expected: all PASS

- [ ] **Step 3: Run ruff**

Run: `ssh netbox-dev.test1 'cd /opt/netbox/netbox-pdu-control && python3 -m ruff check netbox_pdu_control/config_backup.py netbox_pdu_control/backends/base.py netbox_pdu_control/backends/raritan.py netbox_pdu_control/models.py netbox_pdu_control/views.py netbox_pdu_control/urls.py netbox_pdu_control/jobs.py netbox_pdu_control/tests/test_config_backup.py'`
Expected: `All checks passed!` — fix any reported issues and re-run before moving on

- [ ] **Step 4: Manual smoke test via the real dev environment**

This plugin is already installed against `netbox-dev.test1`'s Docker NetBox with real Raritan PDUs registered (`pdu01`, `pdu02`, `pdu03` — see Task 3 of the design spec's live-data confirmation). After migrating:

1. Log into the NetBox UI, navigate to a `ManagedPDU` detail page (e.g. `pdu01`)
2. Click "Save Config" — expect a green "Config saved to NetBox." message
3. Navigate to the underlying Device's page → "Config Context" tab → confirm the JSON snapshot (matching the shape confirmed in the design spec) is visible
4. Navigate to the Device's "Change Log" tab → confirm a new change entry was recorded
5. Click "Save Config" again with no PDU-side changes → still succeeds (message doesn't imply an error)
6. (Optional, only if testing the git path) Set `config_backup_path` in `configuration.py`, restart, click "Save Config" again, then `ssh netbox-dev.test1 'git -C <path> log --oneline'` to confirm a commit was made

- [ ] **Step 5: Report status to the user**

Summarize: tests passing, ruff clean, manual smoke test result, and remind the user that all commits are local-only on `release/0.5.0` (not pushed) pending their decision to open a PR.
