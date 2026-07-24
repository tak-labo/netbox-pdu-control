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
