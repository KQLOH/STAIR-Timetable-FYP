"""User-local Windows storage with protected ACLs and no reparse traversal."""
from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import stat
import uuid

from backend.review.models import ReviewError
from backend.review.windows_security import current_actor

REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT, independent of host stat exports


def is_reparse(path):
    try:
        return bool(path.lstat().st_file_attributes & REPARSE)
    except FileNotFoundError:
        return False


def user_local_root():
    """Windows Known Folder API, rather than a caller-controlled environment."""
    if os.name != 'nt':
        raise ReviewError('WINDOWS_LOGON_REQUIRED', 422)
    class GUID(ctypes.Structure):
        _fields_ = [('a', wintypes.DWORD), ('b', wintypes.WORD), ('c', wintypes.WORD), ('d', ctypes.c_ubyte * 8)]
    folder = GUID.from_buffer_copy(uuid.UUID('F1B32785-6FBA-4FCF-9D55-7B8E7F157091').bytes_le)
    shell, ole = ctypes.WinDLL('shell32'), ctypes.WinDLL('ole32')
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(wintypes.LPWSTR)]
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    value = wintypes.LPWSTR()
    if shell.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(value)):
        raise ReviewError('REVIEW_LOCAL_ROOT_UNAVAILABLE', 503)
    try:
        return Path(value.value)
    finally:
        ole.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))


class _Attributes(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', wintypes.BOOL)]


class WindowsDirectories:
    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.security = ctypes.WinDLL('advapi32', use_last_error=True)
        k, s = self.kernel, self.security
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k.CreateFileW.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        k.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_Attributes)]
        k.LocalFree.argtypes = [ctypes.c_void_p]
        s.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        s.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
        s.SetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]

    @contextmanager
    def descriptor(self):
        sid = current_actor().removeprefix('windows-logon:')
        sd = ctypes.c_void_p()
        # Exact protected DACL; current logon and SYSTEM only, inherited by files
        # and subdirectories. Do not grant Everyone/Users or rely on umask.
        if not self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f'D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)', 1, ctypes.byref(sd), None):
            raise OSError(ctypes.get_last_error(), 'Private security descriptor unavailable')
        try:
            yield sd
        finally:
            self.kernel.LocalFree(sd)

    @contextmanager
    def open_directory(self, path, *, writable_acl=False):
        access = 0x80 | (0x40000 | 0x20000 if writable_acl else 0)
        # Open the reparse point itself, never its target. Disallow rename/delete
        # while validation/initialization is using this directory.
        handle = self.kernel.CreateFileW(str(path), access, 3, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), 'Storage directory access refused')
        try:
            tags = (wintypes.DWORD * 2)()
            if not self.kernel.GetFileInformationByHandleEx(handle, 9, tags, ctypes.sizeof(tags)):
                raise OSError(ctypes.get_last_error(), 'Storage attributes unavailable')
            if tags[0] & REPARSE or not tags[0] & 0x10:
                raise ReviewError('REVIEW_STORAGE_PATH_INVALID', 422)
            value = ctypes.create_unicode_buffer(32768)
            size = self.kernel.GetFinalPathNameByHandleW(handle, value, len(value), 0)
            if not size or size >= len(value):
                raise OSError(ctypes.get_last_error(), 'Storage path unavailable')
            text = value.value
            if not text.startswith('\\\\?\\') or text.startswith('\\\\?\\UNC\\'):
                raise ReviewError('REVIEW_STORAGE_PATH_INVALID', 422)
            yield handle, Path(text[4:])
        finally:
            self.kernel.CloseHandle(handle)

    def create(self, path, sd):
        attrs = _Attributes(ctypes.sizeof(_Attributes), sd, False)
        if not self.kernel.CreateDirectoryW(str(path), ctypes.byref(attrs)):
            error = ctypes.get_last_error()
            if error != 183:  # Existing objects must still pass handle checks.
                raise OSError(error, 'Private directory creation refused')

    def restrict(self, path, sd):
        present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not self.security.GetSecurityDescriptorDacl(sd, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)) or not present:
            raise OSError('Private DACL unavailable')
        # Checked before this function is called; the handle pins the object.
        handle = self.kernel.CreateFileW(str(path), 0x60080, 3, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), 'Private ACL access refused')
        try:
            tags = (wintypes.DWORD * 2)()
            if not self.kernel.GetFileInformationByHandleEx(handle, 9, tags, ctypes.sizeof(tags)) or tags[0] & REPARSE:
                raise ReviewError('REVIEW_STORAGE_PATH_INVALID', 422)
            status = self.security.SetSecurityInfo(handle, 1, 0x80000004, None, None, acl, None)
            if status:
                raise OSError(status, 'Private ACL application refused')
        finally:
            self.kernel.CloseHandle(handle)


