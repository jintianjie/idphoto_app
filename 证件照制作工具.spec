# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = [('model_download_config.json', '.')]
hiddenimports = ['onnxruntime', 'mtcnnruntime']
datas += collect_data_files('qfluentwidgets')
datas += collect_data_files('mtcnnruntime')
hiddenimports += collect_submodules('ui')
hiddenimports += collect_submodules('core')
hiddenimports += collect_submodules('qfluentwidgets')


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtQuickWidgets', 'PySide6.QtQuickControls2', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'qfluentwidgets.multimedia', 'PySide6.Qt3DAnimation', 'PySide6.Qt3DCore', 'PySide6.Qt3DExtras', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic', 'PySide6.Qt3DRender', 'PySide6.Qt3DWidgets', 'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtBluetooth', 'PySide6.QtPositioning', 'PySide6.QtLocation', 'PySide6.QtSensors', 'PySide6.QtSerialPort', 'PySide6.QtWebChannel', 'PySide6.QtWebSockets', 'PySide6.QtWebView', 'PySide6.QtTextToSpeech', 'PySide6.QtHelp', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtScxml', 'PySide6.QtStateMachine', 'PySide6.QtVirtualKeyboard', 'PySide6.QtRemoteObjects', 'PySide6.QtUiTools', 'PySide6.QtDesigner', 'PySide6.QtAxContainer', 'PySide6.QtNfc', 'PySide6.QtSpatialAudio', 'PySide6.QtMultimediaQuick', 'PySide6.QtGrpc', 'PySide6.QtHttpServer', 'PySide6.QtNetworkAuth', 'matplotlib', 'scipy', 'pandas', 'IPython', 'jupyter', 'notebook', 'sklearn', 'torch', 'tensorflow', 'pytest', 'PIL.ImageQt'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='证件照制作工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='证件照制作工具',
)
