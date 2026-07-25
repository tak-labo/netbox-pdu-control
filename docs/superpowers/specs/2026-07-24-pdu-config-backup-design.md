# PDU Config Backup — Design Spec

## Overview

Add the ability to save a snapshot of a PDU's actual on-device configuration (as
opposed to NetBox's own connection/metadata fields).

Primary storage is NetBox's own `Device.local_context_data` (native JSONField —
no new model, no migration for the data itself) — this makes the snapshot visible
in the standard "Config Context" tab, and every save is automatically recorded by
NetBox's built-in Change Log (`core.ObjectChange`), giving before/after diffing for
free. This also sets up cleanly for a possible future extension: diffing the saved
snapshot against a NetBox `ConfigTemplate`-rendered "desired config" — both would
live in the same place NetBox already uses for config-context data.

Git backup is an **optional, opt-in second write**: if `config_backup_path` is
configured, every save is *also* committed to a local git repo, for people who want
unlimited-retention history browsable via `git log`/`git diff` (Change Log's
`CHANGELOG_RETENTION` defaults to 90 days). If unset, the git step is skipped
entirely — no error, no directory created.

## Scope

- **In scope:** Raritan backend only (`RaritanPDUClient.get_full_config()`), manual
  "Save Config" button on the PDU detail page, periodic automatic backup job,
  `local_context_data` write (always), optional git commit (only if configured).
- **Out of scope:** UniFi backend (`get_full_config()` not implemented — no UniFi PDU
  is registered in this environment yet), remote git push (e.g. to Forgejo/GitLab),
  in-NetBox diff/restore UI, comparison against `ConfigTemplate`, `DataSource`
  integration for browsing git content without SSH (git's own history isn't visible
  in NetBox either way — `DataSource` only mirrors the *latest* file content, so it
  wouldn't add historical diffing; can be revisited later as a pure convenience view).

## Config snapshot shape (Raritan)

Confirmed against a live PDU (`pdu01`, PX3-5138JR). `get_full_config()` returns:

```json
{
  "pdu": {
    "name": "pdu01", "startupState": 2, "cycleDelay": 10, "inRushGuardDelay": 200,
    "outletPowerStateSequence": [], "powerOnDelay": 3, "latchingRelays": false,
    "energyPulseEnabled": false, "energyPulsesPerKWh": 10000,
    "demandUpdateInterval": 60, "demandAveragingIntervals": 10,
    "suspendTripCauseOutlets": true, "inhibitRelayControl": false
  },
  "network": {
    "model": "PX3-5138JR", "serial_number": "RJL7800097",
    "firmware_version": "4.3.13.5-52458", "pdu_name": "pdu01",
    "rated_voltage": "100V", "rated_current": "15A", "rated_frequency": "50/60Hz",
    "rated_power": "1.5kVA", "hw_revision": "0x03",
    "pdu_mac_address": "00:0d:5d:11:f0:b8", "dns_servers": "192.168.200.1",
    "default_gateway": "192.168.200.1", "ntp_servers": "192.168.90.11",
    "network_interfaces": [
      {"name": "ETHERNET", "mac_address": "00:0d:5d:11:f0:b8",
       "ip_address": "192.168.200.11", "config_method": "DHCP", "link_speed": "100M Full"}
    ]
  },
  "outlets": [
    {"outlet_number": 1, "settings": {"name": "pbs", "startupState": 3,
      "usePduCycleDelay": true, "cycleDelay": 10, "nonCritical": false, "sequenceDelay": 0}}
  ],
  "inlets": [
    {"inlet_number": 1, "settings": {"name": "feed-1"}}
  ]
}
```

`network` reuses `get_pdu_info()` minus `device_time_epoch` (changes every call, would
make every snapshot look different for no reason).

## Components

### 1. `backends/base.py` — new method

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

### 2. `backends/raritan.py` — implementation

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

No change to `unifi.py` — it inherits the default `get_full_config()` that raises
`PDUClientError`; the save-config view/job treat that as a normal, reportable failure.

### 3. `models.py` — new fields on `ManagedPDU`

```python
last_config_saved = models.DateTimeField(
    null=True, blank=True, verbose_name=_("Last Config Saved"),
)
config_backup_enabled = models.BooleanField(
    default=True, verbose_name=_("Config Backup Enabled"),
    help_text=_("Include this PDU in periodic config backup jobs"),
)
```

Add `"config_backup_enabled"` to `clone_fields`. New migration `0011_managedpdu_config_backup.py`.

### 4. `config_backup.py` — new module

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
    git_committed: bool | None  # None = git backup not configured
    git_error: str | None = None


def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True, text=True, timeout=30,
    )


def _commit_to_git(repo_path: str, filename: str, config: dict) -> bool:
    """Write the snapshot into a local git repo and commit if changed. Returns True if committed."""
    repo_path = Path(repo_path)
    repo_path.mkdir(parents=True, exist_ok=True)

    if not (repo_path / ".git").exists():
        result = _run_git(repo_path, "init")
        if result.returncode != 0:
            raise RuntimeError(f"git init failed: {result.stderr}")

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
        except RuntimeError as e:
            result.git_committed = False
            result.git_error = str(e)
            logger.error("Git config backup failed [%s]: %s", managed_pdu, e)

    managed_pdu.last_config_saved = timezone.now()
    managed_pdu.save(update_fields=["last_config_saved"])

    return result
