# 詳細設計書

本ドキュメントは NetBox PDU Control プラグインの内部設計をまとめたものです。コード変更時の参照用として、アーキテクチャ・データモデル・主要な処理フローを図とともに解説します。

対象読者: 本プラグインの開発・保守を行うエンジニア。

---

## 1. 概要

NetBox PDU Control は、NetBox に登録された `Device`(PDU 本体)に対して、ベンダー各社の管理 API 経由でアウトレット/インレットの状態取得・電源制御・名前同期を行う NetBox プラグインです。

- 対応ベンダー: Raritan(JSON-RPC 2.0)、Ubiquiti UniFi USP-PDU-Pro(REST API)
- ベンダー差異は `backends/` 配下の抽象化レイヤーに閉じ込め、View 層・同期ロジックはベンダー非依存
- NetBox 標準の generic view / DRF / GraphQL(strawberry-django)パターンに準拠

---

## 2. 全体アーキテクチャ

```mermaid
graph TB
    subgraph NetBox
        UI["Web UI<br/>(managedpdu.html ほか)"]
        API["REST API<br/>(api/views.py, api/serializers.py)"]
        GQL["GraphQL<br/>(graphql/schema.py, types.py)"]
        DB[("PostgreSQL<br/>ManagedPDU / PDUOutlet / PDUInlet 等")]
        RQ["django-rq Worker<br/>(jobs.py)"]
        SJ["NetBox System Job<br/>(JobRunner, system_job)"]
    end

    subgraph Plugin["netbox_pdu_control"]
        Views["views.py<br/>(ObjectView / power / sync / push-name)"]
        Jobs["jobs.py<br/>sync_managed_pdu()<br/>fetch_pdu_metrics()"]
        Factory["backends/__init__.py<br/>get_pdu_client()"]
        Base["backends/base.py<br/>BasePDUClient (ABC)"]
        Raritan["backends/raritan.py<br/>RaritanPDUClient"]
        UniFi["backends/unifi.py<br/>UniFiPDUClient"]
    end

    subgraph External["外部 PDU"]
        RaritanHW["Raritan PX3/PX4<br/>(Xerus, JSON-RPC 2.0)"]
        UniFiHW["Ubiquiti USP-PDU-Pro<br/>(UniFi Network Controller API)"]
    end

    UI --> Views
    API --> DB
    GQL --> DB
    Views --> DB
    Views --> Jobs
    Views -. "power cycle は非同期" .-> RQ
    RQ --> Jobs
    SJ --> Jobs
    Jobs --> Factory
    Factory --> Base
    Base -.implements.- Raritan
    Base -.implements.- UniFi
    Raritan -->|HTTPS Basic認証<br/>JSON-RPC| RaritanHW
    UniFi -->|HTTPSセッション/APIキー<br/>REST| UniFiHW
    Jobs --> DB
```

**ポイント**

