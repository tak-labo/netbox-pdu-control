# netbox-secrets 連携セットアップ手順

本ドキュメントは、PDU の API 認証情報(ユーザー名・パスワード)を平文フィールドではなく
[netbox-secrets](https://github.com/Onemind-Services-LLC/netbox-secrets) 経由で管理するための
セットアップ手順です。アーキテクチャの詳細は [design.md §9](design.md#9-セキュリティ上の考慮事項) を参照してください。

対象読者: NetBox 管理者・本プラグインの運用担当者。

---

## 1. 前提: netbox-secrets のインストール・有効化

netbox-secrets 自体のインストール手順は [公式リポジトリ](https://github.com/Onemind-Services-LLC/netbox-secrets) に従ってください。

```bash
pip install netbox-secrets
```

`configuration.py` に追加:

```python
PLUGINS = [
    "netbox_pdu_control",
    "netbox_secrets",
]

PLUGINS_CONFIG = {
    "netbox_secrets": {
        # Secret をどのモデルに割り当て可能にするか。Device に割り当てるので必須。
        "apps": [
            "dcim.device",
        ],
    },
}
```

**`apps` について:** netbox-secrets がどの NetBox モデルに Secret を紐付け可能にするかを、
`app_label.model` 形式で列挙する設定です。ここに列挙されていないモデルには Secret を
紐付けることはできません。効果は2つあります:

1. **割り当て先の制限** — Secret 作成時に `assigned_object_type` として選択できるモデルを、
   ここに列挙したものだけに絞る(netbox-secrets 内部の `SECRET_ASSIGNABLE_MODELS` フィルタ)
2. **UIへの自動表示** — 列挙したモデルの詳細画面に「Secrets」パネル/タブが自動的に追加される

本プラグインは PDU の認証情報を **Device** に紐付ける設計(§2〜§5 参照)なので、
`"apps": ["dcim.device"]` が必須です。他のモデル(仮想マシンなど)にも netbox-secrets を
使いたい場合は、そのモデルをこのリストに追記してください。

マイグレーションと static ファイル収集:

```bash
python manage.py migrate
python manage.py collectstatic --no-input
```

netbox-pdu-control 側は netbox-secrets を**ソフト依存**として扱うため(`pyproject.toml` に依存関係として追加していません)、
netbox-secrets が未導入でも既存の平文フィールド(`API Username` / `API Password`)がそのまま動作します。
以降の手順は netbox-secrets を実際に使う場合のみ必要です。

---

## 2. SecretRole を作成する

本プラグインは role の **slug が `pdu-credentials`** の `Secret` を検索します(`credentials.py` の `SECRET_ROLE_SLUG` 定数)。

1. NetBox UI で **Secrets → Secret Roles → Add** を開く
2. Name: 任意(例: `PDU Credentials`)、**Slug は必ず `pdu-credentials`** にする
3. 保存

---

## 3. 管理者(あなた自身)の User Key を作成・有効化する

netbox-secrets は「マスターキー」を各ユーザーの RSA 公開鍵で暗号化して保持する方式です。最初に有効化された
User Key が自動的にマスターキーを生成します。

1. **Secrets → User Keys → Add** を開く
2. RSA 鍵ペアを新規生成するか、既存の公開鍵を貼り付ける
   (公開鍵生成 API: `GET /api/plugins/secrets/generate-rsa-key-pair/` でも生成可能)
3. 保存 — これが最初の User Key であれば、この時点で自動的にアクティブ化されマスターキーが生成される
4. 生成された **秘密鍵は必ず安全な場所に保管**する(サーバーには保存されない。失うと自分の Secret が復号できなくなる)

---

## 4. サービスアカウントを作成する(バックグラウンドジョブ用)

定期同期(`PDUSyncJob` / `PDUGetMetricsJob`)や電源サイクル後のRQジョブはHTTPリクエストを持たないため、
ログインユーザーのセッションキーではなく、専用のサービスアカウントの秘密鍵でマスターキーを復号します。

1. NetBox に新規ユーザーを作成する(例: `pdu-sync`)。ログイン用途ではないので強力なランダムパスワードを設定し、
   グループ/パーミッションは Secret の読み取りに必要な最小限に絞る
2. このユーザーで(または管理権限を持つユーザーが代理で)RSA鍵ペアを生成する
3. **Secrets → User Keys → Add** で `pdu-sync` ユーザーの User Key を作成
   - 手順3で最初のUser Keyが既に存在する場合、この新しいUser Keyは非アクティブな状態で作成される
   - **Secrets → User Keys → Activate User Keys** を開き、既にアクティブな鍵を持つ管理者が
     自分の秘密鍵を使って `pdu-sync` の User Key を有効化する(マスターキーが `pdu-sync` の公開鍵でも
     暗号化され、これで `pdu-sync` の秘密鍵でも復号可能になる)
4. `pdu-sync` の**秘密鍵**をNetBoxサーバー上の安全な場所に配置する(例: `/opt/netbox/pdu-sync.pem`、
   パーミッションは `600`、所有者は NetBox 実行ユーザーのみ読み取り可能に)

```bash
chmod 600 /opt/netbox/pdu-sync.pem
chown netbox:netbox /opt/netbox/pdu-sync.pem
```

5. `configuration.py` に追記:

```python
PLUGINS_CONFIG = {
    "netbox_pdu_control": {
        # ...既存の設定...
        "service_account": "pdu-sync",
        "service_private_key_path": "/opt/netbox/pdu-sync.pem",
    },
}
```

6. NetBox を再起動してPLUGINS_CONFIGを反映する

---

## 5. PDU の認証情報を Secret として登録する

各 `ManagedPDU` に対応する NetBox `Device` に、role `pdu-credentials` の Secret を1件作成します。

1. 対象デバイスの詳細画面を開く(または REST API `POST /api/plugins/secrets/secrets/`)
2. Secret を追加:
   - **Role**: `pdu-credentials`(手順2で作成したもの)
   - **Name**: PDU の API ユーザー名(例: `admin`)— これが `credentials.py` で `username` として使われる
   - **Plaintext**: PDU の API パスワード — これが `password` として使われる
3. 保存にはセッションキーが必要。UI操作中であればブラウザが自動的に処理する
   (未取得の場合は秘密鍵の入力を求められる)

保存後、この Device に紐づく `ManagedPDU` の同期・電源制御は、次回アクセス時から
このSecretの値を優先して使用します。既存の `API Username` / `API Password` フィールドの値は
削除する必要はありません(フォールバックとして残しておけます)。

---

## 6. 動作確認

- **Web UI からの操作**(Sync ボタン、Power ON/OFF など): ログイン中のユーザー自身の
  セッションキーで復号されます。ブラウザでログインしていれば追加操作は不要です。
- **バックグラウンド/定期実行**(`PDUSyncJob`、`PDUGetMetricsJob`、Power Cycle 後の RQ ジョブ):
  手順4で設定したサービスアカウントの秘密鍵で復号されます。

うまく復号できない場合(鍵の不一致、Secretが存在しない等)は、`netbox_pdu_control.credentials` ロガーに
`ERROR` レベルで理由が記録され、既存の平文フィールドに自動フォールバックします(処理自体は止まりません)。
ログを確認してください:

```bash
docker compose logs netbox | grep netbox_pdu_control.credentials
```

---

## 7. トラブルシューティング

| 症状 | 原因の候補 |
|---|---|
| 常に平文フィールドが使われる(Secretが反映されない) | SecretRole の slug が `pdu-credentials` になっていない / Secret が対象 Device に紐づいていない |
| Web UI操作時にフォールバックする | 自分の User Key が未作成・非アクティブ、またはセッションキー期限切れ |
| バックグラウンドジョブ実行時にフォールバックする | `service_account` / `service_private_key_path` が未設定、またはそのユーザーの User Key が非アクティブ |
| エラーログに "No UserKey found" | 該当ユーザー(ログインユーザーまたはサービスアカウント)の User Key が作成されていない |
| エラーログに "No active session key" | ブラウザ側でセッションキーが未取得(netbox-secrets 側のUI操作を一度行う) |

---

## 関連ドキュメント

- [design.md](design.md) — アーキテクチャ全体・セキュリティ考慮事項
- [netbox-secrets 公式リポジトリ](https://github.com/Onemind-Services-LLC/netbox-secrets)
