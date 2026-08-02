import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

from .backends import get_pdu_client
from .choices import SyncStatusChoices

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


def _commit_to_git(repo_path: str, filename: str, config: dict, source: str) -> bool:
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

    commit = _run_git(repo_path, "commit", "-m", f"Update config for {filename} ({source})")
    if commit.returncode != 0:
        raise RuntimeError(f"git commit failed: {commit.stderr}")
    return True


def _config_filename(managed_pdu) -> str:
    # "device" prefix makes the leading number self-explanatory (a bare
    # "3-pdu03.json" doesn't say what "3" is; "device3-pdu03.json" does).
    return f"device{managed_pdu.device.pk}-{slugify(managed_pdu.device.name)}.json"


def _config_repo_and_filename(managed_pdu) -> tuple[Path, str] | None:
    """Return (repo_path, filename) if config_backup_path is configured and the repo exists."""
    plugin_config = settings.PLUGINS_CONFIG.get("netbox_pdu_control", {})
    repo_path = plugin_config.get("config_backup_path")
    if not repo_path:
        return None

    repo_path = Path(repo_path)
    if not (repo_path / ".git").exists():
        return None

    return repo_path, _config_filename(managed_pdu)


def _last_two_commits(repo_path: Path, filename: str) -> list[tuple[str, datetime]] | None:
    """Return [(newest_hash, newest_date), (previous_hash, previous_date)] touching
    filename, or None if fewer than two commits exist. Date is the commit's author date."""
    log = _run_git(repo_path, "log", "-n", "2", "--format=%H%x09%aI", "--", filename)
    lines = log.stdout.splitlines()
    if len(lines) < 2:
        return None
    return [(h, datetime.fromisoformat(d)) for h, d in (line.split("\t") for line in lines)]


def get_config_diff(managed_pdu) -> tuple[str, datetime, str, datetime, str] | None:
    """
    Return (diff, previous_date, previous_hash, current_date, current_hash) — the git
    diff between the last two saved config snapshots for this PDU as a unified diff
    string, plus the commit date and short hash of each snapshot. Returns None if
    config_backup_path is not configured, the git repo doesn't exist yet, or fewer
    than two snapshots have been committed.
    """
    located = _config_repo_and_filename(managed_pdu)
    if located is None:
        return None
    repo_path, filename = located

    commits = _last_two_commits(repo_path, filename)
    if commits is None:
        return None
    (current_hash, current_date), (previous_hash, previous_date) = commits

    diff = _run_git(repo_path, "diff", previous_hash, current_hash, "--", filename)
    return diff.stdout, previous_date, previous_hash[:7], current_date, current_hash[:7]


def get_config_snapshot_pair(managed_pdu) -> tuple[str, datetime, str, str, datetime, str] | None:
    """
    Return (previous_text, previous_date, previous_hash, current_text, current_date,
    current_hash) — the full content, commit date and short hash of the last two saved
    config snapshots for this PDU, for a side-by-side comparison. Returns None under
    the same conditions as get_config_diff().
    """
    located = _config_repo_and_filename(managed_pdu)
    if located is None:
        return None
    repo_path, filename = located

    commits = _last_two_commits(repo_path, filename)
    if commits is None:
        return None
    (current_hash, current_date), (previous_hash, previous_date) = commits

    previous = _run_git(repo_path, "show", f"{previous_hash}:{filename}")
    current = _run_git(repo_path, "show", f"{current_hash}:{filename}")
    return previous.stdout, previous_date, previous_hash[:7], current.stdout, current_date, current_hash[:7]


def save_config_backup(managed_pdu, request=None, source: str = "manual") -> ConfigBackupResult:
    """
    Fetch the PDU's full config and save it.

    Always writes to `managed_pdu.device.local_context_data` (visible in NetBox's
    Config Context tab; history via NetBox's own Change Log). Additionally commits
    to a local git repo if `config_backup_path` is set in PLUGINS_CONFIG.

    `source` ("manual" or "auto") is recorded in the git commit message to
    distinguish button-triggered saves from the periodic backup job.

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
        filename = _config_filename(managed_pdu)
        try:
            result.git_committed = _commit_to_git(repo_path, filename, config, source)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            result.git_committed = False
            result.git_error = str(e)
            logger.error("Git config backup failed [%s]: %s", managed_pdu, e)

    managed_pdu.last_config_saved = timezone.now()
    managed_pdu.config_backup_status = SyncStatusChoices.SUCCESS
    managed_pdu.save(update_fields=["last_config_saved", "config_backup_status"])

    return result
