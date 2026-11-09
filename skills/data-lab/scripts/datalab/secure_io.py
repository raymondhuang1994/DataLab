"""Handle-anchored writes for bundled tools; this does not sandbox other code.

POSIX walks every ancestor with O_NOFOLLOW and uses dir_fd for mkdir/open.
Windows holds every ancestor without FILE_SHARE_WRITE/DELETE, rejects reparse points,
and opens the final file with CREATE_NEW or OPEN_REPARSE_POINT. References:
https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfileinformationbyhandle
https://docs.python.org/3/library/os.html#os.open
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import errno
import os
from pathlib import Path, PureWindowsPath
import re
import stat


def identity(info):
    return (info.st_dev, info.st_ino)


def _check_directory(path, info, expected, forbidden):
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"写入父路径不是普通目录: {path}")
    actual = identity(info)
    if actual in forbidden:
        raise ValueError(f"输出不能进入原始数据目录: {path}")
    if path in expected and actual != expected[path]:
        raise ValueError(f"输出目录在运行期间被替换: {path}")


def _validate_windows_path(path):
    """Reject ADS/device names, whose semantics differ from ordinary files."""
    value = PureWindowsPath(path)
    drive = value.drive
    ordinary_drive = re.fullmatch(r"(?:\\\\\?\\)?[A-Za-z]:", drive)
    unc_drive = drive.startswith("\\\\") and not drive.startswith(("\\\\.\\", "\\\\?\\"))
    extended_unc = drive.lower().startswith("\\\\?\\unc\\")
    if not value.is_absolute() or not (ordinary_drive or unc_drive or extended_unc):
        raise ValueError("Windows 输出必须使用普通磁盘或 UNC 共享的绝对路径。")
    reserved = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]|CONIN\$|CONOUT\$)$", re.I)
    for component in value.parts[1:]:
        if (":" in component or component.endswith((".", " "))
                or reserved.fullmatch(component.split(".", 1)[0])
                or any(ord(char) < 32 for char in component)):
            raise ValueError(f"Windows 输出不能使用设备名、附加数据流或尾部空格/点: {component}")


@contextmanager
def _posix_directory(path, *, expected, forbidden, create, exclusive, created):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    handles = []
    current = Path(path.anchor)
    try:
        handle = os.open(current, flags)
        handles.append(handle)
        _check_directory(current, os.fstat(handle), expected, forbidden)
        parts = path.parts[1:]
        for index, component in enumerate(parts):
            current = current / component
            made = False
            final = index == len(parts) - 1
            if exclusive and final:
                os.mkdir(component, mode=0o700, dir_fd=handle)
                made = True
            try:
                child = os.open(component, flags, dir_fd=handle)
            except FileNotFoundError:
                if not create:
                    raise
                # EEXIST means a concurrent actor won creation; never adopt it.
                os.mkdir(component, mode=0o700, dir_fd=handle)
                made = True
                child = os.open(component, flags, dir_fd=handle)
            handles.append(child)
            handle = child
            info = os.fstat(handle)
            _check_directory(current, info, expected, forbidden)
            if made and created is not None:
                created[current] = identity(info)
        yield handle, os.fstat(handle)
    finally:
        for handle in reversed(handles):
            os.close(handle)


@lru_cache(maxsize=1)
def _windows_api():
    import ctypes
    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD),
                    ("creation", wintypes.FILETIME), ("access", wintypes.FILETIME),
                    ("write", wintypes.FILETIME), ("volume", wintypes.DWORD),
                    ("size_high", wintypes.DWORD), ("size_low", wintypes.DWORD),
                    ("links", wintypes.DWORD), ("index_high", wintypes.DWORD),
                    ("index_low", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    return ctypes, kernel, FileInformation


def _windows_handle(path, *, directory, create=False):
    ctypes, kernel, information_type = _windows_api()
    name = str(path)
    if not name.startswith("\\\\?\\"):
        name = "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name
    # Deny directory write handles as well as delete/rename: changing reparse
    # data through FSCTL_SET_REPARSE_POINT must not redirect subsequent opens.
    access = 0 if directory else 0x80000000 | 0x40000000  # GENERIC_READ | WRITE
    share = 1  # FILE_SHARE_READ only; never FILE_SHARE_WRITE/DELETE
    attributes = 0x00200000 | (0x02000000 if directory else 0x80)
    handle = kernel.CreateFileW(name, access, share, None, 1 if create else 3, attributes, None)
    if handle == ctypes.c_void_p(-1).value:
        code = ctypes.get_last_error()
        if code in (80, 183):
            raise FileExistsError(errno.EEXIST, "Output already exists", str(path))
        raise ctypes.WinError(code)
    try:
        information = information_type()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        if information.attributes & 0x400:
            raise ValueError(f"输出不能使用链接或目录联接: {path}")
        if bool(information.attributes & 0x10) != directory:
            raise ValueError(f"输出文件/目录类型不符: {path}")
        return handle
    except BaseException:
        kernel.CloseHandle(handle)
        raise


@contextmanager
def _windows_directory(path, *, expected, forbidden, create, exclusive, created):
    _validate_windows_path(path)
    _, kernel, _ = _windows_api()
    handles = []
    current = Path(path.anchor)
    try:
        handle = _windows_handle(current, directory=True)
        handles.append(handle)
        # All ancestors and this directory are now held against rename/delete;
        # os.stat gives the same native identity representation as os.fstat.
        _check_directory(current, current.stat(), expected, forbidden)
        parts = path.parts[1:]
        for index, component in enumerate(parts):
            current = current / component
            made = False
            if exclusive and index == len(parts) - 1:
                current.mkdir(mode=0o700)
                made = True
            try:
                handle = _windows_handle(current, directory=True)
            except FileNotFoundError:
                if not create:
                    raise
                current.mkdir(mode=0o700)
                made = True
                handle = _windows_handle(current, directory=True)
            handles.append(handle)
            info = current.stat()
            _check_directory(current, info, expected, forbidden)
            if made and created is not None:
                created[current] = identity(info)
        yield None, current.stat()
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(handle)


@contextmanager
def locked_directory(path, *, expected=None, forbidden=(), create=False, exclusive=False, created=None):
    """Hold verified ancestors throughout the caller's relative write."""
    path = Path(os.path.abspath(path))
    implementation = _windows_directory if os.name == "nt" else _posix_directory
    try:
        with implementation(path, expected=expected or {}, forbidden=set(forbidden),
                            create=create, exclusive=exclusive, created=created) as result:
            yield result
    except FileExistsError as exc:
        raise ValueError(f"其他进程已创建输出文件或目录，不能覆盖或接管: {exc.filename}") from exc
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError(f"输出目录包含链接或被替换: {path}") from exc
        raise


@contextmanager
def locked_file(path, *, create, expected=None, forbidden=()):
    path = Path(os.path.abspath(path))
    if os.name == "nt":
        _validate_windows_path(path)
    with locked_directory(path.parent, expected=expected, forbidden=forbidden) as (parent_fd, _):
        if os.name == "nt":
            import msvcrt
            _, kernel, _ = _windows_api()
            handle = _windows_handle(path, directory=False, create=create)
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_NOINHERIT)
            except BaseException:
                kernel.CloseHandle(handle)
                raise
        else:
            flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            if create:
                flags |= os.O_CREAT | os.O_EXCL
            descriptor = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
        with os.fdopen(descriptor, "r+b") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError(f"输出必须是无硬链接的普通文件: {path}")
            yield stream
