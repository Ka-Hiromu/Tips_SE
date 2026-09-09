"""起動.bat と同じ挙動の exe ランチャー。

このexe（プロジェクト直下に置く）をダブルクリックすると、
同じフォルダの .venv の pythonw で src/main_app.py を起動する。
= 起動.bat をそのまま exe にしたもの。コンソール窓は出ない。
"""
import os
import subprocess
import sys


def main():
    # exe（frozen）の場所＝プロジェクト直下を基準にする
    base = os.path.dirname(os.path.abspath(sys.executable))
    pythonw = os.path.join(base, ".venv", "Scripts", "pythonw.exe")
    python = os.path.join(base, ".venv", "Scripts", "python.exe")
    runner = pythonw if os.path.exists(pythonw) else python
    main_py = os.path.join(base, "src", "main_app.py")
    subprocess.Popen([runner, main_py], cwd=base)


if __name__ == "__main__":
    main()
