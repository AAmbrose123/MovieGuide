@echo off
cd /d "%~dp0"
python -c "import webview" 2>nul || (
  echo First run: installing pywebview for the app window...
  python -m pip install --user pywebview
)
start "" pythonw movie_guide.py
