"""
Build Writing Tools into a single Windows executable with PyInstaller.

Usage (from Windows_and_Linux):
    .venv\\Scripts\\python.exe pyinstaller-build-script.py

Assets are embedded in the onefile executable AND copied next to it, so:
  * users can move just the .exe and still get icons/options, and
  * users can edit options.json / swap icons next to the exe if they prefer.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(HERE, "dist")
BUILD_DIR = os.path.join(HERE, "build")

# Files/directories embedded into the exe and copied beside it.
ASSET_ITEMS = [
    "icons",
    "locales",
    "options.json",
    "options_examples.json",
    "Latest_Version_for_Update_Check.txt",
]

# Conda puts the C runtime DLLs used by _ctypes/_ssl/_hashlib/_lzma in
# Library\bin instead of DLLs. PyInstaller's dependency scan does not look
# there, which produces a onefile exe that dies on startup with:
#   ImportError: DLL load failed while importing _ctypes
# Add them explicitly so the build works with both conda and python.org.
RUNTIME_DLLS = [
    "ffi-8.dll",
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
    "libexpat.dll",
    "liblzma.dll",
    "libbz2.dll",
]


def _add_binary_args():
    args = []
    search_dirs = [
        os.path.join(sys.base_prefix, "Library", "bin"),
        os.path.join(sys.base_prefix, "DLLs"),
        os.path.dirname(sys.executable),
    ]
    added = set()
    for dll_name in RUNTIME_DLLS:
        for directory in search_dirs:
            source = os.path.join(directory, dll_name)
            if not os.path.exists(source):
                continue
            # PyInstaller uses ';' as the source/destination separator on Windows.
            args.extend(["--add-binary", f"{source}{os.pathsep}."])
            added.add(dll_name.lower())
            break
    if added:
        print(f"Bundling runtime DLLs: {', '.join(sorted(added))}")
    return args


def _add_data_args():
    args = []
    for item in ASSET_ITEMS:
        source = os.path.join(HERE, item)
        if not os.path.exists(source):
            print(f"Warning: asset not found, skipping: {source}")
            continue
        # PyInstaller uses ';' as the source/destination separator on Windows.
        args.extend(["--add-data", f"{source}{os.pathsep}{item if os.path.isdir(source) else '.'}"])
    return args


def _copy_assets_to_dist():
    for item in ASSET_ITEMS:
        source = os.path.join(HERE, item)
        if not os.path.exists(source):
            continue
        target = os.path.join(DIST_DIR, item)
        try:
            if os.path.isdir(source):
                if os.path.exists(target):
                    shutil.rmtree(target)
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        except OSError as exc:
            print(f"Warning: could not copy {item} to dist: {exc}")


def run_pyinstaller_build():
    pyinstaller_command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--windowed",
        "--icon=icons/app_icon.ico",
        "--name=Writing Tools",
        "--clean",
        "--noconfirm",
        # Make sure optional runtime-selected backends are visible to PyInstaller.
        "--hidden-import=pynput.keyboard._win32",
        "--hidden-import=pynput.mouse._win32",
        "--hidden-import=darkdetect._windows_detect",
        "--collect-submodules=google.genai",
        "--copy-metadata=google-genai",
        # Exclude unnecessary modules
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "IPython",
        "--exclude-module", "jedi",
        "--exclude-module", "email_validator",
        # NOTE: do NOT exclude `cryptography` — google-genai's auth chain pulls
        # it in heavily during `genai.Client()` construction. Excluding it
        # makes the compiled exe crash on startup.
        "--exclude-module", "psutil",
        "--exclude-module", "pyzmq",
        "--exclude-module", "tornado",
        # Exclude PySide6 modules that are not used.
        "--exclude-module", "PySide6.QtNetwork",
        "--exclude-module", "PySide6.QtXml",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.QtQuickWidgets",
        "--exclude-module", "PySide6.QtPrintSupport",
        "--exclude-module", "PySide6.QtSql",
        "--exclude-module", "PySide6.QtTest",
        "--exclude-module", "PySide6.QtSvg",
        "--exclude-module", "PySide6.QtSvgWidgets",
        "--exclude-module", "PySide6.QtHelp",
        "--exclude-module", "PySide6.QtMultimedia",
        "--exclude-module", "PySide6.QtMultimediaWidgets",
        "--exclude-module", "PySide6.QtOpenGL",
        "--exclude-module", "PySide6.QtOpenGLWidgets",
        "--exclude-module", "PySide6.QtPositioning",
        "--exclude-module", "PySide6.QtLocation",
        "--exclude-module", "PySide6.QtSerialPort",
        "--exclude-module", "PySide6.QtWebChannel",
        "--exclude-module", "PySide6.QtWebSockets",
        "--exclude-module", "PySide6.QtWinExtras",
        "--exclude-module", "PySide6.QtNetworkAuth",
        "--exclude-module", "PySide6.QtRemoteObjects",
        "--exclude-module", "PySide6.QtTextToSpeech",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.QtWebEngine",
        "--exclude-module", "PySide6.QtBluetooth",
        "--exclude-module", "PySide6.QtNfc",
        "--exclude-module", "PySide6.QtWebView",
        "--exclude-module", "PySide6.QtCharts",
        "--exclude-module", "PySide6.QtDataVisualization",
        "--exclude-module", "PySide6.QtPdf",
        "--exclude-module", "PySide6.QtPdfWidgets",
        "--exclude-module", "PySide6.QtQuick3D",
        "--exclude-module", "PySide6.QtQuickControls2",
        "--exclude-module", "PySide6.QtQuickParticles",
        "--exclude-module", "PySide6.QtQuickTest",
        "--exclude-module", "PySide6.QtSensors",
        "--exclude-module", "PySide6.QtStateMachine",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.Qt3DRender",
        "--exclude-module", "PySide6.Qt3DInput",
        "--exclude-module", "PySide6.Qt3DLogic",
        "--exclude-module", "PySide6.Qt3DAnimation",
        "--exclude-module", "PySide6.Qt3DExtras",
    ]
    pyinstaller_command.extend(_add_binary_args())
    pyinstaller_command.extend(_add_data_args())
    pyinstaller_command.append("main.py")

    try:
        if os.path.exists(BUILD_DIR):
            shutil.rmtree(BUILD_DIR)
        if os.path.exists(DIST_DIR):
            shutil.rmtree(DIST_DIR)

        print("Running PyInstaller...")
        # Let PyInstaller's dependency resolver find conda's Library\bin DLLs
        # (it does not search that directory by default).
        env = os.environ.copy()
        conda_bin = os.path.join(sys.base_prefix, "Library", "bin")
        if os.path.isdir(conda_bin):
            env["PATH"] = conda_bin + os.pathsep + env.get("PATH", "")
        subprocess.run(pyinstaller_command, check=True, cwd=HERE, env=env)

        _copy_assets_to_dist()

        exe_path = os.path.join(DIST_DIR, "Writing Tools.exe")
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print(f"Build completed successfully: {exe_path} ({size_mb:.1f} MB)")
        else:
            print("Build completed, but the expected exe was not found.")
            sys.exit(1)

        # Clean intermediate build files.
        if os.path.exists(BUILD_DIR):
            shutil.rmtree(BUILD_DIR, ignore_errors=True)

    except subprocess.CalledProcessError as exc:
        print(f"Build failed with error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    run_pyinstaller_build()
