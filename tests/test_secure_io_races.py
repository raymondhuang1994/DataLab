"""Actual parent swaps must never write files/directories into raw inputs."""
import os
from pathlib import Path

import pytest

from datalab.safety import output_session, safe_write_bytes
from datalab import secure_io


def raw_file(tmp_path):
    raw = tmp_path / "inputs"
    raw.mkdir()
    source = raw / "source.csv"
    source.write_bytes(b"value\n1\n")
    return source


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir_fd race; Windows handle locks tested separately")
def test_file_open_stays_on_directory_handle_after_parent_swap(tmp_path, monkeypatch):
    source = raw_file(tmp_path)
    output = tmp_path / "task"
    fired = []
    real_open = secure_io.os.open
    with pytest.raises(ValueError):
        with output_session(output, raw_inputs=[source], directory=True) as session:
            parent = session.ensure_directory(output / "tables")
            moved = output / "original-tables"

            def raced_open(path, flags, *args, **kwargs):
                if Path(path).name == "new.csv" and flags & os.O_CREAT and not fired:
                    fired.append(True)
                    parent.rename(moved)
                    parent.symlink_to(source.parent, target_is_directory=True)
                return real_open(path, flags, *args, **kwargs)

            monkeypatch.setattr(secure_io.os, "open", raced_open)
            safe_write_bytes(parent / "new.csv", b"owned new data")
    assert fired
    assert source.read_bytes() == b"value\n1\n"
    assert not (source.parent / "new.csv").exists()
    assert (output / "original-tables/new.csv").read_bytes() == b"owned new data"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mkdirat race; Windows handle locks tested separately")
def test_mkdir_stays_on_directory_handle_after_parent_swap(tmp_path, monkeypatch):
    source = raw_file(tmp_path)
    output = tmp_path / "task"
    fired = []
    real_mkdir = secure_io.os.mkdir
    with pytest.raises(ValueError):
        with output_session(output, raw_inputs=[source], directory=True) as session:
            parent = session.ensure_directory(output / "tables")
            moved = output / "original-tables"

            def raced_mkdir(path, *args, **kwargs):
                if Path(path).name == "new-child" and kwargs.get("dir_fd") is not None and not fired:
                    fired.append(True)
                    parent.rename(moved)
                    parent.symlink_to(source.parent, target_is_directory=True)
                return real_mkdir(path, *args, **kwargs)

            monkeypatch.setattr(secure_io.os, "mkdir", raced_mkdir)
            safe_write_bytes(parent / "new-child/new.csv", b"owned new data")
    assert fired
    assert source.read_bytes() == b"value\n1\n"
    assert not (source.parent / "new-child").exists()
    assert (output / "original-tables/new-child").is_dir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX root traversal race; Windows ancestor handles are locked")
def test_root_swap_during_secure_traversal_cannot_redirect_output(tmp_path, monkeypatch):
    source = raw_file(tmp_path)
    output = tmp_path / "task"
    fired = []
    real_open = secure_io.os.open
    with pytest.raises(ValueError):
        with output_session(output, raw_inputs=[source], directory=True):
            def raced_open(path, flags, *args, **kwargs):
                if str(path) == "task" and flags & os.O_DIRECTORY and not fired:
                    fired.append(True)
                    output.rename(tmp_path / "original-task")
                    output.symlink_to(source.parent, target_is_directory=True)
                return real_open(path, flags, *args, **kwargs)

            monkeypatch.setattr(secure_io.os, "open", raced_open)
            safe_write_bytes(output / "new.csv", b"owned new data")
    assert fired
    assert source.read_bytes() == b"value\n1\n"
    assert not (source.parent / "new.csv").exists()
    assert not (source.parent / "audit").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows rename-denying ancestor handles")
def test_windows_parent_is_locked_against_rename_until_file_write_finishes(tmp_path, monkeypatch):
    source = raw_file(tmp_path)
    output = tmp_path / "task"
    observed = []
    write_handles = []
    real_handle = secure_io._windows_handle
    with output_session(output, raw_inputs=[source], directory=True) as session:
        parent = session.ensure_directory(output / "tables")

        def attempt_rename(path, *, directory, create=False):
            if not directory and Path(path).name == "new.csv":
                ctypes, kernel, _ = secure_io._windows_api()
                attempted = kernel.CreateFileW(str(parent), 0x40000000, 1 | 2 | 4,
                                               None, 3, 0x02000000 | 0x00200000, None)
                if attempted == ctypes.c_void_p(-1).value:
                    write_handles.append("blocked")
                else:
                    kernel.CloseHandle(attempted)
                    write_handles.append("opened")
                try:
                    parent.rename(output / "moved")
                except OSError:
                    observed.append("blocked")
                else:
                    observed.append("renamed")
            return real_handle(path, directory=directory, create=create)

        monkeypatch.setattr(secure_io, "_windows_handle", attempt_rename)
        safe_write_bytes(parent / "new.csv", b"owned new data")
    assert observed == ["blocked"]
    assert write_handles == ["blocked"]
    assert source.read_bytes() == b"value\n1\n"
    assert not (source.parent / "new.csv").exists()
    assert (output / "tables/new.csv").read_bytes() == b"owned new data"


@pytest.mark.parametrize("path", [r"C:\out\data.csv:other", r"C:\out\NUL.txt",
                                  r"C:\out\CON", r"C:\out\trailing.\file.txt",
                                  r"C:\out\trailing \file.txt", r"\\.\NUL"])
def test_windows_stream_device_and_normalization_aliases_are_rejected(path):
    with pytest.raises(ValueError):
        secure_io._validate_windows_path(path)


@pytest.mark.parametrize("path", [r"C:\数据 分析\报告.docx", r"\\server\share\report.txt",
                                  r"\\?\C:\long\report.txt", r"\\?\UNC\server\share\report.txt"])
def test_normal_windows_disk_and_share_paths_are_accepted(path):
    secure_io._validate_windows_path(path)
