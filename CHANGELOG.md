# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `ManagedPDU.web_gui_url_dns` property: when `ip_address.dns_name` is set,
  the Actions card shows separate "Web GUI (IP)" / "Web GUI (DNS)" links
  instead of a single link, mirroring netbox-bmc's Web GUI (IP)/(DNS)
  behavior
- ManagedPDU detail page: new "Credentials" card showing whether PDU
  credentials are resolved from a netbox-secrets `Secret` (role
  `pdu-credentials`) or the plaintext fallback fields, mirroring
  netbox-bmc's Credentials card. The redundant "API Username" row was
  removed from the main info card (now shown here instead, only when
  falling back to plaintext)

### Changed
- ManagedPDU detail page: consolidated the "PDU WebGUI" link (previously in
  the page header, renamed to "Web GUI" to match netbox-bmc), and the
  "Sync PDU" / "Get Metrics" buttons (previously in the Sync Status card
  footer) into a single "Actions" card at the top of the right column,
  mirroring the pattern used by netbox-bmc

---

## [0.4.1] - 2026-07-19

### Added
- **Test Connection** button on the ManagedPDU Add/Edit form: verifies
  vendor/API URL/credentials work before saving, without writing anything
  to NetBox (mirrors the pattern used by netbox-bmc)
- `ManagedPDU.ip_address`: optional FK to `ipam.IPAddress`, filtered to IPs
  assigned to the selected Device. Selecting one auto-fills the API URL
  field with `https://<ip>` via JS; the API URL field remains the value
  actually used for connections
- `docs/netbox-secrets-setup.md`: added a Docker (netbox-docker) setup path
  alongside the existing non-Docker instructions, including how to bind-mount
  the service account's private key into the container

### Changed
- The **Test Connection** button now prefers a netbox-secrets `Secret` on
  the selected Device over the typed API Username/Password fields, matching
  `get_pdu_client()`'s own credential priority

---

## [0.4.0] - 2026-07-18

### **Breaking Changes**
- Dropped support for NetBox 4.5.x; `min_version` is now `4.6.0`. CI no longer
  tests against NetBox 4.5.4. If you are running NetBox 4.5.x, upgrade to
  4.6.x before updating this plugin past 0.3.6.

### Added
- Tested Hardware: add Raritan PX4-534AJ-E7 and PX4-5884J-E7
- `docs/design.md`: detailed design document with architecture, ER, and
  sequence diagrams (Mermaid), linked into the MkDocs nav
- `docs/netbox-secrets-setup.md`: step-by-step guide for configuring
  netbox-secrets (SecretRole, User Keys, service account) with this plugin
