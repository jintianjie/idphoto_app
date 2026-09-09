@echo off
chcp 936 >nul
setlocal EnableDelayedExpansion
title 打包 证件照制作工具 -^> dist
cd /d "%~dp0"

REM ============================================================
REM  证件照制作工具 - 一键打包脚本 (PyInstaller onedir)
REM ------------------------------------------------------------
REM  用法（参数可组合，顺序任意）：
REM    build_exe.bat            完整流程：定位Python -^> 校验依赖 -^> 打包 -^> 瘦身 -^> 体积报告
REM    build_exe.bat /nodeps    跳过依赖检查与安装（依赖已就绪时可省几十秒）
REM    build_exe.bat /noslim    跳过 DLL 瘦身（保留 opengl/ffmpeg 等）
REM    build_exe.bat /zip       打包完成后额外压缩成 dist\证件照制作工具_日期.zip
REM    build_exe.bat /upx       额外用 UPX 压缩 cv2.pyd 等大文件（体积再降约 75MB，
REM                             代价是启动时现场解压 + 个别杀软误报；需自备 upx.exe）
REM    build_exe.bat /smoke     打包后自动试启动 8 秒，验证 exe 能否存活
REM    build_exe.bat /nopause   结束时不暂停（自动化 / CI 场景）
REM    build_exe.bat /console   生成带控制台的版本（排错用，默认是窗口程序）
REM    build_exe.bat /clean     只清理 build/dist 缓存后退出
REM
REM  产物：dist\证件照制作工具\证件照制作工具.exe
REM  日志：build_log.txt（仅记录 pip 安装与关键步骤）
REM ============================================================

set "LOG=%~dp0build_log.txt"
set "APPNAME=证件照制作工具"
set "ENTRY=launcher.py"
set "OUTDIR=dist\%APPNAME%"

set "DO_DEPS=1"
set "DO_SLIM=1"
set "DO_ZIP=0"
set "DO_SMOKE=0"
set "USE_CONSOLE=0"
set "CLEAN_ONLY=0"
set "DO_PAUSE=1"
set "DO_UPX=0"

for %%A in (%*) do (
    if /i "%%A"=="/nodeps"  set "DO_DEPS=0"
    if /i "%%A"=="/noslim"  set "DO_SLIM=0"
    if /i "%%A"=="/zip"     set "DO_ZIP=1"
    if /i "%%A"=="/smoke"   set "DO_SMOKE=1"
    if /i "%%A"=="/console" set "USE_CONSOLE=1"
    if /i "%%A"=="/clean"   set "CLEAN_ONLY=1"
    if /i "%%A"=="/nopause" set "DO_PAUSE=0"
    if /i "%%A"=="/upx"     set "DO_UPX=1"
    if /i "%%A"=="/?"       goto :usage
    if /i "%%A"=="/help"    goto :usage
)

echo ============================================================
echo   证件照制作工具 打包脚本
echo ============================================================
echo [%date% %time%] === 打包开始 === > "%LOG%"

REM ------------------------------------------------------------
REM  步骤 0：定位 Python 解释器
REM  优先级：项目内 .venv ^> 已知 idphoto 环境 ^> py 启动器 ^> python
REM ------------------------------------------------------------
set "PY="
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
    goto :py_ok
)
if exist "C:\Users\plasx520\.workbuddy\binaries\python\envs\idphoto\Scripts\python.exe" (
    set "PY=C:\Users\plasx520\.workbuddy\binaries\python\envs\idphoto\Scripts\python.exe"
    goto :py_ok
)
where py >nul 2>&1
if not errorlevel 1 (
    set "PY=py"
    goto :py_ok
)
where python >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    goto :py_ok
)

echo [错误] 未找到 Python 解释器，请安装 Python 3.9+ 并勾选 Add to PATH。
echo [错误] Python not found. >> "%LOG%"
goto :fail

:py_ok
echo [0/6] Python: %PY%
"%PY%" -c "import sys;print(sys.version)" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [错误] Python 无法执行：%PY%
    goto :fail
)

