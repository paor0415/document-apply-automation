"""ログ設定。

出力先は2つ:
  1. 標準出力（コンソールで実行したときに見える）
  2. logs/automation.log（実行履歴が残る。日付ごとにローテーション）

UI(ui.py)は、これに加えて自前のハンドラを登録して画面上のログ欄にも表示する。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler

import config

LOGGER_NAME = "dx_apply"

_CONSOLE_FORMAT = "%(asctime)s [%(levelname)-7s] %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s:%(lineno)d | %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """ルートロガーを設定して返す。複数回呼んでもハンドラは重複しない。"""
    logger = logging.getLogger(LOGGER_NAME)

    if logger.handlers:  # 既に設定済みなら何もしない
        return logger

    logger.setLevel(level)
    logger.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt=_DATE_FORMAT))
    logger.addHandler(console)

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        config.LOG_DIR / "automation.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """各モジュール用の子ロガーを返す。"""
    if name is None:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