def _lexical(path, root):
    path = Path(path)
    if (not path.is_absolute() or str(path).startswith('\\\\') or
            path == root or not path.is_relative_to(root)):
        raise ReviewError('REVIEW_STORAGE_OUTSIDE_LOCAL_ROOT', 422)
    for part in path.parts[1:]:
        name = part.split('.')[0].upper()
        if (part in {'.', '..'} or part.endswith((' ', '.')) or
                any(c in part for c in '<>:"|?*') or any(ord(c) < 32 for c in part) or
                name in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} or
                name in {f'{prefix}{n}' for prefix in ('COM', 'LPT') for n in '123456789¹²³'}):
            raise ReviewError('REVIEW_STORAGE_PATH_INVALID', 422)
    return path


def _chain(path):
    return list(reversed(path.parents)) + [path]


def _tree(directory):
    pending, result = [directory], []
    links = {}
    while pending:
        path = pending.pop()
        info = path.lstat()
        if info.st_file_attributes & REPARSE:
            raise ReviewError('REVIEW_STORAGE_PATH_INVALID', 422)
        result.append(path)
        if len(result) > 40000:
            raise ReviewError('REVIEW_STORAGE_CAPACITY', 413)
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        elif info.st_nlink > 1:
            key = (info.st_dev, info.st_ino)
            count, expected = links.get(key, (0, info.st_nlink))
            links[key] = (count + 1, expected)
    # Journal publication deliberately hard-links a staging file to a record.
    # Reject links with names outside this tree before changing their ACLs.
    if any(count != expected for count, expected in links.values()):
        raise ReviewError('REVIEW_STORAGE_PATH_INVALID', 422)
    return result


@contextmanager
def writer_lock(directory):
    import msvcrt
    path = directory / 'writer.lock'
    if is_reparse(path) or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
        raise ReviewError('REVIEW_STORAGE_PATH_INVALID', 422)
    with path.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise ReviewError('REVIEW_STORAGE_BUSY', 409) from None
        try:
            yield
        finally:
            handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def initialize_private_directory(directory):
    """Create first, then canonicalize verified handles (Store Python redirects)."""
    root = user_local_root()
    requested = _lexical(directory, root)
    api = WindowsDirectories()
    try:
        with ExitStack() as held, api.descriptor() as sd:
            # Existing ancestors must be real directories before any creation.
            for ancestor in _chain(root):
                held.enter_context(api.open_directory(ancestor))
            current = root
            for part in requested.relative_to(root).parts:
                candidate = current / part
                if not candidate.exists() and not is_reparse(candidate):
                    api.create(candidate, sd)
                _, actual = held.enter_context(api.open_directory(candidate))
                _lexical(actual, root)
                # Resolve-time changes are permitted only within the trusted
                # user-local root, with no reparse points in the physical chain.
                for ancestor in _chain(actual):
                    held.enter_context(api.open_directory(ancestor))
                current = actual
            records = current / 'records'
            if not records.exists() and not is_reparse(records):
                api.create(records, sd)
            _, actual_records = held.enter_context(api.open_directory(records))
            if actual_records != records:
                raise ReviewError('REVIEW_STORAGE_PATH_INVALID', 422)
            # Coordinate with an already running review host before touching
            # ACLs or enumerating its staged/committed hard links.
            with writer_lock(current):
                entries = _tree(current)
                for path in entries:
                    api.restrict(path, sd)
            return current
    except OSError:
        raise ReviewError('REVIEW_STORAGE_ACCESS_DENIED', 503) from None


def validate_storage_paths(directory):
    root = user_local_root()
    _lexical(directory, root)
    for path in _chain(directory) + [directory / 'records']:
        info = path.lstat()
        if info.st_file_attributes & REPARSE or not stat.S_ISDIR(info.st_mode):
            raise ReviewError('REVIEW_STORAGE_PATH_INVALID', 422)
