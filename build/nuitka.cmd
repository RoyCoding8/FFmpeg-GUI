@REM One-shot Nuitka onefile build. Gate: output ≤ 400 MB AND cold start ≤ 8 s on
@REM the reference machine — else fall back to PyInstaller --onedir and RECORD the
@REM reason in tmp/IMPLEMENTATION_PLAN.md (plan D6).
@echo off
setlocal
uv run python build\make_icon.py || exit /b 1
uv run nuitka ^
  --onefile ^
  --windows-console-mode=disable ^
  --enable-plugin=pyside6 ^
  --include-data-dir=assets=assets ^
  --include-data-dir=src\ffgui=src\ffgui ^
  --windows-icon-from-ico=build\ffgui.ico ^
  --company-name=ffgui ^
  --product-name=ffgui ^
  --file-version=0.1.0 ^
  --output-dir=build\dist ^
  --output-filename=ffgui.exe ^
  src\ffgui\app.py