REM ------------------------------------------------------------
REM  步骤 1：校验 PyInstaller，缺失则自动安装（含国内镜像回退）
REM ------------------------------------------------------------
echo [1/6] 校验 PyInstaller...
"%PY%" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo      未安装，正在自动安装 PyInstaller...
    call :pip_install pyinstaller
    if errorlevel 1 (
        echo [错误] PyInstaller 安装失败，详情见 build_log.txt
        goto :fail
    )
)
REM 说明：不用 for /f 取版本 —— for /f 的命令串以引号开头时会被 cmd 拆坏，
REM       改为写入临时文件再 set /p 读取，中文路径与含分号的 -c 代码都安全。
set "PYI_VER=未知"
"%PY%" -c "import PyInstaller,sys;sys.stdout.write(PyInstaller.__version__)" > "%TEMP%\idp_pyiver.tmp" 2>nul
if exist "%TEMP%\idp_pyiver.tmp" set /p PYI_VER=<"%TEMP%\idp_pyiver.tmp"
if exist "%TEMP%\idp_pyiver.tmp" del /q "%TEMP%\idp_pyiver.tmp" >nul 2>&1
echo      PyInstaller %PYI_VER% 就绪

REM ------------------------------------------------------------
REM  步骤 2：依赖自检（默认开启，/nodeps 可跳过）
REM ------------------------------------------------------------
if "%DO_DEPS%"=="0" goto :deps_skip
echo [2/6] 校验项目依赖...
set "DEPS=PySide6 qfluentwidgets numpy cv2 PIL onnxruntime"
"%PY%" -c "import importlib.util as u,os,sys;m=[n for n in os.environ['DEPS'].split() if u.find_spec(n) is None];sys.stdout.write('      缺失: '+','.join(m)+'\n') if m else None;sys.exit(1 if m else 0)"
if errorlevel 1 (
    echo      尝试按 requirements.txt 安装...
    call :pip_install_file requirements.txt
    "%PY%" -c "import importlib.util as u,os,sys;m=[n for n in os.environ['DEPS'].split() if u.find_spec(n) is None];sys.exit(1 if m else 0)" >nul 2>&1
    if errorlevel 1 (
        echo [错误] 依赖仍不完整，请手动执行：
        echo        "%PY%" -m pip install -r requirements.txt
        goto :fail
    )
)
echo      依赖完整
:deps_skip

REM ------------------------------------------------------------
REM  步骤 3：清理旧构建（保证产物干净，避免 DLL 残留）
REM ------------------------------------------------------------
echo [3/6] 清理旧构建缓存...
if exist "%~dp0build" rmdir /s /q "%~dp0build"
if exist "%~dp0dist\%APPNAME%" rmdir /s /q "%~dp0dist\%APPNAME%"
echo      已清理 build\ 与 dist\%APPNAME%\
if "%CLEAN_ONLY%"=="1" (
    echo.
    echo [完成] 已按 /clean 参数仅执行清理。
    goto :done_silent
)

REM ------------------------------------------------------------
REM  步骤 4：PyInstaller 打包
REM  策略说明：
REM    - 不启用 UPX：上百 MB 的 DLL 现场解压会明显拖慢启动，
REM      体积改由 --exclude-module 与打包后瘦身控制。
REM    - 只保留程序实际用到的 Qt 模块，剔除 3D/Qml/Charts/WebEngine 等。
REM ------------------------------------------------------------
echo [4/6] 正在打包（视机器性能约 2-6 分钟）...

set "MODE_OPT=--windowed"
if "%USE_CONSOLE%"=="1" set "MODE_OPT=--console"

set "ICON_OPT="
if exist "%~dp0icon.ico" set ICON_OPT=--icon "%~dp0icon.ico"

