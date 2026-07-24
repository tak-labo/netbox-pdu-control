"""
Test cases for NetBox PDU Plugin views.

Run inside Docker:
  docker compose exec netbox python manage.py test netbox_pdu_control.tests.test_views -v2
"""

from unittest.mock import MagicMock, patch

from django.urls import reverse

from ..backends.base import PDUClientError
from ..choices import OutletStatusChoices, VendorChoices
from ..models import ManagedPDU, PDUInlet, PDUOutlet
from ..testing import PluginViewTestCase
from ..testing.utils import disable_warnings
from .test_models import create_test_device, create_test_pdu


class ManagedPDUViewTest(PluginViewTestCase):
    """Tests for ManagedPDU CRUD views."""

    @classmethod
    def setUpTestData(cls):
        cls.device1 = create_test_device("PDU-VIEW-1")
        cls.device2 = create_test_device("PDU-VIEW-2")
        cls.device3 = create_test_device("PDU-VIEW-3")
        cls.pdu = create_test_pdu(cls.device1)

    def setUp(self):
        super().setUp()
        self.base_url = "plugins:netbox_pdu_control:managedpdu"

    def test_list_view(self):
        self.add_permissions("netbox_pdu_control.view_managedpdu")
        url = self._get_url("list")
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)

    def test_list_view_without_permission(self):
        with disable_warnings("django.request"):
            url = self._get_url("list")
            response = self.client.get(url)
            self.assertHttpStatus(response, 403)

    def test_detail_view(self):
        self.add_permissions("netbox_pdu_control.view_managedpdu")
        url = self._get_url("detail", self.pdu)
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)
        self.assertEqual(response.context["object"], self.pdu)

    def test_detail_view_credentials_card_plaintext_fallback_when_secrets_unavailable(self):
        """netbox-secrets not installed -> plaintext source message, no secret_found lookup performed."""
        self.add_permissions("netbox_pdu_control.view_managedpdu")
        url = self._get_url("detail", self.pdu)
        with patch.dict("sys.modules", {"netbox_secrets": None, "netbox_secrets.models": None}):
            response = self.client.get(url)
        self.assertHttpStatus(response, 200)
        self.assertFalse(response.context["secrets_available"])
        self.assertFalse(response.context["secret_found"])
        self.assertContains(response, "netbox-secrets not installed")

    def test_detail_view_credentials_card_no_secret_found(self):
        """netbox-secrets installed but no Secret exists for this device -> fallback message + plaintext fields shown."""
        self.add_permissions("netbox_pdu_control.view_managedpdu")
        url = self._get_url("detail", self.pdu)
        fake_secret_qs = MagicMock()
        fake_secret_qs.exists.return_value = False
        fake_secrets_module = MagicMock()
        fake_secrets_module.Secret.objects.filter.return_value = fake_secret_qs
        with patch.dict("sys.modules", {"netbox_secrets": MagicMock(), "netbox_secrets.models": fake_secrets_module}):
            response = self.client.get(url)
        self.assertHttpStatus(response, 200)
        self.assertTrue(response.context["secrets_available"])
        self.assertFalse(response.context["secret_found"])
        self.assertContains(response, "No secret found for this device")
        self.assertContains(response, "Falling back to plaintext fields")
        self.assertContains(response, self.pdu.api_username)

    def test_detail_view_credentials_card_secret_found(self):
        """netbox-secrets installed and a matching Secret exists -> success message, no plaintext fields shown."""
        self.add_permissions("netbox_pdu_control.view_managedpdu")
        url = self._get_url("detail", self.pdu)
        fake_secret_qs = MagicMock()
        fake_secret_qs.exists.return_value = True
        fake_secrets_module = MagicMock()
        fake_secrets_module.Secret.objects.filter.return_value = fake_secret_qs
        with patch.dict("sys.modules", {"netbox_secrets": MagicMock(), "netbox_secrets.models": fake_secrets_module}):
            response = self.client.get(url)
        self.assertHttpStatus(response, 200)
        self.assertTrue(response.context["secrets_available"])
        self.assertTrue(response.context["secret_found"])
        self.assertContains(response, "Secret found (pdu-credentials role)")
        self.assertNotContains(response, "stored (plaintext)")

    def test_add_view_get(self):
        self.add_permissions("netbox_pdu_control.add_managedpdu")
        url = self._get_url("add")
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)

    def test_add_view_post(self):
        # Use superuser to bypass ObjectPermission and form field restrictions.
        superuser = self.create_test_user(username="superuser_add", is_superuser=True)
        self.client.force_login(superuser)
        url = self._get_url("add")
        form_data = self.post_data(
            {
                "device": self.device2,
                "vendor": VendorChoices.RARITAN,
                "api_url": "https://new.example.com",
                "api_username": "admin",
                "api_password": "secret",
                "verify_ssl": False,
            }
        )
        response = self.client.post(url, form_data, follow=True)
        self.assertHttpStatus(response, 200)
        self.assertTrue(ManagedPDU.objects.filter(device=self.device2).exists())

    def test_edit_view(self):
        superuser = self.create_test_user(username="superuser_edit", is_superuser=True)
        self.client.force_login(superuser)
        url = self._get_url("edit", self.pdu)
        form_data = self.post_data(
            {
                "device": self.device1,
                "vendor": VendorChoices.RARITAN,
                "api_url": "https://edited.example.com",
                "api_username": "admin",
                "api_password": "secret",
                "verify_ssl": False,
            }
        )
        response = self.client.post(url, form_data, follow=True)
        self.assertHttpStatus(response, 200)
        self.pdu.refresh_from_db()
        self.assertEqual(self.pdu.api_url, "https://edited.example.com")

    def test_delete_view(self):
        superuser = self.create_test_user(username="superuser_del", is_superuser=True)
        self.client.force_login(superuser)
        pdu = create_test_pdu(self.device3)
        url = self._get_url("delete", pdu)
        response = self.client.post(url, {"confirm": True}, follow=True)
        self.assertHttpStatus(response, 200)
        self.assertFalse(ManagedPDU.objects.filter(pk=pdu.pk).exists())
        self.assertEqual(response.redirect_chain[0][0], self._get_url("list"))


