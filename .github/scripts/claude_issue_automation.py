"""CI③(Issue → Claude Code実装 → PR自動作成)の前処理と後処理。

.github/workflows/claude-issue-implement.yml から呼び出す。外部パッケージは使わず、Python標準ライブラリだけで動く。

サブコマンド:
  guard      実装を始めてよいかを判定する(claude-ready ラベル・書き込み権限・重複の確認)
  create-pr  Claude Code がpushしたブランチからPRを作成する(GitHub App のトークンで実行する)

guard の環境変数:
  GITHUB_TOKEN, GITHUB_REPOSITORY, ISSUE_NUMBER, ISSUE_STATE, IS_PULL_REQUEST,
  LABEL_NAME, SENDER_LOGIN, SENDER_TYPE
  結果は GITHUB_OUTPUT に proceed(true/false)・branch・reason として書き出す。

create-pr の環境変数:
  GH_APP_TOKEN(GitHub App の Installation Access Token)、GITHUB_REPOSITORY,
  ISSUE_NUMBER, ISSUE_TITLE, BRANCH, RUN_URL
  結果は GITHUB_OUTPUT に pr_url・created(true/false)として書き出す。

終了コード:
  0  判定・作成が完了した(guard で「実装しない」と判定した場合も 0)
  1  設定不備・API失敗など。トークンはログに出さない。
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

GITHUB_API = "https://api.github.com"

# 実装開始の明示的なGOとするラベル。これ以外のラベルでは何もしない。
TRIGGER_LABEL = "claude-ready"

# Issue番号から機械的に判別できるブランチ名にする(例: claude/issue-28)。
BRANCH_PREFIX = "claude/issue-"

# claude-ready を付けた人に必要なリポジトリ権限(書き込み権限以上)。
WRITE_PERMISSIONS = {"admin", "maintain", "write"}

BASE_BRANCH = "main"


class AutomationError(Exception):
    """処理を失敗として扱うエラー。メッセージはログにそのまま出すので、トークンを含めないこと。"""


# ---------------------------------------------------------------------------
# 判定ロジック(GitHub API を呼ばない純粋な関数。ローカルでテストできるように分けている)
# ---------------------------------------------------------------------------

def branch_name(issue_number: int) -> str:
    return f"{BRANCH_PREFIX}{issue_number}"


def references_issue(pr_body: str | None, issue_number: int) -> bool:
    """PR本文が Closes / Fixes / Resolves #N でこのIssueを参照しているか。"""
    pattern = rf"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#{issue_number}\b"
    return re.search(pattern, pr_body or "") is not None


def find_duplicate_prs(open_prs: list[dict], issue_number: int, branch: str) -> list[dict]:
    """このIssueに対応するopen PR(同じheadブランチ、または本文でこのIssueを閉じるPR)を返す。"""
    return [
        pr
        for pr in open_prs
        if pr.get("head", {}).get("ref") == branch or references_issue(pr.get("body"), issue_number)
    ]


def decide(
    *,
    label: str,
    is_pull_request: bool,
    issue_state: str,
    sender_type: str,
    sender_permission: str,
    branch_exists: bool,
    duplicate_prs: list[dict],
    branch: str,
) -> tuple[bool, str]:
    """実装を始めてよいかを判定する。(proceed, 理由) を返す。安全側に倒し、迷う場合は始めない。"""
    if label != TRIGGER_LABEL:
        return False, f"対象外のラベルです({label})。{TRIGGER_LABEL} のときだけ実行します。"
    if is_pull_request:
        return False, "Pull Request へのラベル付与は対象外です。Issue にだけ反応します。"
    if issue_state != "open":
        return False, f"Issue が open ではありません({issue_state})。"
    if sender_type != "User":
        return False, f"ラベルを付けたのが人間のユーザーではありません({sender_type})。"
    if sender_permission not in WRITE_PERMISSIONS:
        return False, f"ラベルを付けたユーザーに書き込み権限がありません({sender_permission})。"
    if duplicate_prs:
        urls = ", ".join(pr.get("html_url", "?") for pr in duplicate_prs)
        return False, f"このIssueに対応するopen PRがすでにあります: {urls}"
    if branch_exists:
        return False, (
            f"ブランチ {branch} がすでにあります。既存ブランチを上書きしないため実行しません。"
            "続きは手動で行うか、不要ならブランチを削除してから claude-ready を付け直してください。"
        )
    return True, "実装を開始します。"


def pr_body(issue_number: int, run_url: str) -> str:
    return (
        f"Closes #{issue_number}\n\n"
        "## 概要\n"
        f"CI③(Issue → Claude Code 実装 → PR自動作成)により、Issue #{issue_number} の内容を Claude Code が実装しました。\n\n"
        "- 実装の範囲・仕様は、上記 Issue と `PROJECT_CONTEXT.md` / `decisions.md` を正とします。\n"
        "- このPRは自動でMergeされません。CI①・CI②の結果と変更内容を確認し、開発者が判断してください。\n"
        f"- 実行ログ: {run_url}\n"
    )


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------

def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise AutomationError(f"環境変数 {name} が設定されていません。")
    return value


