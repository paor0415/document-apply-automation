"""動作確認用のダミーWebサーバー。

portal.html / apply.html を配信し、apply.html からのPOSTを受け取って結果ページを返す。

■ なぜ静的HTMLで済ませずサーバーを立てるのか
JavaScriptだけで「送信できたフリ」をさせると、添付ファイルが実際に送られたかを
確認できない。実際にmultipart/form-dataを受信してファイルの中身のハッシュまで
検証することで、Playwrightの set_input_files() が本当に機能しているかを確かめられる。
"""

from __future__ import annotations

import email
import hashlib
import html
import threading
from email.policy import default as email_policy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# 単体実行(python dummy_site/server.py)でも親ディレクトリのモジュールを読めるようにする
if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logger_setup import get_logger  # noqa: E402

logger = get_logger("dummy_server")

# 受け取った申請内容の履歴。テストからの検証に使う。
SUBMISSIONS: list[dict[str, Any]] = []

# 部署のvalue → 表示名。結果ページで人間が読める形にするために使う。
_DEPARTMENT_LABELS = {
    "dev": "開発部",
    "sales": "営業部",
    "hr": "人事部",
    "ga": "総務部",
    "acc": "経理部",
}

_REQUIRED_FIELDS = {
    "applicant_name": "氏名",
    "apply_date": "申請日",
    "department": "部署",
    "doc_title": "書類名",
}


def _parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    """multipart/form-data を解析する。

    Python 3.13 で cgi モジュールが削除されたため、標準の email パーサを使う。
    multipart/form-data は MIME マルチパートと同じ構造なので、これで解析できる。

    Returns:
        (テキスト項目の辞書, ファイル項目の辞書{name: (ファイル名, 中身)})
    """
    # emailパーサはヘッダ付きのメッセージを期待するので、ヘッダ部分を組み立てて連結する
    prelude = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    message = email.message_from_bytes(prelude + body, policy=email_policy)

    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}

    if not message.is_multipart():
        return fields, files

    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()

        if filename:
            files[str(name)] = (filename, payload)
        else:
            fields[str(name)] = payload.decode("utf-8", errors="replace")

    return fields, files


class DummySiteHandler(SimpleHTTPRequestHandler):
    """静的ファイル配信 + POST /submit の処理。"""

    def do_POST(self) -> None:  # noqa: N802 - http.serverの規定名
        if self.path != "/submit":
            self.send_error(404, "Not Found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Bad Content-Length")
            return

        body = self.rfile.read(length) if length > 0 else b""
        content_type = self.headers.get("Content-Type", "")

        if not content_type.startswith("multipart/form-data"):
            self._respond_html(400, _error_page(["フォームの送信形式が不正です。"]))
            return

        fields, files = _parse_multipart(content_type, body)

        # サーバー側バリデーション。実サイトも必須チェックを持っている想定の再現。
        missing = [
            label for key, label in _REQUIRED_FIELDS.items() if not fields.get(key, "").strip()
        ]
        if missing:
            logger.warning("[ダミーサーバー] 必須項目が不足しています: %s", ", ".join(missing))
            self._respond_html(200, _error_page([f"{m} が入力されていません。" for m in missing]))
            return

        attachment_name, attachment_bytes = files.get("attachment", ("", b""))
        record = {
            "fields": fields,
            "attachment_name": attachment_name,
            "attachment_size": len(attachment_bytes),
            "attachment_sha256": (
                hashlib.sha256(attachment_bytes).hexdigest() if attachment_bytes else ""
            ),
        }
        SUBMISSIONS.append(record)

        logger.info(
            "[ダミーサーバー] 申請を受信しました: 氏名=%s / 書類名=%s / 添付=%s (%d バイト)",
            fields.get("applicant_name"),
            fields.get("doc_title"),
            attachment_name or "(なし)",
            len(attachment_bytes),
        )

        self._respond_html(200, _success_page(fields, attachment_name, attachment_bytes))

    def _respond_html(self, status: int, content: str) -> None:
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 規定シグネチャ
        """アクセスログを標準エラーではなく、このツールのロガーへ流す。"""
        logger.debug("[ダミーサーバー] %s", format % args)


