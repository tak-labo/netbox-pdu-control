"""
Tests for config_backup.py.

Run inside Docker:
  docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_config_backup -v2
"""

import subprocess
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from ..choices import SyncStatusChoices
from ..config_backup import get_config_diff, get_config_snapshot_pair, save_config_backup
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
    def test_updates_config_backup_status_to_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client

        self.assertEqual(self.pdu.config_backup_status, SyncStatusChoices.NEVER)
        save_config_backup(self.pdu)
        self.pdu.refresh_from_db()
        self.assertEqual(self.pdu.config_backup_status, SyncStatusChoices.SUCCESS)

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
    def test_with_config_backup_path_commits_to_git(self, mock_get_client):
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

    @patch("netbox_pdu_control.config_backup._commit_to_git")
    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_git_timeout_does_not_raise_and_still_saves_context(self, mock_get_client, mock_commit):
        import subprocess as sp

        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client
        mock_commit.side_effect = sp.TimeoutExpired(cmd=["git", "commit"], timeout=30)

        with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": "/some/path"}}):
            result = save_config_backup(self.pdu)

        self.pdu.device.refresh_from_db()
        self.assertEqual(self.pdu.device.local_context_data, SAMPLE_CONFIG)
        self.assertFalse(result.git_committed)
        self.assertIsNotNone(result.git_error)

    @patch("netbox_pdu_control.config_backup.subprocess.run")
    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_missing_git_binary_does_not_raise(self, mock_get_client, mock_run):
        import tempfile

        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client
        mock_run.side_effect = FileNotFoundError("git: command not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": tmpdir}}):
                result = save_config_backup(self.pdu)

        self.pdu.device.refresh_from_db()
        self.assertEqual(self.pdu.device.local_context_data, SAMPLE_CONFIG)
        self.assertFalse(result.git_committed)
        self.assertIn("command not found", result.git_error)

    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_commit_message_records_source(self, mock_get_client):
        import tempfile

        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": tmpdir}}):
                save_config_backup(self.pdu, source="auto")

            log = subprocess.run(["git", "-C", tmpdir, "log", "-1", "--format=%s"], capture_output=True, text=True)
            self.assertIn("(auto)", log.stdout)


class GetConfigDiffTest(TestCase):
    def setUp(self):
        self.pdu = create_test_pdu()

    def test_returns_none_without_config_backup_path(self):
        with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {}}):
            self.assertIsNone(get_config_diff(self.pdu))

    def test_returns_none_without_git_repo(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": tmpdir}}):
                self.assertIsNone(get_config_diff(self.pdu))

    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_returns_none_after_single_snapshot(self, mock_get_client):
        import tempfile

        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": tmpdir}}):
                save_config_backup(self.pdu)
                self.assertIsNone(get_config_diff(self.pdu))

    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_returns_diff_after_second_snapshot(self, mock_get_client):
        import tempfile

        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": tmpdir}}):
                save_config_backup(self.pdu)
                mock_client.get_full_config.return_value = {**SAMPLE_CONFIG, "pdu": {"name": "pdu02"}}
                save_config_backup(self.pdu)

                result = get_config_diff(self.pdu)

        self.assertIsNotNone(result)
        diff, previous_date, previous_hash, current_date, current_hash = result
        self.assertIn("pdu01", diff)
        self.assertIn("pdu02", diff)
        self.assertLessEqual(previous_date, current_date)
        self.assertNotEqual(previous_hash, current_hash)
        self.assertTrue(previous_hash)
        self.assertTrue(current_hash)

    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_returns_diff_after_third_snapshot(self, mock_get_client):
        """Regression: with 3+ commits, _last_two_commits() used to return the full
        history, and unpacking it as exactly two (hash, date) pairs raised ValueError."""
        import tempfile

        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": tmpdir}}):
                save_config_backup(self.pdu)
                mock_client.get_full_config.return_value = {**SAMPLE_CONFIG, "pdu": {"name": "pdu02"}}
                save_config_backup(self.pdu)
                mock_client.get_full_config.return_value = {**SAMPLE_CONFIG, "pdu": {"name": "pdu03"}}
                save_config_backup(self.pdu)

                result = get_config_diff(self.pdu)

        self.assertIsNotNone(result)
        diff, previous_date, previous_hash, current_date, current_hash = result
        self.assertIn("pdu02", diff)
        self.assertIn("pdu03", diff)


class GetConfigSnapshotPairTest(TestCase):
    def setUp(self):
        self.pdu = create_test_pdu()

    def test_returns_none_without_config_backup_path(self):
        with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {}}):
            self.assertIsNone(get_config_snapshot_pair(self.pdu))

    def test_returns_none_without_git_repo(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": tmpdir}}):
                self.assertIsNone(get_config_snapshot_pair(self.pdu))

    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_returns_none_after_single_snapshot(self, mock_get_client):
        import tempfile

        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": tmpdir}}):
                save_config_backup(self.pdu)
                self.assertIsNone(get_config_snapshot_pair(self.pdu))

    @patch("netbox_pdu_control.config_backup.get_pdu_client")
    def test_returns_full_previous_and_current_text(self, mock_get_client):
        import tempfile

        mock_client = MagicMock()
        mock_client.get_full_config.return_value = SAMPLE_CONFIG
        mock_get_client.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(PLUGINS_CONFIG={"netbox_pdu_control": {"config_backup_path": tmpdir}}):
                save_config_backup(self.pdu)
                mock_client.get_full_config.return_value = {**SAMPLE_CONFIG, "pdu": {"name": "pdu02"}}
                save_config_backup(self.pdu)

                pair = get_config_snapshot_pair(self.pdu)

        self.assertIsNotNone(pair)
        previous_text, previous_date, previous_hash, current_text, current_date, current_hash = pair
        self.assertIn("pdu01", previous_text)
        self.assertNotIn("pdu02", previous_text)
        self.assertIn("pdu02", current_text)
        self.assertLessEqual(previous_date, current_date)
        self.assertNotEqual(previous_hash, current_hash)
