"""設定値を一箇所に集約したモジュール。

実サイトへ接続する際は、原則このファイルだけを書き換えれば動くように設計している。
書き換えが必要な箇所には【要差し替え】と明記してある。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"


# =============================================================================
# 1. ブラウザ接続設定
# =============================================================================

# CDP(Chrome DevTools Protocol)接続を使うかどうか。
#   True  : 既に起動済み・ログイン済みのローカルChromeへ接続する（実サイト運用時はこちら）
#   False : Playwrightが新しいブラウザを起動する（ダミーサイトでの動作確認用）
#
# 【要差し替え】実サイト運用時は True にする。
USE_CDP = False

# CDP接続先のポート。事前に下記コマンドでChromeを起動しておく必要がある。
#
#   macOS:
#     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
#       --remote-debugging-port=9222 \
#       --user-data-dir="$HOME/chrome-debug-profile"
#
#   Windows:
#     "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ^
#       --remote-debugging-port=9222 ^
#       --user-data-dir="%USERPROFILE%\\chrome-debug-profile"
#
# SSO/クライアント証明書認証は、この起動済みChrome側で先に通しておく。
CDP_PORT = 9222
CDP_ENDPOINT = f"http://localhost:{CDP_PORT}"

# USE_CDP=False のとき、ブラウザを画面表示せずに動かすか。
#   True  : 非表示（既定）。処理は最後まで自動実行され、何が行われたかは
#           UIの「処理フロー」欄と、各ステップの証跡スクリーンショットで確認する。
#   False : ブラウザを画面表示する。動きを直接目視したいときだけ使う。
HEADLESS = True

# 1操作（クリック・入力など）ごとに待つミリ秒数。既定は 0（全速力）。
# HEADLESS=False で動きを1つずつ目視したいときだけ 800〜3000 程度にする。
SLOW_MO_MS = 0

# 証跡スクリーンショットに「今なにをした段階か」のバナーを焼き込み、操作した要素を
# 枠線（操作中=赤、入力済み=緑）で強調するか。見た目だけの注入で、フォームの値や
# 送信内容には一切影響しない。UIのフロー詳細で画像を見たとき、状況が一目で分かる。
SHOW_PROGRESS_BANNER = True

# HEADLESS=False でブラウザを目視する場合に、節目（ページを開いた・入力が終わった等）
# ごとに入れる小休止（ミリ秒）。非表示実行では待つ意味がないため既定は 0。
STEP_PAUSE_MS = 0

# フロー完了後（成功・失敗いずれの場合も）、ブラウザを自動で閉じずに残すか。
# HEADLESS=False で画面表示しているときだけ意味を持つ（非表示実行では画面が無く
# 「残して確認する」意味がないため、常に自動で閉じる）。
#   True  : 画面に表示されたまま残る。確認が終わったら、利用者がウィンドウの✕ボタン
#           （CDP接続時は開いたタブ）で手動で閉じる。失敗時、どのセレクタ・画面で
#           止まったかをそのまま目視できるため、デバッグにも有用。
#   False : フロー完了直後に自動で閉じる
KEEP_BROWSER_OPEN = True


# =============================================================================
# 2. ダミーサイト（動作確認用のローカルサーバー）設定
# =============================================================================

DUMMY_SERVER_HOST = "127.0.0.1"
DUMMY_SERVER_PORT = 8765
DUMMY_SITE_DIR = BASE_DIR / "dummy_site"
DUMMY_BASE_URL = f"http://{DUMMY_SERVER_HOST}:{DUMMY_SERVER_PORT}"

# ダミーサーバーを自動起動するか。実サイト運用時は False にする。
START_DUMMY_SERVER = True


# =============================================================================
# 3. サイトURL 【要差し替え】
# =============================================================================

# 社内ポータル(OOOサイト)のURL。
# 【要差し替え】実サイトのポータルURL（例: "https://portal.example.co.jp/"）に変更する。
PORTAL_URL = f"{DUMMY_BASE_URL}/portal.html"

# 書類申請サイトのURL。ポータルのリンク経由で遷移するので通常は使わないが、
# 遷移後に「意図したページに来たか」を検証するために前方一致で確認する。
# 【要差し替え】実サイトの申請ページURLの先頭部分に変更する。
APPLY_URL_PREFIX = f"{DUMMY_BASE_URL}/apply.html"

# 遷移先URLの検証を行うか。実サイトのURLが不明な間は False にしておくと止まらない。
VERIFY_APPLY_URL = True


# =============================================================================
# 4. セレクタ定義 【最重要・要差し替え】
# =============================================================================
#
# ここが実サイト適用時のメイン作業箇所。
# ブラウザの開発者ツール(F12)で対象要素を右クリック →「Copy」→「Copy selector」で
# 取得したCSSセレクタを貼り付ける。
#
# CSSセレクタ以外に、Playwrightの以下の記法もそのまま使える:
#   "text=申請する"              … 表示テキストで指定
#   "role=button[name='送信']"   … アクセシビリティroleで指定
#   "xpath=//button[@id='x']"    … XPathで指定
#
# 実サイトではidが自動生成される(例: id="mat-input-3")ことがあり、その場合idは
# 使わず、label文言やname属性で指定する方が壊れにくい。


@dataclass(frozen=True)
class PortalSelectors:
    """社内ポータル(OOOサイト)側のセレクタ。"""

    # 書類申請サイトへ遷移するリンク。
    # 【要差し替え】実サイトのリンク要素に変更する。
    apply_link: str = "#link-to-apply"


@dataclass(frozen=True)
class FormSelectors:
    """書類申請サイト側のフォーム要素セレクタ。"""

    # --- 入力欄 --- 【すべて要差し替え】
    applicant_name: str = "#applicant-name"  # 氏名 (input[type=text])
    apply_date: str = "#apply-date"  # 申請日 (input[type=date])
    department: str = "#department"  # 部署 (select)
    doc_title: str = "#doc-title"  # 書類名 (input[type=text])
    reason: str = "#reason"  # 申請理由 (textarea)

    # --- 添付ファイル --- 【要差し替え】
    # input[type=file] 要素を指定する。画面上で非表示(display:none)にされていても
    # set_input_files() は動作するので、見た目のボタンではなくinput要素を指定すること。
    attachment: str = "#attachment"

    # --- 送信ボタン --- 【要差し替え】
    submit_button: str = "#submit-button"

    # --- 送信結果の判定 --- 【要差し替え】
    # 送信成功時にだけ現れる要素。ここが出現するまで待って成功と判定する。
    success_indicator: str = "#result-success"
    # 送信失敗時(バリデーションエラー等)に現れる要素。出現したら失敗として扱う。
    error_indicator: str = "#result-error"


PORTAL_SELECTORS = PortalSelectors()
FORM_SELECTORS = FormSelectors()


# =============================================================================
# 5. タイムアウト設定（ミリ秒）
# =============================================================================

# 要素の出現を待つ標準時間。社内サイトが遅い場合は延ばす。
ELEMENT_TIMEOUT_MS = 10_000

# ページ遷移の完了を待つ時間。
NAVIGATION_TIMEOUT_MS = 30_000

# 送信後に結果画面が出るのを待つ時間。ファイルアップロードを伴うため長めに取る。
SUBMIT_TIMEOUT_MS = 30_000

# リンククリック時、新規タブが開くかどうかを判定するための待機時間。
# この時間内に新規タブが開かなければ「同一タブ内で遷移した」とみなす。
POPUP_DETECT_TIMEOUT_MS = 3_000


# =============================================================================
# 6. 動作フラグ
# =============================================================================

# True の場合、送信ボタンを押す直前で処理を止める（実サイトでの試験投入時に使う）。
# 実データを誤送信したくない検証段階では True にしておくこと。
DRY_RUN = False

# 各ステップのスクリーンショットを logs/screenshots/ に保存するか。
SAVE_SCREENSHOTS = True
SCREENSHOT_DIR = LOG_DIR / "screenshots"
