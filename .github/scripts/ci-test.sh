#!/usr/bin/env bash
# プロジェクトごとの「テストの実行」。
# CI①(ci.yml)と、CI③の Claude Code が、このスクリプトを実行する。ローカルでも同じコマンドで実行できる。
#
#   bash .github/scripts/ci-test.sh
#
# 1つでも失敗したら、終了コード1で終わる(CIが失敗になる)。
# このファイルは .github/ 配下にあるため、CI③の Claude Code は変更できない
# (AIがテストを書き換えて「成功」にすることを防ぐ)。変更は人間がPRで行う。
set -uo pipefail
cd "$(dirname "$0")/../.."

failed=0

# $1: チェックの名前, $2以降: 実行するコマンド
run_check() {
	local name="$1"
	shift
	echo "::group::${name}"
	if "$@"; then
		echo "OK: ${name}"
	else
		echo "::error::失敗: ${name}"
		failed=1
	fi
	echo "::endgroup::"
}

# ---------------------------------------------------------------------------
# 共通チェック(どのプロジェクトでも行う)
# ---------------------------------------------------------------------------

# プロジェクトの管理文書がそろっているか。
check_required_files() {
	local missing=0
	for file in README.md PROJECT_CONTEXT.md decisions.md CLAUDE.md AGENTS.md GEMINI.md .gitignore; do
		if [ ! -f "$file" ]; then
			echo "見つかりません: $file"
			missing=1
		fi
	done
	return "$missing"
}

# 秘密情報を入れるファイルが、誤ってGitに含まれていないか。
check_no_env_files() {
	local found
	found="$(git ls-files | grep -E '(^|/)\.env(\..+)?$' | grep -vE '\.env\.example$' || true)"
	if [ -n "$found" ]; then
		echo "Gitに含めてはいけないファイルがあります:"
		echo "$found"
		return 1
	fi
	return 0
}

# 自動化用のPythonスクリプトに、文法エラーがないか。
check_automation_scripts() {
	python3 - <<'PY'
import pathlib
import sys

ok = True
for path in sorted(pathlib.Path(".github/scripts").glob("*.py")):
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as error:
        print(f"文法エラー: {path}: {error}")
        ok = False
sys.exit(0 if ok else 1)
PY
}

run_check "管理文書がそろっている" check_required_files
run_check ".env がGitに含まれていない" check_no_env_files
run_check "自動化スクリプトに文法エラーがない" check_automation_scripts

# ---------------------------------------------------------------------------
# プロジェクト固有のテスト(技術構成が決まったら、ここに追加する)
# ---------------------------------------------------------------------------
# 例: Node.js のプロジェクト
#   run_check "Lint" npm run lint
#   run_check "単体テスト" npm test
#   run_check "ビルド" npm run build
#
# 例: Godot のプロジェクト
#   run_check "スモークテスト" bash tests/smoke/run_smoke_test.sh

if [ "$failed" -ne 0 ]; then
	echo "::error::テストに失敗しました。"
	exit 1
fi
echo "すべてのチェックに成功しました。"
