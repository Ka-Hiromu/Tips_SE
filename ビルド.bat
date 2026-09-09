@echo off
chcp 932 >nul
cd /d "%~dp0"
echo Tips_SE をビルドしています...
".venv\Scripts\pyinstaller.exe" "Tips_SE.spec" --noconfirm
echo.
if exist "dist\Tips_SE\Tips_SE.exe" (
    echo ビルド完了: dist\Tips_SE\Tips_SE.exe
) else (
    echo ビルドに失敗しました。上の出力を確認してください。
)
pause
