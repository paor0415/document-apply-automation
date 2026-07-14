"""UIとブラウザ操作の間で受け渡すデータ構造。

UI層(ui.py)とブラウザ操作層(browser_controller.py / form_filler.py)を
この dataclass 一つで繋いでいる。UIをTkinterからStreamlitやFastAPIに
差し替える場合も、最終的に ApplicationData を組み立てて渡せばよい。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from exceptions import ValidationError


@dataclass
class ApplicationData:
    """1件の書類申請の入力内容。

    フィールド名は「申請内容としての意味」で付けている。実サイトのHTML要素名とは
    独立させてあり、要素との対応付けは config.py の FormSelectors 側で行う。
    """

    applicant_name: str
    apply_date: str  # "YYYY-MM-DD" 形式（input[type=date] がこの形式を要求する）
    department: str
    doc_title: str
    reason: str = ""
    attachment_path: Path | None = None

    # 添付なしを許容するか。実サイトで添付必須ならここを False にする。
    allow_no_attachment: bool = field(default=True, repr=False)

    def validate(self) -> None:
        """入力値を検証する。

        ブラウザを起動する前に呼ぶこと。ブラウザ操作は時間がかかるうえ、
        途中で失敗すると中途半端な状態のフォームが残るため、
        明らかな入力ミスは事前に弾く。

        Raises:
            ValidationError: 入力値に不備がある場合。
        """
        errors: list[str] = []

        if not self.applicant_name.strip():
            errors.append("氏名が未入力です。")

        if not self.apply_date.strip():
            errors.append("申請日が未入力です。")
        else:
            try:
                date.fromisoformat(self.apply_date)
            except ValueError:
                errors.append(f"申請日の形式が不正です: {self.apply_date!r} (YYYY-MM-DD で入力してください)")

        if not self.department.strip():
            errors.append("部署が未選択です。")

        if not self.doc_title.strip():
            errors.append("書類名が未入力です。")

        if self.attachment_path is None:
            if not self.allow_no_attachment:
                errors.append("添付ファイルが指定されていません。")
        else:
            path = Path(self.attachment_path)
            if not path.exists():
                errors.append(f"添付ファイルが存在しません: {path}")
            elif not path.is_file():
                errors.append(f"添付先がファイルではありません: {path}")

        if errors:
            raise ValidationError(
                "入力内容に不備があります:\n" + "\n".join(f"  - {e}" for e in errors),
                hint="UIの入力欄を修正して、もう一度実行してください。",
            )

    def as_log_dict(self) -> dict[str, str]:
        """ログ出力用の辞書。添付はファイル名だけにしてパスの流出を防ぐ。"""
        return {
            "氏名": self.applicant_name,
            "申請日": self.apply_date,
            "部署": self.department,
            "書類名": self.doc_title,
            "申請理由": self.reason or "(なし)",
            "添付": Path(self.attachment_path).name if self.attachment_path else "(なし)",
        }
