@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

rem 优先用项目自带的 PySide6 虚拟环境（已含 qfluentwidgets）
set "PYEXE=C:\Users\plasx520\.workbuddy\binaries\python\envs\idphoto\Scripts\python.exe"
if not exist "%PYEXE%" (
  set "PYEXE=python.exe"
)

"%PYEXE%" "mirror_builder.py"
endlocal
