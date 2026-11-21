"""Protect account records with Windows DPAPI for the current Windows user."""
import ctypes
from ctypes import wintypes


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _transform(data, protect):
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    operation.argtypes = (
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.POINTER(DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)
    )
    operation.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = (ctypes.c_void_p,)
    local_free.restype = ctypes.c_void_p
    source_bytes = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = DataBlob(len(data), source_bytes)
    output = DataBlob()
    try:
        # CRYPTPROTECT_UI_FORBIDDEN. The default scope is the Windows user.
        if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(source_bytes, 0, len(data))
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            local_free(output.data)


def seal(data):
    return _transform(data, protect=True)


def unseal(data):
    return _transform(data, protect=False)
