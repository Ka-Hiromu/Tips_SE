"""アプリ共通の定数・パス解決・設定JSON・ロギング設定。

GUI から独立させてあるため、ここは PySide6 に依存しない。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "Tips_SE ポン出し"

SOUND_EXTS: tuple[str, ...] = (".wav", ".flac", ".mp3", ".ogg", ".aiff", ".aif", ".w64")
SOUND_FILE_FILTER = ("音声ファイル (" + " ".join(f"*{e}" for e in SOUND_EXTS)
                     + ");;すべて (*.*)")

log = logging.getLogger("tips_se")


def resource_path(rel: str) -> Path:
    """同梱リソースのパス（開発時/PyInstaller frozen時の両対応）。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return base / rel


def bundled_presets_dir() -> Path:
    """配布物と並ぶ presets フォルダ（開発時はリポジトリ直下）。

    resource_path とは別扱い。PyInstaller の onedir では _MEIPASS ではなく
    exe と同じ階層に置かれ、利用者が中身を差し替えられるようにしてある。
    """
    base = (Path(sys.executable).parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent.parent)
    return base / "presets"


def app_data_dir() -> Path:
    """設定・ログの保存先（%LOCALAPPDATA%\Tips_SE、無ければホーム）。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "Tips_SE"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        d = Path.home()
    return d


def config_path() -> Path:
    return app_data_dir() / "config.json"


def load_app_config() -> dict:
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_app_config(cfg: dict) -> None:
    try:
        config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except Exception:
        pass


def setup_logging() -> Path:
    """ファイルへのロギングを設定（windowedビルドでもエラーを追える）。"""
    log_path = app_data_dir() / "tips_se.log"
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # コンソールがあれば併せて出力
    if sys.stderr:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        root.addHandler(sh)
    return log_path
