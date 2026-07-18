"""
Tests for netbox_pdu_control.credentials.get_credential().

Verifies the plaintext fallback path (netbox-secrets not installed / no
matching secret / decrypt failure) and the netbox-secrets success path.
Django and netbox-secrets are faked via sys.modules so these tests run in
isolation, without requiring Django to be installed/configured
(consistent with test_backends_raritan.py / test_backends_unifi.py /
test_factory.py, which also require no NetBox/DB).
"""

import builtins
import unittest
from unittest.mock import MagicMock, patch

from netbox_pdu_control.credentials import Credential, get_credential


def _mock_managed_pdu(api_username="admin", api_password="secret"):
    pdu = MagicMock()
    pdu.api_username = api_username
    pdu.api_password = api_password
    pdu.device = MagicMock()
    return pdu


def _block_import(name):
    """Return an import hook that raises ImportError for the given module name."""
    real_import = builtins.__import__

    def _fake_import(module_name, *args, **kwargs):
        if module_name == name or module_name.startswith(name + "."):
            raise ImportError(f"No module named {module_name!r}")
        return real_import(module_name, *args, **kwargs)

    return _fake_import


def _fake_django_modules():
    """Fake sys.modules entries for the django.contrib.contenttypes.models import."""
    contenttypes_models = MagicMock()
    return {
        "django": MagicMock(),
        "django.contrib": MagicMock(),
        "django.contrib.contenttypes": MagicMock(),
        "django.contrib.contenttypes.models": contenttypes_models,
    }


def _fake_secrets_modules(secret_role_exists, secret_exists, secret=None):
    role_qs = MagicMock()
    role_qs.exists.return_value = secret_role_exists

    secret_qs = MagicMock()
    secret_qs.exists.return_value = secret_exists
    secret_qs.first.return_value = secret

    models = MagicMock()
    models.SecretRole.objects.filter.return_value = role_qs
    models.Secret.objects.filter.return_value = secret_qs
    return {"netbox_secrets": MagicMock(), "netbox_secrets.models": models}


class TestGetCredentialFallback(unittest.TestCase):
    def test_falls_back_to_plaintext_when_netbox_secrets_not_installed(self):
        """netbox-secrets unavailable (ImportError) -> plaintext fields are used."""
        managed_pdu = _mock_managed_pdu(api_username="user1", api_password="pass1")
        with patch("builtins.__import__", side_effect=_block_import("netbox_secrets")):
            cred = get_credential(managed_pdu)
        self.assertEqual(cred, Credential(username="user1", password="pass1", source="plaintext_fallback"))

    def test_falls_back_when_secret_role_not_found(self):
        """netbox-secrets installed, but no matching SecretRole -> plaintext fields."""
        managed_pdu = _mock_managed_pdu(api_username="user2", api_password="pass2")
        fake_modules = {**_fake_django_modules(), **_fake_secrets_modules(secret_role_exists=False, secret_exists=False)}

        with patch.dict("sys.modules", fake_modules):
            cred = get_credential(managed_pdu)

        self.assertEqual(cred, Credential(username="user2", password="pass2", source="plaintext_fallback"))

    def test_falls_back_when_no_secret_assigned_to_device(self):
        """SecretRole exists but no Secret assigned to this device -> plaintext fields."""
        managed_pdu = _mock_managed_pdu(api_username="user3", api_password="pass3")
        fake_modules = {**_fake_django_modules(), **_fake_secrets_modules(secret_role_exists=True, secret_exists=False)}

        with patch.dict("sys.modules", fake_modules):
            cred = get_credential(managed_pdu)

        self.assertEqual(cred.source, "plaintext_fallback")

    def test_falls_back_when_decrypt_raises(self):
        """netbox-secrets available and a secret exists, but decrypt() errors -> plaintext fields."""
        managed_pdu = _mock_managed_pdu(api_username="user4", api_password="pass4")
        fake_secret = MagicMock()
        fake_secret.decrypt.side_effect = Exception("boom")
        fake_modules = {
            **_fake_django_modules(),
            **_fake_secrets_modules(secret_role_exists=True, secret_exists=True, secret=fake_secret),
        }

        with (
            patch.dict("sys.modules", fake_modules),
            patch("netbox_pdu_control.credentials._resolve_master_key", return_value=b"key"),
        ):
            cred = get_credential(managed_pdu)

        self.assertEqual(cred.source, "plaintext_fallback")

    def test_falls_back_when_decrypted_plaintext_is_none(self):
        """decrypt() succeeds but leaves .plaintext as None (e.g. wrong key) -> plaintext fields."""
        managed_pdu = _mock_managed_pdu(api_username="user5", api_password="pass5")
        fake_secret = MagicMock()
        fake_secret.plaintext = None
        fake_modules = {
            **_fake_django_modules(),
            **_fake_secrets_modules(secret_role_exists=True, secret_exists=True, secret=fake_secret),
        }

        with (
            patch.dict("sys.modules", fake_modules),
            patch("netbox_pdu_control.credentials._resolve_master_key", return_value=b"key"),
        ):
            cred = get_credential(managed_pdu)

        self.assertEqual(cred.source, "plaintext_fallback")

    def test_uses_secret_when_available_and_decrypt_succeeds(self):
        """netbox-secrets available, secret found, decrypt succeeds -> secret values are used."""
        managed_pdu = _mock_managed_pdu(api_username="fallback_user", api_password="fallback_pass")
        fake_secret = MagicMock()
        fake_secret.name = "secret_user"
        fake_secret.plaintext = "secret_pass"
        fake_modules = {
            **_fake_django_modules(),
            **_fake_secrets_modules(secret_role_exists=True, secret_exists=True, secret=fake_secret),
        }

        with (
            patch.dict("sys.modules", fake_modules),
            patch("netbox_pdu_control.credentials._resolve_master_key", return_value=b"key"),
        ):
            cred = get_credential(managed_pdu)

        self.assertEqual(cred, Credential(username="secret_user", password="secret_pass", source="netbox_secrets"))
        fake_secret.decrypt.assert_called_once_with(b"key")


if __name__ == "__main__":
    unittest.main()
