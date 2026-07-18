"""
Helper for retrieving PDU credentials from netbox-secrets.

Decryption path:
  Web views (request available):
    The session key (cookie or X-Session-Key header) decrypts the user's
    UserKey to obtain the master_key.

  Background jobs (system jobs, RQ jobs — no request):
    A service account's RSA private key (PEM), configured via
    PLUGINS_CONFIG["netbox_pdu_control"]["service_private_key_path"], decrypts
    the service account's UserKey to obtain the master_key.

Secret layout convention:
  - SecretRole slug : pdu-credentials
  - Secret.name     : PDU API username (unencrypted field)
  - Secret.plaintext: PDU API password (RSA-encrypted)
  - assigned_object : Device (the ManagedPDU's device)

Fallback behavior:
  If netbox-secrets is not installed, no SecretRole/Secret is found, or
  decryption fails, falls back to ManagedPDU.api_username/api_password
  (plaintext fields) for backward compatibility.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

# Django is imported lazily inside the functions that need it (not at module
# scope) so that this module — and get_pdu_client(), which imports it — stays
# importable in isolated unit tests that run without Django installed/configured
# (see tests/test_backends_raritan.py, test_backends_unifi.py, test_factory.py).

if TYPE_CHECKING:
    from django.http import HttpRequest

    from .models import ManagedPDU

logger = logging.getLogger("netbox_pdu_control.credentials")

SECRET_ROLE_SLUG = "pdu-credentials"


@dataclass
class Credential:
    username: str
    password: str
    source: str  # "netbox_secrets" | "plaintext_fallback"


def get_credential(managed_pdu: ManagedPDU, request: HttpRequest | None = None) -> Credential:
    """
    Return the credential to use for the given ManagedPDU.

    If netbox-secrets is available:
      - with a request, decrypt using the requesting user's session key
      - without a request, decrypt using the service account's private key

    Falls back to managed_pdu.api_username / managed_pdu.api_password if
    netbox-secrets is unavailable, has no matching secret, or decryption fails.
    """
    try:
        return _get_from_secrets(managed_pdu, request)
    except _SecretsUnavailable:
        logger.debug("netbox-secrets unavailable, using plaintext fields")
    except _SecretNotFound:
        logger.debug("No pdu-credentials secret for device %s, using plaintext fields", managed_pdu.device)
    except Exception as e:
        # If netbox-secrets is available but decryption fails, log at error level
        # rather than silently falling back — a silent fallback to plaintext is
        # easy to miss in day-to-day operation.
        logger.error("Failed to decrypt secret for %s: %s, using plaintext fields", managed_pdu.device, e)

    return Credential(
        username=managed_pdu.api_username,
        password=managed_pdu.api_password,
        source="plaintext_fallback",
    )


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


class _SecretsUnavailable(Exception):
    pass


class _SecretNotFound(Exception):
    pass


def _get_from_secrets(managed_pdu: ManagedPDU, request: HttpRequest | None) -> Credential:
    try:
        from netbox_secrets.models import Secret, SecretRole
    except ImportError:
        raise _SecretsUnavailable from None

    from django.contrib.contenttypes.models import ContentType

    device = managed_pdu.device
    device_ct = ContentType.objects.get_for_model(device)

    role_qs = SecretRole.objects.filter(slug=SECRET_ROLE_SLUG)
    if not role_qs.exists():
        raise _SecretNotFound(f"SecretRole '{SECRET_ROLE_SLUG}' not found")

    secret_qs = Secret.objects.filter(
        role__in=role_qs,
        assigned_object_type=device_ct,
        assigned_object_id=device.pk,
    )
    if not secret_qs.exists():
        raise _SecretNotFound

    secret = secret_qs.first()
    master_key = _resolve_master_key(request)
    secret.decrypt(master_key)

    if secret.plaintext is None:
        raise Exception("decrypt returned None — wrong key?")

    return Credential(
        username=secret.name,
        password=secret.plaintext,
        source="netbox_secrets",
    )


def _resolve_master_key(request: HttpRequest | None) -> bytes:
    """
    Return the master_key (bytes).

    With a request  → session key from cookie or X-Session-Key header
    Without one     → service account private key
    """
    if request is not None:
        return _master_key_from_request(request)

    return _master_key_from_service_account()


def _master_key_from_request(request) -> bytes:
    from netbox_secrets.constants import SESSION_COOKIE_NAME
    from netbox_secrets.models import SessionKey, UserKey

    session_key_b64 = request.META.get("HTTP_X_SESSION_KEY") or request.COOKIES.get(SESSION_COOKIE_NAME)
    if not session_key_b64:
        raise Exception(f"No X-Session-Key header or {SESSION_COOKIE_NAME} cookie in request")

    session_key = base64.b64decode(session_key_b64)
    try:
        uk = UserKey.objects.get(user=request.user)
    except UserKey.DoesNotExist as e:
        raise Exception(f"No UserKey found for user {request.user}") from e

    try:
        session_key_obj = uk.session_key
    except SessionKey.DoesNotExist as e:
        raise Exception(f"No active session key for user {request.user}") from e

    # Decryption failure (expired/invalid session key) raises InvalidKey, which
    # the caller (get_credential) catches via the generic `except Exception`.
    return session_key_obj.get_master_key(session_key)


def _master_key_from_service_account() -> bytes:
    """
    Resolve the master_key using the service account configured via
    PLUGINS_CONFIG["netbox_pdu_control"]["service_account"] and
    ["service_private_key_path"].

    configuration.py example:
        PLUGINS_CONFIG = {
            "netbox_pdu_control": {
                "service_account": "pdu-sync",
                "service_private_key_path": "/opt/netbox/pdu-sync.pem",
            }
        }
    """
    from django.conf import settings
    from netbox_secrets.models import UserKey

    plugin_cfg = settings.PLUGINS_CONFIG.get("netbox_pdu_control", {})
    account = plugin_cfg.get("service_account")
    key_path = plugin_cfg.get("service_private_key_path")

    if not account or not key_path:
        raise Exception(
            "service_account and service_private_key_path must be set in "
            "PLUGINS_CONFIG['netbox_pdu_control'] for background job decryption"
        )

    pem = Path(key_path).read_text()

    try:
        uk = UserKey.objects.get(user__username=account)
    except UserKey.DoesNotExist as e:
        raise Exception(f"No UserKey for service account '{account}'") from e

    master_key = uk.get_master_key(private_key=pem)
    if master_key is None:
        raise Exception(f"master_key is None for service account '{account}' — check that the private key matches")
    return master_key