"%PY%" -m PyInstaller %ENTRY% ^
  --name "%APPNAME%" ^
  --onedir ^
  --noconfirm ^
  --clean ^
  %MODE_OPT% ^
  %ICON_OPT% ^
  --add-data "model_download_config.json;." ^
  --hidden-import onnxruntime ^
  --hidden-import mtcnnruntime ^
  --collect-submodules ui ^
  --collect-submodules core ^
  --collect-submodules qfluentwidgets ^
  --collect-data qfluentwidgets ^
  --collect-data mtcnnruntime ^
  --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtQuickWidgets --exclude-module PySide6.QtQuickControls2 ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtMultimediaWidgets ^
  --exclude-module qfluentwidgets.multimedia ^
  --exclude-module PySide6.Qt3DAnimation --exclude-module PySide6.Qt3DCore ^
  --exclude-module PySide6.Qt3DExtras --exclude-module PySide6.Qt3DInput ^
  --exclude-module PySide6.Qt3DLogic --exclude-module PySide6.Qt3DRender ^
  --exclude-module PySide6.Qt3DWidgets --exclude-module PySide6.QtCharts ^
  --exclude-module PySide6.QtDataVisualization --exclude-module PySide6.QtBluetooth ^
  --exclude-module PySide6.QtPositioning --exclude-module PySide6.QtLocation ^
  --exclude-module PySide6.QtSensors --exclude-module PySide6.QtSerialPort ^
  --exclude-module PySide6.QtWebChannel --exclude-module PySide6.QtWebSockets ^
  --exclude-module PySide6.QtWebView --exclude-module PySide6.QtTextToSpeech ^
  --exclude-module PySide6.QtHelp --exclude-module PySide6.QtPdf ^
  --exclude-module PySide6.QtPdfWidgets --exclude-module PySide6.QtOpenGL ^
  --exclude-module PySide6.QtOpenGLWidgets --exclude-module PySide6.QtScxml ^
  --exclude-module PySide6.QtStateMachine --exclude-module PySide6.QtVirtualKeyboard ^
  --exclude-module PySide6.QtRemoteObjects --exclude-module PySide6.QtUiTools ^
  --exclude-module PySide6.QtDesigner --exclude-module PySide6.QtAxContainer ^
  --exclude-module PySide6.QtNfc --exclude-module PySide6.QtSpatialAudio ^
  --exclude-module PySide6.QtMultimediaQuick --exclude-module PySide6.QtGrpc ^
  --exclude-module PySide6.QtHttpServer --exclude-module PySide6.QtNetworkAuth ^
  --exclude-module matplotlib --exclude-module scipy --exclude-module pandas ^
  --exclude-module IPython --exclude-module jupyter --exclude-module notebook ^
  --exclude-module sklearn --exclude-module torch --exclude-module tensorflow ^
  --exclude-module pytest --exclude-module PIL.ImageQt

if errorlevel 1 (
    echo.
    echo [错误] PyInstaller 构建失败。
    goto :fail
)

if not exist "%OUTDIR%\%APPNAME%.exe" (
    echo [错误] 未找到产物：%OUTDIR%\%APPNAME%.exe
    goto :fail
)
echo      构建成功：%OUTDIR%\%APPNAME%.exe

REM ------------------------------------------------------------
REM  步骤 5：瘦身 —— 剔除确实用不到的 DLL
REM  （ffmpeg 视频读写 / 软件 OpenGL / 未使用的 Qt 子模块）
REM  这些文件删除后不影响启动、抠图、换底与排版。
REM ------------------------------------------------------------
if "%DO_SLIM%"=="0" goto :slim_skip
echo [5/6] 瘦身中...
set "SLIM=%OUTDIR%\_internal"
set "DROP_FILES=cv2\opencv_videoio_ffmpeg500_64.dll cv2\opencv_videoio_ffmpeg501_64.dll cv2\opencv_videoio_ffmpeg502_64.dll PySide6\opengl32sw.dll PySide6\Qt6VirtualKeyboard.dll PySide6\Qt6Quick.dll PySide6\Qt6QuickControls2.dll PySide6\Qt6QuickWidgets.dll PySide6\Qt6Qml.dll PySide6\Qt6QmlMeta.dll PySide6\Qt6QmlModels.dll PySide6\Qt6QmlWorkerScript.dll PySide6\Qt6Pdf.dll PySide6\Qt6PdfWidgets.dll PySide6\Qt6OpenGL.dll PySide6\Qt6OpenGLWidgets.dll PySide6\Qt6Multimedia.dll PySide6\Qt6MultimediaWidgets.dll PySide6\QtMultimedia.pyd PySide6\QtMultimediaWidgets.pyd PySide6\avcodec-61.dll PySide6\avformat-61.dll PySide6\avutil-59.dll PySide6\swresample-5.dll PySide6\swscale-8.dll PIL\_avif.cp313-win_amd64.pyd PIL\_imagingtk.cp313-win_amd64.pyd"
set "DROP_N=0"
for %%F in (%DROP_FILES%) do (
    if exist "%SLIM%\%%F" (
        del /q "%SLIM%\%%F" >nul 2>&1
        echo      剔除 %%F
        set /a DROP_N+=1
    )
)
del /q "%SLIM%\PySide6\Qt6Quick*.dll" >nul 2>&1
del /q "%SLIM%\PySide6\Qt6Qml*.dll" >nul 2>&1
del /q "%SLIM%\PySide6\Qt6Pdf*.dll" >nul 2>&1
REM Qt 自带的 96 个翻译文件（约 6.4MB）：项目没用 QTranslator，qfluentwidgets 也不带 .qm，
REM 未显式加载翻译时这些文件本就不生效，纯属冗余。
if exist "%SLIM%\PySide6\translations" (
    rmdir /s /q "%SLIM%\PySide6\translations" >nul 2>&1
    echo      剔除 PySide6\translations\ （Qt 翻译文件，未使用）
)
echo      共剔除 %DROP_N% 个文件
:slim_skip

