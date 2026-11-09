"""Maintenance commands must not replace existing packages or environments."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def module(relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location("maintenance_" + path.stem, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_package_keeps_previous_archive_unchanged(tmp_path):
    packaging = module("tools/package.py")
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.txt").write_text("package content", encoding="utf-8")
    destination = tmp_path / "releases" / "previous.zip"
    destination.parent.mkdir()
    destination.write_bytes(b"previous archive")
    with pytest.raises(ValueError):
        packaging.package(source, destination, "DataLab")
    assert destination.read_bytes() == b"previous archive"


def test_package_creates_a_separate_valid_archive(tmp_path):
    packaging = module("tools/package.py")
    source = tmp_path / "source"
    source.mkdir()
    original = source / "data.txt"
    original.write_text("package content", encoding="utf-8")
    destination = tmp_path / "releases" / "new.zip"
    result = packaging.package(source, destination, "DataLab")
    with zipfile.ZipFile(destination) as archive:
        assert archive.read("DataLab/data.txt") == b"package content"
    assert result["files"] == 1 and original.read_bytes() == b"package content"


def test_package_refuses_links_into_other_material(tmp_path):
    packaging = module("tools/package.py")
    source = tmp_path / "source"
    source.mkdir()
    private = tmp_path / "private.txt"
    private.write_text("private material", encoding="utf-8")
    try:
        (source / "borrowed.txt").symlink_to(private)
    except OSError as exc:
        pytest.skip(f"Creating symlinks is unavailable: {exc}")
    with pytest.raises(ValueError):
        packaging.package(source, tmp_path / "release" / "new.zip", "DataLab")
    assert not (tmp_path / "release" / "new.zip").exists()


def test_bootstrap_never_installs_into_existing_virtualenv(tmp_path, monkeypatch):
    bootstrap = module("skills/data-lab/scripts/bootstrap.py")
    environment = tmp_path / ".venv"
    environment.mkdir()
    config = environment / "pyvenv.cfg"
    config.write_text("home = existing-environment", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["bootstrap.py", "--venv", str(environment)])

    def no_process(*args, **kwargs):
        pytest.fail("An existing virtual environment must not run installation commands")

    monkeypatch.setattr(bootstrap.subprocess, "run", no_process)
    with pytest.raises(SystemExit) as stopped:
        bootstrap.main()
    assert stopped.value.code == 2
    assert config.read_text(encoding="utf-8") == "home = existing-environment"
    assert list(environment.iterdir()) == [config]


def test_installer_keeps_existing_skill_unchanged(tmp_path, monkeypatch):
    installer = module("tools/install_skill.py")
    destination = tmp_path / "skills"
    previous = destination / "data-lab"
    previous.mkdir(parents=True)
    marker = previous / "SKILL.md"
    marker.write_text("Existing user-customized skill", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["install_skill.py", "--dest", str(destination)])
    with pytest.raises(SystemExit) as stopped:
        installer.main()
    assert stopped.value.code == 2
    assert marker.read_text(encoding="utf-8") == "Existing user-customized skill"
    assert list(previous.iterdir()) == [marker]


def test_installer_copies_into_new_directory_without_changing_source(tmp_path, monkeypatch):
    installer = module("tools/install_skill.py")
    project = tmp_path / "project"
    source = project / "skills" / "data-lab"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("Skill source", encoding="utf-8")
    (source / "scripts").mkdir()
    (source / "scripts" / "run.py").write_text("print('sample')\n", encoding="utf-8")
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    monkeypatch.setattr(installer, "__file__", str(project / "tools" / "install_skill.py"))
    destination = tmp_path / "installed"
    monkeypatch.setattr(sys, "argv", ["install_skill.py", "--dest", str(destination)])
    installer.main()
    for relative, content in before.items():
        assert (source / relative).read_bytes() == content
        assert (destination / "data-lab" / relative).read_bytes() == content


def test_new_bootstrap_uses_only_new_install_target_and_local_cache(tmp_path, monkeypatch):
    bootstrap = module("skills/data-lab/scripts/bootstrap.py")
    destination = tmp_path / "new-environment"
    calls = []
    monkeypatch.setattr(sys, "argv", ["bootstrap.py", "--venv", str(destination), "--parquet"])
    original_environment = dict(os.environ)

    def simulated_process(arguments, **kwargs):
        calls.append((arguments, kwargs))
        if arguments[1:3] == ["-m", "venv"]:
            # Simulate the external writer without installing or networking.
            (destination / "pyvenv.cfg").write_text("new isolated environment", encoding="utf-8")
        assert kwargs["timeout"] > 0
        assert Path(kwargs["env"]["TMP"]).is_relative_to(destination)
        assert Path(kwargs["env"]["MPLCONFIGDIR"]).is_relative_to(destination)
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(bootstrap.subprocess, "run", simulated_process)
    bootstrap.main()
    assert calls[0][0][-1] == str(destination)
    installs = [command for command, _ in calls if "pip" in command]
    assert len(installs) == 2
    assert all("--require-virtualenv" in command and "--no-cache-dir" in command for command in installs)
    assert all(Path(command[0]).is_relative_to(destination) for command in installs)
    audit = json.loads(next((destination / "audit").glob("*.json")).read_text(encoding="utf-8"))
    assert audit["status"] == "completed"
    assert audit["external_writers"] and audit["os_read_only_enforced"] is False
    assert dict(os.environ) == original_environment


def test_sample_generator_creates_new_dataset_and_preserves_previous(tmp_path, monkeypatch):
    generator = module("examples/generate_samples.py")
    output = tmp_path / "samples"
    monkeypatch.setattr(sys, "argv", ["generate_samples.py", "--out", str(output)])
    generator.main()
    before = {name: (output / name).read_bytes() for name in ("sales.csv", "customers.csv", "series.csv")}
    assert all(before.values())
    with pytest.raises(ValueError):
        generator.main()
    assert all((output / name).read_bytes() == content for name, content in before.items())
