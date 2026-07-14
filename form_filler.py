"""フォーム入力・ファイル添付・送信を担当するモジュール。

■ 設計方針
実サイトのフォームは、同じ「部署」欄でも <select> かもしれないし <input> かもしれない。
セレクタだけ差し替えれば動くようにするため、_fill_field() は要素のタグ種別を
実行時に判別して入力方法を切り替える。これにより config.py のセレクタ変更だけで
大抵のフォームに対応できる。
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Locator, Page
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import config
import visual_feedback as visual
from exceptions import ElementNotFoundError, FileAttachError, SubmitError
from logger_setup import get_logger
from models import ApplicationData

logger = get_logger("form")


class FormFiller:
    """書類申請フォームへの入力から送信までを担当する。"""

    def __init__(self, page: Page, selectors: config.FormSelectors | None = None) -> None:
        self.page = page
        self.selectors = selectors or config.FORM_SELECTORS

    # ------------------------------------------------------------------
    # ステップ1: フォーム入力
    # ------------------------------------------------------------------

    def fill_form(self, data: ApplicationData) -> None:
        """各入力欄に値を埋める。

        入力・添付・送信の実行順序は workflow.run_application_flow() が制御する
        （ステップごとの状態をUIへ通知するため、ここでは束ねない）。
        """
        logger.info("フォームへの入力を開始します")
        visual.show_step(self.page, "フォームに入力しています…")

        # (セレクタ, 入力値, 項目名, 必須か) の順。
        # 【要差し替え】実サイトの項目が増減する場合は、この対応表と
        # config.py の FormSelectors、models.py の ApplicationData を合わせて変更する。
        fields: list[tuple[str, str, str, bool]] = [
            (self.selectors.applicant_name, data.applicant_name, "氏名", True),
            (self.selectors.apply_date, data.apply_date, "申請日", True),
            (self.selectors.department, data.department, "部署", True),
            (self.selectors.doc_title, data.doc_title, "書類名", True),
            (self.selectors.reason, data.reason, "申請理由", False),
        ]

        for selector, value, label, required in fields:
            if not value and not required:
                logger.debug("%s は空のためスキップします", label)
                continue
            self._fill_field(selector, value, label)

        logger.info("フォームへの入力が完了しました")
        visual.show_step(self.page, "フォーム入力が完了しました（入力済みの欄は緑枠）")
        visual.pause(self.page)
        self._screenshot("03_form_filled")

    def _fill_field(self, selector: str, value: str, label: str) -> None:
        """1つの入力欄に値を埋める。要素の種別に応じて入力方法を切り替える。"""
        locator = self.page.locator(selector)

        try:
            locator.wait_for(state="visible", timeout=config.ELEMENT_TIMEOUT_MS)
        except PlaywrightTimeoutError as e:
            raise ElementNotFoundError(f"入力欄「{label}」", selector) from e

        # 同じセレクタに複数要素が該当すると、Playwrightは操作時にエラーを出す。
        # セレクタが緩すぎるサインなので、原因を明示して早めに止める。
        count = locator.count()
        if count > 1:
            raise ElementNotFoundError(
                f"入力欄「{label}」に {count} 個の要素が該当してしまいました",
                selector,
                hint=(
                    "セレクタが緩すぎて複数の要素にマッチしています。\n"
                    "     config.py でより具体的なセレクタ（id指定や親要素からの絞り込み）に変更してください。"
                ),
            )

        try:
            visual.mark_active(locator)
            tag = locator.evaluate("el => el.tagName.toLowerCase()")

            if tag == "select":
                self._select_option(locator, value, label, selector)
            else:
                # input / textarea はどちらも fill() で入力できる。
                # fill() は既存の値をクリアしてから入力するので、再実行時も値が重複しない。
                locator.fill(value)

            visual.mark_done(locator)
            logger.info("  入力: %s = %r", label, value)
        except ElementNotFoundError:
            raise
        except PlaywrightError as e:
            raise ElementNotFoundError(
                f"入力欄「{label}」への入力に失敗しました",
                selector,
                hint=(
                    f"要素は見つかりましたが操作できませんでした。\n"
                    f"     Playwrightからのメッセージ: {e}\n"
                    "     要素が他の要素に隠れている、または読み取り専用の可能性があります。"
                ),
            ) from e

    def _select_option(self, locator: Locator, value: str, label: str, selector: str) -> None:
        """<select> のプルダウンから選択する。

        実サイトでは option の value属性 と 表示ラベル が異なることが多い
        （例: <option value="dev">開発部</option>）。どちらで指定されても動くよう、
        まず value属性 で試し、駄目なら表示ラベルで試す。
        """
        try:
            locator.select_option(value=value)
            return
        except PlaywrightError:
            pass

        try:
            locator.select_option(label=value)
            return
        except PlaywrightError as e:
            available = locator.evaluate(
                "el => Array.from(el.options).map(o => `${o.label} (value=${o.value})`)"
            )
            raise ElementNotFoundError(
                f"プルダウン「{label}」で {value!r} を選択できませんでした",
                selector,
                hint=(
                    f"選択可能な項目: {', '.join(available) if available else '(なし)'}\n"
                    f"     Playwrightからのメッセージ: {e}"
                ),
            ) from e

    # ------------------------------------------------------------------
    # ステップ2: ファイル添付
    # ------------------------------------------------------------------

    def attach_file(self, file_path: Path) -> None:
        """添付ファイルをアップロードする。

        set_input_files() は input[type=file] に直接ファイルを渡す方式なので、
        OSのファイル選択ダイアログは開かない（＝ダイアログ操作の自動化は不要）。
        input要素が display:none で隠されていても動作する。

        Raises:
            FileAttachError: 添付に失敗した場合。
        """
        selector = self.selectors.attachment
        logger.info("ファイルを添付します: %s", file_path)

        # ブラウザに渡す前にローカル側で確認する。ここで弾けば原因が明確になる。
        if not file_path.exists():
            raise FileAttachError(
                f"添付ファイルが見つかりません: {file_path}",
                hint="ファイルパスが正しいか、ファイルが移動・削除されていないか確認してください。",
            )
        if not file_path.is_file():
            raise FileAttachError(
                f"指定されたパスはファイルではありません: {file_path}",
                hint="フォルダではなく、ファイルを指定してください。",
            )

        size = file_path.stat().st_size
        if size == 0:
            raise FileAttachError(
                f"添付ファイルが空です（0バイト）: {file_path}",
                hint="中身のあるファイルを指定してください。",
            )

        locator = self.page.locator(selector)

        try:
            # 添付用のinputは意図的に隠されていることが多いため、
            # visible ではなく attached（DOM上に存在する）で待つ。
            locator.wait_for(state="attached", timeout=config.ELEMENT_TIMEOUT_MS)
        except PlaywrightTimeoutError as e:
            raise FileAttachError(
                f"添付ファイルの入力欄が見つかりませんでした（セレクタ: {selector}）",
                hint=(
                    "config.py の FormSelectors.attachment を実サイトに合わせてください。\n"
                    "     見た目の「参照」ボタンではなく、input[type=file] 要素を指定する必要があります。\n"
                    "     開発者ツールのコンソールで document.querySelectorAll('input[type=file]') を\n"
                    "     実行すると、対象の要素を探せます。"
                ),
            ) from e

        try:
            locator.set_input_files(str(file_path))
        except PlaywrightError as e:
            raise FileAttachError(
                f"ファイルの添付に失敗しました: {file_path}\n  Playwrightからのメッセージ: {e}",
                hint=(
                    "・指定した要素が input[type=file] か確認してください\n"
                    "     ・サイト側のファイルサイズ上限や拡張子制限に抵触していないか確認してください"
                ),
            ) from e

        logger.info("ファイルを添付しました: %s (%s)", file_path.name, _format_size(size))
        visual.show_step(self.page, f"ファイルを添付しました: {file_path.name}")
        visual.pause(self.page)
        self._screenshot("04_file_attached")

    # ------------------------------------------------------------------
    # ステップ3: 送信
    # ------------------------------------------------------------------

    def submit(self) -> str:
        """申請ボタンを押して送信し、結果を確認する。

        Returns:
            結果画面のテキスト。

        Raises:
            SubmitError: 送信に失敗した、または結果を確認できなかった場合。
        """
        selector = self.selectors.submit_button

        if config.DRY_RUN:
            logger.warning(
                "DRY_RUN が有効なため、送信ボタンは押しません（config.py の DRY_RUN=False で実際に送信されます）"
            )
            visual.show_step(self.page, "DRY_RUN: 送信直前で停止しました（送信していません）")
            self._screenshot("05_dry_run_stopped")
            return "DRY_RUN: 送信直前で停止しました。実際には送信していません。"

        logger.info("申請ボタンを押します（セレクタ: %s）", selector)
        button = self.page.locator(selector)

        try:
            button.wait_for(state="visible", timeout=config.ELEMENT_TIMEOUT_MS)
        except PlaywrightTimeoutError as e:
            raise SubmitError(
                f"申請ボタンが見つかりませんでした（セレクタ: {selector}）",
                hint="config.py の FormSelectors.submit_button を実サイトに合わせてください。",
            ) from e

        # 入力必須項目が未入力だとボタンがdisabledのままのサイトがある。
        # その状態でclickすると原因の分かりにくいタイムアウトになるため、先に確認する。
        if not button.is_enabled():
            raise SubmitError(
                "申請ボタンが押せない状態（無効）です。",
                hint=(
                    "入力必須の項目が埋まっていない可能性があります。\n"
                    "     logs/screenshots/03_form_filled.png で入力後のフォームを確認してください。"
                ),
            )

        visual.show_step(self.page, "申請ボタンを押して送信します")
        visual.mark_active(button)
        visual.pause(self.page)

        try:
            button.click()
        except PlaywrightError as e:
            raise SubmitError(
                f"申請ボタンのクリックに失敗しました: {e}",
                hint="ボタンが他の要素（モーダル等）に隠れていないか確認してください。",
            ) from e

        return self._wait_for_result()

    def _wait_for_result(self) -> str:
        """送信結果（成功要素 or 失敗要素）が現れるまで待つ。

        成功・失敗どちらが先に現れるか分からないので、両方を同時に待って
        先に現れた方で判定する。
        """
        success_sel = self.selectors.success_indicator
        error_sel = self.selectors.error_indicator
        logger.info("送信結果を待っています（最大 %d 秒）", config.SUBMIT_TIMEOUT_MS / 1000)

        combined = f"{success_sel}, {error_sel}"
        try:
            self.page.locator(combined).first.wait_for(
                state="visible", timeout=config.SUBMIT_TIMEOUT_MS
            )
        except PlaywrightTimeoutError as e:
            self._screenshot("05_submit_timeout")
            raise SubmitError(
                f"送信後、結果画面を確認できませんでした（{config.SUBMIT_TIMEOUT_MS / 1000:.0f}秒待機）。\n"
                f"  探した要素: 成功={success_sel} / 失敗={error_sel}",
                hint=(
                    "申請自体は完了している可能性があります。実際のサイトで申請状況を確認してください。\n"
                    "     logs/screenshots/05_submit_timeout.png に送信後の画面を保存しました。\n"
                    "     結果画面のHTMLに合わせて config.py の success_indicator / error_indicator を修正してください。"
                ),
            ) from e

        visual.show_step(self.page, "送信結果の画面です")
        self._screenshot("05_result")

        error_locator = self.page.locator(error_sel)
        if error_locator.count() > 0 and error_locator.first.is_visible():
            message = (error_locator.first.inner_text() or "").strip()
            raise SubmitError(
                f"サイト側が申請を受け付けませんでした。\n  サイトからのメッセージ: {message}",
                hint="入力内容がサイトの要件を満たしているか確認してください。",
            )

        result_text = (self.page.locator(success_sel).first.inner_text() or "").strip()
        logger.info("申請の送信に成功しました")
        logger.info("サイトからの応答: %s", result_text.replace("\n", " / "))
        return result_text

    # ------------------------------------------------------------------
    # 補助
    # ------------------------------------------------------------------

    def _screenshot(self, name: str) -> None:
        if not config.SAVE_SCREENSHOTS:
            return
        try:
            config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(config.SCREENSHOT_DIR / f"{name}.png"), full_page=True)
        except PlaywrightError as e:
            logger.debug("スクリーンショットの保存に失敗しました（処理は継続します）: %s", e)


def _format_size(num_bytes: int) -> str:
    """バイト数を読みやすい単位にする。"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
