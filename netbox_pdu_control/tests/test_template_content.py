"""
Tests for the DeviceManagedPDUButton plugin template extension (template_content.py).

Run inside Docker:
  docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_template_content -v2
"""

from django.contrib.auth import get_user_model
from django.test import Client

from ..models import PDUInlet, PDUOutlet
from ..testing import PluginViewTestCase
from .test_models import create_test_device, create_test_pdu

User = get_user_model()


class DeviceRightPageTest(PluginViewTestCase):
    """Tests for the plugin content injected onto dcim.Device detail pages."""

    @classmethod
    def setUpTestData(cls):
        cls.pdu_device = create_test_device("Inlets-Card-PDU")
        cls.pdu = create_test_pdu(cls.pdu_device)
        cls.connected_device = create_test_device("Inlets-Card-Connected")
        cls.unrelated_device = create_test_device("Inlets-Card-Unrelated")

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="template-content-test", is_active=True)
        self.client = Client()
        self.client.force_login(self.user)
        self.add_permissions("dcim.view_device")

    def _get_device_page(self, device):
        return self.client.get(f"/dcim/devices/{device.pk}/")

    def test_pdu_device_shows_inlets_card(self):
        PDUInlet.objects.create(managed_pdu=self.pdu, inlet_number=1, power_w=42.5)
        response = self._get_device_page(self.pdu_device)
        self.assertHttpStatus(response, 200)
        content = response.content.decode()
        self.assertIn('card-header">PDU Inlets', content)
        self.assertNotIn('card-header">PDU Outlets', content)

    def test_pdu_device_without_inlets_shows_no_card(self):
        response = self._get_device_page(self.pdu_device)
        content = response.content.decode()
        self.assertNotIn('card-header">PDU Inlets', content)

    def test_connected_device_shows_outlets_card_not_inlets(self):
        PDUOutlet.objects.create(
            managed_pdu=self.pdu,
            outlet_number=1,
            connected_device=self.connected_device,
        )
        response = self._get_device_page(self.connected_device)
        content = response.content.decode()
        self.assertIn('card-header">PDU Outlets', content)
        self.assertNotIn('card-header">PDU Inlets', content)

    def test_unrelated_device_shows_neither_card(self):
        response = self._get_device_page(self.unrelated_device)
        content = response.content.decode()
        self.assertNotIn('card-header">PDU Outlets', content)
        self.assertNotIn('card-header">PDU Inlets', content)