```

`device.name` is slugified for the git filename (NetBox's `Device` model has no
`slug` field, unlike `DeviceType`; `name` is what's actually unique/human-readable).

### 5. `views.py` — new `ManagedPDUSaveConfigView`

Mirrors `ManagedPDUSyncView`:

```python
@register_model_view(models.ManagedPDU, name="save_config")
class ManagedPDUSaveConfigView(View):
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
        except PDUClientError as e:
            messages.error(request, f"Config save error: {e}")
            logger.error("Config save failed [%s]: %s", managed_pdu, e)

        return redirect(managed_pdu.get_absolute_url())
```

### 6. `urls.py` — new route

```python
path(
    "managed-pdus/<int:pk>/save-config/",
    views.ManagedPDUSaveConfigView.as_view(),
    name="managedpdu_save_config",
),
```

### 7. `templates/netbox_pdu_control/managedpdu.html` — new button

Added next to the existing Sync/Get Metrics buttons (~line 156):

```html
<form method="post" action="{% url 'plugins:netbox_pdu_control:managedpdu_save_config' pk=object.pk %}" class="d-inline">
  {% csrf_token %}
  <button type="submit" class="btn btn-outline-secondary btn-sm">
    <i class="mdi mdi-content-save"></i> Save Config
  </button>
</form>
```

And a "Last Config Saved" row next to "Last Synced" (~line 174). Actual snapshot
content isn't rendered on this page — users click through to the Device's own
"Config Context" tab (existing NetBox core view) to see it.

### 8. `jobs.py` — new periodic job

Same pattern as `PDUSyncJob` / `PDUGetMetricsJob`:

```python
_config_backup_interval = _plugin_config.get("config_backup_poll_interval", 0)

if _metrics_interval > 0 or _sync_interval > 0 or _config_backup_interval > 0:
    from netbox.jobs import JobFailed, JobRunner, system_job

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

### 9. `README.md` — document new PLUGINS_CONFIG keys

```python
PLUGINS_CONFIG = {
    "netbox_pdu_control": {
        # ... existing keys ...
        # Optional: directory for an additional local git-backed PDU config backup,
        # for unlimited-retention history beyond NetBox's own Change Log (which
        # defaults to 90 days via the CHANGELOG_RETENTION admin setting). The plugin
        # runs `git init` here automatically on first use. Config is always saved to
        # the PDU's Device > Config Context regardless of this setting.
        # "config_backup_path": "/opt/netbox/pdu-config-backups",
        # Interval in minutes for automatic config backup. Set to 0 or remove to disable.
        "config_backup_poll_interval": 0,
    }
}
```

## Data Flow (manual save)

1. User clicks "Save Config" on the PDU detail page
2. POST to `/managed-pdus/<pk>/save-config/`
3. View checks `change_managedpdu` permission
4. `save_config_backup()` calls `client.get_full_config()` (Raritan: 1 + 2N RPC calls
   for N outlets/inlets, reusing existing RID-listing helpers)
5. Writes `managed_pdu.device.local_context_data` — NetBox's Change Log records the
   diff automatically
6. If `config_backup_path` is set: also writes/commits the same JSON to the git repo
   (skipped silently if unset)
7. `managed_pdu.last_config_saved` updated
8. Flash message: NetBox-only save vs NetBox+git save vs git-failed-but-NetBox-ok vs
   fetch error
9. Redirect back to PDU detail page

Periodic job does the same via `PDUConfigBackupJob`, without `request` (uses the
service account for `netbox-secrets`, same as `PDUSyncJob`).

## Error Handling

- No permission → error message, no fetch attempted
- Backend doesn't implement `get_full_config()` (UniFi) → `PDUClientError`, same
  error-message/logging path as any other PDU communication failure — nothing is saved
- `local_context_data` save fails → propagates as a normal Django/DB error (not
  expected in practice; no special handling)
- `config_backup_path` unset → git step silently skipped, NetBox save still succeeds
- git `init`/`commit` failure → caught, surfaced as a warning message alongside the
  successful NetBox save (doesn't fail the whole operation)
- No changes since last snapshot (git side) → not an error, no commit made,
  `git_committed=False`

## Permissions

Uses existing `change_managedpdu` permission — no new permissions needed.

## Testing

- `RaritanPDUClient.get_full_config()`: mock JSON-RPC responses, assert the combined
  dict shape (pdu/network/outlets/inlets)
- `save_config_backup()`:
  - without `config_backup_path` configured: `device.local_context_data` updated,
    `result.git_committed is None`, no filesystem/git calls made
  - with `config_backup_path` set to `tmp_path`: `local_context_data` updated *and*
    git repo initialized/committed, `result.git_committed is True`
  - second call with identical config: `local_context_data` still updated (Change Log
    will show no diff), git `result.git_committed is False`
  - git command failure (e.g. mock `subprocess.run` to fail): `local_context_data`
    still saved, `result.git_error` set, no exception raised
- `ManagedPDUSaveConfigView`: permission check, each message-path above
- `PDUConfigBackupJob`: iterates only `config_backup_enabled=True` PDUs, failure in
  one PDU doesn't stop others