def _page(title: str, body: str, accent: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>{html.escape(title)}</title>
<style>
  body {{ font-family: "Hiragino Sans","Yu Gothic",system-ui,sans-serif;
         background:#eef2f6; margin:0; color:#1f2933; }}
  header {{ background:{accent}; color:#fff; padding:18px 28px; }}
  header h1 {{ margin:0; font-size:20px; }}
  .wrap {{ max-width:720px; margin:28px auto; padding:0 20px; }}
  .card {{ background:#fff; border-radius:8px; padding:28px 32px;
          box-shadow:0 1px 3px rgba(0,0,0,.1); }}
  .icon {{ font-size:40px; text-align:center; margin-bottom:10px; }}
  h2 {{ text-align:center; font-size:19px; margin:0 0 6px; color:{accent}; }}
  .sub {{ text-align:center; font-size:12px; color:#64748b; margin:0 0 24px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ padding:11px 12px; text-align:left; border-bottom:1px solid #edf0f3;
           vertical-align:top; }}
  th {{ width:33%; color:#475569; background:#f8fafc; font-weight:600; white-space:nowrap; }}
  td {{ word-break:break-all; }}
  ul {{ margin:0; padding-left:20px; font-size:13px; line-height:1.9; }}
  .hash {{ font-family:ui-monospace,Menlo,monospace; font-size:10px; color:#94a3b8; }}
  .back {{ display:inline-block; margin-top:20px; font-size:13px; color:#1f5faa; }}
</style>
</head>
<body>
<header><h1>書類申請システム（ダミー）</h1></header>
<div class="wrap"><div class="card">{body}</div></div>
</body>
</html>"""


def _success_page(fields: dict[str, str], attachment_name: str, attachment_bytes: bytes) -> str:
    """送信成功ページ。

    ★ id="result-success" の要素が、自動化側の成功判定に使われる。
      （config.py の FormSelectors.success_indicator と対応）
    """
    dept_value = fields.get("department", "")
    dept_label = _DEPARTMENT_LABELS.get(dept_value, dept_value)
    receipt = f"DOC-{abs(hash(tuple(sorted(fields.items())))) % 1000000:06d}"

    if attachment_name:
        digest = hashlib.sha256(attachment_bytes).hexdigest()
        attach_html = (
            f"{html.escape(attachment_name)}<br>"
            f"<span class='hash'>{len(attachment_bytes):,} バイト / SHA256: {digest[:32]}…</span>"
        )
    else:
        attach_html = "(添付なし)"

    rows = [
        ("受付番号", html.escape(receipt)),
        ("氏名", html.escape(fields.get("applicant_name", ""))),
        ("申請日", html.escape(fields.get("apply_date", ""))),
        ("部署", html.escape(f"{dept_label} (value={dept_value})")),
        ("書類名", html.escape(fields.get("doc_title", ""))),
        ("申請理由", html.escape(fields.get("reason", "")) or "(未記入)"),
        ("添付ファイル", attach_html),
    ]
    table = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)

    body = f"""
      <div id="result-success">
        <div class="icon">✅</div>
        <h2>申請を受け付けました</h2>
        <p class="sub">受付番号: {html.escape(receipt)}</p>
        <table>{table}</table>
      </div>
      <a class="back" href="/portal.html">← ポータルへ戻る</a>
    """
    return _page("申請完了（ダミー）", body, "#14532d")


def _error_page(messages: list[str]) -> str:
    """送信失敗ページ。

    ★ id="result-error" の要素が、自動化側の失敗判定に使われる。
      （config.py の FormSelectors.error_indicator と対応）
    """
    items = "".join(f"<li>{html.escape(m)}</li>" for m in messages)
    body = f"""
      <div id="result-error">
        <div class="icon">⚠️</div>
        <h2>申請を受け付けられませんでした</h2>
        <p class="sub">入力内容をご確認ください</p>
        <ul>{items}</ul>
      </div>
      <a class="back" href="/apply.html">← フォームへ戻る</a>
    """
    return _page("申請エラー（ダミー）", body, "#b91c1c")


class DummySiteServer:
    """ダミーサイトをバックグラウンドスレッドで動かす。

    with文で使うと自動で停止する:

        with DummySiteServer() as server:
            ...  # server.base_url にアクセスできる
    """

    def __init__(self, host: str, port: int, directory: Path) -> None:
        self.host = host
        self.port = port
        self.directory = directory
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        if self._httpd is not None:
            return

        handler = partial(DummySiteHandler, directory=str(self.directory))
        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as e:
            raise RuntimeError(
                f"ダミーサーバーをポート {self.port} で起動できませんでした: {e}\n"
                f"  → 他のプロセスがポートを使用中の可能性があります。"
                f"config.py の DUMMY_SERVER_PORT を変更してください。"
            ) from e

        # daemon=True にしておくと、メインスレッド終了時に道連れで終了する
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        logger.info("ダミーサーバーを起動しました: %s", self.base_url)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("ダミーサーバーを停止しました")

    def __enter__(self) -> "DummySiteServer":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


if __name__ == "__main__":
    # 単体でサーバーだけ起動したい場合: python dummy_site/server.py
    import config
    from logger_setup import setup_logging

    setup_logging()
    server = DummySiteServer(config.DUMMY_SERVER_HOST, config.DUMMY_SERVER_PORT, config.DUMMY_SITE_DIR)
    server.start()
    print(f"ブラウザで {server.base_url}/portal.html を開いてください（Ctrl+C で停止）")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()
