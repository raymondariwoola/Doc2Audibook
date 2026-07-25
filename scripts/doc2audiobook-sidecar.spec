# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the private Phase 4A desktop HTTP sidecar.

This recipe intentionally excludes every optional provider package. The first
desktop package supports Edge TTS only and does not redistribute Piper, Kokoro,
OpenAI, local voice models, or their transitive native libraries.
"""

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata


REPO_ROOT = Path(SPEC).resolve().parents[1]

# The source registry also contains platform-native and optional providers.
# A generated runtime hook narrows the frozen process itself to Edge, so an
# Apple ``say`` installation or a stray machine-local package cannot widen the
# Phase 4A provider surface. The hook lives under PyInstaller's ignored work
# directory and is recreated deterministically for every build.
edge_only_runtime_hook = Path(workpath) / "doc2audiobook-edge-only-hook.py"
edge_only_runtime_hook.parent.mkdir(parents=True, exist_ok=True)
edge_only_runtime_hook.write_text(
    "\n".join(
        [
            '"""Restrict the Phase 4A frozen sidecar to Edge TTS."""',
            "import providers as _providers",
            "_providers._REGISTRY.clear()",
            "_providers._REGISTRY[_providers.EdgeTTSProvider.id] = "
            "_providers.EdgeTTSProvider",
            "_providers._INSTANCES.clear()",
            "",
        ]
    ),
    encoding="utf-8",
)

# edge-tts reads its installed distribution metadata for its user agent/version.
# Keeping that metadata explicit makes the spec independent of hook changes.
datas = copy_metadata("edge-tts")

hiddenimports = [
    "edge_tts",
    "edge_tts.communicate",
    "edge_tts.voices",
    "uvicorn.lifespan.on",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
]

# These imports are optional in providers.py. Excluding them is both a size and
# a licensing boundary: the Phase 4A package is intentionally Edge-only.
excluded_optional_providers = [
    "gradio",
    "kokoro_onnx",
    "numpy",
    "onnxruntime",
    "openai",
    "phonemizer",
    "piper",
    "piper_phonemize",
    "scipy",
    "soundfile",
    "torch",
]

a = Analysis(
    [str(REPO_ROOT / "web_api" / "desktop.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(edge_only_runtime_hook)],
    excludes=excluded_optional_providers,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="doc2audiobook-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