class ManagedPDUConnectionTestViewTest(PluginViewTestCase):
    """Tests for the pre-save "Test Connection" endpoint used by the Add/Edit form."""

    @classmethod
    def setUpTestData(cls):
        cls.url = reverse("plugins:netbox_pdu_control:managedpdu_test_connection")
        cls.device = create_test_device("PDU-CONNTEST-1")

    def _post(self, data):
        return self.client.post(self.url, data)

    def test_without_permission_returns_403(self):
        response = self._post({"vendor": VendorChoices.RARITAN, "api_url": "https://192.168.1.100"})
        self.assertHttpStatus(response, 403)

    def test_missing_vendor_returns_ok_false(self):
        self.add_permissions("netbox_pdu_control.add_managedpdu")
        response = self._post({"api_url": "https://192.168.1.100"})
        self.assertHttpStatus(response, 200)
        self.assertFalse(response.json()["ok"])

    def test_missing_api_url_returns_ok_false(self):
        self.add_permissions("netbox_pdu_control.add_managedpdu")
        response = self._post({"vendor": VendorChoices.RARITAN})
        self.assertHttpStatus(response, 200)
        self.assertFalse(response.json()["ok"])

    def test_unknown_vendor_returns_ok_false(self):
        self.add_permissions("netbox_pdu_control.add_managedpdu")
        response = self._post({"vendor": "fakevendor", "api_url": "https://192.168.1.100"})
        self.assertHttpStatus(response, 200)
        self.assertFalse(response.json()["ok"])

    @patch("netbox_pdu_control.backends.raritan.requests.Session")
    @patch("netbox_pdu_control.backends.raritan.RaritanPDUClient.get_pdu_info")
    def test_success(self, mock_get_pdu_info, mock_session):
        self.add_permissions("netbox_pdu_control.add_managedpdu")
        mock_get_pdu_info.return_value = {"model": "PX3-TEST", "firmware_version": "4.3.1"}
        response = self._post(
            {
                "vendor": VendorChoices.RARITAN,
                "api_url": "https://192.168.1.100",
                "api_username": "admin",
                "api_password": "secret",
                "verify_ssl": "false",
            }
        )
        self.assertHttpStatus(response, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("PX3-TEST", body["message"])

    @patch("netbox_pdu_control.backends.raritan.requests.Session")
    @patch("netbox_pdu_control.backends.raritan.RaritanPDUClient.get_pdu_info")
    def test_pdu_client_error_returns_ok_false(self, mock_get_pdu_info, mock_session):
        self.add_permissions("netbox_pdu_control.add_managedpdu")
        mock_get_pdu_info.side_effect = PDUClientError("Connection refused")
        response = self._post(
            {
                "vendor": VendorChoices.RARITAN,
                "api_url": "https://192.168.1.100",
                "api_username": "admin",
                "api_password": "secret",
                "verify_ssl": "false",
            }
        )
        self.assertHttpStatus(response, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertIn("Connection refused", body["message"])

    @patch("netbox_pdu_control.views.get_credential")
    def test_uses_secret_credential_over_typed_form_values(self, mock_get_credential):
        """When the selected Device has a netbox-secrets Secret, it takes priority over typed fields."""
        from ..credentials import Credential

        self.add_permissions("netbox_pdu_control.add_managedpdu")
        mock_get_credential.return_value = Credential(
            username="secret_user", password="secret_pass", source="netbox_secrets"
        )

        mock_client = MagicMock()
        mock_client.get_pdu_info.return_value = {"model": "PX3-TEST"}
        mock_backend_class = MagicMock(return_value=mock_client)

        with patch.dict("netbox_pdu_control.views._VENDOR_BACKENDS", {VendorChoices.RARITAN: mock_backend_class}):
            response = self._post(
                {
                    "device": self.device.pk,
                    "vendor": VendorChoices.RARITAN,
                    "api_url": "https://192.168.1.100",
                    "api_username": "typed_user",
                    "api_password": "typed_pass",
                    "verify_ssl": "false",
                }
            )

        self.assertHttpStatus(response, 200)
        self.assertTrue(response.json()["ok"])
        mock_backend_class.assert_called_once_with(
            base_url="https://192.168.1.100",
            username="secret_user",
            password="secret_pass",
            verify_ssl=False,
        )

    def test_uses_typed_form_values_when_device_has_no_secret(self):
        """No mocking of get_credential(): with no matching Secret, the typed fields are used as-is."""
        self.add_permissions("netbox_pdu_control.add_managedpdu")

        mock_client = MagicMock()
        mock_client.get_pdu_info.return_value = {"model": "PX3-TEST"}
        mock_backend_class = MagicMock(return_value=mock_client)

        with patch.dict("netbox_pdu_control.views._VENDOR_BACKENDS", {VendorChoices.RARITAN: mock_backend_class}):
            response = self._post(
                {
                    "device": self.device.pk,
                    "vendor": VendorChoices.RARITAN,
                    "api_url": "https://192.168.1.100",
                    "api_username": "typed_user",
                    "api_password": "typed_pass",
                    "verify_ssl": "false",
                }
            )

        self.assertHttpStatus(response, 200)
        self.assertTrue(response.json()["ok"])
        mock_backend_class.assert_called_once_with(
            base_url="https://192.168.1.100",
            username="typed_user",
            password="typed_pass",
            verify_ssl=False,
        )


class PDUOutletViewTest(PluginViewTestCase):
    """Tests for PDUOutlet views."""

    @classmethod
    def setUpTestData(cls):
        cls.pdu = create_test_pdu()
        cls.outlet = PDUOutlet.objects.create(
            managed_pdu=cls.pdu,
            outlet_number=1,
            outlet_name="Outlet 1",
        )

    def setUp(self):
        super().setUp()
        self.base_url = "plugins:netbox_pdu_control:pduoutlet"

    def test_list_view(self):
        self.add_permissions("netbox_pdu_control.view_pduoutlet")
        url = self._get_url("list")
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)

    def test_detail_view(self):
        self.add_permissions("netbox_pdu_control.view_pduoutlet")
        url = self._get_url("detail", self.outlet)
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)


