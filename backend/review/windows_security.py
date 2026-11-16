"""Current Windows logon identity and user-bound DPAPI; no password collection."""
import ctypes
from ctypes import wintypes
import os


def _libraries():
    if os.name != 'nt':
        raise RuntimeError('WINDOWS_LOGON_REQUIRED')
    return (ctypes.WinDLL('kernel32', use_last_error=True),
            ctypes.WinDLL('advapi32', use_last_error=True),
            ctypes.WinDLL('crypt32', use_last_error=True))


def current_actor():
    kernel, security, _ = _libraries()
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise RuntimeError('WINDOWS_IDENTITY_UNAVAILABLE')
    try:
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise RuntimeError('WINDOWS_IDENTITY_UNAVAILABLE')
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        value = wintypes.LPWSTR()
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            raise RuntimeError('WINDOWS_IDENTITY_UNAVAILABLE')
        try:
            return 'windows-logon:' + value.value
        finally:
            kernel.LocalFree(ctypes.cast(value, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


class WindowsProtection:
    """DPAPI user scope (never CRYPTPROTECT_LOCAL_MACHINE), with integrity checks."""
    def __init__(self):
        self.kernel, _, self.crypto = _libraries()
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        common = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                  ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        self.crypto.CryptProtectData.argtypes = common
        self.crypto.CryptUnprotectData.argtypes = common

    def _apply(self, raw, decrypt):
        buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        source, result = _Blob(len(raw), buf), _Blob()
        function = self.crypto.CryptUnprotectData if decrypt else self.crypto.CryptProtectData
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise RuntimeError('REVIEW_PRIVATE_DATA_UNAVAILABLE')
        try:
            return ctypes.string_at(result.data, result.size)
        finally:
            self.kernel.LocalFree(result.data)

    def protect(self, raw):
        return self._apply(raw, False)

    def unprotect(self, raw):
        return self._apply(raw, True)
