# NetBox PDU Control

NetBox plugin for PDU management.

![NetBox](https://img.shields.io/badge/NetBox-4.5%20%7C%204.6-blue)
![Python](https://img.shields.io/badge/Python-3.12%2B-blue)
![PyPI](https://img.shields.io/pypi/v/netbox-pdu-control)
![License](https://img.shields.io/badge/License-Apache%202.0-green)

> **[日本語版](README.ja.md)**

---

## Supported Vendors

| Vendor | Product | Protocol | Authentication |
|--------|---------|----------|----------------|
| Raritan | Xerus series | JSON-RPC 2.0 over HTTPS | HTTP Basic Auth |
| Ubiquiti | USP-PDU-Pro | UniFi Network Controller REST API | API Key or Session |

---

## Tested Hardware

The following hardware has been confirmed to work with this plugin:

| Vendor | Model | Version | Notes |
|--------|-------|---------|-------|
| Raritan | PX3-5138JR | Firmware 4.3.x (Xerus) | Full support: outlets, inlets, power control, thresholds |
| Raritan | PX3-5201JR | Firmware 4.3.x (Xerus) | Full support: outlets, inlets, power control, thresholds |
| Raritan | PX3-5496JV | Firmware 4.3.x (Xerus) | Full support: outlets, inlets, power control, thresholds |
| Raritan | PX3-5497JV | Firmware 4.3.x (Xerus) | Full support: outlets, inlets, power control, thresholds |
| Raritan | PX3-5702JV | Firmware 4.3.x (Xerus) | Full support: outlets, inlets, power control, thresholds |
| Raritan | PX4-534AJ-E7 | Firmware 4.3.x (Xerus) | Full support: outlets, inlets, power control, thresholds |
| Raritan | PX4-5884J-E7 | Firmware 4.3.x (Xerus) | Full support: outlets, inlets, power control, thresholds |
| Ubiquiti | USP-PDU-Pro | UniFi OS 5.0.16 / Network 10.1.89 | Outlet control/monitoring; inlet data via aggregate power; no thresholds |

Other Raritan PDUs running Xerus firmware (PX2, PX3, PX4, BCM families) should work, but have not been directly tested.

---

## Features

- **Sync** hardware info (model, serial, firmware, rated power/voltage/current), network settings (IP, MAC, NTP, DNS) from the PDU
- **Outlet monitoring** — voltage, current, active power, power factor, accumulated energy per outlet
- **Inlet monitoring** — total input current, voltage, power, apparent power, frequency
- **Power control** — ON / OFF / Power Cycle per outlet with one click
- **Name push** — write outlet/inlet names from NetBox back to the PDU; syncs `PowerOutlet.label` / `PowerPort.label` on the connected device automatically
- **Threshold display** — warning and critical thresholds per sensor (Raritan only)
- **Background jobs** — post-cycle status refresh via RQ worker
- **Config backup** — save the PDU's on-device configuration to NetBox (Device Config Context) and, optionally, a local git repo, via a manual button or periodic job (Raritan only)
- **REST API & GraphQL** — full NetBox-native API for all models
- **Multi-vendor architecture** — add new vendors by implementing a single base class

---

## Install

### Standard (non-Docker)

**1. Install the package**

```bash
source /opt/netbox/venv/bin/activate
pip install netbox-pdu-control
```

**2. Enable the plugin**

Add to `/opt/netbox/netbox/netbox/configuration.py`:

```python
PLUGINS = ["netbox_pdu_control"]

PLUGINS_CONFIG = {
    "netbox_pdu_control": {
        # Interval in minutes for automatic Prometheus metrics fetch.
        # Set to 0 or remove to disable periodic fetching.
        "metrics_poll_interval": 5,
        # Interval in minutes for automatic full PDU sync (hardware info, outlets, inlets).
        # Set to 0 or remove to disable periodic syncing.
        "sync_poll_interval": 60,
        # Optional: directory for an additional local git-backed PDU config backup,
        # for unlimited-retention history beyond NetBox's own Change Log (which
        # defaults to 90 days via the CHANGELOG_RETENTION admin setting). The plugin
        # runs `git init` here automatically on first use. Config is always saved to
        # the PDU's Device > Config Context regardless of this setting.
        # Requires the `git` CLI to be installed in the NetBox web and worker
        # environments for the optional git-backup path to work.
        # "config_backup_path": "/opt/netbox/pdu-config-backups",
        # Interval in minutes for automatic config backup. Set to 0 or remove to disable.
        "config_backup_poll_interval": 0,
        # Only needed if netbox-secrets is installed and used for PDU credentials —
        # lets background/system jobs decrypt secrets without an HTTP session.
        # "service_account": "pdu-sync",
        # "service_private_key_path": "/opt/netbox/pdu-sync.pem",
    }
}
```

**3. Run migrations and restart**

```bash
cd /opt/netbox/netbox
python manage.py migrate
sudo systemctl restart netbox netbox-rq
```

---

### Docker (netbox-docker)

See also: [Using NetBox Plugins](https://github.com/netbox-community/netbox-docker/wiki/Using-Netbox-Plugins)

**1. Create `plugin_requirements.txt`**

```
netbox-pdu-control
```

**2. Create `Dockerfile-Plugins`**

```dockerfile
FROM netboxcommunity/netbox:latest

COPY ./plugin_requirements.txt /opt/netbox/
RUN /usr/local/bin/uv pip install -r /opt/netbox/plugin_requirements.txt

COPY configuration/configuration.py /etc/netbox/config/configuration.py
COPY configuration/plugins.py /etc/netbox/config/plugins.py
RUN DEBUG="true" SECRET_KEY="dummydummydummydummydummydummydummydummydummydummy" \
    /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py collectstatic --no-input
```

**3. Configure `docker-compose.override.yml`**

```yaml
services:
  netbox:
    image: netbox:latest-plugins
    pull_policy: never
    ports:
      - 8000:8080
    build:
      context: .
      dockerfile: Dockerfile-Plugins
  netbox-worker:
    image: netbox:latest-plugins
    pull_policy: never
```

**4. Enable the plugin**

Add to `configuration/plugins.py`:

```python
PLUGINS = ["netbox_pdu_control"]

PLUGINS_CONFIG = {
    "netbox_pdu_control": {
        # Interval in minutes for automatic Prometheus metrics fetch.
        # Set to 0 or remove to disable periodic fetching.
        "metrics_poll_interval": 5,
        # Interval in minutes for automatic full PDU sync (hardware info, outlets, inlets).
        # Set to 0 or remove to disable periodic syncing.
        "sync_poll_interval": 60,
        # Optional: directory for an additional local git-backed PDU config backup,
        # for unlimited-retention history beyond NetBox's own Change Log (which
        # defaults to 90 days via the CHANGELOG_RETENTION admin setting). The plugin
        # runs `git init` here automatically on first use. Config is always saved to
        # the PDU's Device > Config Context regardless of this setting.
        # Requires the `git` CLI to be installed in the NetBox web and worker
        # environments for the optional git-backup path to work.
        # "config_backup_path": "/opt/netbox/pdu-config-backups",
        # Interval in minutes for automatic config backup. Set to 0 or remove to disable.
        "config_backup_poll_interval": 0,
        # Only needed if netbox-secrets is installed and used for PDU credentials —
        # lets background/system jobs decrypt secrets without an HTTP session.
        # "service_account": "pdu-sync",
        # "service_private_key_path": "/opt/netbox/pdu-sync.pem",
    }
}
```

**5. Build, start, and migrate**

```bash
docker compose build --no-cache
docker compose up -d
docker compose exec netbox python manage.py migrate
```

---

## Configure

### Create a ManagedPDU

Go to **Plugins → PDU Management → Add** and fill in the connection details.

| Field | Description |
|-------|-------------|
| Device | NetBox Device record for this PDU |
| IP Address | Optional: pick from IPs already assigned to the selected Device — auto-fills API URL with `https://<ip>` |
| Vendor | `Raritan` or `Ubiquiti (USP-PDU-Pro)` |
| API URL | Base URL of the PDU or UniFi controller (e.g. `https://192.168.1.1`) |
| API Username | Username for authentication. Leave blank on Ubiquiti to use API key mode |
| API Password | Password or API key |
| Verify SSL | Uncheck if the PDU uses a self-signed certificate |

Click **Test Connection** on the Add/Edit form to verify the vendor/API URL/credentials work
before saving — this does not write anything to NetBox, it only checks connectivity.

### Ubiquiti-specific options

- **API key mode**: leave `API Username` blank and set `API Password` to the API key — no session required
- **Controller type**: UDM/UCG and standalone controllers are auto-detected
- **Site**: append `/s/<site>` to the API URL to target a non-default site (e.g. `https://192.168.1.1/s/mysite`)

---

## Credentials

netbox-pdu-control resolves PDU credentials in the following order:

1. **netbox-secrets** (preferred) — `Secret` with role `pdu-credentials` assigned to the Device.
   `Secret.name` = API username, `Secret.plaintext` = API password (RSA-encrypted).
2. **Plaintext fallback** — the `API Username` / `API Password` fields on `ManagedPDU`, used when
   netbox-secrets is not installed, no matching secret is found, or the secret can't be decrypted
   (see the session key note below).

For background jobs (scheduled sync/metrics), set `service_account` and `service_private_key_path`
in `PLUGINS_CONFIG` so the job can decrypt secrets without an HTTP session.

> **netbox-secrets session key**: web-triggered actions (Sync, Push Name to PDU, Save Config, ...)
> decrypt the Secret using netbox-secrets' per-browser-session key, not just being logged in to
> NetBox. If you haven't unlocked netbox-secrets in the current browser session (or it expired),
> these actions silently fall back to the plaintext fields above — if those are also empty, the
> request fails with a clear "No usable PDU credentials" error rather than a confusing 401 from
> the PDU. Unlock netbox-secrets (enter your RSA private key and request a session key) and retry.

---

## Use

### Syncing a PDU

Open a ManagedPDU detail page and click **Sync**. The plugin fetches hardware info and updates all outlet and inlet records. Sync status and timestamp are displayed on the detail page.

### Power control

On an outlet detail page, use the **Actions** card to turn the outlet ON, OFF, or trigger a Power Cycle. After a cycle, the status is updated automatically by a background job.

### Pushing names to the PDU

On an outlet or inlet detail page, click **Push Name to PDU** to write the name stored in NetBox to the PDU. The corresponding `PowerOutlet.label` or `PowerPort.label` on the connected NetBox device is also updated.

### Config backup

On a ManagedPDU detail page, click **Save Config** to fetch the PDU's full on-device configuration. It's always written to the Device's Config Context (`local_context_data`), and additionally committed to a local git repo when `config_backup_path` is set in `PLUGINS_CONFIG`. A periodic job can do the same automatically — see `config_backup_poll_interval` and `config_backup_enabled` (per-PDU toggle).

Once at least two snapshots have been saved (git-backed only), two views compare them, each showing the commit date and short hash of both sides:

- The ManagedPDU detail page's **Config Diff** card — a unified diff.
- The Device detail page's **PDU Config** tab — a full side-by-side comparison.

---

## Versions

| Plugin version | NetBox version |
|---------------|----------------|
| 0.4.0 – 0.5.0 | 4.6.0+ (NetBox 4.5.x support dropped) |
| 0.3.0 – 0.3.6 | 4.5.0 – 4.6.xx |
| 0.1.0 – 0.2.0 | 4.5.0 – 4.5.xx |

---

## Vendor notes

### Raritan

- Uses JSON-RPC 2.0, not standard REST — each resource path is a separate endpoint
- Outlet power control uses 0-based index (`/model/pdu/0/outlet/{N}`)
- Outlet/inlet data is accessed via opaque RIDs returned by `getOutlets` / `getInlets`

### Ubiquiti

- `outlet_overrides` requires all outlets in a single PUT — partial updates reset unspecified outlets
- Inlet API is not supported — `outlet_ac_power_consumption` is exposed as Inlet 1 instead
- Sensor thresholds are not available via the UniFi API

---

## Development

```bash
# Run the same checks as CI (lint + Docker integration tests)
make ci

# Lint only
make lint

# Run integration tests via Docker
docker compose exec netbox python manage.py test netbox_pdu_control.tests -v 2

# Restart after code changes
docker compose restart netbox netbox-worker

# Apply migrations
docker compose exec netbox python manage.py migrate

# Generate migrations (requires DEVELOPER=True)
docker compose exec -e DEVELOPER=True netbox python manage.py makemigrations netbox_pdu_control
```

A pre-push hook is installed automatically by pre-commit. It runs lint and Docker integration tests before every `git push`.

```bash
# Install the pre-push hook (run once after cloning)
uvx pre-commit install --hook-type pre-push
```

### Adding a new vendor

1. Create `netbox_pdu_control/backends/<vendor>.py` implementing `BasePDUClient`
2. Register it in `netbox_pdu_control/backends/__init__.py` under `_VENDOR_BACKENDS`
3. Add the choice to `VendorChoices` in `netbox_pdu_control/choices.py`
4. Generate and apply a migration

---

## License

This project is licensed under the [Apache License 2.0](LICENSE).
See [NOTICE](NOTICE) for attribution details.