class PDUInletViewTest(PluginViewTestCase):
    """Tests for PDUInlet views."""

    @classmethod
    def setUpTestData(cls):
        cls.pdu = create_test_pdu()
        cls.inlet = PDUInlet.objects.create(
            managed_pdu=cls.pdu,
            inlet_number=1,
            inlet_name="Inlet 1",
        )

    def setUp(self):
        super().setUp()
        self.base_url = "plugins:netbox_pdu_control:pduinlet"

    def test_list_view(self):
        self.add_permissions("netbox_pdu_control.view_pduinlet")
        url = self._get_url("list")
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)

    def test_detail_view(self):
        self.add_permissions("netbox_pdu_control.view_pduinlet")
        url = self._get_url("detail", self.inlet)
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)


class PDUOutletPowerViewTest(PluginViewTestCase):
    """Tests for PDUOutlet power control views (ON / OFF / cycle)."""

    @classmethod
    def setUpTestData(cls):
        cls.pdu = create_test_pdu()
        cls.outlet = PDUOutlet.objects.create(
            managed_pdu=cls.pdu,
            outlet_number=1,
            outlet_name="Outlet 1",
        )

    def _url(self, action):
        return reverse(f"plugins:netbox_pdu_control:pduoutlet_{action}", kwargs={"pk": self.outlet.pk})

    def test_power_on_without_permission(self):
        # View does its own permission check and redirects — no 403.
        response = self.client.post(self._url("power_on"))
        self.assertHttpStatus(response, 302)

    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_power_on(self, mock_get_client):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_client.get_outlet_power_state_by_index.return_value = "on"
        mock_get_client.return_value = mock_client

        response = self.client.post(self._url("power_on"))

        self.assertHttpStatus(response, 302)
        mock_client.set_outlet_power_state.assert_called_once_with(0, "on")
        mock_client.get_outlet_power_state_by_index.assert_called_once_with(0)
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.status, OutletStatusChoices.ON)

    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_power_off(self, mock_get_client):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_client.get_outlet_power_state_by_index.return_value = "off"
        mock_get_client.return_value = mock_client

        response = self.client.post(self._url("power_off"))

        self.assertHttpStatus(response, 302)
        mock_client.set_outlet_power_state.assert_called_once_with(0, "off")
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.status, OutletStatusChoices.OFF)

    @patch("netbox_pdu_control.views.django_rq")
    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_power_cycle(self, mock_get_client, mock_rq):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        response = self.client.post(self._url("power_cycle"))

        self.assertHttpStatus(response, 302)
        mock_client.set_outlet_power_state.assert_called_once_with(0, "cycle")
        # Background job must be enqueued for cycle
        mock_queue.enqueue_in.assert_called_once()

    def test_power_on_without_permission_does_not_call_backend(self):
        with patch("netbox_pdu_control.views.get_pdu_client") as mock_get_client:
            self.client.post(self._url("power_on"))
            mock_get_client.assert_not_called()


