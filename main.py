"""エントリーポイント。

使い方:
    python main.py              UIを起動する（通常はこれ）
    python main.py --demo       UIなしで、ダミーデータを使って一連のフローを実行する
    python main.py --serve      ダミーサーバーだけ起動する（ブラウザで手動確認したいとき）
    python main.py --headless   ブラウザを画面表示せずに実行する（--demo と併用）
    python main.py --cdp        CDP接続を強制する（config.USE_CDP より優先）
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from datetime import date
from pathlib import Path

import config
from dummy_site.server import DummySiteServer
from logger_setup import get_logger, setup_logging
from models import ApplicationData
from workflow import run_application_flow

logger = get_logger("main")


def _start_dummy_server_if_needed() -> DummySiteServer | None:
    """設定に応じてダミーサーバーを起動する。

    実サイト運用時は config.START_DUMMY_SERVER = False にすることで、
    このサーバーは一切起動しなくなる。
    """
    if not config.START_DUMMY_SERVER:
        return None

    server = DummySiteServer(
        config.DUMMY_SERVER_HOST, config.DUMMY_SERVER_PORT, config.DUMMY_SITE_DIR
    )
    server.start()
    return server


def _make_demo_data() -> ApplicationData:
    """--demo 用のサンプル入力。添付ファイルもその場で作る。"""
    sample = config.BASE_DIR / "sample_attachment.txt"
    if not sample.exists():
        sample.write_text(
            "これは動作確認用のサンプル添付ファイルです。\n"
            "書類申請自動化ツールのデモ実行で使用します。\n",
            encoding="utf-8",
        )

    return ApplicationData(
        applicant_name="山田 太郎",
        apply_date=date.today().isoformat(),
        department="dev",  # 開発部
        doc_title="業務委託契約書",
        reason="新規プロジェクト開始に伴う契約書の申請です。",
        attachment_path=sample,
    )


def run_demo(use_cdp: bool | None) -> int:
    """UIを使わずにフローを実行する。動作確認・CI向け。"""
    data = _make_demo_data()

    def on_progress(step: int, total: int, label: str) -> None:
        logger.info("[ステップ %d/%d] %s", step, total, label)

    result = run_application_flow(data, use_cdp=use_cdp, on_progress=on_progress)

    print()
    if result.success:
        print("=" * 68)
        print("  デモ実行: 成功")
        print("=" * 68)
        print(result.detail)
        return 0

    print("=" * 68)
    print("  デモ実行: 失敗")
    print("=" * 68)
    print(result.summary)
    return 1


def serve_only() -> int:
    """ダミーサーバーだけ起動して待機する。"""
    server = DummySiteServer(
        config.DUMMY_SERVER_HOST, config.DUMMY_SERVER_PORT, config.DUMMY_SITE_DIR
    )
    server.start()
    print(f"\nブラウザで {server.base_url}/portal.html を開いてください（Ctrl+C で停止）\n")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\n停止します...")
        server.stop()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="書類申請業務の自動化ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--demo", action="store_true", help="UIなしでダミーデータを使いフローを実行")
    parser.add_argument("--serve", action="store_true", help="ダミーサーバーのみ起動")
    parser.add_argument("--headless", action="store_true", help="ブラウザを画面表示しない")
    parser.add_argument("--cdp", action="store_true", help="CDP接続を強制する")
    parser.add_argument("--no-cdp", action="store_true", help="ブラウザ新規起動を強制する")
    parser.add_argument("--debug", action="store_true", help="詳細ログを出力する")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    setup_logging(level=logging.DEBUG if args.debug else logging.INFO)

    if args.headless:
        config.HEADLESS = True

    # コマンドライン引数を config より優先する。
    # None のままなら config.USE_CDP の設定に従う。
    use_cdp: bool | None = None
    if args.cdp:
        use_cdp = True
    elif args.no_cdp:
        use_cdp = False

    if args.serve:
        return serve_only()

    server = _start_dummy_server_if_needed()
    try:
        if args.demo:
            return run_demo(use_cdp)

        # UIモード。ここでUIのインポートを行うのは、--demo/--serve を
        # ディスプレイのない環境（CI・SSH越し）で動かせるようにするため。
        from ui import launch_ui

        logger.info("UIを起動します")
        launch_ui()
        logger.info("UIを終了しました")
        return 0
    finally:
        if server is not None:
            server.stop()


if __name__ == "__main__":
    sys.exit(main())
