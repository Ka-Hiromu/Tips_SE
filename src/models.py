"""パッド/プリセットのデータモデル（JSONで保存/読込）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path


@dataclass
class Pad:
    index: int
    path: str = ""          # 割り当て音源の絶対パス（空なら未割当）
    label: str = ""         # 表示名
    key: str = ""           # 割当キー（1文字, 空なら無し）
    loop: bool = False
    fade_in: float = 0.0    # 秒
    fade_out: float = 0.0   # 秒
    gain: float = 1.0       # 0.0-1.5
    exclusive: bool = False  # 再生時に他を止める（排他再生）

    @property
    def assigned(self) -> bool:
        return bool(self.path)

    def display_name(self) -> str:
        if self.label:
            return self.label
        if self.path:
            return Path(self.path).stem
        return ""


@dataclass
class Annotation:
    """戯曲PDF上に置く注釈。座標はページに対する正規化値(0.0-1.0)で持つ。"""
    kind: str               # "stamp"（音出しスタンプ）/ "text"（メモ）
    page: int
    x: float                # ページ幅に対する比率
    y: float                # ページ高に対する比率
    pad: int = 0            # stamp用: 対象パッドのindex（表示番号 = pad+1）
    text: str = ""          # text用: メモ本文
    color: str = "#ff5252"  # stampの色
    font_size: int = 14     # text用


@dataclass
class Preset:
    rows: int = 4
    cols: int = 4
    master_gain: float = 1.0
    default_fade_out: float = 0.3  # 全停止時のフェード
    pads: list[Pad] = field(default_factory=list)
    pdf_path: str = ""             # 戯曲PDFの絶対パス
    pdf_page: int = 0              # 最後に開いていたページ
    annotations: list[Annotation] = field(default_factory=list)

    @property
    def count(self) -> int:
        return self.rows * self.cols

    def ensure_pads(self) -> None:
        """rows*cols 個になるよう Pad を補充/トリム。"""
        n = self.count
        existing = {p.index: p for p in self.pads}
        self.pads = [existing.get(i, Pad(index=i)) for i in range(n)]

    # ---- 永続化 ----
    def to_json(self) -> str:
        d = {
            "rows": self.rows,
            "cols": self.cols,
            "master_gain": self.master_gain,
            "default_fade_out": self.default_fade_out,
            "pads": [asdict(p) for p in self.pads],
            "pdf_path": self.pdf_path,
            "pdf_page": self.pdf_page,
            "annotations": [asdict(a) for a in self.annotations],
        }
        return json.dumps(d, ensure_ascii=False, indent=2)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Preset":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        preset = cls(
            rows=d.get("rows", 4),
            cols=d.get("cols", 4),
            master_gain=d.get("master_gain", 1.0),
            default_fade_out=d.get("default_fade_out", 0.3),
            pads=[Pad(**pd) for pd in d.get("pads", [])],
            pdf_path=d.get("pdf_path", ""),
            pdf_page=d.get("pdf_page", 0),
            annotations=[Annotation(**a) for a in d.get("annotations", [])],
        )
        preset.ensure_pads()
        return preset