- `views.py` は `get_pdu_client()` 経由でのみベンダー API にアクセスし、ベンダー固有コードを持たない([CLAUDE.md](../CLAUDE.md) の規約通り)。
- 電源サイクル(Power Cycle)のみ `django_rq` でバックグラウンドジョブ化し、5 秒後に状態を再取得する(PDU 側の反映ラグを吸収)。
- 定期実行(`PDUSyncJob` / `PDUGetMetricsJob`)は NetBox 標準の `system_job` デコレータで登録され、`PLUGINS_CONFIG` の `sync_poll_interval` / `metrics_poll_interval` が 0 より大きい場合のみ有効化される。
- `get_pdu_client()` は認証情報を直接 `ManagedPDU` から読まず、`credentials.get_credential()` 経由で解決する(netbox-secrets 優先・平文フォールバック、詳細は[§9](#9-セキュリティ上の考慮事項))。

---

## 3. データモデル(ER 図)

```mermaid
erDiagram
    Device ||--o| ManagedPDU : "1:1 (PDU本体)"
    Device ||--o{ PDUOutlet : "0..N (接続先デバイス)"
    ManagedPDU ||--o{ PDUOutlet : outlets
    ManagedPDU ||--o{ PDUInlet : inlets
    ManagedPDU ||--o{ PDUInletLinePair : inlet_linepairs
    ManagedPDU ||--o{ PDUOverCurrentProtector : ocps
    ManagedPDU ||--o{ PDUNetworkInterface : network_interfaces
    PDUInlet ||--o{ PDUInletLinePair : "inlet_number で対応付け"

    ManagedPDU {
        int id PK
        int device_id FK "OneToOne, on_delete=CASCADE"
        int ip_address_id FK "ipam.IPAddress, 任意, SET_NULL"
        string vendor "raritan / ubiquiti"
        string api_url
        string api_username
        string api_password "平文保存・APIでは非公開"
        bool verify_ssl
        string sync_status
        string metrics_status
        bool sync_enabled
        bool metrics_enabled
        datetime last_synced
        datetime last_metrics_fetched
        string pdu_model
        string serial_number
        string firmware_version
        string grafana_panel_base_url
    }

    PDUOutlet {
        int id PK
        int managed_pdu_id FK
        int outlet_number "PDU上の番号(1-indexed)"
        string outlet_name
        int connected_device_id FK "SET_NULL, 自動同期(ケーブル接続から)"
        string status "on/off/unknown"
        float current_a
        float power_w
        float voltage_v
        float power_factor
        float energy_wh
        datetime last_updated_from_pdu
    }

    PDUInlet {
        int id PK
        int managed_pdu_id FK
        int inlet_number
        string inlet_name
        float current_a
        float power_w
        float apparent_power_va
        float voltage_v
        float frequency_hz
        float poleline_l1_current_a "3相PDUのみ"
        float unbalanced_current_pct "3相PDUのみ"
    }

    PDUInletLinePair {
        int id PK
        int managed_pdu_id FK
        int inlet_number
        string line_pair "L1L2/L2L3/L3L1"
        float voltage_v
        float power_w
    }

    PDUOverCurrentProtector {
        int id PK
        int managed_pdu_id FK
        string ocp_id "C1,C2,C3等"
        float rating_current_a
        bool tripped
    }

    PDUNetworkInterface {
        int id PK
        int managed_pdu_id FK
        string interface_name "ETH1,ETH2等"
        string mac_address
        string ip_address
    }
```

**モデルごとの更新方針**(`CLAUDE.md` にも明記されている重要な差異):

| モデル | 更新方針 | 理由 |
|---|---|---|
| `PDUOutlet` / `PDUInlet` | `update_or_create`(存在すれば更新、なければ作成) | outlet/inlet 番号は基本的に不変 |
| `PDUInletLinePair` / `PDUNetworkInterface` | 全削除→再作成 | 3相/NIC構成が変わりうるため差分管理せず単純化 |
| `PDUOverCurrentProtector` | `update_or_create` | OCP 数は固定だが取得元がメトリクスAPIのみ |
| `PDUOutlet.connected_device` | NetBox の `PowerOutlet` ケーブル接続から自動導出 | 手動フィールドだが sync 時に上書きされる(下記シーケンス参照) |

---

## 4. ベンダーバックエンド抽象化

```mermaid
classDiagram
    class BasePDUClient {
        <<abstract>>
        +bool supports_prometheus_metrics
        +get_pdu_info() dict
        +get_all_outlet_data() list~dict~
        +get_single_outlet_data(index) dict
        +get_all_inlet_data() list~dict~
        +get_single_inlet_data(index) dict
        +set_outlet_power_state(index, state)
        +get_outlet_power_state_by_index(index) str
        +set_outlet_name(index, name)
        +set_inlet_name(index, name)
        +get_outlet_thresholds(index) list~dict~
        +get_inlet_thresholds(index) list~dict~
    }

    class RaritanPDUClient {
        +supports_prometheus_metrics = True
        HTTP Basic認証
        JSON-RPC 2.0 (/model/pdu/0 ほか)
        +get_all_metrics_prometheus() dict
    }

    class UniFiPDUClient {
        +supports_prometheus_metrics = False
        セッション/APIキー認証
        UDM・スタンドアロン両対応の自動判定
        time.sleep(3) を含む電源サイクル実装
    }

    class PDUClientError {
        <<Exception>>
    }

    BasePDUClient <|-- RaritanPDUClient
    BasePDUClient <|-- UniFiPDUClient
    RaritanPDUClient ..> PDUClientError : raises
    UniFiPDUClient ..> PDUClientError : raises

    class get_pdu_client {
        <<factory function>>
        +get_pdu_client(managed_pdu) BasePDUClient
    }
    get_pdu_client ..> BasePDUClient : creates
```

新しいベンダーを追加する手順(`backends/base.py` docstring より):

1. `backends/<vendor>.py` に `BasePDUClient` を実装したクラスを作成
2. `backends/__init__.py` の `_VENDOR_BACKENDS` に登録
3. `choices.VendorChoices` にベンダーを追加
4. マイグレーション作成

---

## 5. 主要シーケンス

### 5.1 PDU フルシンク(`ManagedPDUSyncView`)

```mermaid
sequenceDiagram
    actor User
    participant View as ManagedPDUSyncView
    participant Sync as sync_managed_pdu()
    participant Factory as get_pdu_client()
    participant Vendor as RaritanPDUClient / UniFiPDUClient
    participant PDU as 実機PDU
    participant DB as PostgreSQL

    User->>View: POST /managed-pdus/<pk>/sync/
    View->>View: パーミッションチェック(change_managedpdu)
    View->>Sync: sync_managed_pdu(managed_pdu)
    activate Sync
    Sync->>Factory: get_pdu_client(managed_pdu)
    Factory-->>Sync: Vendorクライアント
    Sync->>Vendor: get_pdu_info()
    Vendor->>PDU: HTTPリクエスト
    PDU-->>Vendor: モデル/シリアル/NIC情報
    Vendor-->>Sync: dict
    Sync->>DB: transaction.atomic() 開始
    Sync->>DB: ManagedPDU 更新(pdu_model等)
    Sync->>DB: Device.serial 同期
    Sync->>DB: PDUNetworkInterface 全削除→再作成
    Sync->>Vendor: get_all_outlet_data()
    Vendor->>PDU: HTTPリクエスト
    PDU-->>Vendor: outlet一覧
    Sync->>DB: PDUOutlet.update_or_create (ループ)
    Sync->>DB: NetBox PowerOutlet のケーブル接続から<br/>connected_device を再計算
    Sync->>Vendor: get_all_inlet_data()
    Vendor->>PDU: HTTPリクエスト
    PDU-->>Vendor: inlet一覧
    Sync->>DB: PDUInlet.update_or_create (ループ)
    Sync->>DB: sync_status=success, last_synced=now
    Sync->>DB: transaction.atomic() コミット
    deactivate Sync
    Sync-->>View: (作成数, 更新数)
    View-->>User: メッセージ表示 + リダイレクト

    Note over Sync,DB: 例外発生時は transaction 全体がロールバックされ、<br/>sync_status=failed のみ update_fields で保存(View側)
```

### 5.2 アウトレット電源制御(ON/OFF は同期、Cycle は非同期)

```mermaid
sequenceDiagram
    actor User
    participant View as PDUOutletPowerView
    participant Vendor as VendorClient
    participant PDU as 実機PDU
    participant RQ as django_rq Queue
    participant Job as update_outlet_status()
    participant DB as PostgreSQL

    User->>View: POST /outlets/<pk>/power-{on|off|cycle}/
    View->>Vendor: set_outlet_power_state(index, state)
    Vendor->>PDU: 電源状態変更コマンド

    alt state = on / off
        View->>Vendor: get_outlet_power_state_by_index(index)
        Vendor->>PDU: 状態取得
        PDU-->>Vendor: on/off
        View->>DB: PDUOutlet.status を即時保存
    else state = cycle
        View->>RQ: enqueue_in(5秒後, update_outlet_status, ...)
        Note right of RQ: PDU側の再起動反映を待つため<br/>5秒ディレイで非同期実行
        RQ-->>Job: 5秒後に実行
        Job->>Vendor: get_outlet_power_state_by_index(index)
        Vendor->>PDU: 状態取得
        Job->>DB: PDUOutlet.status を保存
    end

    View-->>User: メッセージ表示 + リダイレクト
```

### 5.3 名前プッシュ(NetBox → PDU、双方向ラベル同期)

```mermaid
sequenceDiagram
    actor User
    participant View as PDUOutletPushNameView
    participant Vendor as VendorClient
    participant PDU as 実機PDU
    participant DB as PostgreSQL

    User->>View: POST /outlets/<pk>/push-name/
    View->>Vendor: set_outlet_name(index, outlet.outlet_name)
    Vendor->>PDU: 名前書き込み
    alt 成功
        View->>DB: outlet_number に一致する<br/>NetBox PowerOutlet を正規表現で検索
        View->>DB: PowerOutlet.label = outlet_name として保存
        View-->>User: 成功メッセージ(PDU側 + NetBox側の両方)
    else 失敗(PDUClientError)
        View-->>User: エラーメッセージのみ(NetBox側は更新しない)
    end
```

### 5.4 メトリクス取得(Prometheus 対応ベンダーのみ)

```mermaid
sequenceDiagram
    actor User
    participant View as ManagedPDUGetMetricsView
    participant Fetch as fetch_pdu_metrics()
    participant Vendor as RaritanPDUClient
    participant PDU as 実機PDU
    participant DB as PostgreSQL

    User->>View: POST /managed-pdus/<pk>/get-metrics/
    View->>Fetch: fetch_pdu_metrics(managed_pdu)
    Fetch->>Vendor: supports_prometheus_metrics チェック
    alt 非対応ベンダー
        Fetch-->>View: PDUClientError
        View->>DB: metrics_status=failed
        View-->>User: エラーメッセージ
    else 対応ベンダー(Raritan)
        Fetch->>Vendor: get_all_metrics_prometheus()
        Vendor->>PDU: GET /metrics (Prometheus exposition format)
        PDU-->>Vendor: メトリクステキスト
        Vendor-->>Fetch: outlets/inlets/ocps の dict
        Fetch->>DB: transaction.atomic()
        Fetch->>DB: PDUOutlet.filter(...).update(**metrics) ループ
        Fetch->>DB: PDUInletLinePair 全削除→再作成(inlet毎)
        Fetch->>DB: PDUOverCurrentProtector.update_or_create ループ
        Fetch->>DB: metrics_status=success, last_metrics_fetched=now
        Fetch-->>View: (更新件数)
        View-->>User: 成功メッセージ
    end
```

---

## 6. バックグラウンド処理・定期実行

| 種別 | 起動元 | 実装 | 用途 |
|---|---|---|---|
| RQワーカー(即時 enqueue) | `PDUOutletPowerView`(cycle時のみ) | `jobs.update_outlet_status()` | 電源サイクル5秒後の状態再取得 |
| NetBox System Job | `settings.PLUGINS_CONFIG["netbox_pdu_control"]["sync_poll_interval"]` > 0 | `jobs.PDUSyncJob`(`system_job(interval=...)`) | `sync_enabled=True` の全PDUを定期フルシンク |
| NetBox System Job | 同上 `metrics_poll_interval` | `jobs.PDUGetMetricsJob` | `metrics_enabled=True` の全PDUを定期メトリクス取得 |

いずれも1台の失敗が全体を止めないよう `try/except` でPDUごとに独立して処理し、失敗PDUのみ `sync_status`/`metrics_status` を `failed` に更新する。

---

## 7. REST API / GraphQL

```mermaid
graph LR
    subgraph "REST API (api/)"
        SV["serializers.py<br/>ManagedPDUSerializer<br/>PDUOutletSerializer<br/>PDUInletSerializer"]
        VS["views.py<br/>NetBoxModelViewSet × 3"]
    end
    subgraph "GraphQL (graphql/)"
        TY["types.py<br/>strawberry_django.type"]
        FL["filters.py"]
        SC["schema.py<br/>NetBoxMgmtPDUQuery"]
    end
    VS --> SV
    SC --> TY
    TY --> FL
```

- REST API: NetBox標準の `NetBoxModelViewSet` + `NetBoxModelSerializer` パターン。`ManagedPDUSerializer.api_password` は `write_only=True` — **書き込みは可能だがレスポンスには一切出力されない**(平文保存フィールドの漏洩防止)。
- GraphQL: strawberry-django ベース。`enums.py` が `choices.py` の選択肢をGraphQL Enumとしてミラーリング。

---

## 8. URL構成(`urls.py`)

NetBox標準の `get_model_urls()` によるCRUD URL(一覧・詳細・作成・編集・削除)に加え、以下の非CRUDエンドポイントを個別定義:

- `managed-pdus/<pk>/sync/` — フルシンク
- `managed-pdus/<pk>/get-metrics/` — メトリクス取得
- `managed-pdus/<pk>/bulk-power/` — 複数アウトレット一括電源制御
- `managed-pdus/test-connection/`(pkなし)— Add/Edit フォームの入力値(未保存)で接続テスト
- `outlets/<pk>/{sync,power-on,power-off,power-cycle,push-name}/`
- `inlets/<pk>/{sync,push-name}/`

**Add/Edit フォームの接続テスト・IP自動入力:** `ManagedPDUEditView` は `template_name` を
`netbox_pdu_control/managedpdu_edit.html` に明示的に上書きしている(NetBoxの generic
`ObjectEditView` は `<app>/<model>_edit.html` を自動探索しないため、`netbox-bmc` と同様に
明示指定が必要)。このテンプレートで:
- **Test Connection** ボタン: フォームの vendor/api_url/api_username/api_password/verify_ssl を
  `ManagedPDUConnectionTestView` に fetch で POST し、保存前に接続確認する(DBには一切書き込まない)
- **IP Address** ピッカー(`ip_address` フィールド、`query_params={"device_id": "$device"}` で
  選択中の Device に紐づくIPのみ表示): 選択すると JS が `/api/ipam/ip-addresses/<id>/` を
  fetch し、`api_url` フィールドに `https://<ip>` を自動入力する(`ip_address` 自体はDBに
  保存されるが、実際の接続には `api_url` の値のみが使われる)

---

## 9. セキュリティ上の考慮事項

- 認証情報は `credentials.py` の `get_credential()` が解決する。優先順位は次の通り(`netbox-bmc` プラグインの方式を踏襲):
  1. **netbox-secrets**(導入済みの場合)— role `pdu-credentials` を持ち Device に紐づけられた `Secret`。`Secret.name`=ユーザー名、`Secret.plaintext`=パスワード(RSA暗号化)。Web View 経由ならリクエストのセッションキーで、バックグラウンドジョブ(system job / RQ)なら `PLUGINS_CONFIG["netbox_pdu_control"]["service_account"]` のサービスアカウント秘密鍵で復号する。
  2. **平文フォールバック** — netbox-secrets 未導入・該当Secretなし・復号失敗時は `ManagedPDU.api_username`/`api_password` にフォールバックする。
  復号に失敗した場合はエラーログを残した上でフォールバックする(無音でのフォールバックは運用上気付きにくいため)。
- `get_pdu_client(managed_pdu, request=None)` は `request` を `get_credential()` に転送する。View 層からは常に `request` を渡し、System Job/RQ ジョブからは `request=None`(サービスアカウント経路)で呼び出す。
- `ManagedPDU.api_password`(フォールバックフィールド)は **平文で DB に保存**される(NetBox標準の暗号化フィールドは未使用)。
- REST APIシリアライザ・ログ出力のいずれにも `api_password` を含めないこと(`CLAUDE.md` にも明記された規約)。
- 電源サイクル用のRQジョブ(`jobs.update_outlet_status`)は、以前は `managed_pdu.api_password` を平文でジョブ引数としてRedisに渡していたが、実装はその引数を使わず `outlet.managed_pdu` から再取得していたため、この不要な平文受け渡しは削除済み。
- 全ての制御系View(sync / power / push-name)は `request.user.has_perm("netbox_pdu_control.change_managedpdu")` を個別にチェックしてから処理する。
- `verify_ssl=False` の場合、両バックエンドで `urllib3.disable_warnings(InsecureRequestWarning)` を呼び出し、自己署名証明書運用時の警告ログ氾濫を抑止(意図的な設計)。

---

## 10. 関連ドキュメント

- [README](../README.md) — インストール手順、対応ハードウェア、設定例
- [CONTRIBUTING](../CONTRIBUTING.md) — 開発フロー、リリース手順
- [COMPATIBILITY](../COMPATIBILITY.md) — NetBoxバージョン互換表
- [CHANGELOG](../CHANGELOG.md) — バージョンごとの変更履歴
