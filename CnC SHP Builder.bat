@echo off
cd /d "%~dp0CnC_SHP_Builder"
py -3 main.py
if errorlevel 1 (
    echo.
    echo ERROR: Could not start the SHP Builder.
    echo Make sure Python 3 and Pillow are installed.
    echo Run:  pip install pillow
    pause
)
