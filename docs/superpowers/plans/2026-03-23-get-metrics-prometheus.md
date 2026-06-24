# Get Metrics (Prometheus) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Get Metrics" button to the PDU detail page that fetches outlet/inlet metrics via the Raritan Prometheus endpoint in a single HTTP request, without touching outlet status or energy reset time.

**Architecture:** Add `get_all_metrics_prometheus()` to `RaritanPDUClient` (parses Prometheus text format), a `supports_prometheus_metrics` flag to `BasePDUClient`, a new `ManagedPDUGetMetricsView` that updates only metric fields, and a "Get Metrics" button next to the existing "Sync PDU" button.

**Tech Stack:** Python, Django, requests, Prometheus text format (line-by-line parsing with re), NetBox plugin conventions

---

## File Map

| File | Change |
|------|--------|
| `netbox_pdu_control/backends/base.py` | Add `supports_prometheus_metrics = False` class attribute |
| `netbox_pdu_control/backends/raritan.py` | Add `supports_prometheus_metrics = True`, `_PROMETHEUS_METRIC_MAP`, `_parse_prometheus_text()`, `get_all_metrics_prometheus()` |
| `netbox_pdu_control/views.py` | Add `ManagedPDUGetMetricsView` |
| `netbox_pdu_control/urls.py` | Add `managed-pdus/<int:pk>/get-metrics/` route |
| `netbox_pdu_control/templates/netbox_pdu_control/managedpdu.html` | Add "Get Metrics" form/button next to "Sync PDU" |
| `netbox_pdu_control/tests/test_backends_raritan.py` | Add tests for `_parse_prometheus_text()` and `get_all_metrics_prometheus()` |

---

### Task 1: Add `supports_prometheus_metrics` flag to `BasePDUClient`

**Files:**
- Modify: `netbox_pdu_control/backends/base.py`

- [ ] **Step 1: Add the class attribute**

In `base.py`, add to `BasePDUClient` (after the `__init__` method, before `get_pdu_info`):

```python
#: Set to True in backends that implement get_all_metrics_prometheus().
supports_prometheus_metrics: bool = False
```

- [ ] **Step 2: Verify lint passes**

```bash
cd /Users/tak/project/personal/netbox/netbox-pdu-control
make lint
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add netbox_pdu_control/backends/base.py
git commit -m "Add supports_prometheus_metrics flag to BasePDUClient"
```

---

### Task 2: Implement Prometheus parser and `get_all_metrics_prometheus()` in `RaritanPDUClient`

> **Prerequisite:** Task 1 must be completed first (`supports_prometheus_metrics` must exist in `BasePDUClient`).

**Files:**
- Modify: `netbox_pdu_control/backends/raritan.py`
- Test: `netbox_pdu_control/tests/test_backends_raritan.py`

#### What to implement

Add to `raritan.py`:

1. Module-level constant (after the `logger` line):

```python
import re

_PROMETHEUS_METRIC_MAP = {
    "raritan_pdu_current_ampere": "current_a",
    "raritan_pdu_activepower_watt": "power_w",
    "raritan_pdu_apparentpower_voltampere": "apparent_power_va",
    "raritan_pdu_voltage_volt": "voltage_v",
    "raritan_pdu_powerfactor": "power_factor",
    "raritan_pdu_linefrequency_hertz": "frequency_hz",
    "raritan_pdu_activeenergy_watthour_total": "energy_wh",
}
```

2. Class attribute on `RaritanPDUClient`:

```python
supports_prometheus_metrics: bool = True
```

3. New private method `_parse_prometheus_text(self, text: str) -> dict`:

```python
def _parse_prometheus_text(self, text: str) -> dict:
    """
    Parse Prometheus text exposition format from Raritan PDU.

    Returns:
        {
            "outlets": [{"outlet_number": int, "name": str, "current_a": float|None, ...}],
            "inlets":  [{"inlet_number": int,  "name": str, "current_a": float|None, ...}],
        }
    Skips OCP (overcurrentprotector) and per-poleline lines.
    Outlet IDs are numeric strings ("1", "2", ...).
    Inlet IDs are like "I1", "I2".
    """
    line_re = re.compile(r"^(\w+)\{([^}]+)\}\s+([\d.eE+\-]+)")
    label_re = re.compile(r'(\w+)="([^"]*)"')

    outlets: dict[str, dict] = {}
    inlets: dict[str, dict] = {}

    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        m = line_re.match(line)
        if not m:
            continue
        metric_name, labels_str, value_str = m.groups()

        field = _PROMETHEUS_METRIC_MAP.get(metric_name)
        if not field:
            continue

        labels = dict(label_re.findall(labels_str))

        # Skip OCP lines and per-poleline lines
        if "overcurrentprotectorid" in labels or "poleline" in labels:
            continue

        try:
            value = round(float(value_str), 2)
        except ValueError:
            continue

        outlet_id = labels.get("outletid")
        inlet_id = labels.get("inletid")

        if outlet_id:
            if outlet_id not in outlets:
                outlets[outlet_id] = {"name": labels.get("outletname", "")}
            outlets[outlet_id][field] = value
        elif inlet_id:
            if inlet_id not in inlets:
                inlets[inlet_id] = {"name": labels.get("inletname", "")}
            if field not in inlets[inlet_id]:
                inlets[inlet_id][field] = value

    outlet_list = []
    for oid in sorted(outlets, key=lambda x: int(x)):
        d = outlets[oid]
        outlet_list.append({
            "outlet_number": int(oid),
            "name": d.get("name", ""),
            "current_a": d.get("current_a"),
            "power_w": d.get("power_w"),
            "voltage_v": d.get("voltage_v"),
            "power_factor": d.get("power_factor"),
            "energy_wh": d.get("energy_wh"),
        })

    inlet_list = []
    for iid in sorted(inlets):
        d = inlets[iid]
        inlet_num_m = re.search(r"\d+", iid)
        inlet_list.append({
            "inlet_number": int(inlet_num_m.group()) if inlet_num_m else 1,
            "name": d.get("name", ""),
            "current_a": d.get("current_a"),
            "power_w": d.get("power_w"),
            "apparent_power_va": d.get("apparent_power_va"),
            "voltage_v": d.get("voltage_v"),
            "power_factor": d.get("power_factor"),
            "frequency_hz": d.get("frequency_hz"),
            "energy_wh": d.get("energy_wh"),
        })

    return {"outlets": outlet_list, "inlets": inlet_list}
```

4. New public method `get_all_metrics_prometheus(self) -> dict`:

```python
def get_all_metrics_prometheus(self) -> dict:
    """
    Fetch all outlet and inlet metrics from the Raritan Prometheus endpoint.

    Single HTTP GET request to /cgi-bin/dump_prometheus.cgi?include_names=1.
    Returns {"outlets": [...], "inlets": [...]}.
    Does NOT return outlet switching state or energy reset time.
    Raises PDUClientError on any network or HTTP failure.
    """
    url = f"{self.base_url}/cgi-bin/dump_prometheus.cgi?include_names=1"
    try:
        response = self.session.get(url, verify=self.verify_ssl, timeout=10)
        response.raise_for_status()
    except requests.exceptions.SSLError as e:
        raise PDUClientError(f"SSL error: {e}") from e
    except requests.exceptions.ConnectionError as e:
        raise PDUClientError(f"Connection error: {e}") from e
    except requests.exceptions.Timeout as e:
        raise PDUClientError(f"Request timed out: {url}") from e
    except requests.exceptions.HTTPError as e:
        raise PDUClientError(f"HTTP error {response.status_code}: {e}") from e
    return self._parse_prometheus_text(response.text)
```

- [ ] **Step 1: Write failing tests**

Add to `test_backends_raritan.py`:

```python
SAMPLE_PROMETHEUS = """\
# HELP raritan_pdu_activeenergy_watthour_total Total activeenergy consumed in watthour
# TYPE raritan_pdu_activeenergy_watthour_total counter
raritan_pdu_activeenergy_watthour_total{pduid="1", pduname="My PDU", inletid="I1", inletname="Main"} 30857906.58
raritan_pdu_activeenergy_watthour_total{pduid="1", pduname="My PDU", outletid="1", outletname="Server01"} 553051.00
raritan_pdu_activeenergy_watthour_total{pduid="1", pduname="My PDU", outletid="2", outletname=""} 896839.58
#HELP raritan_pdu_activepower_watt The current value of the activepower in watt
#TYPE raritan_pdu_activepower_watt gauge
raritan_pdu_activepower_watt{pduid="1", pduname="My PDU", inletid="I1", inletname="Main"} 124.49
raritan_pdu_activepower_watt{pduid="1", pduname="My PDU", outletid="1", outletname="Server01"} 0.00
raritan_pdu_activepower_watt{pduid="1", pduname="My PDU", outletid="2", outletname=""} 124.49
# HELP raritan_pdu_current_ampere The current value of the current in ampere
# TYPE raritan_pdu_current_ampere gauge
raritan_pdu_current_ampere{pduid="1", pduname="My PDU", inletid="I1", inletname="Main"} 0.64
raritan_pdu_current_ampere{pduid="1", pduname="My PDU", overcurrentprotectorid="C1", overcurrentprotectorname=""} 0.64
raritan_pdu_current_ampere{pduid="1", pduname="My PDU", overcurrentprotectorid="C1", overcurrentprotectorname="", poleline="L1"} 0.64
raritan_pdu_current_ampere{pduid="1", pduname="My PDU", outletid="1", outletname="Server01"} 0.00
raritan_pdu_current_ampere{pduid="1", pduname="My PDU", outletid="2", outletname=""} 0.64
#HELP raritan_pdu_voltage_volt The current value of the voltage in volt
#TYPE raritan_pdu_voltage_volt gauge
raritan_pdu_voltage_volt{pduid="1", pduname="My PDU", inletid="I1", inletname="Main"} 199.71
raritan_pdu_voltage_volt{pduid="1", pduname="My PDU", outletid="1", outletname="Server01"} 199.71
raritan_pdu_voltage_volt{pduid="1", pduname="My PDU", outletid="2", outletname=""} 199.71
#HELP raritan_pdu_powerfactor The current value of the powerfactor
#TYPE raritan_pdu_powerfactor gauge
raritan_pdu_powerfactor{pduid="1", pduname="My PDU", inletid="I1", inletname="Main"} 0.97
raritan_pdu_powerfactor{pduid="1", pduname="My PDU", outletid="1", outletname="Server01"} 1.00
raritan_pdu_powerfactor{pduid="1", pduname="My PDU", outletid="2", outletname=""} 0.97
#HELP raritan_pdu_linefrequency_hertz The current value of the linefrequency in hertz
#TYPE raritan_pdu_linefrequency_hertz gauge
raritan_pdu_linefrequency_hertz{pduid="1", pduname="My PDU", inletid="I1", inletname="Main"} 50.0
# HELP raritan_pdu_apparentpower_voltampere The current value of the apparentpower in voltampere
# TYPE raritan_pdu_apparentpower_voltampere gauge
raritan_pdu_apparentpower_voltampere{pduid="1", pduname="My PDU", inletid="I1", inletname="Main"} 128.44
"""


class TestParsePrometheusText(unittest.TestCase):
    """Tests for _parse_prometheus_text()."""

    def setUp(self):
        self.client = _make_client()

    def test_outlet_count(self):
        """Parses correct number of outlets."""
        result = self.client._parse_prometheus_text(SAMPLE_PROMETHEUS)
        self.assertEqual(len(result["outlets"]), 2)

    def test_outlet_metrics(self):
        """Outlet metrics are parsed correctly."""
        result = self.client._parse_prometheus_text(SAMPLE_PROMETHEUS)
        outlet1 = result["outlets"][0]
        self.assertEqual(outlet1["outlet_number"], 1)
        self.assertEqual(outlet1["name"], "Server01")
        self.assertEqual(outlet1["current_a"], 0.0)
        self.assertEqual(outlet1["power_w"], 0.0)
        self.assertEqual(outlet1["voltage_v"], 199.71)
        self.assertEqual(outlet1["power_factor"], 1.0)
        self.assertEqual(outlet1["energy_wh"], 553051.0)

    def test_outlet_empty_name(self):
        """Empty outletname becomes empty string."""
        result = self.client._parse_prometheus_text(SAMPLE_PROMETHEUS)
        self.assertEqual(result["outlets"][1]["name"], "")

    def test_outlet_number_ordering(self):
        """Outlets are sorted by outlet_number ascending."""
        result = self.client._parse_prometheus_text(SAMPLE_PROMETHEUS)
        numbers = [o["outlet_number"] for o in result["outlets"]]
        self.assertEqual(numbers, sorted(numbers))

    def test_ocp_lines_skipped(self):
        """OCP and poleline entries are not counted as outlets or inlets."""
        result = self.client._parse_prometheus_text(SAMPLE_PROMETHEUS)
        for outlet in result["outlets"]:
            self.assertIn("outlet_number", outlet)
        # No OCP bleed into outlets
        self.assertEqual(len(result["outlets"]), 2)

    def test_inlet_metrics(self):
        """Inlet metrics are parsed correctly."""
        result = self.client._parse_prometheus_text(SAMPLE_PROMETHEUS)
        self.assertEqual(len(result["inlets"]), 1)
        inlet = result["inlets"][0]
        self.assertEqual(inlet["inlet_number"], 1)
        self.assertEqual(inlet["name"], "Main")
        self.assertEqual(inlet["current_a"], 0.64)
        self.assertEqual(inlet["power_w"], 124.49)
        self.assertEqual(inlet["apparent_power_va"], 128.44)
        self.assertEqual(inlet["voltage_v"], 199.71)
        self.assertEqual(inlet["power_factor"], 0.97)
        self.assertEqual(inlet["frequency_hz"], 50.0)
        self.assertEqual(inlet["energy_wh"], 30857906.58)

    def test_unknown_metrics_ignored(self):
        """Unknown metric names do not cause errors and are ignored."""
        text = 'unknown_metric{outletid="1"} 99.0\n'
        result = self.client._parse_prometheus_text(text)
        self.assertEqual(result["outlets"], [])

    def test_empty_text(self):
        """Empty input returns empty lists."""
        result = self.client._parse_prometheus_text("")
        self.assertEqual(result["outlets"], [])
        self.assertEqual(result["inlets"], [])


class TestGetAllMetricsPrometheus(unittest.TestCase):
    """Tests for get_all_metrics_prometheus()."""

    def setUp(self):
        self.client = _make_client()

    def _mock_get(self, text, status_code=200):
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        resp.raise_for_status = MagicMock()
        if status_code >= 400:
            resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
        self.client.session.get.return_value = resp

    def test_calls_prometheus_endpoint(self):
        """Requests the correct Prometheus URL with include_names=1."""
        self._mock_get(SAMPLE_PROMETHEUS)
        self.client.get_all_metrics_prometheus()
        call_url = self.client.session.get.call_args[0][0]
        self.assertIn("/cgi-bin/dump_prometheus.cgi", call_url)
        self.assertIn("include_names=1", call_url)

    def test_returns_parsed_data(self):
        """Returns parsed outlet and inlet data."""
        self._mock_get(SAMPLE_PROMETHEUS)
        result = self.client.get_all_metrics_prometheus()
        self.assertIn("outlets", result)
        self.assertIn("inlets", result)
        self.assertEqual(len(result["outlets"]), 2)

    def test_raises_on_http_error(self):
        """Raises PDUClientError on HTTP 401."""
        self._mock_get("", status_code=401)
        with self.assertRaises(PDUClientError):
            self.client.get_all_metrics_prometheus()

    def test_raises_on_connection_error(self):
        """Raises PDUClientError on ConnectionError."""
        self.client.session.get.side_effect = requests.exceptions.ConnectionError("refused")
        with self.assertRaises(PDUClientError):
            self.client.get_all_metrics_prometheus()

    def test_supports_prometheus_metrics_flag(self):
        """RaritanPDUClient.supports_prometheus_metrics is True."""
        self.assertTrue(self.client.supports_prometheus_metrics)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/tak/project/personal/netbox/netbox-pdu-control
uvx pytest netbox_pdu_control/tests/test_backends_raritan.py::TestParsePrometheusText \
           netbox_pdu_control/tests/test_backends_raritan.py::TestGetAllMetricsPrometheus -v
```

