"""申請フロー全体の組み立て。

ブラウザ操作の「手順」だけをここに集約している。UI(ui.py)もCLI(main.py --demo)も
この run_application_flow() を呼ぶだけなので、UIをStreamlitやFastAPIに差し替えても
このファイルは変更不要。

フローは6ステップに分かれており、各ステップの実行状態(StepState)を on_flow
コールバックでUIへ通知する。UI側はこれを「処理フロー」として一覧表示し、
クリックすると そのステップ中のログ + 証跡スクリーンショット を確認できる。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

import config
from browser_controller import BrowserController
from exceptions import AutomationError
from form_filler import FormFiller
from logger_setup import LOGGER_NAME, get_logger
from models import ApplicationData

logger = get_logger("workflow")

# (ステップ名, そのステップの証跡スクリーンショットのファイル名候補)
# 候補が複数あるステップは、実行結果によって保存される名前が変わる
# （例: 送信ステップは 通常=05_result / DRY_RUN=05_dry_run_stopped / 結果不明=05_submit_timeout）。
STEP_DEFS: list[tuple[str, tuple[str, ...]]] = [
    ("ブラウザに接続", ()),
    ("ポータルサイトにアクセス", ("01_portal.png",)),
    ("書類申請サイトへ遷移", ("02_apply_form.png",)),
    ("フォームに入力", ("03_form_filled.png",)),
    ("ファイルを添付", ("04_file_attached.png",)),
    ("申請を送信・結果確認", ("05_result.png", "05_dry_run_stopped.png", "05_submit_timeout.png")),
]
TOTAL_STEPS = len(STEP_DEFS)

# 進捗通知用のコールバック: (現在のステップ番号, 全ステップ数, 説明文)
ProgressCallback = Callable[[int, int, str], None]
# フロー状態通知用のコールバック: 全ステップの状態一覧（コピー）を受け取る
FlowCallback = Callable[[list["StepState"]], None]


@dataclass
class StepState:
    """フロー1ステップの実行状態。UIの「処理フロー」表示に使う。"""

    index: int  # 1始まり
    name: str
    status: str = "pending"  # pending / running / success / skipped / error
    detail: str = ""  # このステップで実施した内容（実行中に出たログ）
    screenshot_names: tuple[str, ...] = field(default=())


def make_initial_steps() -> list[StepState]:
    """全ステップを未実行状態で生成する。UIの初期表示にも使う。"""
    return [
        StepState(index=i, name=name, screenshot_names=shots)
        for i, (name, shots) in enumerate(STEP_DEFS, start=1)
    ]


class _StepLogCollector(logging.Handler):
    """フロー実行中のログを、現在のステップごとに束ねるハンドラ。

    ステップの「詳細」を別途組み立てる手間をかけず、既にログとして出している
    情報（アクセス先URL、入力した値、サイトからの応答など）をそのまま流用する。
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        self._buckets: dict[int, list[str]] = {}
        self.current_step = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buckets.setdefault(self.current_step, []).append(self.format(record))
        except Exception:  # noqa: BLE001 - ログ収集で本処理を止めない
            pass

    def detail_for(self, index: int) -> str:
        return "\n".join(self._buckets.get(index, []))


@dataclass
class FlowResult:
    """フローの実行結果。"""

    success: bool
    message: str
    detail: str = ""
    steps: list[StepState] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return self.message if not self.detail else f"{self.message}\n\n{self.detail}"


