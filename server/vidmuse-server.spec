# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for VidMuse Local Server
Builds a single executable for the B70 workstation
"""

block_cipher = None

# Main script
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Include workflow JSON files
        ('workflows', 'workflows'),
        # Include any static assets
        ('../README.md', '.'),
    ],
    hiddenimports=[
        # FastAPI / Starlette
        'fastapi',
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # Pydantic
        'pydantic',
        'pydantic.deprecated',
        # Audio libs
        'librosa',
        'librosa.core',
        'librosa.feature',
        'librosa.beat',
        'soundfile',
        'audioread',
        # NumPy/SciPy stack
        'numpy',
        'scipy',
        'scipy.fft',
        # WebSocket
        'websockets',
        'websockets.legacy',
        'websockets.legacy.server',
        'websockets.legacy.protocol',
        # HTTP client
        'requests',
        'urllib3',
        # Standard lib modules that might be lazily imported
        'email.mime',
        'email.mime.text',
        'email.mime.multipart',
        'email.mime.base',
        'email.generator',
        'email.header',
        'email.utils',
        'json',
        'asyncio',
        'pathlib',
        'tempfile',
        'shutil',
        'uuid',
        'datetime',
        'typing',
        'collections',
        'collections.abc',
        'functools',
        'itertools',
        'math',
        'random',
        'hashlib',
        'base64',
        'io',
        'warnings',
        'inspect',
        'importlib',
        'importlib.metadata',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy things we don't need bundled
        'matplotlib',
        'tkinter',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'wx',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
        'pylint',
        'black',
        'mypy',
        'sphinx',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Remove duplicate libs
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='vidmuse-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Icon for the executable
    icon='icon.ico',
)
