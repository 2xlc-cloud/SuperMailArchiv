"""Build a standalone Windows executable with the version from app_info.py."""

import sys
from pathlib import Path

from app_info import APP_NAME, VERSION, VERSION_TEXT


def version_resource():
    windows_version = (*VERSION, 0)
    return f"""VSVersionInfo(
    ffi=FixedFileInfo(
        filevers={windows_version!r}, prodvers={windows_version!r},
        mask=0x3f, flags=0, OS=0x40004, fileType=0x1, subtype=0, date=(0, 0)
    ),
    kids=[
        StringFileInfo([StringTable('040704B0', [
            StringStruct('FileDescription', {APP_NAME!r}),
            StringStruct('FileVersion', {VERSION_TEXT!r}),
            StringStruct('InternalName', {APP_NAME!r}),
            StringStruct('OriginalFilename', {APP_NAME + '.exe'!r}),
            StringStruct('ProductName', {APP_NAME!r}),
            StringStruct('ProductVersion', {VERSION_TEXT!r}),
        ])]),
        VarFileInfo([VarStruct('Translation', [1031, 1200])])
    ]
)
"""


def main():
    if sys.platform != "win32":
        raise SystemExit("Die Windows-EXE muss unter Windows gebaut werden.")
    try:
        from PyInstaller.__main__ import run
    except ModuleNotFoundError as error:
        if error.name != "PyInstaller":
            raise
        raise SystemExit("Bitte PyInstaller installieren: python -m pip install pyinstaller") from None

    project = Path(__file__).resolve().parent
    build = project / "build"
    build.mkdir(exist_ok=True)
    resource = build / "version_info.txt"
    resource.write_text(version_resource(), encoding="utf-8")
    run([
        "--noconfirm", "--clean", "--onefile", "--windowed", "--noupx",
        "--name", APP_NAME, "--version-file", str(resource),
        "--workpath", str(build / "work"), "--specpath", str(build),
        "--distpath", str(project / "dist"), str(project / "app.py"),
    ])


if __name__ == "__main__":
    main()
