# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Tested Hardware: add Raritan PX4-534AJ-E7 and PX4-5884J-E7
- `docs/design.md`: detailed design document with architecture, ER, and
  sequence diagrams (Mermaid), linked into the MkDocs nav

### Changed
- CI: cache the cloned NetBox repo and pip packages per NetBox/Python matrix
  combination to speed up the `test` job

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
