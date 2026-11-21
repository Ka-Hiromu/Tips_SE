"""ONJO（KANAE PROJECT）の保存ファイル(.lst)を Tips_SE の Preset へ変換する。

GUI に依存しないので、ここだけ見れば変換規則が分かる。

.lst の実体は CP932 の INI 形式で、[Player00]〜[Player63] が常に 64 個並ぶ。
ONJO 側に無い情報（フェード長）や Tips_SE に無い機能（再生範囲・連続再生）は
近い挙動へ寄せるか、取り込めない旨を ImportReport.warnings に積む。
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from string import ascii_uppercase

from appconfig import SOUND_EXTS
from models import Pad, Preset, Sound

log = logging.getLogger("tips_se")

# ONJO はフェード長をプロジェクト全体で持ち、.lst には有無しか残らないので既定値を補う
ONJO_FADE_SEC = 2.0

# Start / End は 100ns 単位
_TICKS_PER_SEC = 10_000_000

_SECTION_RE = re.compile(r"^Player(\d+)$", re.IGNORECASE)

# 同じフォルダを何度も走査しないための上限（本番フォルダは普通これより小さい）
_SCAN_LIMIT = 20_000


def _build_key_table() -> tuple[tuple[str, str | None], ...]:
    """ONJO の「キー:」コンボボックスの並び（ONJO.exe 内の文字列表から採取）。

    添字がそのまま .lst の Key= の値になる。Tips_SE の割当キーは1文字なので、
    対応する文字を持たないキー（Enter・矢印・F1 など）は None にして警告へ回す。
    """
    rows: list[tuple[str, str | None]] = [("なし", None)]
    rows += [(c, c) for c in "0123456789"]
    rows += [(c, c.lower()) for c in ascii_uppercase]
    rows += [(n, None) for n in ("Backspace", "Tab", "Clear", "Enter", "Shift",
                                 "Ctrl", "Alt", "Pause", "Esc", "スペース",
                                 "PageUp", "PageDown", "End", "Home",
                                 "←", "↑", "→", "↓")]
    rows += [(f"{c}(テンキー)", c) for c in "0123456789*+,-./"]
    rows += [(f"F{i}", None) for i in range(1, 13)]
    rows += [(n, None) for n in ("Scroll Lock", "左Shift", "右Shift",
                                 "左Ctrl", "右Ctrl", "左Alt", "右Alt")]
    rows += [(c, c) for c in ":;,-./@[\\]^"]
    return tuple(rows)


_KEY_TABLE = _build_key_table()


@dataclass
class ImportReport:
    """取り込み結果。warnings は利用者にそのまま見せる文章。"""
    pads: int = 0
    relocated: int = 0          # パスが変わっていて同フォルダから見つけた音源の数
    warnings: list[str] = field(default_factory=list)


class _FileIndex:
    """.lst と同じフォルダ以下のファイル名→実パスの索引（初回アクセス時に作る）。

    ONJO は音源を絶対パスで持つため、フォルダを移した .lst はほぼ全滅する。
    ファイル名で引き直せば実用上はたいてい復帰できる。
    """

    def __init__(self, base: Path):
        self.base = base
        self._map: dict[str, str] | None = None

    def find(self, name: str) -> str | None:
        if self._map is None:
            self._map = self._scan()
        return self._map.get(name.casefold())

    def _scan(self) -> dict[str, str]:
        out: dict[str, str] = {}
        try:
            for root, _dirs, files in os.walk(self.base):
                for f in files:
                    out.setdefault(f.casefold(), str(Path(root) / f))
                    if len(out) >= _SCAN_LIMIT:
                        return out
        except OSError as e:
            log.warning("ONJO取り込み: フォルダ走査に失敗 %s", e)
        return out


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("cp932", "utf-8-sig"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp932", errors="replace")


def _parse_ini(text: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    cur: dict[str, str] | None = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            cur = sections.setdefault(s[1:-1], {})
        elif cur is not None and "=" in s:
            k, v = s.split("=", 1)
            cur[k.strip()] = v.strip()
    return sections


def _to_int(s: str | None, default: int = 0) -> int:
    try:
        return int(float(s))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _to_float(s: str | None, default: float = 0.0) -> float:
    try:
        return float(s)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _grid_for(count: int) -> tuple[int, int]:
    """count 個が収まる、なるべく正方形に近い行×列（Tips_SE の上限は 8×8）。"""
    for cols in range(4, 9):
        rows = max(1, ceil(count / cols))
        if rows <= cols:
            return rows, cols
    return 8, 8


def _resolve_path(raw: str, index: _FileIndex) -> tuple[str, bool]:
    """音源パスを実在するものへ寄せる。戻り値は (パス, 差し替えたか)。"""
    if not raw:
        return raw, False
    if Path(raw).exists():
        return raw, False
    found = index.find(Path(raw).name)
    if found:
        return found, True
    return raw, False


def load_lst(path: str | Path, *, multi: str = "random") -> tuple[Preset, ImportReport]:
    """ONJO の .lst を読み込んで (Preset, ImportReport) を返す。

    multi は1プレイヤーに複数音源があるときの扱い。
    "random" なら確率（均等）のランダム再生へ、"first" なら先頭1本だけ取り込む。
    """
    p = Path(path)
    sections = _parse_ini(_read_text(p))
    index = _FileIndex(p.parent)
    rep = ImportReport()

    players: list[tuple[int, dict[str, str]]] = []
    for name, sec in sections.items():
        m = _SECTION_RE.match(name)
        if m:
            players.append((int(m.group(1)), sec))
    if not players:
        raise ValueError("ONJO のリストファイルとして読めません（[PlayerNN] がありません）")

    pads: list[Pad] = []
    for idx, sec in sorted(players):
        n = _to_int(sec.get("FileCount"), 0)
        files = [sec[k] for k in (f"FileName{i}" for i in range(max(n, 0)))
                 if sec.get(k)]
        if not files:
            continue

        label = f"パッド{idx + 1}"
        pad = Pad(index=idx)
        resolved: list[str] = []
        for f in files:
            rp, moved = _resolve_path(f, index)
            rep.relocated += int(moved)
            ext = Path(rp).suffix.lower()
            if ext not in SOUND_EXTS:
                rep.warnings.append(
                    f"{label}: {ext or '拡張子なし'} は Tips_SE では鳴らせません"
                    f"（{Path(rp).name}）")
            resolved.append(rp)

        if len(resolved) == 1 or multi == "first":
            pad.path = resolved[0]
            if len(resolved) > 1:
                rep.warnings.append(
                    f"{label}: 音源が{len(resolved)}個ありますが先頭だけ取り込みました"
                    "（コンフィグの「ランダム再生」を入れると全部取り込めます）")
        else:
            prob = round(100.0 / len(resolved), 2)
            pad.random_enabled = True
            pad.sources = [Sound(path=f, label=Path(f).stem, prob=prob)
                           for f in resolved]
            rep.warnings.append(
                f"{label}: 音源{len(resolved)}個を確率（均等）のランダム再生にしました"
                "（ONJO の順番どおりの連続再生は再現できません）")

        pad.gain = min(1.5, max(0.0, _to_float(sec.get("Volume"), 0.8)))
        pad.loop = _to_int(sec.get("isRepeat")) != 0
        pad.exclusive = _to_int(sec.get("isSolo")) != 0
        pad.fade_in = ONJO_FADE_SEC if _to_int(sec.get("isFadeIn")) else 0.0
        pad.fade_out = ONJO_FADE_SEC if _to_int(sec.get("isFadeOut")) else 0.0

        key_idx = _to_int(sec.get("Key"), 0)
        if 0 < key_idx < len(_KEY_TABLE):
            key_name, ch = _KEY_TABLE[key_idx]
            if ch:
                pad.key = ch
            else:
                rep.warnings.append(
                    f"{label}: 割当キー「{key_name}」は Tips_SE では使えないため空にしました")
        elif key_idx != 0:
            rep.warnings.append(f"{label}: 割当キーの番号 {key_idx} が不明なため空にしました")

        start = _to_int(sec.get("Start"), -1)
        end = _to_int(sec.get("End"), -1)
        pad.start = start / _TICKS_PER_SEC if start > 0 else 0.0
        pad.end = end / _TICKS_PER_SEC if end > 0 else 0.0
        if pad.end > 0 and pad.end <= pad.start:
            rep.warnings.append(
                f"{label}: 再生範囲が逆（{pad.start:.1f}秒〜{pad.end:.1f}秒）なので"
                "終了側を無視して末尾まで鳴らします")
            pad.end = 0.0

        pads.append(pad)

    rep.pads = len(pads)
    last = max((pad.index for pad in pads), default=0)
    rows, cols = _grid_for(last + 1)
    preset = Preset(rows=rows, cols=cols, pads=pads)
    preset.ensure_pads()
    return preset, rep