class PDUOutletPushNameViewTest(PluginViewTestCase):
    """Tests for PDUOutletPushNameView."""

    @classmethod
    def setUpTestData(cls):
        cls.pdu = create_test_pdu()
        cls.outlet = PDUOutlet.objects.create(
            managed_pdu=cls.pdu,
            outlet_number=1,
            outlet_name="Test Server",
        )

    def _url(self):
        return reverse("plugins:netbox_pdu_control:pduoutlet_push_name", kwargs={"pk": self.outlet.pk})

    def test_push_name_without_permission_redirects(self):
        response = self.client.post(self._url())
        self.assertHttpStatus(response, 302)

    def test_push_name_without_permission_does_not_call_backend(self):
        with patch("netbox_pdu_control.views.get_pdu_client") as mock_get_client:
            self.client.post(self._url())
            mock_get_client.assert_not_called()

    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_push_name(self, mock_get_client):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        response = self.client.post(self._url())

        self.assertHttpStatus(response, 302)
        mock_client.set_outlet_name.assert_called_once_with(0, "Test Server")

    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_push_name_pdu_error_does_not_update_netbox_label(self, mock_get_client):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_client.set_outlet_name.side_effect = PDUClientError("connection refused")
        mock_get_client.return_value = mock_client

        from dcim.models import PowerOutlet as NbPowerOutlet

        po = NbPowerOutlet.objects.create(
            device=self.pdu.device,
            name="Outlet 1",
            label="old label",
        )

        self.client.post(self._url())

        po.refresh_from_db()
        self.assertEqual(po.label, "old label")


class PDUInletPushNameViewTest(PluginViewTestCase):
    """Tests for PDUInletPushNameView."""

    @classmethod
    def setUpTestData(cls):
        cls.pdu = create_test_pdu()
        cls.inlet = PDUInlet.objects.create(
            managed_pdu=cls.pdu,
            inlet_number=1,
            inlet_name="Main Input",
        )

    def _url(self):
        return reverse("plugins:netbox_pdu_control:pduinlet_push_name", kwargs={"pk": self.inlet.pk})

    def test_push_name_without_permission_redirects(self):
        response = self.client.post(self._url())
        self.assertHttpStatus(response, 302)

    def test_push_name_without_permission_does_not_call_backend(self):
        with patch("netbox_pdu_control.views.get_pdu_client") as mock_get_client:
            self.client.post(self._url())
            mock_get_client.assert_not_called()

    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_push_name(self, mock_get_client):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        response = self.client.post(self._url())

        self.assertHttpStatus(response, 302)
        mock_client.set_inlet_name.assert_called_once_with(0, "Main Input")

    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_push_name_pdu_error_does_not_update_netbox_label(self, mock_get_client):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_client.set_inlet_name.side_effect = PDUClientError("connection refused")
        mock_get_client.return_value = mock_client

        from dcim.models import PowerPort as NbPowerPort

        pp = NbPowerPort.objects.create(
            device=self.pdu.device,
            name="Power Port 1",
            label="old label",
        )

        self.client.post(self._url())

        pp.refresh_from_db()
        self.assertEqual(pp.label, "old label")


