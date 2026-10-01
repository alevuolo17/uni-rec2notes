#!/usr/bin/env python3
"""Build the Windows Vulkan whisper.cpp zip that setup installs for `--backend vulkan`: upstream ships none.

Run on Windows x64 with Visual Studio Build Tools (C++), the Vulkan SDK, git, and cmake on PATH:
`py docs/whisper_vulkan.py [workdir]`. It builds the version of setup's cpu zip with upstream's Windows
release flags plus Vulkan (backends loaded at run time, every CPU variant: no Vulkan GPU falls back to the
CPU), zips whisper-cli, its DLLs and the license under Release/ as upstream does, and prints the entry
for setup.WINDOWS_BUILDS. Rerun after changing the cpu version, then host the zip at the printed URL.
The workdir defaults to `%SystemDrive%\\whisper-vulkan-<version>`, outside the user folder: the DLLs keep
their source paths (ggml's asserts), so a zip built in the home folder would ship its path and username,
and the script refuses one.
"""

import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rec2notes import setup  # noqa: E402

FLAGS = ["-A", "x64", "-DBUILD_SHARED_LIBS=ON", "-DGGML_NATIVE=OFF", "-DGGML_BACKEND_DL=ON",
         "-DGGML_CPU_ALL_VARIANTS=ON", "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DWHISPER_SDL2=OFF"]


def main() -> None:
    version = setup.WINDOWS_BUILDS["cpu"][0]
    url = setup.WINDOWS_BUILDS["vulkan"][1]
    default = Path(os.environ.get("SystemDrive", "C:") + "\\", f"whisper-vulkan-{version}")
    work = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else default
    source = work / "whisper.cpp"
    if not source.exists():
        run(["git", "clone", "--depth", "1", "--branch", version, setup.REPO_URL, str(source)])
    run(["cmake", "-S", str(source), "-B", str(source / "build"), *FLAGS])
    run(["cmake", "--build", str(source / "build"), "--config", "Release", "-j", str(os.cpu_count() or 1)])
    release = source / "build" / "bin" / "Release"
    files = [release / "whisper-cli.exe", *sorted(release.glob("*.dll")), source / "LICENSE"]
    home = str(Path.home()).lower().encode()
    if leaks := [f.name for f in files if home in f.read_bytes().lower()]:
        sys.exit(f"{', '.join(leaks)} hold {Path.home()}: build outside the home folder")
    archive = work / url.rsplit("/", 1)[1]
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for file in files:
            z.write(file, f"Release/{file.name}")
            print(f"  {file.name}")
    sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(f"\n{archive}\nsetup.WINDOWS_BUILDS[\"vulkan\"] = (\"{version}\", \"{url}\",\n    \"{sha256}\")")


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
