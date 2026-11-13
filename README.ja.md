# beom-oss-analyzer

[English](README.md) | [한국어](README.ko.md) | **日本語** | [简体中文](README.zh-CN.md)

**オープンソースを漏れなく分析し、必要な機能だけを自分のリポジトリに移植して、オリジナルと同じであることを検証するツールキット**

![version](https://img.shields.io/badge/version-0.3.0-blue) ![python](https://img.shields.io/badge/python-3.11%2B-blue) ![license](https://img.shields.io/badge/license-use%20only%2C%20no%20modification-lightgrey)

他人が作った機能を「ざっと読んで真似して作る」のではなく、コード全体を根拠(ファイル:行)付きで分析し、
移植した結果がオリジナルと同じ動作をするかをプログラムで確認します。Claude Code のような AI エージェントが手順どおりに使えるよう
スキル・MCP ツール・CLI をまとめて提供し、AI が単独で決めてはならないことは、人の承認なしには完了しないようにしています。

## 目次

- [できること](#できること)
- [主要な概念](#主要な概念)
- [動作要件](#動作要件)
- [インストール](#インストール)
- [デスクトップアプリと自動実行](#デスクトップアプリと自動実行)
- [クイックスタート 1: リポジトリの全件分析](#クイックスタート-1-リポジトリの全件分析)
- [クイックスタート 2: 機能の移植と検証](#クイックスタート-2-機能の移植と検証)
- [クイックスタート 3: 要望から作る](#クイックスタート-3-要望から作る)
- [オリジナルを改修して使う場合 (カスタム)](#オリジナルを改修して使う場合-カスタム)
- [ケースファイルの形式](#ケースファイルの形式)
- [コマンド一覧](#コマンド一覧)
- [データと保存場所](#データと保存場所)
- [設定](#設定)
- [安全方針](#安全方針)
- [既知の制限](#既知の制限)
- [ライセンス](#ライセンス)

## できること

| やりたいこと | ツールキットが行うこと |
|---|---|
| **機能を漏れなく把握** | すべてのファイルを分類し、すべての関数をレビュー単位に分けて、結論ごとに根拠(ファイル:行)を付けます。確定できなかったものは「未確認リスト」として残します。 |
| **実際の API 一覧** | フレームワークごとのルールで HTTP・CLI のエントリポイントを見つけ、コード内で組み立てられる URL まで展開して実際のパス一覧を作成します。 |
| **変更前の影響確認** | 変更しようとする関数がどこに影響するか(呼び出し元・実装・エントリポイント・テスト)、コアに手を入れずに差し込める拡張ポイントがどこにあるかを示します。 |
| **オリジナルのアップデートへの追従** | 2 つのバージョンを比較し、自分たちの改修と衝突する箇所(関数の削除・名前変更・シグネチャ変更)を見つけます。 |
| **機能だけを選んで移植** | 1 つのエントリポイントから必要なコード・状態・設定・外部依存を切り出し、項目ごとにどこへ移植したかを台帳に記録します。別の言語で作り直しても構いません。 |
| **移植結果の検証** | 同じ入力をオリジナルと自分のコードに与えて結果を比較します(HTTP・CLI・関数単位)。意図的に変えた動作は期待値で、オリジナルにない新しい動作も期待値で検証します。 |
| **欲しい機能を作る** | 参考にするオリジナルなしで「こんな機能を作って」から始め、要件と合格基準を先に承認してもらってから作り、基準どおりにできたか・異常な入力にも耐えるかを検証します。 |
| **AI の近道を阻止** | 差分の許容・機能の省略・テストの縮小・期待値は、人が承認するまで完了になりません。検証後にコードが変わったり、記録がツールキットの外で書き換えられたりすると検出します。 |

## 主要な概念

**根拠レベル** — 結論がどれだけ確かかをレベルとして記録します。

| レベル | 意味 | 取得方法 |
|---|---|---|
| L0 | コードを読み、構造マップで確認 | デフォルト |
| L1 | 言語サーバーで呼び出し・参照を確認 | `lsp` |
| L2 | 値がどこからどこへ流れるかを確認 | `dataflow` |
| L3 | 実際に実行されることを確認 | カバレッジレポート、差分検証 |

**台帳** — 分析・移植の状態は、プロジェクトごとの SQLite 台帳(`~/.oss-analyzer/projects/<id>/ledger.db`)に蓄積されます。
セッションが変わっても、複数のエージェントが同時に作業しても、同じ記録を引き継いで書き込みます。

**移植完了の条件 (ゲート 7 つ)** — すべてを満たしたときにのみ、`port-finish` が完了判定を出します。

1. 移植対象の項目がすべて処理済み (移植 / 代替 / 省略)
2. 移植先の位置が現在もそのまま存在する (行の内容まで)
3. 差分検証に合格 — 最後に保存したケースすべてについて、**現在のコード基準** で (検証後にコードが変わると無効)
4. ケースが移植対象の関数をすべて実行している (オリジナルのカバレッジ、取得できない場合は自分のコードのカバレッジ)
5. 人による確認項目がすべて承認済み (差分の許容・省略・ケース変更・期待値。承認後に内容が変わると無効)
6. 記録がツールキットの外で変更されていない (台帳のハッシュチェーン・ゴールデンハッシュ)
7. ライセンス確認が必要なファイルがない

すべての移植コマンドの応答には `next_step`(次にやること)が付きます。

## 動作要件

| 区分 | 内容 |
|---|---|
| 必須 | Python 3.11+, git |
| 必須エンジン | 構造マップエンジン、エントリポイントルールエンジン — `setup` がインストール (チェックサム検証) |
| 任意 | 言語サーバー(L1)、データフローエンジン(L2, JDK 21+)、カバレッジツール(L3)、コンテナエンジン(他人のコードの隔離実行) |
| 言語サーバー(L1) | Rust · Java · Kotlin · TypeScript/JavaScript · Python · Go · C/C++ · C# · Dart · Ruby (その他の言語は L0 まで。ファイル・シンボルの分類は言語に依存しません) |
| エントリポイントルール | Axum · Spring · Express · FastAPI/Flask · Gin · clap · Next.js (ルールのないフレームワークは自動的に未確認扱い) |

エンジン名・ライセンス・インストール先は [THIRD_PARTY.md](THIRD_PARTY.md) に記載しています。

## インストール

```bash
# 1. ツールキットのインストール — どのフォルダからでも oss-analyzer コマンドで使えるように
pipx install git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git
# (または) uv tool install git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git

# 2. 状態チェック — エンジン・言語サーバー・スキル・MCP 登録
oss-analyzer doctor

# 3. エンジンのインストール — まず計画だけを確認し、選んだものだけをインストール
oss-analyzer setup --languages python typescript
oss-analyzer setup --apply codebase-memory-mcp ast-grep lsp:python lsp:typescript coverage
```

- `setup` はオプションなしで実行すると **計画だけ** を表示し、何も変更しません。`--apply` に指定したものだけをインストールします。
- 他人のコードを隔離して実行するには、コンテナエンジンが必要です。Windows では `setup --apply sandbox` が WSL ディストリビューション内に
  コンテナエンジンをインストールします (デスクトップアプリは不要)。すでに `docker` があればそれを使用します。
- JDK・Node.js・Go・Rust などの言語ランタイムはご自身でインストールしてください。

### AI エージェント (Claude Code) との連携

どちらか一方だけを使用します。

```bash
# A. setup でスキル + MCP を登録
oss-analyzer setup --apply skill mcp-register
oss-analyzer setup --apply skill mcp-register --scope project --project-dir <プロジェクト>   # 1 つのプロジェクトでのみ

# B. Claude Code プラグイン (ツールキットは先に上記の 1 でインストール)
claude plugin marketplace add ParkBeomsoo/beom-oss-analyzer
claude plugin install beom-oss-analyzer@beom-oss-analyzer
```

- スキルはインストールしたコピーにバージョンを記録するため、ツールキットが更新されると `doctor` が `outdated` として知らせます。
- 同じ名前のスキルを自作している場合や、別のインストールを指す MCP 登録がある場合は、上書きせずに知らせます。
- MCP を使わず CLI だけで使用しても構いません。承認・完了判定などのルールは CLI でも同じように動作します。

### アップデートとアンインストール

```bash
pipx upgrade beom-oss-analyzer
oss-analyzer setup --update                      # インストール済みのスキルだけを新しいバージョンに (新規インストールはしない)
oss-analyzer setup --remove skill mcp-register   # このツールキットがインストール・登録したものだけを元に戻す
pipx uninstall beom-oss-analyzer
```

## デスクトップアプリと自動実行

```bash
pipx install "beom-oss-analyzer[app] @ git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git"
beom-oss-analyzer                    # アプリを開く
oss-analyzer app --shortcut          # スタートメニュー(検索) + デスクトップのショートカット作成、--remove-shortcut で削除
```

初めてアプリを開いたときにもショートカットを作るか尋ねます。Linux では `.desktop` エントリを作ります。

**ホームでモードを選ぶ**

| モード | こんなとき | 段階 |
|---|---|---|
| **要望ベース** | こういう機能を作って (参照する元がない) | 要件 → 合格基準 → 計画 → 実装 → 検証 → 引き継ぎ |
| **オープンソースベース** | このリポジトリの機能を自分のプロジェクトへ | 分析 → 範囲 → 元の記録 → 計画 → 実装 → 元と比較 → 引き継ぎ |

オープンソースモードで必須なのはリポジトリの URL (またはパス) だけです。機能は入力欄に言葉で書くか、空のままにするとエージェントが機能一覧を示して選ばせます。

**エージェント · モデル · 努力** — 送信ボタンの隣のボタン一つで選びます。

| エージェント | 補足 |
|---|---|
| Claude Code | 操作ごとにツールキットの権限ルールで確認。モデル: 既定 · Opus · Sonnet · Haiku (エイリアスなので常に最新)、努力 低 → 最大 |
| Codex | 操作ごとの質問の代わりにサンドボックスで制限 (対象フォルダ内のみ書き込み)。モデルと努力の段階は Codex がアカウント向けに保持する一覧をそのまま使うので、新しいモデルも自動で表示 |
| Gemini CLI | 実験的、努力の設定なし |

一覧にないモデル名は直接入力できます。モデルと努力は実行中にも変更できます (次のラウンドから)。

**権限** — モードを選び、種類別の詳細は *オプション → 権限* にあります。

| モード | 動作 |
|---|---|
| すべて確認 | 編集・コマンドごとに確認 |
| 編集を自動許可 (既定) | 対象フォルダ内の編集とビルド・テストはそのまま、その他のコマンドとウェブは確認 |
| 自動 | コマンドも確認なしで実行、ウェブとフォルダ外のファイルのみ確認 — 信頼できる作業にのみ |

push・公開・フォルダ外の削除・システム変更は常に拒否します。エージェントが検証ツールに渡すコマンド (ケースファイル内のコマンドを含む) も同じルールを通ります。
認証: *自分のログイン* (サブスクリプション) または *API キー* (環境変数のキーで従量課金) — できることは同じです。

**人が決めること** — これだけがカードで表示されます。

- **要件と合格基準**、次に **計画**: *承認*、*修正* (要件・ケース・計画の手順を自分で直して *保存して承認* — 元帳にユーザー作成として残り
  エージェントにも伝達)、または入力欄に直してほしい点を書く (承認せずにエージェントが修正)。
- エージェントの **質問** と **権限リクエスト** (`Y` 許可 · `N` 拒否)。
- 最後の **引き継ぎ**: 受け入れるか、メッセージを送って続行 — 停止・完了した実行にメッセージを送るとそのまま続きます。

承認はアプリ内でのみ、表示された内容そのものに結び付けて元帳に記録されます。エージェントが届く承認経路はありません。

**右パネル**

| タブ | 内容 |
|---|---|
| 状況 | 進捗、計画の手順、要件、完了条件 |
| 基準 | 合意したもの — 要件、合格ケース (入力 → 期待)、計画、誰がいつ承認したか |
| 検証 | 検証したもの — 要件ごとのケースの入力 → 期待 → 実際の結果、各完了条件の意味 |
| 変更 | 変更ファイルと diff、ファイル単位の取り消し、チェックポイント (ラウンドごとに対象フォルダを記録し任意の時点へ戻す) |

**ラウンド** — エージェントのセッション 1 回が 1 ラウンドです。要望 1 件で通常 4〜6 ラウンド (質問・修正があれば増加)、既定の上限は 8 です。
2 ラウンド続けて進展がなければ自動で止まり、いつでもメッセージで続けられるので、上限はコストの安全装置にすぎません。

**データの保存場所** — すべて手元のコンピュータに残ります。[データと保存場所](#データと保存場所)を参照してください。

アプリなし: `oss-analyzer run --mode request --target-root <フォルダ> --request "<要望>"` (`--agent codex`、`--resume <実行>`)、回答はアプリで行います。

Claude Code は[公式ガイド](https://code.claude.com/docs)どおりにインストールして `claude auth login`、Codex は `npm i -g @openai/codex` の後 `codex login`
(確認: `oss-analyzer agent-status`)。自分のコンピュータで本人が使うならサブスクリプションのログイン、他の人にサービスするなら API キーを使います
([Anthropic 規約](https://code.claude.com/docs/en/legal-and-compliance))。

## クイックスタート 1: リポジトリの全件分析

**AI エージェントに任せる場合** — Claude Code で:

> このリポジトリを全件分析して https://github.com/<組織>/<リポジトリ>

スキルが以下の手順に従って進め、大きなリポジトリでは複数のサブエージェントが単位を分担して並列に分析します。

**CLI で直接行う場合**

```bash
oss-analyzer open https://github.com/<組織>/<リポジトリ> --ref v1.2.0   # 分析専用 clone、コミット固定 → project_id
oss-analyzer inventory <pid>                    # ファイル分類・シンボル・構造マップ・エントリポイント・分析単位
oss-analyzer features <pid>                     # 実際の URL・CLI エントリポイント一覧

# 分析ループ (単位がなくなるまで繰り返す)
oss-analyzer next <pid> --agent a1              # 次の単位を受け取る
oss-analyzer context <pid> <unit_id> --agent a1 --raw
oss-analyzer finding <pid> <unit_id> --agent a1 --json finding.json   # 結論 + 根拠
oss-analyzer unknown-add <pid> --agent a1 --json unknown.json        # 確定できなかったもの
oss-analyzer complete <pid> <unit_id> --agent a1 --summary "この単位が行うこと"

oss-analyzer coverage <pid>                     # ファイル・シンボルの 2 軸の完了率と失敗項目
oss-analyzer unknowns <pid>                     # 残っている未確認
oss-analyzer export <pid>                       # Markdown + JSON レポート
```

`finding.json` の例:

```json
{"text": "注文作成時にまず在庫をロックし、決済失敗時に解除する", "severity": "important",
 "evidence": [{"path": "src/order/service.rs", "start_line": 120, "end_line": 158}],
 "symbol_ids": ["s_1a2b3c"]}
```

## クイックスタート 2: 機能の移植と検証

例: オリジナルの `POST /api/v1/orders` を、自分のリポジトリに別の言語で移植する場合。

```bash
# 1. 移植範囲を確認 → 移植台帳を作成
oss-analyzer slice <pid> "POST /api/v1/orders"
oss-analyzer port-create <pid> --name 注文 --targets "POST /api/v1/orders" --language javascript --target-root <自分のリポジトリ>

# 2. オリジナルの応答を保存 (ゴールデン) — オリジナルはコンテナで隔離実行
oss-analyzer port-capture <pid> <port_id> --cases cases.json \
    --source-image <オリジナルの実行イメージ> --source-url http://127.0.0.1:18001 --source-cmd "<オリジナルのサーバー起動コマンド>" \
    --coverage-cmd "<カバレッジレポート生成コマンド>" --coverage-report cov.json --explore 40

# 3. 自分のコードを書く → 項目ごとにどこへ移植したかを記録
oss-analyzer port-map <pid> <port_id> --json map.json

# 4. 比較検証 (同時リクエストまで)
oss-analyzer port-verify <pid> <port_id> --target-url http://127.0.0.1:18002 --target-cmd "node server.js" --concurrency 8

# 5. 進捗状況と次にやること
oss-analyzer port-status <pid> <port_id>

# 6. 人による承認 — ユーザーがターミナルで直接実行 (エージェントが代わりに実行すると拒否される)
oss-analyzer port-approve <pid> <port_id>

# 7. 完了判定 — ゲート 7 つがすべて満たされたときにのみ発行
oss-analyzer port-finish <pid> <port_id>
```

`map.json` の例 — 移植(`ported`)は自分のコードの `パス:行`、代替・省略には理由が必要です。

```json
[{"item": "s_1a2b3c", "status": "ported", "target": "src/orders/create.js:12"},
 {"item": "s_4d5e6f", "status": "replaced", "note": "オリジナルのキューの代わりに既存のメッセージサービスを使用"},
 {"item": "s_7a8b9c", "status": "omitted", "note": "管理者統計は今回の範囲外"}]
```

**移植する機能を変更して取り込む**

| 状況 | 方法 | 判定 |
|---|---|---|
| オリジナルと同じであるべき | ケースだけを作成 | オリジナルの応答と比較 |
| 意図的に変える (値を決められる) | ケースに `expect` | オリジナルの代わりに期待値と比較、期待値は人が承認 |
| オリジナルにない新しい動作 | `"new": true` + `expect` | オリジナルは実行せず、期待値で判定 |
| 形式だけが異なり、値は決められない | `port-accept --case <名前> --reason "<理由>"` | 差分の許容を提案 → 人が承認 |
| 機能の一部を外す | `port-map` で `omitted` + 理由 | 人が承認 |

- 承認画面には、オリジナルの応答と期待値(または実際の差分)が並べて表示されます。承認後に内容が変わると再承認が必要です。
- オリジナルをカバレッジで計測できない場合は、`port-verify --target-coverage-cmd ... --target-coverage-report lcov.info` で自分のコード側の実行範囲を測定します。
- 自分のコードを修正して行がずれた場合は、`port-map --follow` で移植先の位置を更新します。

## クイックスタート 3: 要望から作る

アプリで **要望ベース** を選ぶか、CLI で:

```bash
oss-analyzer make-create --name 注文合計 --request "数量を受け取って合計を計算。1 個 2000 ウォン、10 個以上で 10% 割引、0 以下なら 400" --target-root <フォルダ>
oss-analyzer make-req <pid> <port_id> --json reqs.json          # [{"text": "合計 = 数量 x 2000 ウォン"}, …] → R1, R2 …
oss-analyzer port-capture <pid> <port_id> --cases cases.json --explore 20   # 要件ごとに expect + req
oss-analyzer port-approve <pid> <port_id>                       # 実装前に要件・合格基準を承認
oss-analyzer port-map <pid> <port_id> --json map.json           # 要件 → 実装位置 "パス:行"
oss-analyzer port-verify <pid> <port_id> --target-url http://127.0.0.1:8080 --target-cmd "<サーバー起動コマンド>"
oss-analyzer port-finish <pid> <port_id>
```

- ケースごとに `req`(確認する要件)と `expect`(期待値)が必要で、要件ごとに少なくとも 1 つのケースが必要です。
- 入力の自動変形ケースは、期待値の代わりに **クラッシュしない・5xx でない** ことで判定します (異常な入力は 4xx で拒否して初めて合格)。
- 組み込み先のリポジトリがある場合は、先に `open`・`inventory` してから `--context <その project_id>` で渡すと、既存の構造に合わせて作ります。

## オリジナルを改修して使う場合 (カスタム)

```bash
oss-analyzer extpoints <pid>                                  # まず、コアに手を入れずに差し込める箇所を確認
oss-analyzer impact <pid> pricing.calc_total --verify         # 変更すると影響を受けるエントリポイント・テスト・結論
oss-analyzer custom-add <pid> --description "割引計算の変更" --kind modify --targets pricing.calc_total

# オリジナルの新しいバージョンが出たら
oss-analyzer open <リポジトリ> --ref v1.3.0 && oss-analyzer inventory <新しい pid>
oss-analyzer compare <基準 pid> <新しい pid> --save                # 変更箇所 + 自分たちの改修と衝突する箇所
```

改修したオリジナルを元のオリジナルと比較検証するには、改修したリポジトリを `--target-root` に指定し、[クイックスタート 2](#クイックスタート-2-機能の移植と検証) と同じ方法で検証します。

## ケースファイルの形式

```json
{
  "defaults": {"headers": {"Content-Type": "application/json"}},
  "cases": [
    {"name": "注文作成", "method": "POST", "path": "/api/v1/orders", "json": {"sku": "A1", "qty": 2},
     "match": {"id": "[0-9a-f]{32}"}, "ignore": ["createdAt"]},
    {"name": "注文照会", "method": "GET", "path": "/api/v1/orders/{{注文作成.id}}"},
    {"name": "数量0", "method": "POST", "path": "/api/v1/orders", "json": {"sku": "A1", "qty": 0}, "compare": "status"},
    {"name": "割引適用", "method": "POST", "path": "/api/v1/orders", "json": {"sku": "A1", "qty": 10},
     "expect": {"status": 201, "equals": {"total": 18000}}},
    {"name": "クーポン_新機能", "new": true, "method": "POST", "path": "/api/v1/orders",
     "json": {"sku": "A1", "qty": 1, "coupon": "WELCOME"}, "expect": {"equals": {"discount": 1000}}}
  ]
}
```

| フィールド | 意味 |
|---|---|
| `method`·`path`·`json`·`headers` | HTTP ケース |
| `argv`·`stdin` | CLI ケース |
| `call: {source, target}`·`args`·`kwargs` | 関数単位の比較 (`パス:関数名`、言語が異なっても可) |
| `{{ケース名.フィールド}}` | 前のケースの結果の値を使用 (名前にスペースを含めない) |
| `ignore` | 比較しないパス (`items.*.t`) |
| `match` | 値の代わりに正規表現で確認 (id・時刻のように毎回変わる値) |
| `compare: "status"` | ステータスコードのみ比較 |
| `expect` | オリジナルの代わりに期待値と比較 — `status`・`body`・`equals`(パス: 値、配列は `items.0.price`)・`match` |
| `new: true` | オリジナルにない動作 (`expect` 必須、オリジナルでは実行しない) |
| `explore: false`·`concurrent: false` | 入力の自動変形・同時リクエスト検査の対象から除外 |

表にないキーは保存時に拒否します (`argv` の代わりに `args` のような誤ったキーが黙って無視されると別の入力で検証されるため)。要望モードではケースごとに `req` も必要です。

## コマンド一覧

| 分類 | コマンド |
|---|---|
| 準備 | `doctor` `setup` `open` `list` `inventory` `classify` `agent-status` |
| アプリ・自動実行 | `app` `run` `runs` `data` |
| 分析ループ | `next` `claim` `renew` `context` `finding` `withdraw` `mark` `unknown-add` `unknown-update` `complete` `review` `pending` |
| 精密確認 | `lsp`(L1) `dataflow`(L2) `runtime-ingest` `runtime-run` `runtime-runs`(L3) |
| 結果 | `coverage` `features` `units` `unknowns` `export` `license` |
| カスタム | `extpoints` `impact` `custom-add` `custom-list` `custom-update` `compare` |
| 要望から作る | `make-create` `make-req` (以降は port-* コマンドと共通) |
| 移植 | `slice` `port-create` `port-map` `port-capture` `port-verify` `port-accept` `port-status` `port-approve` `port-finish` |

各コマンドのオプションは `oss-analyzer <コマンド> --help` で確認できます。すべてのコマンドは `{ok, data, warnings, errors}` 形式の JSON を出力し、
失敗すると `errors[0].recovery` に解決方法が示されます。エージェント向けの詳細な手順は、スキルのドキュメント(`oss-analyze` スキルの SKILL.md・CLI.md)にあります。

## データと保存場所

Claude Code (`~/.claude`)・Codex (`~/.codex`) と同様に、ホーム下の一つのフォルダ (`~/.oss-analyzer`、`OSS_ANALYZER_HOME` で変更) にまとめ、
中は性質ごとに分けます。どこにも送信せず、秘密情報は保存しません — キー・トークンは環境変数からのみ読み、出力ではマスクします。

| フォルダ | 内容 | 削除してよいか |
|---|---|---|
| `settings.json` | ユーザー設定 (整理の周期、既定値) | 可 — 既定値に戻る |
| `projects/<id>/` | 分析・移植の元帳 `ledger.db` (SQLite、スキーマバージョン、ハッシュチェーン)、固定コミットの元のクローン、ゴールデン応答 | **不可** — 承認・検証の記録 |
| `runs/<実行>/` | 自動実行 1 件 = 会話 1 件: `run.json` 状態、`events.jsonl` の流れ (追記のみ)、`inbox/` 質問・承認リクエスト、`shadow.git` 対象フォルダのチェックポイント、`agent/` ラウンドごとに作り直すエージェント設定 | ごみ箱へ |
| `trash/` | 削除した実行、7 日後に自動で空にする | 可 |
| `app/` | ショートカットが指すアプリアイコン | 不可 |
| `cache/` · `cbm-cache/` | エージェントの状態、検証ハーネス、構造マップの索引 | 可 — 再作成 |
| `work/` | 検証・カバレッジ用の一時コピー、使用後すぐ削除 | 可 |
| `bin/` · `engines/` · `node/` | `setup` がインストールした分析エンジン | 可 — `setup` で再インストール |

エージェントとの会話の原本は各 CLI が本来置く場所 (`~/.claude/projects`、`~/.codex/sessions`) にあり、セッション id で続けます。
対象フォルダには `.oss/cases.json` (合格ケース) だけを書きます — コミットするか無視するかは自由です。

| | Claude Code | Codex | このツールキット |
|---|---|---|---|
| 会話 | `projects/<フォルダ>/<セッション>.jsonl` | `sessions/YYYY/MM/DD/*.jsonl` | `runs/<実行>/events.jsonl` (+ エージェント自身の会話記録) |
| 設定 | `settings.json` | `config.toml` | `settings.json` |
| 状態 | ファイル変更履歴 (チェックポイント) | バージョン付き SQLite | プロジェクトごとの SQLite 元帳 (スキーマバージョン、ハッシュチェーン) + 実行ごとの git チェックポイント |
| 整理 | 既定で 30 日経った会話を自動削除 | 保持 | 既定は保持、希望すれば完了・非表示の実行を 30/90 日後に自動整理、ごみ箱は 7 日後に空にする |

実行を削除しても承認・検証の結果は消えません — プロジェクトの元帳に残ります。

```bash
oss-analyzer data                        # どこに何がどれだけ
oss-analyzer data --trash <実行>          # 実行をごみ箱へ (--restore <名前>、--empty-trash)
oss-analyzer data --prune 30 --dry-run   # 30 日間そのままの完了・非表示の実行
oss-analyzer data --set-cleanup 30       # 起動時に自動整理 (0 = オフ)
```

アプリではサイドバー下の **保存領域**、実行の ⋯ メニューの **ごみ箱へ**。

## 設定

| 環境変数 | デフォルト値 | 説明 |
|---|---|---|
| `OSS_ANALYZER_HOME` | `~/.oss-analyzer` | 台帳・clone・エンジン・バックアップの場所 |
| `OSS_ANALYZER_CONTAINER` | `auto` | 隔離実行エンジン `auto`・`docker`・`wsl`・`none` |
| `OSS_ANALYZER_WSL_DISTRO` | `oss-sandbox` | Windows で使用する WSL ディストリビューション |
| `OSS_ANALYZER_LEASE_SECONDS` | `3600` | 分析単位のリース時間 |
| `OSS_ANALYZER_UNIT_MAX_LINES` / `_UNIT_MAX_CHARS` | `1500` / `45000` | 分析単位のサイズ上限 |
| `OSS_ANALYZER_CONTEXT_MAX_CHARS` | `24000` | `context` の 1 ページあたりのサイズ |
| `OSS_ANALYZER_LSP_TIMEOUT_SECONDS` / `_LSP_IDLE_SECONDS` | `900` / `600` | 言語サーバーの応答待ち時間・アイドル時の終了時間 |
| `OSS_ANALYZER_RULEPACKS` | — | 追加のエントリポイントルールフォルダ |
| `OSS_ANALYZER_CBM` · `_ASTGREP` · `_JOERN` · `_JAVA_HOME` | 自動検出 | エンジン実行ファイルの直接指定 |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | スキルをインストールする Claude Code の設定フォルダ |

## 安全方針

- **分析対象のコードは実行しません。** 言語サーバーによるビルドスクリプト・マクロの実行も無効にします。実行が必要な検証(カバレッジ・移植時のオリジナル)は、
  コンテナ(ネットワーク遮断)内で、またはユーザーが信頼すると指定したリポジトリに限り一時コピー上で実行します。
- **シークレットファイルは読み込まず**、通常のファイル内の秘密値(パスワード・キー・トークン・URL 内の認証情報)は出力時に `****` でマスクします。
  実行時の環境変数からも、トークン・キー類は除外して渡します。
- 分析は **専用 clone の固定コミット** でのみ行います。作業中のオリジナルフォルダにある未コミットの変更は反映されません。
- 移植における人の承認は、デスクトップアプリ・MCP の確認ダイアログ・ユーザーのターミナル入力でのみ受け付けます — 自動実行中のエージェントからは受け付けません。

## 既知の制限

| 制限 | 対応 |
|---|---|
| 差分検証は実行した入力についてのみ証明 | 入力の自動変形・カバレッジで範囲を広げ、残りの範囲は根拠の limitations に記録。性能は比較しない |
| カバレッジは「実行された」ことのみを証明 | どの分岐・入力によるものかはケースで別途確認 |
| 名前ベースの呼び出し関係は候補にすぎない | 言語サーバー(L1)または実行(L3)で確認して初めて根拠になる |
| 動的ディスパッチ・リフレクション・マクロ生成コード | 自動的に未確認扱い |
| データフローエンジンの初回実行が遅い (1 万行の Java で約 15 分) | キャッシュ、`dataflow --build-only` で事前生成 |
| CLI は呼び出しのたびに言語サーバーを新たに起動 | 同じ言語はプロセス間で順番待ちさせる。MCP モードはサーバーを再利用 |
| 設定値によって変わるパス | デフォルト値で展開し、未確認として表示 |
| ローカルプログラムのため、同じ権限で意図的に記録を偽造されると防げない | 偽造の試みはゲートの失敗として表面化する。最終判定はクリーンな環境で再実行可能 |

## ライセンス

**使用許可・改変禁止** — [LICENSE](LICENSE) (オープンソースライセンスではありません)。

本ライセンスの条件は、韓国語で書かれた [LICENSE](LICENSE) の原文が優先されます。以下は参考用の要約です。

- 改変していない状態でのインストール・使用は、誰でも可能です (会社の業務・商用目的を含む)。ツールキットで作成した成果物はユーザーのものです。
- 改変・二次的著作物の作成・改変版の配布・コードの一部を他のソフトウェアに組み込むことには、著作者の書面による許可が必要です。
- 改善の提案は、このリポジトリの Issue・プルリクエストで受け付けます。

使用しているライブラリ・外部エンジンは、それぞれのライセンスに従います — [THIRD_PARTY.md](THIRD_PARTY.md)。
