import argparse
import json
import logging
import os
import platform
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore

try:
    import jsonschema  # type: ignore
except Exception:  # pragma: no cover
    jsonschema = None  # type: ignore

__author__ = "Matt Lowe"
__email__ = "marl.scot.1@googlemail.com"
__version__ = "1.1.2"
__license__ = "MIT"


HOST_LINE_RE = re.compile(r"^Host\s+(?P<patterns>.+)$", re.IGNORECASE)
KV_RE = re.compile(r"^(?P<key>\w+)\s+(?P<value>.+)$")
SUPPORTED_KEYS = {"hostname", "user", "port", "identityfile"}


@dataclass
class SshHost:
    name: str
    hostname: Optional[str] = None
    user: Optional[str] = None
    port: Optional[str] = None
    identity_file: Optional[str] = None

    @property
    def commandline(self) -> str:
        parts: List[str] = ["ssh"]
        if self.port:
            parts += ["-p", self.port]
        if self.identity_file:
            parts += ["-i", self.identity_file]
        target = self.hostname or self.name
        if self.user:
            target = f"{self.user}@{target}"
        parts.append(target)
        return " ".join(parts)

    def guid(self) -> str:
        # Deterministic GUID so updates overwrite the same profile consistently
        ns = uuid.UUID("12345678-1234-5678-1234-567812345678")
        return str(uuid.uuid5(ns, f"ssh-to-terminal:{self.name}"))


