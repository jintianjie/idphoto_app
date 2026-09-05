@echo off
chcp 936 >nul
title 打包 idphoto_app -> exe
cd /d "%~dp0"

set "PY=C:\Users\plasx520\.workbuddy\binaries\python\envs\idphoto\Scripts\python.exe"

REM 入口用 launcher.py（带启动页，与 run.bat 一致）
REM 策略：不用 UPX 压缩 —— UPX(--lzma) 启动时要现场解压上百 MB 的 DLL，明显拖慢打开速度；
REM 不压缩则内存映射直接加载、秒开。体积靠 --exclude-module + 打包后自动剔除无用 DLL 控制。
REM --exclude-module 剔除非必需的 PySide6 子模块（qfluentwidgets 只用 Core/Gui/Widgets/Svg/Multimedia/Xml）
"%PY%" -m PyInstaller launcher.py ^
  --name "证件照制作工具" ^
  --windowed ^
  --onedir ^
  --noconfirm ^
  --clean ^
  --add-data "model_download_config.json;." ^
  --hidden-import onnxruntime ^
  --hidden-import mtcnnruntime ^
  --collect-submodules ui ^
  --collect-submodules core ^
  --collect-submodules qfluentwidgets ^
  --collect-data qfluentwidgets ^
  --collect-data mtcnnruntime ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtQuickWidgets ^
  --exclude-module PySide6.Qt3DAnimation --exclude-module PySide6.Qt3DCore ^
  --exclude-module PySide6.Qt3DExtras --exclude-module PySide6.Qt3DInput ^
  --exclude-module PySide6.Qt3DLogic --exclude-module PySide6.Qt3DRender ^
  --exclude-module PySide6.Qt3DWidgets --exclude-module PySide6.QtCharts ^
  --exclude-module PySide6.QtDataVisualization --exclude-module PySide6.QtBluetooth ^
  --exclude-module PySide6.QtPositioning --exclude-module PySide6.QtLocation ^
  --exclude-module PySide6.QtSensors --exclude-module PySide6.QtSerialPort ^
  --exclude-module PySide6.QtWebChannel --exclude-module PySide6.QtWebSockets ^
  --exclude-module PySide6.QtTextToSpeech --exclude-module PySide6.QtHelp ^
  --exclude-module PySide6.QtPdf --exclude-module PySide6.QtPdfWidgets ^
  --exclude-module PySide6.QtOpenGL --exclude-module PySide6.QtOpenGLWidgets ^
  --exclude-module PySide6.QtScxml --exclude-module PySide6.QtStateMachine ^
  --exclude-module PySide6.QtVirtualKeyboard --exclude-module PySide6.QtRemoteObjects ^
  --exclude-module PySide6.QtUiTools --exclude-module PySide6.QtDesigner ^
  --exclude-module PySide6.QtAxContainer --exclude-module PySide6.QtNfc ^
  --exclude-module PySide6.QtSpatialAudio --exclude-module PySide6.QtMultimediaQuick ^
  --exclude-module PySide6.QtGrpc --exclude-module PySide6.QtHttpServer ^
  --exclude-module PySide6.QtNetworkAuth --exclude-module PySide6.QtWebView

echo.
echo 构建完成：dist\证件照制作工具\证件照制作工具.exe
echo 正在自动瘦身（剔除视频/软件OpenGL/未用Qt 的 DLL，不影响启动与图像功能）...

set "SLIM=dist\证件照制作工具\_internal"
del /q "%SLIM%\cv2\opencv_videoio_ffmpeg500_64.dll" 2>nul
del /q "%SLIM%\PySide6\opengl32sw.dll" 2>nul
del /q "%SLIM%\PySide6\Qt6Quick.dll" 2>nul
del /q "%SLIM%\PySide6\Qt6Qml.dll" 2>nul
del /q "%SLIM%\PySide6\Qt6QmlMeta.dll" 2>nul
del /q "%SLIM%\PySide6\Qt6QmlModels.dll" 2>nul
del /q "%SLIM%\PySide6\Qt6QmlWorkerScript.dll" 2>nul
del /q "%SLIM%\PySide6\Qt6Pdf.dll" 2>nul
del /q "%SLIM%\PySide6\Qt6OpenGL.dll" 2>nul

echo 瘦身完成。
pause
