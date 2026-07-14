"""このツール専用の例外クラス群。

失敗の種類ごとに例外を分けることで、UI側やログで「何が原因で止まったのか」を
利用者が判別できるようにしている。すべて AutomationError を継承しているので、
まとめて捕まえたい場合は AutomationError だけを except すればよい。
"""

from __future__ import annotations


class AutomationError(Exception):
    """このツールが送出する全例外の基底クラス。

    Attributes:
        message: 利用者向けの説明文。
        hint:    次に何をすればよいかの助言（任意）。
    """

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\n  → 対処: {self.hint}"
        return self.message


class BrowserConnectionError(AutomationError):
    """ブラウザへの接続・起動に失敗した。"""


class NavigationError(AutomationError):
    """ページへのアクセス、またはページ遷移に失敗した。"""


class ElementNotFoundError(AutomationError):
    """指定したセレクタの要素が見つからなかった。

    セレクタの記述ミス、またはサイト側のHTML変更が主な原因。
    """

    def __init__(self, description: str, selector: str, hint: str | None = None) -> None:
        message = f"{description} が見つかりませんでした（セレクタ: {selector}）"
        super().__init__(
            message,
            hint or "config.py のセレクタ定義が実サイトのHTMLと一致しているか確認してください。",
        )
        self.selector = selector


class FileAttachError(AutomationError):
    """添付ファイルのアップロードに失敗した。"""


class SubmitError(AutomationError):
    """申請の送信に失敗した。"""


class ValidationError(AutomationError):
    """UIで入力された値が不正。ブラウザ操作を始める前に検出する。"""