def github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pixel-asset-maker-ci3",
    }


def http_json(url: str, method: str, token: str, body: dict | None = None) -> tuple[int, object]:
    """(HTTPステータス, JSON) を返す。404 は呼び出し側で判定できるように例外にしない。"""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=github_headers(token))
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return 404, {}
        detail = error.read().decode(errors="replace")[:500]
        raise AutomationError(f"{method} {url} が失敗しました: HTTP {error.code} {detail}") from None


def list_open_prs(repo: str, token: str) -> list[dict]:
    prs: list[dict] = []
    for page in range(1, 11):
        status, data = http_json(
            f"{GITHUB_API}/repos/{repo}/pulls?state=open&per_page=100&page={page}", "GET", token
        )
        if status != 200 or not isinstance(data, list):
            raise AutomationError(f"open PRの一覧を取得できませんでした(HTTP {status})。")
        prs.extend(data)
        if len(data) < 100:
            break
    return prs


def branch_exists(repo: str, token: str, branch: str) -> bool:
    quoted = urllib.parse.quote(branch, safe="")
    status, _ = http_json(f"{GITHUB_API}/repos/{repo}/branches/{quoted}", "GET", token)
    return status == 200


def sender_permission(repo: str, token: str, login: str) -> str:
    quoted = urllib.parse.quote(login, safe="")
    status, data = http_json(f"{GITHUB_API}/repos/{repo}/collaborators/{quoted}/permission", "GET", token)
    if status == 404:
        return "none"
    return str(data.get("permission", "none")) if isinstance(data, dict) else "none"


def write_output(**values: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    if path:
        with open(path, "a", encoding="utf-8") as file:
            file.write(lines)
    else:
        print(lines, end="")


def write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as file:
            file.write(text + "\n")


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------

def cmd_guard() -> None:
    repo = require_env("GITHUB_REPOSITORY")
    token = require_env("GITHUB_TOKEN")
    issue_number = int(require_env("ISSUE_NUMBER"))
    label = os.environ.get("LABEL_NAME", "")
    branch = branch_name(issue_number)

    # ラベルが対象外なら、APIを呼ばずにすぐ終わる。
    if label != TRIGGER_LABEL:
        proceed, reason = False, f"対象外のラベルです({label})。"
    else:
        sender = require_env("SENDER_LOGIN")
        proceed, reason = decide(
            label=label,
            is_pull_request=os.environ.get("IS_PULL_REQUEST", "false") == "true",
            issue_state=os.environ.get("ISSUE_STATE", ""),
            sender_type=os.environ.get("SENDER_TYPE", ""),
            sender_permission=sender_permission(repo, token, sender),
            branch_exists=branch_exists(repo, token, branch),
            duplicate_prs=find_duplicate_prs(list_open_prs(repo, token), issue_number, branch),
            branch=branch,
        )

    print(f"判定: {'実行する' if proceed else '実行しない'} / {reason}")
    if not proceed:
        print(f"::notice title=CI③ をスキップしました::{reason}")
    write_summary(f"### CI③ 実行判定(Issue #{issue_number})\n- 結果: {'実行する' if proceed else '実行しない'}\n- 理由: {reason}\n- ブランチ: `{branch}`")
    write_output(proceed="true" if proceed else "false", branch=branch)


def cmd_create_pr() -> None:
    repo = require_env("GITHUB_REPOSITORY")
    token = require_env("GH_APP_TOKEN")
    issue_number = int(require_env("ISSUE_NUMBER"))
    issue_title = require_env("ISSUE_TITLE")
    branch = require_env("BRANCH")
    run_url = os.environ.get("RUN_URL", "")

    if branch != branch_name(issue_number):
        raise AutomationError(f"ブランチ名が想定と違います: {branch}")

    duplicates = find_duplicate_prs(list_open_prs(repo, token), issue_number, branch)
    if duplicates:
        url = duplicates[0].get("html_url", "")
        print(f"::notice title=PRは作成しません::このIssueに対応するopen PRがすでにあります: {url}")
        write_summary(f"### PR自動作成\n- 既存のopen PRがあるため、新しく作成しませんでした: {url}")
        write_output(pr_url=url, created="false")
        return

    status, data = http_json(
        f"{GITHUB_API}/repos/{repo}/pulls",
        "POST",
        token,
        {
            "title": issue_title,
            "head": branch,
            "base": BASE_BRANCH,
            "body": pr_body(issue_number, run_url),
            "draft": False,
        },
    )
    if status != 201 or not isinstance(data, dict):
        raise AutomationError(f"PRを作成できませんでした(HTTP {status})。")
    url = data.get("html_url", "")
    print(f"PRを作成しました: {url}")
    write_summary(f"### PR自動作成\n- 作成したPR: {url}\n- base: `{BASE_BRANCH}` / head: `{branch}`")
    write_output(pr_url=url, created="true")


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if command == "guard":
            cmd_guard()
        elif command == "create-pr":
            cmd_create_pr()
        else:
            raise AutomationError("サブコマンドに guard か create-pr を指定してください。")
    except AutomationError as error:
        print(f"::error::{error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
