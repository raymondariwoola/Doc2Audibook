#!/usr/bin/env python3
"""Prepare and optionally bundle the native Phase 4A desktop application.

The build is native-host-only: Windows x64 builds Windows x64 artifacts, while
an Apple Silicon or Intel Mac builds its matching macOS artifact. PyInstaller
and Tauri are not used as cross-compilers.

Preparation is the default action. It builds React and the one-file Python
sidecar, audits and stages FFmpeg/FFprobe, and writes the Tauri bundle overlay.
Pass ``--bundle`` to invoke the Tauri installer build after preparation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
TAURI_DIR = WEB_DIR / "src-tauri"
SPEC_PATH = REPO_ROOT / "scripts" / "doc2audiobook-sidecar.spec"
BUILD_ROOT = REPO_ROOT / "build" / "desktop"
PYINSTALLER_ROOT = BUILD_ROOT / "pyinstaller"
TAURI_OVERLAY = TAURI_DIR / "tauri.bundle.conf.json"
SIDECAR_BASENAME = "doc2audiobook-sidecar"
PROVENANCE_FILENAME = "desktop-build-provenance.json"

EDGE_ONLY_EXCLUSIONS = (
    "kokoro-onnx",
    "onnxruntime",
    "openai",
    "phonemizer",
    "piper-tts",
    "soundfile",
)


class DesktopBuildError(RuntimeError):
    """A build precondition or policy check failed."""


@dataclass(frozen=True)
class HostTarget:
    triple: str
    operating_system: str
    architecture: str
    executable_suffix: str

    @property
    def ffmpeg_name(self) -> str:
        return f"ffmpeg{self.executable_suffix}"

    @property
    def ffprobe_name(self) -> str:
        return f"ffprobe{self.executable_suffix}"

    @property
    def sidecar_name(self) -> str:
        return f"{SIDECAR_BASENAME}-{self.triple}{self.executable_suffix}"


SUPPORTED_HOSTS: dict[str, HostTarget] = {
    "x86_64-pc-windows-msvc": HostTarget(
        "x86_64-pc-windows-msvc", "Windows", "x86_64", ".exe"
    ),
    "aarch64-apple-darwin": HostTarget(
        "aarch64-apple-darwin", "Darwin", "aarch64", ""
    ),
    "x86_64-apple-darwin": HostTarget(
        "x86_64-apple-darwin", "Darwin", "x86_64", ""
    ),
}


@dataclass(frozen=True)
class FFmpegAudit:
    version_output: str
    build_configuration: str
    classification: str
    override_used: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalise_architecture(value: str) -> str:
    lowered = value.strip().lower()
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "arm64": "aarch64",
    }
    return aliases.get(lowered, lowered)


def parse_native_host(
    rustc_verbose: str,
    *,
    system_name: str | None = None,
    machine_name: str | None = None,
) -> HostTarget:
    triple = ""
    for line in rustc_verbose.splitlines():
        if line.startswith("host:"):
            triple = line.partition(":")[2].strip()
            break
    if not triple:
        raise DesktopBuildError("rustc did not report a host target triple.")
    target = SUPPORTED_HOSTS.get(triple)
    if target is None:
        supported = ", ".join(sorted(SUPPORTED_HOSTS))
        raise DesktopBuildError(
            f"Unsupported native Rust host {triple!r}. Supported hosts: {supported}. "
            "Build each desktop artifact on its target operating system."
        )

    actual_system = system_name or platform.system()
    actual_arch = _normalise_architecture(machine_name or platform.machine())
    if actual_system != target.operating_system or actual_arch != target.architecture:
        raise DesktopBuildError(
            "Rust, Python, and the operating system are not targeting the same "
            f"native host (rustc={triple}, system={actual_system}/{actual_arch}). "
            "Cross-compilation is not supported."
        )
    return target


def _rustc_path() -> Path:
    discovered = shutil.which("rustc")
    if discovered:
        return Path(discovered).resolve()
    fallback = Path.home() / ".cargo" / "bin" / (
        "rustc.exe" if os.name == "nt" else "rustc"
    )
    if fallback.is_file():
        return fallback.resolve()
    raise DesktopBuildError(
        "Rust is required. Install rustup for this machine, reopen the terminal, "
        "and rerun this command."
    )


def detect_native_host() -> tuple[HostTarget, Path]:
    rustc = _rustc_path()
    completed = subprocess.run(
        [str(rustc), "-vV"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return parse_native_host(completed.stdout), rustc


def classify_ffmpeg_build(
    build_configuration: str,
    *,
    command_succeeded: bool,
    allow_unreviewed: bool,
) -> tuple[str, bool]:
    lowered = build_configuration.lower()
    if "--enable-nonfree" in lowered:
        raise DesktopBuildError(
            "FFmpeg was built with --enable-nonfree and cannot be staged. "
            "Supply a redistributable build instead."
        )

    configuration_known = command_succeeded and "--" in build_configuration
    if not configuration_known:
        classification = "unknown-unreviewed"
    elif "--enable-gpl" in lowered:
        classification = "gpl-unreviewed"
    else:
        classification = "lgpl-compatible-candidate"

    needs_override = classification in {"unknown-unreviewed", "gpl-unreviewed"}
    if needs_override and not allow_unreviewed:
        raise DesktopBuildError(
            f"FFmpeg license classification is {classification}. This Phase 4A "
            "build is internal-only; rerun with --allow-unreviewed-ffmpeg only "
            "after accepting that restriction, or provide a reviewed LGPL build."
        )
    return classification, needs_override and allow_unreviewed


def _run_tool_output(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def audit_ffmpeg(ffmpeg: Path, *, allow_unreviewed: bool) -> FFmpegAudit:
    version = _run_tool_output([str(ffmpeg), "-hide_banner", "-version"])
    if version.returncode != 0:
        detail = (version.stdout + version.stderr).strip()
        raise DesktopBuildError(
            f"Could not execute FFmpeg at {ffmpeg}: {detail or 'unknown error'}"
        )
    buildconf = _run_tool_output([str(ffmpeg), "-hide_banner", "-buildconf"])
    configuration = (buildconf.stdout + buildconf.stderr).strip()
    classification, override_used = classify_ffmpeg_build(
        configuration,
        command_succeeded=buildconf.returncode == 0,
        allow_unreviewed=allow_unreviewed,
    )
    return FFmpegAudit(
        version_output=(version.stdout + version.stderr).strip(),
        build_configuration=configuration,
        classification=classification,
        override_used=override_used,
    )


def _resolve_explicit_or_path(
    tool_name: str, explicit: Path | None, environment_name: str
) -> Path:
    if explicit is not None:
        candidate = explicit
    else:
        configured = os.environ.get(environment_name)
        candidate = Path(configured).expanduser() if configured else None
        if candidate is None:
            discovered = shutil.which(tool_name)
            candidate = Path(discovered) if discovered else None
    if candidate is None or not candidate.expanduser().is_file():
        shown = str(candidate) if candidate is not None else tool_name
        raise DesktopBuildError(
            f"Required media tool not found: {shown}. Use --ffmpeg-dir to select "
            "a directory containing both FFmpeg and FFprobe."
        )
    return candidate.expanduser().resolve()


def resolve_media_tools(
    target: HostTarget, ffmpeg_dir: Path | None
) -> tuple[Path, Path]:
    directory = ffmpeg_dir.expanduser().resolve() if ffmpeg_dir else None
    ffmpeg_explicit = directory / target.ffmpeg_name if directory else None
    ffprobe_explicit = directory / target.ffprobe_name if directory else None
    return (
        _resolve_explicit_or_path(
            target.ffmpeg_name, ffmpeg_explicit, "DOC2AUDIOBOOK_FFMPEG"
        ),
        _resolve_explicit_or_path(
            target.ffprobe_name, ffprobe_explicit, "DOC2AUDIOBOOK_FFPROBE"
        ),
    )


def _run_checked(
    command: Sequence[str],
    *,
    cwd: Path,
    extra_path: Path | None = None,
) -> None:
    environment = os.environ.copy()
    if extra_path is not None:
        environment["PATH"] = str(extra_path) + os.pathsep + environment.get("PATH", "")
    subprocess.run(list(command), cwd=cwd, env=environment, check=True)


def build_react() -> None:
    npm = shutil.which("npm")
    if not npm:
        raise DesktopBuildError("Node.js/npm is required to build the React client.")
    _run_checked([npm, "--prefix", str(WEB_DIR), "run", "build"], cwd=REPO_ROOT)
    if not (WEB_DIR / "dist" / "index.html").is_file():
        raise DesktopBuildError("React build completed without web/dist/index.html.")


def pyinstaller_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(PYINSTALLER_ROOT / "dist"),
        "--workpath",
        str(PYINSTALLER_ROOT / "work"),
        str(SPEC_PATH),
    ]


def build_sidecar(target: HostTarget) -> Path:
    _run_checked(pyinstaller_command(), cwd=REPO_ROOT)
    artifact = (
        PYINSTALLER_ROOT
        / "dist"
        / f"{SIDECAR_BASENAME}{target.executable_suffix}"
    )
    if not artifact.is_file():
        raise DesktopBuildError(f"PyInstaller artifact is missing: {artifact}")
    return artifact


def _replace_directory(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, copy_function=shutil.copy2)


def _copy_executable(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if os.name != "nt":
        destination.chmod(
            destination.stat().st_mode
            | stat.S_IXUSR
            | stat.S_IXGRP
            | stat.S_IXOTH
        )


def _file_manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_resource_mapping(resources_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in sorted(item for item in resources_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(resources_dir).as_posix()
        mapping[f"resources/{relative}"] = relative
    return mapping


def stage_payload(
    *,
    tauri_dir: Path,
    target: HostTarget,
    sidecar_source: Path,
    frontend_source: Path,
    ffmpeg_source: Path,
    ffprobe_source: Path,
    ffmpeg_audit: FFmpegAudit,
) -> tuple[Path, Path]:
    binaries_dir = tauri_dir / "binaries"
    resources_dir = tauri_dir / "resources"
    frontend_dir = resources_dir / "frontend"
    tools_dir = resources_dir / "tools"
    licenses_dir = resources_dir / "licenses"

    if not sidecar_source.is_file():
        raise DesktopBuildError(f"Sidecar artifact is missing: {sidecar_source}")
    if not (frontend_source / "index.html").is_file():
        raise DesktopBuildError(
            f"Frontend source does not contain index.html: {frontend_source}"
        )

    _replace_directory(frontend_source, frontend_dir)
    for owned_dir in (tools_dir, licenses_dir):
        if owned_dir.exists():
            shutil.rmtree(owned_dir)
        owned_dir.mkdir(parents=True, exist_ok=True)

    sidecar_destination = binaries_dir / target.sidecar_name
    _copy_executable(sidecar_source, sidecar_destination)
    staged_ffmpeg = tools_dir / target.ffmpeg_name
    staged_ffprobe = tools_dir / target.ffprobe_name
    _copy_executable(ffmpeg_source, staged_ffmpeg)
    _copy_executable(ffprobe_source, staged_ffprobe)

    provenance: dict[str, object] = {
        "schema_version": 1,
        "artifact_policy": {
            "distribution": "internal-development-only",
            "public_distribution_approved": False,
            "signed": False,
        },
        "host_target_triple": target.triple,
        "provider_scope": {
            "supported": ["edge"],
            "excluded_optional_distributions": list(EDGE_ONLY_EXCLUSIONS),
            "local_voice_models_bundled": False,
            "note": (
                "Phase 4A bundles only the online Edge TTS provider. Optional "
                "local and paid provider dependencies are intentionally excluded."
            ),
        },
        "sidecar": {
            "filename": target.sidecar_name,
            "sha256": sha256_file(sidecar_destination),
        },
        "frontend": {
            "files": _file_manifest(frontend_dir),
        },
        "ffmpeg": {
            "filename": target.ffmpeg_name,
            "sha256": sha256_file(staged_ffmpeg),
            "version_output": ffmpeg_audit.version_output,
            "build_configuration": ffmpeg_audit.build_configuration,
            "license_classification": ffmpeg_audit.classification,
            "unreviewed_override_used": ffmpeg_audit.override_used,
        },
        "ffprobe": {
            "filename": target.ffprobe_name,
            "sha256": sha256_file(staged_ffprobe),
        },
    }
    provenance_path = licenses_dir / PROVENANCE_FILENAME
    _write_json(provenance_path, provenance)

    overlay: dict[str, object] = {
        "bundle": {
            "externalBin": [f"binaries/{SIDECAR_BASENAME}"],
            "resources": build_resource_mapping(resources_dir),
        }
    }
    overlay_path = tauri_dir / "tauri.bundle.conf.json"
    _write_json(overlay_path, overlay)
    return provenance_path, overlay_path


def prepare_desktop(
    *,
    ffmpeg_dir: Path | None,
    allow_unreviewed_ffmpeg: bool,
) -> tuple[HostTarget, Path]:
    target, _rustc = detect_native_host()
    ffmpeg, ffprobe = resolve_media_tools(target, ffmpeg_dir)
    audit = audit_ffmpeg(ffmpeg, allow_unreviewed=allow_unreviewed_ffmpeg)

    print(f"Preparing native desktop payload for {target.triple}...")
    build_react()
    sidecar = build_sidecar(target)
    provenance, overlay = stage_payload(
        tauri_dir=TAURI_DIR,
        target=target,
        sidecar_source=sidecar,
        frontend_source=WEB_DIR / "dist",
        ffmpeg_source=ffmpeg,
        ffprobe_source=ffprobe,
        ffmpeg_audit=audit,
    )
    print(f"Staged sidecar: {TAURI_DIR / 'binaries' / target.sidecar_name}")
    print(f"Staged provenance: {provenance}")
    print(f"Generated Tauri overlay: {overlay}")
    print(
        "Provider scope: Edge TTS only; Piper/Kokoro/OpenAI dependencies and "
        "local voice models are not bundled."
    )
    print("Release status: unsigned, internal-development-only Phase 4A build.")
    return target, overlay


def bundle_desktop(target: HostTarget, overlay: Path, *, debug: bool) -> None:
    npm = shutil.which("npm")
    if not npm:
        raise DesktopBuildError("Node.js/npm is required to invoke Tauri.")
    rustc = _rustc_path()
    command = [
        npm,
        "--prefix",
        str(WEB_DIR),
        "run",
        "tauri",
        "--",
        "build",
        "--config",
        str(overlay.relative_to(WEB_DIR)),
    ]
    if debug:
        command.append("--debug")
    print(
        f"Building {'debug' if debug else 'release'} Tauri bundle for "
        f"{target.triple}..."
    )
    _run_checked(command, cwd=REPO_ROOT, extra_path=rustc.parent)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare the native Phase 4A payload; optionally build its unsigned "
            "Tauri installer. Builds are native-host-only."
        )
    )
    parser.add_argument(
        "--ffmpeg-dir",
        type=Path,
        help="directory containing ffmpeg/ffprobe (or .exe on Windows)",
    )
    parser.add_argument(
        "--allow-unreviewed-ffmpeg",
        action="store_true",
        help=(
            "allow a GPL or unknown FFmpeg build in the internal-only artifact; "
            "--enable-nonfree builds are always rejected"
        ),
    )
    parser.add_argument(
        "--bundle",
        action="store_true",
        help="run the Tauri installer build after preparing the payload",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="build a debug Tauri bundle (requires --bundle)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.debug and not args.bundle:
        parser.error("--debug requires --bundle")
    try:
        target, overlay = prepare_desktop(
            ffmpeg_dir=args.ffmpeg_dir,
            allow_unreviewed_ffmpeg=args.allow_unreviewed_ffmpeg,
        )
        if args.bundle:
            bundle_desktop(target, overlay, debug=args.debug)
    except (DesktopBuildError, OSError, subprocess.CalledProcessError) as exc:
        print(f"desktop build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