Expected: FAIL (methods not yet implemented)

- [ ] **Step 3: Implement `_PROMETHEUS_METRIC_MAP`, `_parse_prometheus_text()`, `get_all_metrics_prometheus()` in `raritan.py`**

- Add `import re` at the top of the file (after `import logging`)
- Add `_PROMETHEUS_METRIC_MAP` as a module-level constant (after `logger = ...`)
- Add `supports_prometheus_metrics: bool = True` as a class attribute on `RaritanPDUClient` (after the `__init__` method)
- Add `_parse_prometheus_text()` and `get_all_metrics_prometheus()` as methods (insert before `# Internal helpers` section)

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uvx pytest netbox_pdu_control/tests/test_backends_raritan.py::TestParsePrometheusText \
           netbox_pdu_control/tests/test_backends_raritan.py::TestGetAllMetricsPrometheus -v
```

Expected: all PASS

- [ ] **Step 5: Run all raritan unit tests**

```bash
uvx pytest netbox_pdu_control/tests/test_backends_raritan.py -v
```

Expected: all PASS

- [ ] **Step 6: Lint**

```bash
make lint
```

- [ ] **Step 7: Commit**

```bash
git add netbox_pdu_control/backends/raritan.py \
        netbox_pdu_control/tests/test_backends_raritan.py
