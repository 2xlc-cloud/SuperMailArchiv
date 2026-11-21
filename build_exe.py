"""Build a standalone Windows executable with the version from app_info.py."""

import sys
from pathlib import Path

from app_info import APP_NAME, VERSION, VERSION_TEXT
from program_icon import ico_icon


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
    icon = build / "SuperMailBackup.ico"
    icon.write_bytes(ico_icon())
    run([
        "--noconfirm", "--clean", "--onefile", "--windowed", "--noupx",
        "--name", APP_NAME, "--version-file", str(resource), "--icon", str(icon),
        "--workpath", str(build / "work"), "--specpath", str(build),
        "--distpath", str(project / "dist"), str(project / "app.py"),
    ])
    write_release_files(project / "dist")


def write_release_files(dist):
    """SHA256SUMS.txt (and its signature, if the signing key exists) for a GitHub release; see README."""
    import hashlib

    exe = dist / f"{APP_NAME}.exe"
    sums = dist / "SHA256SUMS.txt"
    sums.write_text(f"{hashlib.sha256(exe.read_bytes()).hexdigest()} *{exe.name}\n", encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
    import sign_release

    if sign_release.key_path().exists():
        print(f"Signiert: {sign_release.sign_file(sums)}")
    else:
        print(f"Kein Signaturschlüssel ({sign_release.key_path()}): {sums.name} ist nicht signiert; "
              "Updates akzeptieren diese Release nicht.")


if __name__ == "__main__":
    main()
