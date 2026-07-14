"""Playwrightの起動・CDP接続・ページ遷移を担当するモジュール。

■ なぜ launch() ではなく connect_over_cdp() なのか
社内サイトがSSOやクライアント証明書認証を使っている場合、Playwrightが新規起動した
まっさらなブラウザではログインを通せない（証明書ストアもセッションも持っていないため）。
そこで、利用者が普段使っていて既にログイン済みのChromeを --remote-debugging-port 付きで
起動しておき、そこへ「外から接続」する方式を採る。認証はChrome側で済んでいる状態を使う。

■ 開発時の逃げ道
config.USE_CDP = False にすると通常の launch() にフォールバックする。
ダミーサイトでの動作確認は認証が不要なので、こちらで足りる。
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType

from playwright.sync_api import Browser, BrowserContext, Page, Playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import config
import visual_feedback as visual
from exceptions import BrowserConnectionError, ElementNotFoundError, NavigationError
from logger_setup import get_logger

logger = get_logger("browser")


class BrowserController:
    """ブラウザの接続からページ遷移までを担当する。

    with文で使うと、正常終了・異常終了のどちらでも後片付けが走る:

        with BrowserController() as bc:
            page = bc.open_portal()
            apply_page = bc.click_link_to_apply(page)
    """

    def __init__(self, use_cdp: bool | None = None) -> None:
        self.use_cdp = config.USE_CDP if use_cdp is None else use_cdp
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        # CDP接続の場合、利用者のChromeを勝手に閉じてはいけないので閉じない。
        # launch した場合だけ自分で閉じる。この区別を保持する。
        self._owns_browser = False
        self._pages_opened: list[Page] = []

    # ------------------------------------------------------------------
    # コンテキストマネージャ
    # ------------------------------------------------------------------

    def __enter__(self) -> "BrowserController":
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 接続
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """ブラウザに接続（またはブラウザを起動）する。

        Raises:
            BrowserConnectionError: 接続にも起動にも失敗した場合。
        """
        self._playwright = sync_playwright().start()

        if self.use_cdp:
            self._connect_over_cdp()
        else:
            self._launch_browser()

    def _connect_over_cdp(self) -> None:
        """起動済みChromeへCDPで接続する。"""
        assert self._playwright is not None
        logger.info("CDP接続を試みます: %s", config.CDP_ENDPOINT)

        try:
            # slow_mo はCDP接続でも有効。目視確認しやすいよう launch() と同じ設定を使う。
            self._browser = self._playwright.chromium.connect_over_cdp(
                config.CDP_ENDPOINT, slow_mo=config.SLOW_MO_MS
            )
        except PlaywrightError as e:
            self._stop_playwright()
            raise BrowserConnectionError(
                f"起動済みChromeへの接続に失敗しました（接続先: {config.CDP_ENDPOINT}）\n"
                f"  Playwrightからのメッセージ: {e}",
                hint=(
                    "デバッグポートを開いた状態でChromeを起動してから、もう一度実行してください。\n"
                    "     macOS の場合:\n"
                    '       "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\\n'
                    f"         --remote-debugging-port={config.CDP_PORT} \\\n"
                    '         --user-data-dir="$HOME/chrome-debug-profile"\n'
                    "     起動後、そのChromeで社内ポータルにログインしておいてください。\n"
                    "     ※ 既にChromeが起動中だとポートが開きません。一度すべて終了してから実行してください。"
                ),
            ) from e

        self._owns_browser = False

        # CDP接続では、利用者が既に開いているタブ群を含むコンテキストが取れる。
        # ここにログイン済みのCookieやセッションが入っているので、これを使う。
        if not self._browser.contexts:
            raise BrowserConnectionError(
                "接続はできましたが、Chrome側に利用可能なウィンドウがありません。",
                hint="Chromeでタブを1つ以上開いた状態にしてから、もう一度実行してください。",
            )

        self._context = self._browser.contexts[0]
        self._context.set_default_timeout(config.ELEMENT_TIMEOUT_MS)
        self._context.set_default_navigation_timeout(config.NAVIGATION_TIMEOUT_MS)
        logger.info(
            "CDP接続に成功しました（既存タブ数: %d、Chromeバージョン: %s）",
            len(self._context.pages),
            self._browser.version,
        )

    def _launch_browser(self) -> None:
        """新規にブラウザを起動する（ダミーサイト検証用）。"""
        assert self._playwright is not None
        logger.info("ブラウザを新規起動します（USE_CDP=False の検証モード）")

        try:
            self._browser = self._playwright.chromium.launch(
                headless=config.HEADLESS,
                slow_mo=config.SLOW_MO_MS,
            )
        except PlaywrightError as e:
            self._stop_playwright()
            raise BrowserConnectionError(
                f"ブラウザの起動に失敗しました: {e}",
                hint="`python -m playwright install chromium` を実行してブラウザ本体を導入してください。",
            ) from e

        self._owns_browser = True
        self._context = self._browser.new_context(accept_downloads=True)
        self._context.set_default_timeout(config.ELEMENT_TIMEOUT_MS)
        self._context.set_default_navigation_timeout(config.NAVIGATION_TIMEOUT_MS)
        logger.info("ブラウザを起動しました（バージョン: %s）", self._browser.version)

    # ------------------------------------------------------------------
    # ページ遷移
    # ------------------------------------------------------------------

    def open_portal(self, url: str | None = None) -> Page:
        """社内ポータル(OOOサイト)を新しいタブで開く。

        Args:
            url: 開くURL。省略時は config.PORTAL_URL。

        Returns:
            開いたページ。

        Raises:
            NavigationError: アクセスに失敗した場合。
        """
        if self._context is None:
            raise BrowserConnectionError(
                "ブラウザに接続していません。", hint="先に connect() を呼んでください。"
            )

        target = url or config.PORTAL_URL
        logger.info("ポータルサイトにアクセスします: %s", target)

        # 利用者が開いている既存タブを乗っ取らないよう、必ず新しいタブを開く。
        page = self._context.new_page()
        self._pages_opened.append(page)

        try:
            response = page.goto(target, wait_until="domcontentloaded")
        except PlaywrightTimeoutError as e:
            raise NavigationError(
                f"ポータルサイトへのアクセスがタイムアウトしました（{config.NAVIGATION_TIMEOUT_MS / 1000:.0f}秒）: {target}",
                hint=(
                    "・URLが正しいか確認してください（config.py の PORTAL_URL）\n"
                    "     ・社内ネットワーク/VPNに接続されているか確認してください\n"
                    "     ・サイトが重い場合は config.py の NAVIGATION_TIMEOUT_MS を延ばしてください"
                ),
            ) from e
        except PlaywrightError as e:
            raise NavigationError(
                f"ポータルサイトへアクセスできませんでした: {target}\n  Playwrightからのメッセージ: {e}",
                hint="URL・ネットワーク接続・ダミーサーバーの起動状況を確認してください。",
            ) from e

        # HTTPステータスの確認。file:// などレスポンスがない場合は response が None になる。
        if response is not None and not response.ok:
            raise NavigationError(
                f"ポータルサイトがエラーを返しました（HTTPステータス {response.status}）: {target}",
                hint=(
                    "認証切れの可能性があります。接続先のChromeで手動アクセスし、"
                    "ログイン状態を確認してください。"
                    if response.status in (401, 403)
                    else "URLが正しいか確認してください。"
                ),
            )

        logger.info("ポータルサイトを開きました（タイトル: %s）", page.title() or "(タイトルなし)")
        visual.show_step(page, "ポータルサイト(OOOサイト)を開きました")
        visual.pause(page)
        self._screenshot(page, "01_portal")
        return page

    def click_link_to_apply(self, page: Page, selector: str | None = None) -> Page:
        """ポータル上のリンクをクリックして書類申請サイトへ遷移する。

        リンクが target="_blank" で新規タブを開く場合と、同一タブ内で遷移する場合の
        両方に対応する。実サイトがどちらの挙動かは事前に分からないため、
        まず新規タブの出現を待ち、出なければ同一タブ遷移とみなす。

        Args:
            page: ポータルのページ。
            selector: リンクのセレクタ。省略時は config.PORTAL_SELECTORS.apply_link。

        Returns:
            申請サイトのページ（新規タブならそのタブ、同一タブ遷移なら引数と同じページ）。

        Raises:
            ElementNotFoundError: リンクが見つからない場合。
            NavigationError: 遷移に失敗した、または遷移先が想定と違う場合。
        """
        link_selector = selector or config.PORTAL_SELECTORS.apply_link
        logger.info("申請サイトへのリンクをクリックします（セレクタ: %s）", link_selector)

        link = page.locator(link_selector)
        try:
            link.wait_for(state="visible", timeout=config.ELEMENT_TIMEOUT_MS)
        except PlaywrightTimeoutError as e:
            raise ElementNotFoundError(
                "申請サイトへのリンク",
                link_selector,
                hint=(
                    "config.py の PortalSelectors.apply_link を実サイトのリンクに合わせてください。\n"
                    "     ブラウザでF12を押し、リンクを右クリック →「検証」で要素を確認できます。"
                ),
            ) from e

        visual.show_step(page, "「書類申請システム」のリンクをクリックします")
        visual.mark_active(link)
        visual.pause(page)

        try:
            # 新規タブが開くかを待つ。開かなければTimeoutErrorになるが、
            # クリック自体は実行済みなので、同一タブ遷移として処理を続ける。
            with page.context.expect_page(timeout=config.POPUP_DETECT_TIMEOUT_MS) as popup_info:
                link.click()
            apply_page = popup_info.value
            self._pages_opened.append(apply_page)
            apply_page.wait_for_load_state("domcontentloaded", timeout=config.NAVIGATION_TIMEOUT_MS)
            logger.info("新規タブで申請サイトが開きました")
        except PlaywrightTimeoutError:
            apply_page = page
            try:
                apply_page.wait_for_load_state(
                    "domcontentloaded", timeout=config.NAVIGATION_TIMEOUT_MS
                )
            except PlaywrightTimeoutError as e:
                raise NavigationError(
                    "リンクはクリックできましたが、申請サイトの読み込みが完了しませんでした。",
                    hint="サイトが重い場合は config.py の NAVIGATION_TIMEOUT_MS を延ばしてください。",
                ) from e
            logger.info("同一タブ内で申請サイトへ遷移しました")

        current_url = apply_page.url
        logger.info("遷移先URL: %s", current_url)

        # 意図しないページ（ログイン画面へのリダイレクト等）に飛んでいないか確認する。
        if config.VERIFY_APPLY_URL and not current_url.startswith(config.APPLY_URL_PREFIX):
            raise NavigationError(
                f"想定と異なるページに遷移しました。\n"
                f"  期待するURLの先頭: {config.APPLY_URL_PREFIX}\n"
                f"  実際のURL:         {current_url}",
                hint=(
                    "ログイン画面にリダイレクトされている可能性があります。接続先のChromeで\n"
                    "     ログイン状態を確認してください。URL自体が想定と違う場合は\n"
                    "     config.py の APPLY_URL_PREFIX を修正するか、VERIFY_APPLY_URL=False にしてください。"
                ),
            )

        logger.info("申請サイトを開きました（タイトル: %s）", apply_page.title() or "(タイトルなし)")
        visual.show_step(apply_page, "書類申請サイトへ遷移しました")
        visual.pause(apply_page)
        self._screenshot(apply_page, "02_apply_form")
        return apply_page

    # ------------------------------------------------------------------
    # 補助
    # ------------------------------------------------------------------

    def _screenshot(self, page: Page, name: str) -> Path | None:
        """スクリーンショットを保存する。失敗しても本処理は止めない。"""
        if not config.SAVE_SCREENSHOTS:
            return None
        try:
            config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            path = config.SCREENSHOT_DIR / f"{name}.png"
            page.screenshot(path=str(path), full_page=True)
            logger.debug("スクリーンショットを保存しました: %s", path)
            return path
        except PlaywrightError as e:
            logger.debug("スクリーンショットの保存に失敗しました（処理は継続します）: %s", e)
            return None

    def screenshot(self, page: Page, name: str) -> Path | None:
        """外部モジュール（form_filler）から使うための公開版。"""
        return self._screenshot(page, name)

    def close(self) -> None:
        """後片付け。

        CDP接続の場合は利用者のChromeなので閉じない。ただし、このツールが開いた
        タブは閉じる（開きっぱなしだとタブが増え続けるため）。ここでの例外は
        本来の処理結果を隠してしまうので、すべて握りつぶしてログに留める。

        config.KEEP_BROWSER_OPEN=True の場合は、成功・失敗を問わずブラウザ
        （新規起動時はウィンドウ、CDP接続時は開いたタブ）を閉じずに残す。
        フローが一瞬で終わって目視確認できない、という問題を避けるためのもの。
        """
        # HEADLESS時は画面が無く「残して確認する」意味がないうえ、見えないブラウザが
        # 実行のたびに溜まっていくため、残すのは画面のあるブラウザのときだけにする。
        keep_open = config.KEEP_BROWSER_OPEN and not (self._owns_browser and config.HEADLESS)
        if keep_open:
            logger.info(
                "KEEP_BROWSER_OPEN が有効なため、ブラウザは閉じずに残します"
                "（確認が終わったら手動で閉じてください）"
            )
            # Playwright側のオブジェクト参照を手放すだけで、close()/stop()は呼ばない。
            # ここで close()/stop() を呼ぶと、新規起動したブラウザ本体ごと終了してしまう。
            self._pages_opened.clear()
            self._browser = None
            self._context = None
            self._playwright = None
            return

        if not self._owns_browser:
            for page in self._pages_opened:
                try:
                    if not page.is_closed():
                        page.close()
                except PlaywrightError:
                    pass
        self._pages_opened.clear()

        if self._browser is not None:
            try:
                # launch した場合は close() でブラウザ終了。
                # CDP接続の場合、close() は「接続を切る」だけで、利用者のChromeは終了しない。
                self._browser.close()
            except PlaywrightError as e:
                logger.debug("ブラウザの切断時に例外が発生しました（無視します）: %s", e)
            self._browser = None

        self._context = None
        self._stop_playwright()
        logger.info("ブラウザとの接続を終了しました")

    def _stop_playwright(self) -> None:
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception as e:  # noqa: BLE001 - 後片付けなので何が来ても止めない
                logger.debug("Playwrightの停止時に例外が発生しました（無視します）: %s", e)
            self._playwright = None
