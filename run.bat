@echo off
chcp 936 >nul
title ID Photo Maker (Fluent UI)

cd /d "%~dp0"

set "LOG=%~dp0run_bat.log"
echo [%time%] === run.bat start === > "%LOG%"

REM ==== 选择 Python 解释器 ====
set "PY="

if exist "C:\Users\plasx520\.workbuddy\binaries\python\envs\idphoto\Scripts\python.exe" (
    set "PY=C:\Users\plasx520\.workbuddy\binaries\python\envs\idphoto\Scripts\python.exe"
    goto :check
)

where py >nul 2>&1
if %errorlevel%==0 (
    set "PY=py"
    goto :check
)

where python >nul 2>&1
if %errorlevel%==0 (
    set "PY=python"
    goto :check
)

echo [ERROR] 未找到 Python, 请安装 Python 3.8+ 并勾选 Add to PATH.
echo [ERROR] Python not found. >> "%LOG%"
echo 下载地址: https://www.python.org/downloads/
goto :end

:check
echo 使用 Python: %PY%
echo Using Python: %PY% >> "%LOG%"
echo 正在检查依赖...

REM ==== 依赖检查 ====
REM 单进程用 find_spec 只查包是否存在,
REM 不执行模块代码, 约几十毫秒。
REM 旧写法逐个 spawn 6 个 python 进程,
REM 光解释器启动就要多花 1 秒以上。
set "DEPS=PySide6 qfluentwidgets numpy cv2 PIL onnxruntime"
"%PY%" -c "import importlib.util as u,sys,os;miss=[n for n in os.environ['DEPS'].split() if u.find_spec(n) is None];sys.stdout.write('MISSING='+','.join(miss)+'\n' if miss else '');sys.exit(1 if miss else 0)" 2>nul
if errorlevel 1 goto :dep_missing

goto :launch

:dep_missing
echo [MISSING] 以下依赖未安装, 尝试自动安装...
echo [MISSING] dependencies missing >> "%LOG%"
"%PY%" -c "import importlib.util as u,os;[print('  - '+n+'  pip: '+{'PySide6':'pyside6','qfluentwidgets':'pyside6-fluent-widgets','numpy':'numpy','cv2':'opencv-python-headless','PIL':'Pillow','onnxruntime':'onnxruntime'}.get(n,n)) for n in os.environ['DEPS'].split() if u.find_spec(n) is None]"
call :pip_install_all
"%PY%" -c "import importlib.util as u,sys,os;sys.exit(1 if [n for n in os.environ['DEPS'].split() if u.find_spec(n) is None] else 0)" 2>nul
if errorlevel 1 goto :dep_fail
goto :launch

:dep_fail
echo [FAILED] 依赖自动安装失败, 请手动安装后重试.
echo [FAILED] pip install failed. >> "%LOG%"
echo.
echo 请手动执行其中一条:
echo   "%PY%" -m pip install pyside6 pyside6-fluent-widgets numpy opencv-python-headless Pillow onnxruntime
echo   "%PY%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyside6 pyside6-fluent-widgets numpy opencv-python-headless Pillow onnxruntime
goto :end

:pip_install_all
REM 一次装齐: 比逐个 install 少走多轮依赖解析
"%PY%" -m pip install pyside6 pyside6-fluent-widgets numpy opencv-python-headless Pillow onnxruntime >> "%LOG%" 2>&1
if not errorlevel 1 goto :eof
echo   try Tsinghua mirror... >> "%LOG%"
"%PY%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyside6 pyside6-fluent-widgets numpy opencv-python-headless Pillow onnxruntime >> "%LOG%" 2>&1
if not errorlevel 1 goto :eof
echo   try Aliyun mirror... >> "%LOG%"
"%PY%" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ pyside6 pyside6-fluent-widgets numpy opencv-python-headless Pillow onnxruntime >> "%LOG%" 2>&1
goto :eof

:launch
echo 依赖检查通过, 正在启动...
echo Dependencies OK, launching launcher.py >> "%LOG%"
"%PY%" launcher.py >> "%LOG%" 2>&1
echo launcher.py exit code: %errorlevel% >> "%LOG%"
goto :end

:end
echo.
echo ============================================================
echo   程序已结束. 若窗口未打开, 请查看 run_bat.log
echo ============================================================
pause
