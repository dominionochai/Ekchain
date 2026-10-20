"""The inert description of everything ``ekchain setup`` may provision.

Every version here is pinned to the literal the Dockerfile uses, so a native
install and the container image resolve the same tool at the same version;
``tests/test_manifest_parity.py`` fails the build if the two drift apart.

Nothing in this module opens a socket or touches the filesystem at import
beyond the single ``assets.lock`` package-data read.
"""

from __future__ import annotations

import json
import os
import platform
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

Kind = Literal["file", "archive", "git", "eztools"]
Validation = Literal["archive", "stix-json", "sigma-tree", "git", "none"]


ANY_ARCH = "any"


@dataclass(frozen=True)
class Asset:
    """One provisionable artifact.

    Attributes:
        key: Stable name used by ``--only`` / ``--skip`` and by the state file.
        version: Pinned upstream version, or a branch/"HEAD" marker.
        kind: How it is fetched and published.
        urls: Architecture -> URL. ``ANY_ARCH`` for arch-independent rows.
        dest: Destination directory, relative to the asset root.
        sentinel: Path relative to *dest* whose presence means "installed".
        pinnable: True when ``assets.lock`` MUST carry a SHA-256 for it.
        size_estimate: Space the asset occupies on disk once installed, in
            bytes, measured against upstream on 2026-08-16 (release assets by
            Content-Length, clones by ``du`` of a ``--depth 1`` working tree).
            For clones this deliberately exceeds the bytes transferred -- a
            shallow iLEAPP clone is ~830 MB on disk against a ~225 MB
            download -- because disk is what runs out.  Drives ``--dry-run``
            and the first-pass total only; a live run refines the file and
            archive rows from Content-Length.
        filename: For ``kind="file"``, the basename written under *dest*.
        siblings: Further ``(basename, url)`` files written into the same dest.
        strip_components: Leading archive path components to drop.
        chmod_x: Files under *dest* to mark executable after extraction.
        symlinks: ``(link_name, target_name)`` pairs created under *dest*.
        shims: Basenames under *dest* to link into ``bin_dir()``.
        requires: HARD dependencies; ``--only`` pulls these in transitively.
        arch_only: Non-empty restricts the row to those architectures.
        substitute: What to do instead on an unsupported architecture.
        git_ref: For ``kind="git"``, the tag or branch to clone.
        supplement_url: A second git repo whose *supplement_paths* are copied
            into *dest* after the primary artifact lands (Chainsaw's mappings
            and rules, which upstream ships only in the source tree).
        validate: Shape check applied before publishing.
        cache_dest: Non-empty routes the row outside the asset root; only
            ``"vol-symbols"`` exists, for the reason in ``volatility_cache_dir``.
        members: For ``kind="eztools"``, the DLL basenames ekchain invokes.
        note: Extra text for the ``--list`` table.
    """

    key: str
    version: str
    kind: Kind
    urls: Mapping[str, str]
    dest: tuple[str, ...]
    sentinel: tuple[str, ...]
    pinnable: bool
    size_estimate: int
    filename: str = ""
    siblings: tuple[tuple[str, str], ...] = ()
    strip_components: int = 0
    chmod_x: tuple[str, ...] = ()
    symlinks: tuple[tuple[str, str], ...] = ()
    shims: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    arch_only: tuple[str, ...] = ()
    substitute: str = ""
    git_ref: str = ""
    supplement_url: str = ""
    supplement_paths: tuple[str, ...] = ()
    validate: Validation = "archive"
    cache_dest: str = ""
    members: tuple[str, ...] = ()
    note: str = ""
    #: Populated from ``assets.lock`` at import; never edited by hand.
    digests: Mapping[str, str] = field(default_factory=dict)

    def url_for(self, arch: str) -> str | None:
        """The download URL for *arch*, or None when upstream ships none."""
        if self.arch_only and arch not in self.arch_only:
            return None
        return self.urls.get(arch) or self.urls.get(ANY_ARCH)


_MB = 1024 * 1024

_RAW_ATTACK = "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master"
_GH = "https://github.com"

#: Pinned versions, each matching a Dockerfile ``ARG``/literal.
SIGMA_VERSION = "r2024-09-02"

_ASSETS: tuple[Asset, ...] = (
    Asset(
        key="attack",
        version="master",
        kind="file",
        urls={ANY_ARCH: f"{_RAW_ATTACK}/enterprise-attack/enterprise-attack.json"},
        filename="enterprise-attack.json",
        siblings=(("ics-attack.json", f"{_RAW_ATTACK}/ics-attack/ics-attack.json"),),
        dest=("attack",),
        sentinel=("enterprise-attack.json",),
        pinnable=False,
        size_estimate=58 * _MB,
        validate="stix-json",
        note="MITRE ATT&CK Enterprise + ICS STIX bundles (tracks master)",
    ),
    Asset(
        key="sigma-rules",
        version=SIGMA_VERSION,
        kind="archive",
        urls={ANY_ARCH: f"{_GH}/SigmaHQ/sigma/archive/refs/tags/{SIGMA_VERSION}.tar.gz"},
        dest=("sigma-rules",),
        sentinel=("rules",),
        # GitHub regenerates tag tarballs, so their gzip bytes are not stable
        # even for a fixed tag; shape validation is the only honest check.
        pinnable=False,
        size_estimate=6 * _MB,
        strip_components=1,
        validate="sigma-tree",
        note="Sigma rules for Chainsaw",
    ),
)


def _load_lock() -> dict[str, dict[str, object]]:
    """Read the checked-in digests.

    Kept as package data rather than a Python literal so ``make assets-lock``
    can rewrite it without touching source.
    """
    lock_file = Path(__file__).with_name("assets.lock")
    try:
        raw = json.loads(lock_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = raw.get("entries")
    return entries if isinstance(entries, dict) else {}


LOCK: Mapping[str, dict[str, object]] = _load_lock()


def _with_digests(asset: Asset) -> Asset:
    """Attach the locked SHA-256 for each architecture of *asset*."""
    if not asset.pinnable:
        return asset
    digests = {
        arch: str(entry["sha256"])
        for arch in asset.urls
        if (entry := LOCK.get(f"{asset.key}:{arch}")) and entry.get("sha256")
    }
    return replace(asset, digests=digests)


ASSETS: tuple[Asset, ...] = tuple(_with_digests(a) for a in _ASSETS)
ASSETS_BY_KEY: Mapping[str, Asset] = {a.key: a for a in ASSETS}


def detect_arch() -> str:
    """Map ``platform.machine()`` onto the Dockerfile's ``TARGETARCH`` values."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return machine


def volatility_cache_dir() -> Path:
    """Where volatility3 itself looks for ISF symbol packs.

    Copied from ``volatility3.framework.constants`` rather than imported: that
    import has the side effect of creating the cache directory, and the *system*
    ``vol`` ekchain prefers is a different install of the same package computing
    the same env-derived path.  ``EKCHAIN_ASSET_ROOT`` deliberately does not
    affect this -- re-rooting the symbols would orphan them from the ``vol`` on
    ``$PATH``.
    """
    cache_home = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_home) if cache_home else Path.home() / ".cache"
    return base / "volatility3" / "symbols"


def expand_requires(keys: set[str]) -> set[str]:
    """Close *keys* transitively over ``Asset.requires``."""
    out = set(keys)
    pending = list(keys)
    while pending:
        current = ASSETS_BY_KEY.get(pending.pop())
        if current is None:
            continue
        for dep in current.requires:
            if dep not in out:
                out.add(dep)
                pending.append(dep)
    return out
