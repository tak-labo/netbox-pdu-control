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

    managed_pdu.device.snapshot()
    managed_pdu.device.local_context_data = config
    managed_pdu.device.save(update_fields=["local_context_data"])

    plugin_config = settings.PLUGINS_CONFIG.get("netbox_pdu_control", {})
    repo_path = plugin_config.get("config_backup_path")

    result = ConfigBackupResult(git_committed=None)
    if repo_path:
        filename = f"{slugify(managed_pdu.device.name)}.json"
        try:
            result.git_committed = _commit_to_git(repo_path, filename, config)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            result.git_committed = False
            result.git_error = str(e)
            logger.error("Git config backup failed [%s]: %s", managed_pdu, e)

    managed_pdu.last_config_saved = timezone.now()
    managed_pdu.save(update_fields=["last_config_saved"])

    return result
