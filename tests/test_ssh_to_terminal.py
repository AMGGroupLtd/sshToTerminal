import json
from pathlib import Path

import builtins
import types
import sys

import pytest

# Ensure project root is on sys.path for module import
sys.path.append(str(Path(__file__).resolve().parents[1]))
import ssh_to_terminal as stt


SSH_SAMPLE_ALL = """
# Sample SSH config with all fields
Host Server01
    HostName 1.2.3.4
    User alice
    Port 2222
    IdentityFile /home/alice/.ssh/id_rsa
""".strip()

SSH_SAMPLE_PARTIAL = """
Host Server02
    HostName example.com
# user and other fields omitted
""".strip()

SSH_SAMPLE_MULTI = """
Host web1 web2
    HostName web.example
    User bob
""".strip()


def write(tmp: Path, name: str, content: str) -> Path:
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_parse_ssh_config_all_fields(tmp_path: Path):
    cfg = write(tmp_path, "config", SSH_SAMPLE_ALL)
    hosts = stt.parse_ssh_config(cfg)
    assert len(hosts) == 1
    h = hosts[0]
    assert h.name == "Server01"
    assert h.hostname == "1.2.3.4"
    assert h.user == "alice"
    assert h.port == "2222"
    assert h.identity_file.endswith("id_rsa")
    assert h.commandline == "ssh -p 2222 -i /home/alice/.ssh/id_rsa alice@1.2.3.4"


def test_parse_ssh_config_missing_fields(tmp_path: Path):
    cfg = write(tmp_path, "config", SSH_SAMPLE_PARTIAL)
    hosts = stt.parse_ssh_config(cfg)
    assert len(hosts) == 1
    h = hosts[0]
    assert h.name == "Server02"
    # without user/port/id the command should omit those flags
    assert h.commandline == "ssh example.com"


def test_parse_ssh_config_multiple_hosts_one_block(tmp_path: Path):
    cfg = write(tmp_path, "config", SSH_SAMPLE_MULTI)
    hosts = stt.parse_ssh_config(cfg)
    names = sorted([h.name for h in hosts])
    assert names == ["web1", "web2"]
    for h in hosts:
        assert h.user == "bob"
        assert h.hostname == "web.example"
        assert h.commandline in {"ssh bob@web.example", "ssh bob@web.example"}


def test_discover_ssh_config_files(tmp_path: Path):
    write(tmp_path, "config", SSH_SAMPLE_ALL)
    write(tmp_path, "notes.txt", "not a config")
    sub = tmp_path / "sub"
    write(sub, "cfg", SSH_SAMPLE_PARTIAL)

    files_recursive = stt.discover_ssh_config_files(tmp_path, include_subdirs=True, excludes=[])
    assert {p.name for p in files_recursive} == {"config", "cfg"}

    files_nosub = stt.discover_ssh_config_files(tmp_path, include_subdirs=False, excludes=[])
    assert {p.name for p in files_nosub} == {"config"}

    files_excluded = stt.discover_ssh_config_files(tmp_path, include_subdirs=True, excludes=["cfg"]) \
        
    assert {p.name for p in files_excluded} == {"config"}


def load_example_settings(project_root: Path) -> dict:
    example = project_root / "example.json"
    assert example.exists(), "example.json should be present in project root"
    return json.loads(example.read_text(encoding="utf-8"))


def test_upsert_profiles_into_example(tmp_path: Path):
    # copy example.json to tmp as a base settings
    project_root = Path(__file__).resolve().parents[1]
    base = load_example_settings(project_root)
    profiles = stt.ensure_profiles_container(base)

    # Create two hosts and upsert
    # Use non-confidential, generic sample data consistent with example.json
    h1 = stt.SshHost(name="Dev Server", hostname="1.2.3.4", user="user")
    h2 = stt.SshHost(name="Server01", hostname="1.2.3.4", user="alice", port="2222", identity_file="/home/alice/.ssh/id_rsa")

    stt.upsert_profiles(profiles, [h1, h2])

    # Should override the existing profile with same name and add the new one
    names = {p["name"] for p in profiles}
    assert "Dev Server" in names
    assert "Server01" in names

    # Validate the commandline mapping rules for the sanitized example
    p1 = next(p for p in profiles if p["name"] == "Dev Server")
    assert p1["commandline"] == "ssh user@1.2.3.4"

    p2 = next(p for p in profiles if p["name"] == "Server01")
    assert p2["commandline"] == "ssh -p 2222 -i /home/alice/.ssh/id_rsa alice@1.2.3.4"
    # GUIDs should be deterministic for given name
    assert p2["guid"] == stt.SshHost(name="Server01").guid()


def test_remove_profiles_by_names(tmp_path: Path):
    data = {"profiles": {"list": []}}
    profiles = stt.ensure_profiles_container(data)

    h1 = stt.SshHost(name="A", hostname="a.example")
    h2 = stt.SshHost(name="B", hostname="b.example")
    stt.upsert_profiles(profiles, [h1, h2])

    assert {p["name"] for p in profiles} == {"A", "B"}

    # remove only A
    stt.remove_profiles(profiles, [h1])
    assert {p["name"] for p in profiles} == {"B"}

    # remove remaining via empty names but with source tag
    # mark current B as created by our tool
    profiles[0]["source"] = "sshToTerminal"
    stt.remove_profiles(profiles, [], source_tag="sshToTerminal")
    assert profiles == []