- PDU credential resolution via [netbox-secrets](https://github.com/Onemind-Services-LLC/netbox-secrets)
  (`SecretRole` slug `pdu-credentials`, assigned to the Device), mirroring the
  pattern used by netbox-bmc. Falls back to the existing plaintext
  `api_username`/`api_password` fields when netbox-secrets is not installed or
  has no matching secret. Add `service_account`/`service_private_key_path` to
  `PLUGINS_CONFIG["netbox_pdu_control"]` to let background/system jobs decrypt
  secrets without an HTTP session.

### Changed
- CI: cache the cloned NetBox repo and pip packages per NetBox/Python matrix
  combination to speed up the `test` job
- CI: drop NetBox 4.5.4 from the test matrix (see Breaking Changes above);
  only NetBox 4.6.3 is tested now
- CI: skip the `test` job entirely for docs-only changes (`*.md`, `docs/**`,
  `mkdocs.yml`)
- `get_pdu_client()` now accepts an optional `request` argument, forwarded
  from all views so netbox-secrets can decrypt using the requesting user's
  session key
- `ManagedPDU.api_password` is now `blank=True` (optional), since it may be
  fully superseded by a netbox-secrets `Secret`

### Fixed
- The Power Cycle background job no longer receives the PDU password as a
  plaintext RQ job argument (stored in Redis) — the argument was unused by
  the job, which already re-fetched credentials via the outlet's `managed_pdu`
- `credentials.py`'s session-key decryption path now matches the real
  netbox-secrets API: reads the `netbox_secrets_sessionid` cookie (via
  `netbox_secrets.constants.SESSION_COOKIE_NAME`, not a hardcoded
  `session_key`) and decrypts through `UserKey.session_key.get_master_key()`
  rather than calling `UserKey.get_master_key()` directly (that method only
  accepts a private key in netbox-secrets, not a session key)

---

## [0.3.6] - 2026-07-18

### Added
- Managed PDUs list and detail page: "PDU WebGUI" button that opens the PDU's
  `api_url` in a new tab (shown for Raritan PDUs only)

---

## [0.3.5] - 2026-07-07

### Changed
- Device PDU Outlets card: W and Wh columns now use comma-separated
  thousands formatting (`intcomma`) for readability

---

## [0.3.4] - 2026-07-06

### Added
- Device "PDU Outlets" card: table now shows Status, W, A, V, PF, and Wh
  columns per outlet, plus a totals row (power, current, energy) and a
  "Last updated" timestamp footer

---

## [0.3.3] - 2026-07-02

### Fixed
- `__version__` in `netbox_pdu_control/__init__.py` had drifted out of sync
  with `pyproject.toml`; corrected to 0.3.2 as an interim fix

### Changed
- `__version__` is now derived from package metadata via
  `importlib.metadata.version("netbox-pdu-control")` for a single source of
  truth, preventing future version-drift issues

---

## [0.3.2] - 2026-07-01

### Added
- `grafana_panel_base_url` field on ManagedPDU; when set alongside
  `pdu_name`, an embedded Grafana panel iframe is rendered on the detail
  page, filtered by `var-pduname`

### Changed
- Corrected author name in `pyproject.toml`

---

## [0.3.1] - 2026-06-25

### Fixed
- Suppress `InsecureRequestWarning` in all backends when `verify_ssl=False`
  (previously only UniFi suppressed it; Raritan was missing it)

### Changed
- README: add `PLUGINS_CONFIG` example documenting `metrics_poll_interval`
  and `sync_poll_interval` options

---

## [0.3.0] - 2026-06-25

### Release Summary
Compatibility release adding NetBox 4.6 support.

### Added
- NetBox 4.6.x support (`max_version` bumped to 4.6.99)

### Changed
- Package renamed from `netbox-pdu-plugin` to `netbox-pdu-control`

### Fixed
- Test configuration: set `DEBUG=False` to avoid django-debug-toolbar system check in NetBox 4.6

---

## [0.2.0] - 2026-03-29

### Release Summary
Feature release that adds automatic metrics collection, PDU metadata synchronization, and improved operational visibility.

### Added
- Periodic "Get Metrics" system job for ManagedPDU synchronization
- PDU name synchronization and display in NetBox
- `metrics_status` and `last_metrics_fetched` tracking for ManagedPDU
- `sync_metrics_enabled` control for metrics polling

### Changed
- Improved ManagedPDU forms, tables, views, and API serializers to expose new sync fields
- Updated backend handling for richer PDU metadata collection

### Fixed
- Job status cleanup now includes PDU metrics jobs on startup

### Deprecated
- N/A

### Removed
- N/A

### Security
- N/A

---

## [0.1.0] - 2026-03-22

### Release Summary
Initial release of NetBox PDU Plugin. This is a **minor** release introducing basic functionality for managing PDU resources in NetBox.

### Added
- Initial plugin structure with Pdu model
- Basic CRUD operations through NetBox UI
- Change logging and journaling support
- Custom fields and tags support
- REST API endpoints for programmatic access
- GraphQL support for flexible queries
- Comprehensive test suite
- Documentation with MkDocs

### Fixed
- N/A (initial release)

### Changed
- N/A (initial release)

### Deprecated
- N/A (initial release)

### Removed
- N/A (initial release)

### Security
- N/A (initial release)

---

## Release Notes Template for Future Versions

When creating a new release, use this template:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Release Summary
Brief narrative summary describing the release type (major/minor/patch) and key highlights.

### **Breaking Changes**
<!-- Only include this section if there are breaking changes -->
- **[#issue]** Description of breaking change and migration path
- Link to detailed migration guide if needed

### Added
- New features and capabilities

### Fixed
- Bug fixes with issue references

### Changed
- Changes to existing functionality

### Deprecated
- Features marked for future removal

### Removed
- Features that have been removed

### Security
- Security improvements and fixes
```

---

**Best Practice**: For clear release communication, ensure each release includes:
1. Narrative summary characterizing the release type (major/minor/patch)
2. Clear indicators for bugs, features, or enhancements
3. Bold "Breaking Changes" header when applicable with migration guidance
4. Detailed changelog with issue references
