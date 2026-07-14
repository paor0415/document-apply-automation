"""ブラウザ画面上の視覚フィードバック（進行バナー・要素ハイライト・小休止）。

自動操作はそのままでは一瞬で終わり、「申請ボタンを押したら何かが走って完了した」
ようにしか見えない。そこで、処理の節目ごとに:

  1. 画面上部のバナーに「今なにをしているか」を表示する
  2. 操作した要素を枠線で強調する（操作中=赤、完了=緑）
  3. 小休止を入れて、画面の状態を目で確認できる間を作る

ここで行うDOM操作はすべて見た目だけのもので、フォームの値・送信内容には一切影響しない。
表示に失敗しても本処理は止めない（握りつぶしてDEBUGログに残す）。
実サイトでもそのまま動くが、不要になったら config.SHOW_PROGRESS_BANNER = False と
config.STEP_PAUSE_MS = 0 で丸ごと無効化できる。
"""

from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

import config
from logger_setup import get_logger

logger = get_logger("visual")

# バナーを生成・更新するJavaScript。ページ遷移すると消えるので、都度呼び直す。
_BANNER_JS = """
(text) => {
  let el = document.getElementById('__dx_progress_banner');
  if (!el) {
    el = document.createElement('div');
    el.id = '__dx_progress_banner';
    el.style.cssText = [
      'position:fixed', 'top:12px', 'left:50%', 'transform:translateX(-50%)',
      'z-index:2147483647', 'background:rgba(17,24,39,.92)', 'color:#fff',
      'padding:10px 22px', 'border-radius:999px',
      'font-size:14px', 'font-family:sans-serif', 'font-weight:600',
      'pointer-events:none', 'white-space:nowrap',
      'box-shadow:0 4px 14px rgba(0,0,0,.35)',
    ].join(';');
    document.body.appendChild(el);
  }
  el.textContent = '\\u{1F916} ' + text;
}
"""

_OUTLINE_JS = """
(el, color) => {
  el.style.outline = '3px solid ' + color;
  el.style.outlineOffset = '2px';
}
"""

_COLOR_ACTIVE = "#e11d48"  # 操作中: 赤
_COLOR_DONE = "#16a34a"  # 完了: 緑


def show_step(page: Page, text: str) -> None:
    """ブラウザ画面上部のバナーに、現在の処理内容を表示する。"""
    if not config.SHOW_PROGRESS_BANNER:
        return
    try:
        page.evaluate(_BANNER_JS, text)
    except PlaywrightError as e:
        logger.debug("進行バナーの表示に失敗しました（処理は継続します）: %s", e)


def mark_active(locator: Locator) -> None:
    """これから操作する要素を赤枠で強調する。"""
    _outline(locator, _COLOR_ACTIVE)


def mark_done(locator: Locator) -> None:
    """操作が完了した要素を緑枠にする。処理の進んだ跡が画面に残る。"""
    _outline(locator, _COLOR_DONE)


def _outline(locator: Locator, color: str) -> None:
    if not config.SHOW_PROGRESS_BANNER:
        return
    try:
        locator.evaluate(_OUTLINE_JS, color)
    except PlaywrightError as e:
        logger.debug("要素の強調表示に失敗しました（処理は継続します）: %s", e)


def pause(page: Page) -> None:
    """画面の状態を目で確認するための小休止。"""
    if config.STEP_PAUSE_MS <= 0:
        return
    try:
        page.wait_for_timeout(config.STEP_PAUSE_MS)
    except PlaywrightError as e:
        logger.debug("小休止に失敗しました（処理は継続します）: %s", e)