class PDUOutletBulkPowerViewTest(PluginViewTestCase):
    """Tests for PDUOutletBulkPowerView (bulk ON/OFF)."""

    @classmethod
    def setUpTestData(cls):
        cls.pdu = create_test_pdu()
        cls.pdu2_device = create_test_device("PDU-BULK-2")
        cls.pdu2 = create_test_pdu(cls.pdu2_device)
        cls.outlet1 = PDUOutlet.objects.create(
            managed_pdu=cls.pdu,
            outlet_number=1,
            outlet_name="Outlet 1",
        )
        cls.outlet2 = PDUOutlet.objects.create(
            managed_pdu=cls.pdu,
            outlet_number=2,
            outlet_name="Outlet 2",
        )
        cls.outlet_other_pdu = PDUOutlet.objects.create(
            managed_pdu=cls.pdu2,
            outlet_number=1,
            outlet_name="Other PDU Outlet",
        )

    def _url(self):
        return reverse(
            "plugins:netbox_pdu_control:pduoutlet_bulk_power",
            kwargs={"pk": self.pdu.pk},
        )

    def test_bulk_power_without_permission_redirects(self):
        response = self.client.post(self._url(), {"action": "on", "pk": [self.outlet1.pk]})
        self.assertHttpStatus(response, 302)

    def test_bulk_power_without_permission_does_not_call_backend(self):
        with patch("netbox_pdu_control.views.get_pdu_client") as mock_get_client:
            self.client.post(self._url(), {"action": "on", "pk": [self.outlet1.pk]})
            mock_get_client.assert_not_called()

    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_bulk_power_on(self, mock_get_client):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        response = self.client.post(self._url(), {"action": "on", "pk": [self.outlet1.pk, self.outlet2.pk]})

        self.assertHttpStatus(response, 302)
        mock_client.set_outlet_power_state.assert_any_call(0, "on")
        mock_client.set_outlet_power_state.assert_any_call(1, "on")
        self.assertEqual(mock_client.set_outlet_power_state.call_count, 2)
        self.outlet1.refresh_from_db()
        self.outlet2.refresh_from_db()
        self.assertEqual(self.outlet1.status, OutletStatusChoices.ON)
        self.assertEqual(self.outlet2.status, OutletStatusChoices.ON)

    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_bulk_power_off(self, mock_get_client):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        response = self.client.post(self._url(), {"action": "off", "pk": [self.outlet1.pk, self.outlet2.pk]})

        self.assertHttpStatus(response, 302)
        self.outlet1.refresh_from_db()
        self.outlet2.refresh_from_db()
        self.assertEqual(self.outlet1.status, OutletStatusChoices.OFF)
        self.assertEqual(self.outlet2.status, OutletStatusChoices.OFF)

    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_bulk_power_no_pks_returns_warning(self, mock_get_client):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        response = self.client.post(self._url(), {"action": "on"})

        self.assertHttpStatus(response, 302)
        mock_client.set_outlet_power_state.assert_not_called()

    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_bulk_power_invalid_action_returns_error(self, mock_get_client):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        response = self.client.post(self._url(), {"action": "cycle", "pk": [self.outlet1.pk]})

        self.assertHttpStatus(response, 302)
        mock_client.set_outlet_power_state.assert_not_called()

    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_bulk_power_ignores_outlets_from_other_pdu(self, mock_get_client):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        response = self.client.post(
            self._url(),
            {"action": "on", "pk": [self.outlet1.pk, self.outlet_other_pdu.pk]},
        )

        self.assertHttpStatus(response, 302)
        mock_client.set_outlet_power_state.assert_called_once_with(0, "on")

    @patch("netbox_pdu_control.views.get_pdu_client")
    def test_bulk_power_continues_after_api_error(self, mock_get_client):
        self.add_permissions("netbox_pdu_control.change_managedpdu")
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.set_outlet_power_state.side_effect = [
            PDUClientError("timeout"),
            None,
        ]

        response = self.client.post(self._url(), {"action": "on", "pk": [self.outlet1.pk, self.outlet2.pk]})

        self.assertHttpStatus(response, 302)
        self.assertEqual(mock_client.set_outlet_power_state.call_count, 2)
        self.outlet2.refresh_from_db()
        self.assertEqual(self.outlet2.status, OutletStatusChoices.ON)


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

        self.add_permissions("netbox_pdu_control.change_managedpdu", "netbox_pdu_control.view_managedpdu")
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
