@echo off
rem Builds MovieGuide.exe on this PC (for testing - releases are built by GitHub).
cd /d "%~dp0"
python -m pip install --user --upgrade pyinstaller pywebview
python -m PyInstaller --noconfirm --onefile --windowed --name MovieGuide --icon MovieGuide.ico --add-data "guide_ui.html;." --add-data "MovieGuide.ico;." --collect-all webview movie_guide.py
echo.
echo Built: %~dp0dist\MovieGuide.exe
pause
