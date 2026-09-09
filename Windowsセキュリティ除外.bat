@echo off
chcp 932 >nul
REM 未署名のexe(アプリ/ランチャー)がWindowsセキュリティに削除される場合の対処。
REM このプロジェクトフォルダをウイルススキャンの除外に追加します（管理者権限が必要）。
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 管理者権限で実行し直します...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    goto :eof
)
set "TARGET=%~dp0"
if "%TARGET:~-1%"=="\" set "TARGET=%TARGET:~0,-1%"
echo 次のフォルダを Windows セキュリティの除外に追加します:
echo   %TARGET%
powershell -NoProfile -Command "Add-MpPreference -ExclusionPath '%TARGET%'"
echo.
echo 完了しました。ランチャー「Tips_SE起動.exe」や「ビルド.bat」が消えずに使えます。
pause