git commit -m "Add Prometheus metrics endpoint support to RaritanPDUClient"
```

---

### Task 3: Add `ManagedPDUGetMetricsView` and wire up URL

**Files:**
- Modify: `netbox_pdu_control/views.py`
- Modify: `netbox_pdu_control/urls.py`

- [ ] **Step 1: Add the view to `views.py`**

Insert after `ManagedPDUSyncView` (after line 238):

```python
class ManagedPDUGetMetricsView(View):
    """
    Fetch outlet/inlet metrics from the Prometheus endpoint and update metric
    fields only. Does not touch outlet status, energy_reset_at, or PDU hardware
    info. Only available for backends that support Prometheus metrics.
    """

    def post(self, request, pk):
        managed_pdu = get_object_or_404(models.ManagedPDU, pk=pk)

        if not request.user.has_perm("netbox_pdu_control.change_managedpdu"):
            messages.error(request, _("You do not have permission to update metrics."))
            return redirect(managed_pdu.get_absolute_url())

        client = get_pdu_client(managed_pdu)

        if not client.supports_prometheus_metrics:
            messages.warning(request, _("This PDU vendor does not support Prometheus metrics."))
            return redirect(managed_pdu.get_absolute_url())

        try:
            with transaction.atomic():
                now = timezone.now()
                data = client.get_all_metrics_prometheus()

                outlet_updated = 0
                for outlet_data in data.get("outlets", []):
                    update_fields = {
                        "current_a": outlet_data.get("current_a"),
                        "power_w": outlet_data.get("power_w"),
                        "voltage_v": outlet_data.get("voltage_v"),
                        "power_factor": outlet_data.get("power_factor"),
                        "energy_wh": outlet_data.get("energy_wh"),
                        "last_updated_from_pdu": now,
                    }
                    if outlet_data.get("name"):
                        update_fields["outlet_name"] = outlet_data["name"]
                    outlet_updated += models.PDUOutlet.objects.filter(
                        managed_pdu=managed_pdu,
                        outlet_number=outlet_data["outlet_number"],
                    ).update(**update_fields)

                inlet_updated = 0
                for inlet_data in data.get("inlets", []):
                    update_fields = {
                        "current_a": inlet_data.get("current_a"),
                        "power_w": inlet_data.get("power_w"),
                        "apparent_power_va": inlet_data.get("apparent_power_va"),
                        "voltage_v": inlet_data.get("voltage_v"),
                        "power_factor": inlet_data.get("power_factor"),
                        "frequency_hz": inlet_data.get("frequency_hz"),
                        "energy_wh": inlet_data.get("energy_wh"),
                        "last_updated_from_pdu": now,
                    }
                    if inlet_data.get("name"):
                        update_fields["inlet_name"] = inlet_data["name"]
                    inlet_updated += models.PDUInlet.objects.filter(
                        managed_pdu=managed_pdu,
                        inlet_number=inlet_data["inlet_number"],
                    ).update(**update_fields)

            messages.success(
                request,
                f"Metrics updated: {outlet_updated} outlets, {inlet_updated} inlets.",
            )
            logger.info(
                "Metrics fetch succeeded [%s]: outlets=%d inlets=%d",
                managed_pdu,
                outlet_updated,
                inlet_updated,
            )

        except PDUClientError as e:
            messages.error(request, f"Metrics fetch error: {e}")
            logger.error("Metrics fetch failed [%s]: %s", managed_pdu, e)

        return redirect(managed_pdu.get_absolute_url())