REM ------------------------------------------------------------
REM  可选：UPX 压缩最大的几个 DLL（/upx，默认关闭）
REM  实测 cv2.pyd 82.3MB -> 20.0MB（省 76%），但启动时需现场解压，
REM  且部分杀软对 UPX 加壳文件误报，故默认不做，按需开启。
REM  只对"最大的两个"下手：cv2.pyd 与 numpy 的 OpenBLAS，
REM  收益占绝大部分，解压开销可控（约 0.5-1 秒）。
REM ------------------------------------------------------------
if "%DO_UPX%"=="0" goto :upx_skip
echo [可选] UPX 压缩大文件...
set "UPX_EXE="
if exist "%~dp0upx.exe" set "UPX_EXE=%~dp0upx.exe"
if not defined UPX_EXE (
    for /f "tokens=*" %%U in ('where upx 2^>nul') do if not defined UPX_EXE set "UPX_EXE=%%U"
)
if not defined UPX_EXE (
    echo      未找到 upx.exe，已跳过（放到脚本同目录或加入 PATH 后重试）
    goto :upx_skip
)
echo      使用：%UPX_EXE%
if exist "%SLIM%\cv2\cv2.pyd" (
    "%UPX_EXE%" --best --lzma "%SLIM%\cv2\cv2.pyd" >nul 2>&1
    echo      已压缩 cv2\cv2.pyd
)
for %%F in ("%SLIM%\numpy.libs\*.dll") do (
    "%UPX_EXE%" --best --lzma "%%~fF" >nul 2>&1
    echo      已压缩 numpy.libs\%%~nxF
)
:upx_skip

REM ------------------------------------------------------------
REM  步骤 6：体积报告
REM ------------------------------------------------------------
echo [6/6] 统计体积...
set "PDIR=%~dp0%OUTDIR%"
set "SIZE_MB=未知"
set "SZ_TMP=%TEMP%\idp_size.tmp"
if exist "%SZ_TMP%" del /q "%SZ_TMP%" >nul 2>&1
"%PY%" -c "import os,sys;t=sum(os.path.getsize(os.path.join(r,f)) for r,_d,_fs in os.walk(sys.argv[1]) for f in _fs);sys.stdout.write(str(round(t/1048576,1)))" "%PDIR%" > "%SZ_TMP%" 2>nul
if exist "%SZ_TMP%" set /p SIZE_MB=<"%SZ_TMP%"
if exist "%SZ_TMP%" del /q "%SZ_TMP%" >nul 2>&1
echo      产物体积：%SIZE_MB% MB