def find_default_settings_path() -> Path:
    # Windows native
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app) / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json"
    # WSL / POSIX: try to resolve Windows user profile mounted under /mnt/c
    user = os.environ.get("USERNAME") or os.environ.get("USER") or os.getlogin()
    candidates = [
        Path(f"/mnt/c/Users/{user}/AppData/Local/Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json"),
        Path.home() / "AppData/Local/Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    # Fallback to typical Windows path under mounted C drive
    return candidates[0]


def discover_ssh_config_files(ssh_dir: Path, include_subdirs: bool, excludes: Iterable[str]) -> List[Path]:
    exclude_names = {os.path.basename(e) for e in excludes}
    files: List[Path] = []
    if include_subdirs:
        for p in ssh_dir.rglob("*"):
            if p.is_file() and p.name not in exclude_names and looks_like_ssh_config(p):
                files.append(p)
    else:
        if ssh_dir.exists():
            for p in ssh_dir.iterdir():
                if p.is_file() and p.name not in exclude_names and looks_like_ssh_config(p):
                    files.append(p)
    return files


def looks_like_ssh_config(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if HOST_LINE_RE.match(line.rstrip("\n")):
                    return True
    except Exception:
        return False
    return False


def parse_ssh_config(path: Path) -> List[SshHost]:
    hosts: List[SshHost] = []
    current_names: List[str] = []
    current_block: Dict[str, str] = {}

    def flush():
        nonlocal current_names, current_block
        if not current_names:
            return
        # Build SshHost for each non-wildcard name
        for hn in current_names:
            if any(ch in hn for ch in ("*", "?")):
                continue
            hosts.append(
                SshHost(
                    name=hn,
                    hostname=current_block.get("hostname"),
                    user=current_block.get("user"),
                    port=current_block.get("port"),
                    identity_file=current_block.get("identityfile"),
                )
            )
        current_names = []
        current_block = {}

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip("\n")
            if not line.strip() or line.strip().startswith("#"):
                continue
            m = HOST_LINE_RE.match(line)
            if m:
                # new host block
                flush()
                patterns = m.group("patterns").split()
                current_names = patterns
                continue
            km = KV_RE.match(line.strip())
            if km:
                key = km.group("key").lower()
                val = km.group("value").strip()
                if key in SUPPORTED_KEYS:
                    current_block[key] = val
    flush()
    return hosts


def load_settings(settings_path: Path) -> Dict:
    if settings_path.exists():
        with settings_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    # initialize a minimal structure
    return {
        "$schema": "https://aka.ms/terminal-profiles-schema",
        "profiles": {"defaults": {}, "list": []},
        "actions": [],
        "schemes": [],
        "themes": [],
    }


def save_settings(settings_path: Path, data: Dict, validate: bool = True, schema_url_fallback: Optional[str] = None) -> None:
    if validate and jsonschema is not None:
        schema_url = data.get("$schema") or schema_url_fallback
        if schema_url and requests is not None:
            try:
                resp = requests.get(schema_url, timeout=10)
                if resp.ok:
                    schema = resp.json()
                    jsonschema.validate(instance=data, schema=schema)
            except Exception:
                # Best-effort validation
                pass
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with settings_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
        f.write("\n")


def ensure_profiles_container(data: Dict) -> List[Dict]:
    prof = data.get("profiles")
    if not isinstance(prof, dict):
        prof = {}
        data["profiles"] = prof
    lst = prof.get("list")
    if not isinstance(lst, list):
        lst = []
        prof["list"] = lst
    return lst


def upsert_profiles(profiles: List[Dict], ssh_hosts: List[SshHost], source_tag: str = "sshToTerminal") -> None:
    by_name: Dict[str, int] = {p.get("name"): i for i, p in enumerate(profiles) if isinstance(p, dict) and p.get("name")}
    for host in ssh_hosts:
        profile = {
            "name": host.name,
            "commandline": host.commandline,
            "guid": f"{{{host.guid()}}}",
            "hidden": False,
            # intentionally omitting 'source' to comply with Windows Terminal auto-generated profile rules
        }
        if host.name in by_name:
            profiles[by_name[host.name]] = profile
        else:
            profiles.append(profile)


def remove_profiles(profiles: List[Dict], ssh_hosts: List[SshHost], source_tag: str = "sshToTerminal") -> None:
    names = {h.name for h in ssh_hosts}
    keep: List[Dict] = []
    for p in profiles:
        if not isinstance(p, dict):
            keep.append(p)
            continue
        n = p.get("name")
        if n in names:
            # drop profiles that match names from provided ssh_hosts
            continue
        if not names:
            # When no specific names provided, remove profiles that look like ours.
            # Detection heuristic: deterministic GUID derived from profile name using our namespace.
            g = p.get("guid")
            if isinstance(n, str) and isinstance(g, str):
                try:
                    expected = f"{{{SshHost(name=n).guid()}}}"
                    if g == expected:
                        # looks like a profile created by this tool; drop it
                        continue
                except Exception:
                    pass
        # otherwise, keep
        keep.append(p)
    profiles[:] = keep


def load_schema_fallback_from_example(example_path: Optional[Path]) -> Optional[str]:
    if not example_path or not example_path.exists():
        return None
    try:
        with example_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("$schema")
    except Exception:
        return None


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    # Pull metadata if present (user said they added these to the file). Fallbacks are provided.
    author = globals().get("__author__", "Unknown")
    email = globals().get("__email__", "unknown@example.com")
    version = globals().get("__version__", "0.0.0")
    license_name = globals().get("__license__", "UNLICENSED")

    p = argparse.ArgumentParser(
        description="Add SSH hosts as profiles to Windows Terminal settings.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            f"Author: {author}\n"
            f"Email:  {email}\n"
            f"Version: {version}\n"
            f"License: {license_name}"
        ),
    )
    p.add_argument("-d", "--debug", action="store_true", help="Show debug messages")
    p.add_argument("-s", "--ssh-dir", default=str(Path.home() / ".ssh"), help="SSH directory to scan")
    p.add_argument("-n", "--nosubdir", action="store_true", help="Do not search subdirectories of the SSH directory")
    p.add_argument("-t", "--terminal", default=None, help="Path to Windows Terminal settings.json")
    p.add_argument("-a", "--add", action="store_true", help="Add SSH config hosts to settings.json")
    p.add_argument("-r", "--remove", action="store_true", help="Remove SSH config hosts from settings.json")
    p.add_argument("-e", "--exclude", action="append", default=[], help="Exclude specified SSH config file names (can be given multiple times)")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(levelname)s: %(message)s")

    if not args.add and not args.remove:
        logging.error("No action specified. Use --add and/or --remove.")
        return 2

    ssh_dir = Path(os.path.expanduser(args.ssh_dir))
    include_subdirs = not args.nosubdir
    settings_path = Path(args.terminal) if args.terminal else find_default_settings_path()

    logging.debug(f"Scanning SSH dir: {ssh_dir} (recursive={include_subdirs})")
    files = discover_ssh_config_files(ssh_dir, include_subdirs, args.exclude)
    logging.info(f"Found {len(files)} SSH config file(s)")

    all_hosts: List[SshHost] = []
    for f in files:
        try:
            hs = parse_ssh_config(f)
            logging.debug(f"{f}: parsed {len(hs)} host(s)")
            all_hosts.extend(hs)
        except Exception as ex:
            logging.warning(f"Failed to parse {f}: {ex}")

    settings = load_settings(settings_path)
    profiles = ensure_profiles_container(settings)

    if args.remove:
        remove_profiles(profiles, all_hosts)
        logging.info("Removal complete")
    if args.add:
        upsert_profiles(profiles, all_hosts)
        logging.info("Add/update complete")

    schema_fallback = load_schema_fallback_from_example(Path(__file__).with_name("example.json"))
    save_settings(settings_path, settings, validate=True, schema_url_fallback=schema_fallback)
    logging.info(f"Saved settings to {settings_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
