"""Preview images of attachments (JPEG, PDF, …) from the Windows shell, the same ones Explorer shows.

Tk can only decode PNG and GIF, so the shell's thumbnail handlers produce a bitmap that is converted to PNG.
"""
import base64
import ctypes
import struct
import subprocess
import uuid
import zlib
from ctypes import wintypes
from pathlib import Path


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text):
        value = uuid.UUID(text)
        guid = cls(value.fields[0], value.fields[1], value.fields[2])
        guid.Data4[:] = value.bytes[8:]
        return guid


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


IID_IMAGE_FACTORY = GUID.parse("bcc18b79-ba16-442f-80c4-8a59c30c463b")  # IShellItemImageFactory
SIIGBF_THUMBNAILONLY = 0x08  # Fail instead of returning a file type icon.
GET_IMAGE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, SIZE, ctypes.c_int,
                               ctypes.POINTER(wintypes.HBITMAP))
RELEASE = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)

ole32 = ctypes.WinDLL("ole32")
shell32 = ctypes.WinDLL("shell32")
gdi32 = ctypes.WinDLL("gdi32")
user32 = ctypes.WinDLL("user32")
ole32.CoInitializeEx.argtypes, ole32.CoInitializeEx.restype = (ctypes.c_void_p, wintypes.DWORD), ctypes.c_long
ole32.CoUninitialize.argtypes, ole32.CoUninitialize.restype = (), None
shell32.SHCreateItemFromParsingName.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID),
                                                ctypes.POINTER(ctypes.c_void_p))
shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
gdi32.GetObjectW.argtypes, gdi32.GetObjectW.restype = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p), ctypes.c_int
gdi32.GetDIBits.argtypes = (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT)
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes, gdi32.DeleteObject.restype = (wintypes.HGDIOBJ,), wintypes.BOOL
user32.GetDC.argtypes, user32.GetDC.restype = (wintypes.HWND,), wintypes.HDC
user32.ReleaseDC.argtypes, user32.ReleaseDC.restype = (wintypes.HWND, wintypes.HDC), ctypes.c_int


def shell_image(path, width, height):
    """PNG bytes of the shell preview of a file, fitted into width × height; None if there is none.

    Runs COM on the calling thread, so it can be used from a worker thread.
    """
    initialized = ole32.CoInitializeEx(None, 0x2) >= 0  # COINIT_APARTMENTTHREADED
    try:
        factory = ctypes.c_void_p()
        if shell32.SHCreateItemFromParsingName(str(path), None, ctypes.byref(IID_IMAGE_FACTORY),
                                               ctypes.byref(factory)) < 0 or not factory:
            return None
        vtable = ctypes.cast(factory, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        try:
            bitmap = wintypes.HBITMAP()
            if GET_IMAGE(vtable[3])(factory, SIZE(width, height), SIIGBF_THUMBNAILONLY,
                                    ctypes.byref(bitmap)) < 0 or not bitmap:
                return None
            try:
                return _png_from_bitmap(bitmap)
            finally:
                gdi32.DeleteObject(bitmap)
        finally:
            RELEASE(vtable[2])(factory)
    except OSError:
        return None
    finally:
        if initialized:
            ole32.CoUninitialize()


# First PDF page through the PDF renderer built into Windows 10/11 (Windows.Data.Pdf), via Windows PowerShell.
PDF_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$methods = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 }
$asTask = $methods | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } |
    Select-Object -First 1
$asAction = $methods | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } |
    Select-Object -First 1
function Await($operation, [type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation)); $task.Wait(); $task.Result }
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
[void][Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime]
[void][Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Pdf)) ([Windows.Storage.StorageFile])
$document = Await ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
[Console]::Out.WriteLine($document.PageCount)
for ($index = 0; $index -lt [Math]::Min($document.PageCount, $Pages); $index++) {
    $page = $document.GetPage($index)
    $options = New-Object Windows.Data.Pdf.PdfPageRenderOptions
    # $Height 0: fit the width only (pages below each other in a scrollable window).
    if ($Height -le 0 -or $page.Size.Width / $page.Size.Height -gt $Width / $Height) {
        $options.DestinationWidth = $Width } else { $options.DestinationHeight = $Height }
    $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    $asAction.Invoke($null, @($page.RenderToStreamAsync($stream, $options))).Wait()
    $source = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream.GetInputStreamAt(0))
    $output = [System.IO.File]::Create("$Png-$index.png"); $source.CopyTo($output); $output.Close()
}
"""


def pdf_pages(path, width, height=0, pages=1):
    """PNG bytes of the first pages of a PDF and its total page count, via Windows.Data.Pdf.

    height 0 fits the width only. Returns ([], 0) if Windows cannot render the file.
    """
    target = Path(path).with_suffix(".seite")

    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    script = (f"$Pdf = {quote(path)}; $Png = {quote(target)}; $Width = {int(width)}; "
              f"$Height = {int(height)}; $Pages = {int(pages)}" + PDF_SCRIPT)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
            capture_output=True, timeout=30 + 5 * pages, check=True, creationflags=0x08000000)  # No console.
        images = []
        for index in range(pages):
            page = Path(f"{target}-{index}.png")
            if not page.exists():
                break
            images.append(page.read_bytes())
        total = int(result.stdout.split()[0]) if result.stdout.split() else len(images)
        return images, total
    except (OSError, ValueError, subprocess.SubprocessError):
        return [], 0
    finally:
        for page in target.parent.glob(target.name + "-*.png"):
            page.unlink(missing_ok=True)


def pdf_page_image(path, width, height):
    """PNG bytes of the first page of a PDF fitted into width × height; None if Windows cannot render it."""
    images, _ = pdf_pages(path, width, height, 1)
    return images[0] if images else None


def preview_image(path, width, height):
    """Shell preview (images, and PDFs if a preview handler is installed); PDFs fall back to Windows.Data.Pdf."""
    image = shell_image(path, width, height)
    if image is None and Path(path).suffix.lower() == ".pdf":
        image = pdf_page_image(path, width, height)
    return image


def _png_from_bitmap(bitmap):
    info = BITMAP()
    if not gdi32.GetObjectW(bitmap, ctypes.sizeof(info), ctypes.byref(info)):
        return None
    width, height = info.bmWidth, abs(info.bmHeight)
    if not width or not height:
        return None
    header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=width, biHeight=-height,
                              biPlanes=1, biBitCount=32)  # Top-down 32-bit BGRA.
    pixels = (ctypes.c_ubyte * (width * height * 4))()
    screen = user32.GetDC(None)
    try:
        lines = gdi32.GetDIBits(screen, bitmap, 0, height, pixels, ctypes.byref(header), 0)
    finally:
        user32.ReleaseDC(None, screen)
    if lines != height:
        return None
    data = bytes(pixels)
    opaque = not any(data[3::4])  # Thumbnails without transparency leave the alpha channel at 0.
    rows = bytearray()
    stride = width * 4
    for y in range(height):
        line = bytearray(data[y * stride:(y + 1) * stride])
        line[0::4], line[2::4] = line[2::4], line[0::4]  # BGRA → RGBA
        if opaque:
            line[3::4] = b"\xff" * width
        rows.append(0)
        rows.extend(line)
    return encode_png(width, height, bytes(rows))


def encode_png(width, height, rows):
    """PNG from RGBA scanlines that each start with filter byte 0."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 6))
            + chunk(b"IEND", b""))