REM ------------------------------------------------------------
REM  可选：冒烟启动验证（/smoke）
REM ------------------------------------------------------------
if "%DO_SMOKE%"=="0" goto :smoke_skip
echo.
echo      冒烟测试：启动 exe 并观察 8 秒...
REM 用 Python 拉起并判定存活：
REM   1) tasklist + findstr 匹配中文进程名在部分代码页下会误判为"已退出"；
REM   2) 这里的等待不用 timeout —— 装了 Git for Windows 的机器上，PATH 里的
REM      GNU timeout 会抢占 Windows 的 timeout.exe，报 "invalid time interval /t"；
REM   3) 收尾由 Python 直接 kill，不残留窗口。
"%PY%" -c "import subprocess,time,os,sys;p=subprocess.Popen(sys.argv[1],cwd=os.path.dirname(sys.argv[1]));time.sleep(8);a=p.poll() is None;p.kill() if a else None;sys.exit(0 if a else 1)" "%CD%\%OUTDIR%\%APPNAME%.exe"
if errorlevel 1 (
    echo [警告] 进程已退出，程序可能启动失败（可用 /console 重新打包看报错）
) else (
    echo      进程存活，冒烟通过。
)
:smoke_skip

REM ------------------------------------------------------------
REM  可选：压缩为 zip（/zip）
REM ------------------------------------------------------------
if "%DO_ZIP%"=="0" goto :zip_skip
echo.
for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "STAMP=%%D"
set "ZSRC=%~dp0%OUTDIR%"
set "ZDST=%~dp0dist\%APPNAME%_%STAMP%.zip"
echo      正在压缩：%APPNAME%_%STAMP%.zip
REM 用 -LiteralPath 整体打包目录：这样 zip 内保留 "证件照制作工具\" 顶层目录，
REM 解压后是一个完整文件夹；若改用管道逐项传入，解压会得到一地散文件。
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -LiteralPath $env:ZSRC -DestinationPath $env:ZDST -CompressionLevel Optimal -Force"
if exist "%ZDST%" (echo      压缩完成) else (echo [警告] 压缩未生成文件)
:zip_skip

goto :done

REM ============================================================
REM  子程序：带镜像回退的 pip 安装（包名形式）
REM ============================================================
:pip_install
"%PY%" -m pip install %* >> "%LOG%" 2>&1
if not errorlevel 1 exit /b 0
echo      官方源失败，尝试清华镜像... >> "%LOG%"
"%PY%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple %* >> "%LOG%" 2>&1
if not errorlevel 1 exit /b 0
echo      清华镜像失败，尝试阿里云镜像... >> "%LOG%"
"%PY%" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ %* >> "%LOG%" 2>&1
if not errorlevel 1 exit /b 0
exit /b 1

REM ============================================================
REM  子程序：带镜像回退的 pip 安装（-r 文件形式）
REM ============================================================
:pip_install_file
"%PY%" -m pip install -r %* >> "%LOG%" 2>&1
if not errorlevel 1 exit /b 0
"%PY%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r %* >> "%LOG%" 2>&1
if not errorlevel 1 exit /b 0
"%PY%" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ -r %* >> "%LOG%" 2>&1
if not errorlevel 1 exit /b 0
exit /b 1

REM ============================================================
:usage
echo.
echo 用法：build_exe.bat [/nodeps] [/noslim] [/zip] [/smoke] [/console] [/clean]
echo   /nodeps   跳过依赖检查安装
echo   /noslim   跳过 DLL 瘦身
echo   /zip      额外压缩为 zip
echo   /smoke    打包后试启动 8 秒
echo   /console  生成带控制台版本（排错）
echo   /clean    只清理构建缓存
echo   /nopause  结束时不暂停（自动化/CI 用）
echo   /upx      用 UPX 压缩大文件（需自备 upx.exe；体积再降约 75MB）
echo.
if "%DO_PAUSE%"=="1" pause
exit /b 0

REM ============================================================
:done_silent
echo.
if "%DO_PAUSE%"=="1" pause
exit /b 0

:done
echo.
echo ============================================================
echo   打包完成
echo   产物：%OUTDIR%\%APPNAME%.exe
echo   体积：%SIZE_MB% MB
echo   提示：/console 可生成带控制台版本用于排错
echo ============================================================
echo [%date% %time%] === 打包完成，体积 %SIZE_MB% MB === >> "%LOG%"
if "%DO_PAUSE%"=="1" pause
exit /b 0

:fail
echo.
echo [失败] 打包未完成，请检查上方错误信息或 build_log.txt
echo [%date% %time%] === 打包失败 === >> "%LOG%"
if "%DO_PAUSE%"=="1" pause
exit /b 1