def _clear_old_screenshots() -> None:
    """前回実行の証跡スクリーンショットを消す。

    消さずに残すと、途中で失敗した実行のフロー詳細を開いたとき、
    前回実行の古い画像が「今回の証跡」として表示されてしまう。
    """
    for _, names in STEP_DEFS:
        for name in names:
            path = config.SCREENSHOT_DIR / name
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def run_application_flow(
    data: ApplicationData,
    use_cdp: bool | None = None,
    on_progress: ProgressCallback | None = None,
    on_flow: FlowCallback | None = None,
) -> FlowResult:
    """申請フローを最初から最後まで実行する。

    この関数は例外を送出しない。成否は FlowResult で返す。UIのワーカースレッドから
    呼ばれるため、例外を投げるとスレッドごと落ちて利用者に何も伝わらないためである。

    Args:
        data:        申請内容。
        use_cdp:     CDP接続を使うか。Noneなら config.USE_CDP に従う。
        on_progress: 進捗通知のコールバック（ステップ番号ベース。CLI表示用）。
        on_flow:     フロー状態通知のコールバック（全ステップの状態一覧。UI表示用）。

    Returns:
        実行結果（steps に各ステップの実施内容が入る）。
    """
    steps = make_initial_steps()
    collector = _StepLogCollector()
    root_logger = logging.getLogger(LOGGER_NAME)
    root_logger.addHandler(collector)

    def notify_flow() -> None:
        if on_flow is not None:
            on_flow([replace(s) for s in steps])  # UIスレッドに渡すのでコピーを送る

    def start_step(index: int) -> None:
        collector.current_step = index
        steps[index - 1].status = "running"
        if on_progress is not None:
            on_progress(index, TOTAL_STEPS, steps[index - 1].name)
        notify_flow()

    def finish_step(index: int, status: str = "success") -> None:
        steps[index - 1].detail = collector.detail_for(index)
        steps[index - 1].status = status
        notify_flow()

    logger.info("=" * 68)
    logger.info("書類申請フローを開始します")
    for key, value in data.as_log_dict().items():
        logger.info("  %s: %s", key, value)
    logger.info("=" * 68)

    current = 0
    try:
        # 入力チェックはブラウザを開く前に行う。
        # 時間のかかるブラウザ操作を始めてから入力ミスで落ちるのを防ぐ。
        data.validate()
        _clear_old_screenshots()
        notify_flow()

        current = 1
        start_step(1)
        with BrowserController(use_cdp=use_cdp) as controller:
            finish_step(1)

            current = 2
            start_step(2)
            portal_page = controller.open_portal()
            finish_step(2)

            current = 3
            start_step(3)
            apply_page = controller.click_link_to_apply(portal_page)
            finish_step(3)

            filler = FormFiller(apply_page)

            current = 4
            start_step(4)
            filler.fill_form(data)
            finish_step(4)

            current = 5
            start_step(5)
            if data.attachment_path is not None:
                filler.attach_file(Path(data.attachment_path))
                finish_step(5)
            else:
                logger.info("添付ファイルの指定がないため、添付処理をスキップします")
                finish_step(5, status="skipped")

            current = 6
            start_step(6)
            result_text = filler.submit()
            finish_step(6)

        logger.info("=" * 68)
        logger.info("書類申請フローが正常に完了しました")
        logger.info("=" * 68)
        return FlowResult(
            success=True, message="申請が完了しました。", detail=result_text, steps=steps
        )

    except AutomationError as e:
        # このツールが想定している失敗。原因と対処が e に入っている。
        logger.error("書類申請フローが失敗しました: %s", e.message)
        if e.hint:
            logger.error("  → 対処: %s", e.hint)
        if 1 <= current <= TOTAL_STEPS:
            finish_step(current, status="error")
        return FlowResult(
            success=False,
            message=e.message,
            detail=f"対処: {e.hint}" if e.hint else "",
            steps=steps,
        )

    except Exception as e:  # noqa: BLE001 - 想定外の例外もUIに届ける必要がある
        logger.exception("想定外のエラーが発生しました")
        if 1 <= current <= TOTAL_STEPS:
            finish_step(current, status="error")
        return FlowResult(
            success=False,
            message=f"想定外のエラーが発生しました: {type(e).__name__}: {e}",
            detail=f"詳しい情報は {config.LOG_DIR / 'automation.log'} を確認してください。",
            steps=steps,
        )

    finally:
        root_logger.removeHandler(collector)