```

- [ ] **Step 2: Add URL to `urls.py`**

After the `managedpdu_sync` path, add:

```python
path(
    "managed-pdus/<int:pk>/get-metrics/",
    views.ManagedPDUGetMetricsView.as_view(),
    name="managedpdu_get_metrics",
),
```

- [ ] **Step 3: Lint**

```bash
make lint
```

Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add netbox_pdu_control/views.py netbox_pdu_control/urls.py
git commit -m "Add ManagedPDUGetMetricsView and get-metrics URL"
```

---

### Task 4: Add "Get Metrics" button to template

**Files:**
- Modify: `netbox_pdu_control/templates/netbox_pdu_control/managedpdu.html`

- [ ] **Step 1: Add the button**

In `managedpdu.html`, find the `card-footer` block containing the "Sync PDU" button (around line 100):

```html
        {% if perms.netbox_pdu_control.change_managedpdu %}
          <div class="card-footer">
            <form method="post" action="{% url 'plugins:netbox_pdu_control:managedpdu_sync' pk=object.pk %}">
              {% csrf_token %}
              <button type="submit" class="btn btn-primary btn-sm">
                <i class="mdi mdi-refresh"></i> Sync PDU
              </button>
            </form>
          </div>
        {% endif %}
```

Replace with:

```html
        {% if perms.netbox_pdu_control.change_managedpdu %}
          <div class="card-footer d-flex gap-2">
            <form method="post" action="{% url 'plugins:netbox_pdu_control:managedpdu_sync' pk=object.pk %}">
              {% csrf_token %}
              <button type="submit" class="btn btn-primary btn-sm">
                <i class="mdi mdi-refresh"></i> Sync PDU
              </button>
            </form>
            <form method="post" action="{% url 'plugins:netbox_pdu_control:managedpdu_get_metrics' pk=object.pk %}">
              {% csrf_token %}
              <button type="submit" class="btn btn-outline-primary btn-sm">
                <i class="mdi mdi-chart-line"></i> Get Metrics
              </button>
            </form>
          </div>
        {% endif %}
```

- [ ] **Step 2: Start docker and verify the button appears**

```bash
cd /Users/tak/project/personal/netbox/netbox-docker
docker compose up -d
```

Open http://0.0.0.0:8000/ → navigate to a ManagedPDU detail page → confirm "Get Metrics" button appears next to "Sync PDU"

- [ ] **Step 3: Test "Get Metrics" button manually**

Click "Get Metrics" → confirm success message appears and outlet/inlet metric values update in the table.

- [ ] **Step 4: Commit**

```bash
git add netbox_pdu_control/templates/netbox_pdu_control/managedpdu.html
git commit -m "Add Get Metrics button to PDU detail page"
```

---

## Summary

After all tasks complete:
- 1 HTTP request replaces ~100+ JSON-RPC calls for metric refresh
- "Sync PDU" remains unchanged (full sync with energy_reset_at)
- "Get Metrics" updates only metric values and names, preserving outlet status and energy reset time
- Raritan-only feature (UniFi returns empty via `supports_prometheus_metrics = False`)
