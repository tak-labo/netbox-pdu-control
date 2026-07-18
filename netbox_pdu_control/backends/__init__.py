from ..credentials import get_credential
from .base import BasePDUClient, PDUClientError
from .raritan import RaritanPDUClient
from .unifi import UniFiPDUClient

_VENDOR_BACKENDS = {
    "raritan": RaritanPDUClient,
    "ubiquiti": UniFiPDUClient,
}


def get_pdu_client(managed_pdu, request=None) -> BasePDUClient:
    """
    Return the appropriate PDU client for the given ManagedPDU instance.

    Credentials are resolved via get_credential() — netbox-secrets if
    available (decrypted using `request`'s session key, or the service
    account when request is None), otherwise the plaintext
    api_username/api_password fields.

    Raises PDUClientError if no backend is registered for the vendor.
    """
    backend_class = _VENDOR_BACKENDS.get(managed_pdu.vendor)
    if not backend_class:
        raise PDUClientError(f"No backend registered for vendor: {managed_pdu.vendor!r}")
    credential = get_credential(managed_pdu, request=request)
    return backend_class(
        base_url=managed_pdu.api_url,
        username=credential.username,
        password=credential.password,
        verify_ssl=managed_pdu.verify_ssl,
        managed_pdu=managed_pdu,
    )
