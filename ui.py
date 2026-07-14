"""ユーザー入力画面（Tkinter）。

■ なぜTkinterか
このツールはローカルChromeへCDP接続する都合上、利用者のPC上でしか動かせない。
そのためWebサーバー形式(Streamlit)の利点が活きにくく、追加インストールが不要で
Playwrightの同期APIと素直に組み合わせられるTkinterを採用した。
（Streamlitはスクリプト再実行モデルとイベントループの都合で、同期APIの扱いに癖がある）

■ 将来Web化する場合
このファイルは workflow.run_application_flow(data) を呼んでいるだけで、
ブラウザ操作のロジックを一切持っていない。Streamlit/FastAPI版を作る場合も
ApplicationData を組み立てて同じ関数を呼べばよく、他のファイルは変更不要。

■ スレッド設計
Playwrightの処理は数十秒かかるため、メインスレッドで実行するとUIが固まる。
ワーカースレッドで実行し、ログと進捗は queue.Queue 経由でUIスレッドに渡す。
Tkinterのウィジェットはメインスレッドからしか触れないため、この受け渡しが必須。
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import tkinter as tk
import webbrowser
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import config
from logger_setup import LOGGER_NAME
from models import ApplicationData
from workflow import FlowResult, StepState, make_initial_steps, run_application_flow

# 部署の選択肢: (画面表示, フォームに渡す値)
# 【要差し替え】実サイトのプルダウンの選択肢に合わせて変更する。
# 右側の値が config.py の FormSelectors.department で指定した <select> に渡される。
# form_filler._select_option() は value属性・表示ラベルのどちらでも選択できるので、
# 実サイトのvalue属性が不明なら、右側に表示ラベル（例: "開発部"）を入れてもよい。
DEPARTMENTS: list[tuple[str, str]] = [
    ("開発部", "dev"),
    ("営業部", "sales"),
    ("人事部", "hr"),
    ("総務部", "ga"),
    ("経理部", "acc"),
]

_PAD = {"padx": 12, "pady": 6}

# 実行ログ欄の配色（コンソール風）。
# ttkウィジェットはOSのテーマに追従するが、tk.Text は自分で色を指定しないと
# 背景だけがテーマに追従して文字が読めなくなることがあるため、明示的に固定する。
_LOG_BG = "#1e1e1e"
_LOG_FG = "#d4d4d4"

# 処理フロー各ステップの表示: status → (アイコン, アイコン色, 状態の表示名)
_STATUS_STYLES: dict[str, tuple[str, str, str]] = {
    "pending": ("○", "#94a3b8", "未実行"),
    "running": ("▶", "#2563eb", "実行中…"),
    "success": ("✅", "#16a34a", "成功"),
    "skipped": ("⏭", "#94a3b8", "スキップ"),
    "error": ("❌", "#dc2626", "失敗"),
}


def _set_hand_cursor(widget: tk.Widget) -> None:
    """クリックできることが分かるよう、カーソルを指の形にする（環境差があるため順に試す）。"""
    for cursor in ("pointinghand", "hand2"):
        try:
            widget.configure(cursor=cursor)
            return
        except tk.TclError:
            continue


class QueueLogHandler(logging.Handler):
    """ログをキューに流すハンドラ。UIスレッドが取り出して画面に表示する。"""

    def __init__(self, log_queue: queue.Queue) -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_queue.put(("log", (record.levelno, self.format(record))))
        except Exception:  # noqa: BLE001 - ログ出力で本処理を止めない
            pass


class ApplicationUI:
    """申請内容を入力し、自動化フローを起動する画面。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.flow_states: list[StepState] = make_initial_steps()

        root.title("書類申請 自動化ツール")
        root.geometry("780x880")
        root.minsize(700, 720)

        self._build_widgets()
        self._attach_log_handler()
        self._poll_queue()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # 画面構築
    # ------------------------------------------------------------------

    def _build_widgets(self) -> None:
        container = ttk.Frame(self.root, padding=14)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)

        self._build_form(container)
        self._build_actions(container)
        self._build_flow(container)
        self._build_log(container)

    def _build_form(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text=" 申請内容 ", padding=10)
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)

        row = 0

        # --- 氏名 ---
        ttk.Label(frame, text="氏名 *").grid(row=row, column=0, sticky="w", **_PAD)
        self.var_name = tk.StringVar()
        ttk.Entry(frame, textvariable=self.var_name).grid(row=row, column=1, columnspan=2, sticky="ew", **_PAD)
        row += 1

        # --- 申請日（今日を初期値にする） ---
        ttk.Label(frame, text="申請日 *").grid(row=row, column=0, sticky="w", **_PAD)
        self.var_date = tk.StringVar(value=date.today().isoformat())
        date_frame = ttk.Frame(frame)
        date_frame.grid(row=row, column=1, columnspan=2, sticky="ew", **_PAD)
        date_frame.columnconfigure(0, weight=1)
        ttk.Entry(date_frame, textvariable=self.var_date).grid(row=0, column=0, sticky="ew")
        ttk.Label(date_frame, text="YYYY-MM-DD").grid(row=0, column=1, padx=(8, 0))
        row += 1

        # --- 部署 ---
        ttk.Label(frame, text="部署 *").grid(row=row, column=0, sticky="w", **_PAD)
        self.var_dept = tk.StringVar(value=DEPARTMENTS[0][0])
        ttk.Combobox(
            frame,
            textvariable=self.var_dept,
            values=[label for label, _ in DEPARTMENTS],
            state="readonly",
        ).grid(row=row, column=1, columnspan=2, sticky="ew", **_PAD)
        row += 1

        # --- 書類名 ---
        ttk.Label(frame, text="書類名 *").grid(row=row, column=0, sticky="w", **_PAD)
        self.var_title = tk.StringVar()
        ttk.Entry(frame, textvariable=self.var_title).grid(row=row, column=1, columnspan=2, sticky="ew", **_PAD)
        row += 1

        # --- 申請理由 ---
        ttk.Label(frame, text="申請理由").grid(row=row, column=0, sticky="nw", **_PAD)
        self.txt_reason = tk.Text(frame, height=4, wrap="word", font=("", 12))
        self.txt_reason.grid(row=row, column=1, columnspan=2, sticky="ew", **_PAD)
        row += 1

        # --- 添付ファイル ---
        ttk.Label(frame, text="添付ファイル").grid(row=row, column=0, sticky="w", **_PAD)
        self.var_file = tk.StringVar()
        ttk.Entry(frame, textvariable=self.var_file).grid(row=row, column=1, sticky="ew", **_PAD)
        ttk.Button(frame, text="参照...", command=self._browse_file, width=8).grid(
            row=row, column=2, sticky="w", padx=(0, 12), pady=6
        )

    def _build_actions(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=1, column=0, sticky="ew", pady=(12, 6))
        frame.columnconfigure(1, weight=1)

        self.btn_run = ttk.Button(frame, text="申請を実行", command=self._on_run)
        self.btn_run.grid(row=0, column=0, sticky="w")

        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=1, sticky="ew", padx=12)

        # 文字色は指定しない。ttk.Label はOSのテーマに応じた色が自動で当たるため、
        # ここで色を固定するとダークモードで背景に埋もれてしまう。
        self.var_status = tk.StringVar(value=self._initial_status())
        ttk.Label(frame, textvariable=self.var_status).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

    def _build_flow(self, parent: ttk.Frame) -> None:
        """処理フローの一覧。各ステップをクリックすると詳細ウィンドウが開く。"""
        frame = ttk.LabelFrame(
            parent, text=" 処理フロー（ステップをクリックすると実施内容を確認できます） ", padding=(10, 6)
        )
        frame.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        frame.columnconfigure(1, weight=1)

        self.flow_icon_labels: list[ttk.Label] = []
        self.flow_name_labels: list[ttk.Label] = []

        for state in self.flow_states:
            row = state.index - 1
            icon = ttk.Label(frame, text="○", width=3, anchor="center")
            name = ttk.Label(frame, text=f"{state.index}. {state.name}")
            icon.grid(row=row, column=0, sticky="w", pady=1)
            name.grid(row=row, column=1, sticky="w", pady=1)
            for widget in (icon, name):
                widget.bind("<Button-1>", lambda _e, idx=state.index: self._show_step_detail(idx))
                _set_hand_cursor(widget)
            self.flow_icon_labels.append(icon)
            self.flow_name_labels.append(name)

    def _build_log(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text=" 実行ログ ", padding=8)
        frame.grid(row=3, column=0, sticky="nsew", pady=(6, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        # 背景色・文字色は明示的に指定する。指定を省くとOSのテーマ（ライト/ダーク）に
        # 追従して背景色が変わり、文字色との組み合わせ次第で読めなくなるため。
        # コンソール風の暗い配色に固定し、どちらのテーマでも同じ見た目にする。
        self.log_view = scrolledtext.ScrolledText(
            frame,
            height=14,
            state="disabled",
            wrap="word",
            font=("Menlo", 11),
            background=_LOG_BG,
            foreground=_LOG_FG,
            insertbackground=_LOG_FG,
            borderwidth=0,
            highlightthickness=0,
        )
        self.log_view.grid(row=0, column=0, sticky="nsew")

        # ログレベルごとに色を付ける（上の暗い背景の上で読める色を選んでいる）
        self.log_view.tag_config("INFO", foreground=_LOG_FG)
        self.log_view.tag_config("WARNING", foreground="#e5a13a")
        self.log_view.tag_config("ERROR", foreground="#f47067")
        self.log_view.tag_config("DEBUG", foreground="#7a8290")

    def _initial_status(self) -> str:
        mode = (
            f"CDP接続モード（ポート {config.CDP_PORT} の起動済みChromeへ接続）"
            if config.USE_CDP
            else "ブラウザ新規起動モード（ダミーサイト検証用）"
        )
        dry = "  ※ DRY_RUN有効：送信直前で停止します" if config.DRY_RUN else ""
        return f"待機中 — {mode}{dry}"

    # ------------------------------------------------------------------
    # イベント処理
    # ------------------------------------------------------------------

    def _browse_file(self) -> None:
        path = filedialog.askopenfilename(
            title="添付ファイルを選択",
            filetypes=[
                ("すべてのファイル", "*.*"),
                ("PDFファイル", "*.pdf"),
                ("Excelファイル", "*.xlsx *.xls"),
                ("Wordファイル", "*.docx *.doc"),
            ],
        )
        if path:
            self.var_file.set(path)

    def _collect_input(self) -> ApplicationData:
        """画面の入力値を ApplicationData に詰める。"""
        dept_label = self.var_dept.get()
        dept_value = next((v for label, v in DEPARTMENTS if label == dept_label), dept_label)

        file_text = self.var_file.get().strip()

        return ApplicationData(
            applicant_name=self.var_name.get().strip(),
            apply_date=self.var_date.get().strip(),
            department=dept_value,
            doc_title=self.var_title.get().strip(),
            reason=self.txt_reason.get("1.0", "end").strip(),
            attachment_path=Path(file_text) if file_text else None,
        )

    def _on_run(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("実行中", "現在処理を実行中です。完了までお待ちください。")
            return

        data = self._collect_input()

        # 入力チェックはここでも行い、不備があればブラウザを開かずにダイアログで知らせる。
        # （workflow側でも検証しているが、UIでは即座にフィードバックする方が親切）
        try:
            data.validate()
        except Exception as e:  # ValidationError
            messagebox.showerror("入力エラー", str(e))
            return

        self._set_running(True)
        self._clear_log()
        self.flow_states = make_initial_steps()
        self._refresh_flow()

        self.worker = threading.Thread(target=self._run_worker, args=(data,), daemon=True)
        self.worker.start()

    def _run_worker(self, data: ApplicationData) -> None:
        """ワーカースレッド本体。ここではUIウィジェットに直接触れてはいけない。"""

        def on_progress(step: int, total: int, label: str) -> None:
            self.queue.put(("progress", (step, total, label)))

        def on_flow(states: list[StepState]) -> None:
            self.queue.put(("flow", states))

        result = run_application_flow(data, on_progress=on_progress, on_flow=on_flow)
        self.queue.put(("done", result))

    def _set_running(self, running: bool) -> None:
        self.btn_run.config(state="disabled" if running else "normal")
        if running:
            self.progress["value"] = 0
        else:
            self.progress["value"] = 0

    # ------------------------------------------------------------------
    # キュー経由のUI更新
    # ------------------------------------------------------------------

    def _attach_log_handler(self) -> None:
        handler = QueueLogHandler(self.queue)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        handler.setLevel(logging.INFO)
        logging.getLogger(LOGGER_NAME).addHandler(handler)

    def _poll_queue(self) -> None:
        """100ミリ秒ごとにキューを確認してUIを更新する。"""
        try:
            while True:
                kind, payload = self.queue.get_nowait()

                if kind == "log":
                    levelno, message = payload
                    self._append_log(message, logging.getLevelName(levelno))
                elif kind == "progress":
                    step, total, label = payload
                    self.progress["value"] = (step / total) * 100
                    self.var_status.set(f"実行中 ({step}/{total}): {label}")
                elif kind == "flow":
                    self.flow_states = payload
                    self._refresh_flow()
                elif kind == "done":
                    self._on_done(payload)

        except queue.Empty:
            pass

        self.root.after(100, self._poll_queue)

    def _append_log(self, message: str, level: str) -> None:
        self.log_view.config(state="normal")
        self.log_view.insert("end", message + "\n", level)
        self.log_view.see("end")
        self.log_view.config(state="disabled")

    def _clear_log(self) -> None:
        self.log_view.config(state="normal")
        self.log_view.delete("1.0", "end")
        self.log_view.config(state="disabled")

    def _on_done(self, result: FlowResult) -> None:
        self._set_running(False)

        if result.steps:
            self.flow_states = result.steps
            self._refresh_flow()

        if result.success:
            self.progress["value"] = 100
            self.var_status.set("完了: 申請が正常に送信されました（フローをクリックすると各ステップの詳細を確認できます）")
            messagebox.showinfo("申請完了", result.summary)
        else:
            self.var_status.set("失敗: " + result.message.splitlines()[0])
            messagebox.showerror("申請に失敗しました", result.summary)

    # ------------------------------------------------------------------
    # 処理フローの表示
    # ------------------------------------------------------------------

    def _refresh_flow(self) -> None:
        """処理フロー欄の表示を flow_states に合わせて更新する。"""
        for state, icon_lbl, name_lbl in zip(
            self.flow_states, self.flow_icon_labels, self.flow_name_labels
        ):
            icon, color, status_text = _STATUS_STYLES.get(state.status, _STATUS_STYLES["pending"])
            icon_lbl.config(text=icon, foreground=color)
            suffix = f"  —  {status_text}" if state.status != "pending" else ""
            name_lbl.config(text=f"{state.index}. {state.name}{suffix}")

    def _find_screenshot(self, state: StepState) -> Path | None:
        """ステップの証跡スクリーンショットを探す。実行結果により名前が変わるため候補順に確認する。"""
        for name in state.screenshot_names:
            path = config.SCREENSHOT_DIR / name
            if path.exists():
                return path
        return None

    def _show_step_detail(self, index: int) -> None:
        """ステップの詳細（実施内容のログ + 証跡スクリーンショット）を別ウィンドウで表示する。"""
        state = self.flow_states[index - 1]
        icon, _, status_text = _STATUS_STYLES.get(state.status, _STATUS_STYLES["pending"])

        win = tk.Toplevel(self.root)
        win.title(f"ステップ{state.index}: {state.name}")
        win.geometry("820x760")

        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=f"{icon} ステップ{state.index}: {state.name}  —  {status_text}",
            font=("", 15, "bold"),
        ).pack(anchor="w")

        ttk.Label(frame, text="実施内容（このステップで実行された処理）:").pack(anchor="w", pady=(12, 3))
        detail_view = scrolledtext.ScrolledText(
            frame,
            height=9,
            wrap="word",
            font=("Menlo", 11),
            background=_LOG_BG,
            foreground=_LOG_FG,
            borderwidth=0,
            highlightthickness=0,
        )
        detail_view.insert("1.0", state.detail or "（このステップはまだ実行されていません）")
        detail_view.config(state="disabled")
        detail_view.pack(fill="x")

        shot = self._find_screenshot(state)
        header = ttk.Frame(frame)
        header.pack(fill="x", pady=(12, 3))
        ttk.Label(header, text="証跡スクリーンショット（実行時のブラウザ画面）:").pack(side="left")

        if shot is None:
            text = (
                "（このステップに証跡画像はありません）"
                if not state.screenshot_names
                else "（証跡画像はまだ保存されていません。ステップ実行後に開き直してください）"
            )
            ttk.Label(frame, text=text).pack(anchor="w")
            return

        ttk.Button(
            header,
            text="別アプリで開く",
            command=lambda: webbrowser.open(shot.as_uri()),
        ).pack(side="right")

        try:
            image = tk.PhotoImage(file=str(shot))
        except tk.TclError:
            ttk.Label(frame, text=f"（画像を読み込めませんでした: {shot}）").pack(anchor="w")
            return

        # ウィンドウ幅に収まるよう整数分率で縮小する（PhotoImageは整数倍率のみ対応）
        factor = max(1, math.ceil(image.width() / 780))
        if factor > 1:
            image = image.subsample(factor, factor)

        canvas_frame = ttk.Frame(frame)
        canvas_frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(canvas_frame, highlightthickness=0, background=_LOG_BG)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.create_image(0, 0, image=image, anchor="nw")
        canvas.configure(scrollregion=(0, 0, image.width(), image.height()))

        # PhotoImageは参照を保持しないとガベージコレクションで消えて表示されなくなる
        win._image_ref = image  # type: ignore[attr-defined]

    def _on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno(
                "確認", "処理を実行中です。終了すると申請が中断されます。終了しますか？"
            ):
                return
        self.root.destroy()


def launch_ui() -> None:
    """UIを起動する（この関数が戻るのはウィンドウが閉じられたとき）。"""
    root = tk.Tk()
    ApplicationUI(root)
    root.mainloop()
