# 自動化の設定手順(AUTOMATION_SETUP.md)

このリポジトリには、AIを使った開発の自動化の仕組みが3つ入っている。ファイルを置いただけでは動かないものがあるため、リポジトリごとに次の設定を行う。

| 名前 | 何をするか | 動くきっかけ | 必要な設定 |
|---|---|---|---|
| CI① テスト(`ci.yml`) | テストを実行する | PR、`main`へのpush | なし(置くだけで動く) |
| CI② Geminiレビュー(`gemini-review.yml`) | PRの変更をGeminiがレビューし、PRにコメントする | PRの作成・更新 | 手順2 |
| CI③ Claude実装(`claude-issue-implement.yml`) | IssueをClaude Codeが実装し、PRを作る | Issueに`claude-ready`ラベルを付ける | 手順1・3・4・5 |

**用語:**
- **Secret(シークレット):** GitHubに預ける秘密の値。登録後は誰も読み出せず、workflowだけが使える。
- **Variable(変数):** GitHubに預ける、秘密ではない設定値。
- どちらもリポジトリごとに保存されるため、新しいリポジトリでは登録し直す。

以下のコマンドの `<owner>/<repo>` は、設定するリポジトリ(例: `Mugen0619/pixel-asset-maker`)に置き換える。

---

## 手順1: `claude-ready`ラベルを作る

```bash
gh label create claude-ready -R <owner>/<repo> --color fb2cd1 --description "Claude Codeによる実装開始の明示的なGO"
```

このラベルは、内容を確認した開発者が付ける「実装開始のGO」。Issueを作っただけでは、CI③は動かない。

## 手順2: Gemini APIキーを登録する(CI②)

```bash
gh secret set GEMINI_API_KEY -R <owner>/<repo>
```

実行すると値の入力を求められるので、APIキーを貼り付ける(コマンドの中に値を直接書かない。履歴に残るため)。

- 未登録の場合、CI②は失敗にはならず、「レビューは実行していません」という警告を出して終わる。
- 使うモデルを変える場合だけ、Variable `GEMINI_MODEL` を設定する(未設定ならworkflowに書かれた既定のモデルを使う)。
  ```bash
  gh variable set GEMINI_MODEL -R <owner>/<repo> --body "<モデル名>"
  ```

## 手順3: Claude Codeのトークンを登録する(CI③)

1. 手元のターミナルで `claude setup-token` を実行し、トークンを生成する(Claudeのサブスクリプションにひもづく。APIの従量課金は使わない)。
2. 生成された値を登録する。
   ```bash
   gh secret set CLAUDE_CODE_OAUTH_TOKEN -R <owner>/<repo>
   ```

## 手順4: PR作成用のGitHub Appを、このリポジトリで使えるようにする(CI③)

CI③は、PRをGitHub App(`night-shift-escape-pr-creator`)の名義で作る。GitHub標準のトークンで作ったPRでは、CI①・CI②が自動で動かないため。

1. **Appをこのリポジトリにインストールする(ブラウザでの操作):**
   GitHubの右上のアイコン → Settings → Applications → Installed GitHub Apps → `night-shift-escape-pr-creator` の Configure → Repository access で、このリポジトリを追加して保存する。
2. **AppのClient IDを登録する(秘密の値ではない):**
   値は、すでに設定済みのリポジトリから確認できる。
   ```bash
   gh variable list -R Mugen0619/night-shift-escape          # CLAUDE_PR_APP_CLIENT_ID の値を確認
   gh variable set CLAUDE_PR_APP_CLIENT_ID -R <owner>/<repo> --body "<Client ID>"
   ```
3. **Appの秘密鍵(Private Key)を登録する:**
   Appを作ったときにダウンロードした `.pem` ファイルを使う。
   ```bash
   gh secret set CLAUDE_PR_APP_PRIVATE_KEY -R <owner>/<repo> < "<.pemファイルのパス>"
   ```
   `.pem` ファイルが手元にない場合は、Appの設定画面(Settings → Developer settings → GitHub Apps → 対象のApp)で新しい秘密鍵を生成する。

Appに必要な権限は、Contents: Read-only と Pull requests: Read & write の2つだけ。

## 手順5: プロジェクトに合わせて3つのファイルを書き換える

workflow本体は書き換えない。プロジェクトごとに変えるのは次の3つ。

| ファイル | 書く内容 |
|---|---|
| `.github/actions/setup-project/action.yml` | 道具の準備(言語・ツールのインストール) |
| `.github/scripts/ci-test.sh` | テストの実行コマンド(「プロジェクト固有のテスト」の部分に追加する) |
| `.gemini/styleguide.md` | Geminiに見てほしい、このプロジェクト固有のレビュー観点 |

Claude Codeに、テスト以外のコマンド(例: `npm run`)の実行を許可する必要がある場合は、Variable `CLAUDE_EXTRA_ALLOWED_TOOLS` を設定する。**先頭にカンマを付ける。**

```bash
gh variable set CLAUDE_EXTRA_ALLOWED_TOOLS -R <owner>/<repo> --body ",Bash(npm run:*)"
```

`.github/` 配下は、CI③のClaude Codeには変更できない(変更があるとpushを止める)。テストをAIが書き換えて「成功」にすることを防ぐため。これらのファイルは、人間がPRで変更する。

---

## 設定後の確認

設定したら、実際に動くことを確かめる(「設定した」と「動く」は別)。

1. **CI①:** 小さな変更のPRを作り、テストが成功することを確認する。
2. **CI①が失敗を検知できること:** わざとテストが失敗する変更(例: `decisions.md`を一時的に削除する)をPRにして、CI①が失敗することを確認する。確認後、そのPRは閉じる。
3. **CI②:** PRにGeminiのレビューコメントが付くことを確認する。CIが成功(緑)でも、コメントの判定(指摘の有無)まで読む。
4. **CI③:** 小さなIssue(例: READMEに1行追加)を作って`claude-ready`を付け、PRが自動で作られ、そのPRでCI①・CI②が承認待ちにならずに動くことを確認する。

## 運用上の注意

- **自動でMergeはされない。** Mergeするかどうかは、CIの結果・レビュー・変更内容を見て開発者が決める。
- **CI③の「変更なし」は成功として終わる。** AIが「変更不要」と判断した場合のほか、仕様が曖昧で停止した場合も含まれる。実行ログで理由を確認する。
- **Geminiのレビューは、API側の混雑で失敗することがある。** 時間を置いて再実行するか、レビューなしで開発者が判断する。
- **AIの指摘や実装を正解として扱わない。** 仕様・テスト・実際の動作で確かめる。
- **公開リポジトリでは、実行ログとPRのコメントも公開される。** 秘密情報や個人に関する情報を、IssueやPRに書かない。
- **非公開リポジトリでは、GitHub Actionsの無料の実行時間に上限がある。**
