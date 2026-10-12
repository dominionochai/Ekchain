"""Tests for ``ekchain setup``'s planning and provisioning.

Offline by construction, in three independent layers:

1. ``provision(..., fetch=...)`` takes the downloader as a parameter and every
   test here passes a local one;
2. ``ekchain.assets.manifest`` is inert data whose only I/O is one package-data
   read;
3. ``tests/conftest.py`` patches ``socket.connect`` and ``httpx`` send to raise,
   so a regression that bypasses the injected fetcher fails loudly instead of
   downloading a gigabyte in CI.
"""

from __future__ import annotations

import json
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from ekchain.assets import install as install_mod
from ekchain.assets.fetch import FetchError, FetchResult, sha256_file
from ekchain.assets.install import Plan, PlanError, SetupOptions, build_plan, provision
from ekchain.assets.manifest import ASSETS_BY_KEY
from ekchain.assets.state import AssetState


class RecordingFetcher:
    """A fetcher that synthesises the right shape for each manifest row."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.urls: list[str] = []
        self.fail: set[str] = set()
        self.corrupt: set[str] = set()

    def head(self, url: str) -> int | None:
        raise AssertionError("--dry-run must issue no requests at all")

    def __call__(self, url: str, dest: Path, *, resume: bool = False) -> FetchResult:
        self.urls.append(url)
        name = url.rsplit("/", 1)[-1]
        if name in self.fail:
            raise FetchError("404 Not Found")
        if name.endswith(".json"):
            body = b'{"objects": []}' if name not in self.corrupt else b'{"objec'
            dest.write_bytes(body)
        elif name.endswith(".tar.gz"):
            _write_tarball(dest, name)
        else:
            _write_zip(dest, name)
        return FetchResult(dest, dest.stat().st_size, sha256_file(dest))


def _write_tarball(dest: Path, name: str) -> None:
    staging = dest.parent / "tar-src" / "prefix"
    (staging / "rules" / "windows").mkdir(parents=True, exist_ok=True)
    (staging / "rules" / "windows" / "rule.yml").write_text("title: test\n")
    (staging / "rules" / "linux").mkdir(parents=True, exist_ok=True)
    (staging / "rules" / "linux" / "rule.yml").write_text("title: test\n")
    (staging / "chainsaw").write_text("#!/bin/sh\n")
    with tarfile.open(dest, "w:gz") as tf:
        tf.add(staging, arcname="prefix")


def _write_zip(dest: Path, name: str) -> None:
    stem = name.removesuffix(".zip")
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("capa", "#!/bin/sh\n")
        zf.writestr(f"{stem}.dll", "binary")
        zf.writestr("hayabusa-3.8.1-lin-x64-musl", "#!/bin/sh\n")


@pytest.fixture()
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A pinned write root and shim directory, with git and root faked out."""
    from ekchain.assets import paths

    root = tmp_path / "assets"
    root.mkdir()
    bin_dir = tmp_path / "bin"
    monkeypatch.setenv("EKCHAIN_ASSET_ROOT", str(root))
    monkeypatch.setattr(install_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(install_mod, "_geteuid", lambda: 1000)
    paths.reset_asset_caches()
    return root, bin_dir


def _plan(
    roots: tuple[Path, Path], keys: set[str] | None = None, **kwargs: Any
) -> tuple[Plan, SetupOptions]:
    """Build a plan, optionally narrowed to *keys*.

    `ekchain setup` deliberately has no --only/--tier any more: it installs
    everything.  Tests still need to exercise one row at a time, so the
    narrowing lives here rather than as production CLI surface.
    """
    options = SetupOptions(**kwargs)
    plan = build_plan(options, roots[0], roots[1])
    if keys is not None:
        plan.selected = [a for a in plan.selected if a.key in keys]
    return plan, options


def _run(
    roots: tuple[Path, Path],
    fetcher: RecordingFetcher | Any,
    keys: set[str] | None = None,
    **kwargs: Any,
) -> Any:
    plan, options = _plan(roots, keys, **kwargs)
    install_mod.preflight(plan, options)
    return provision(plan, options, fetch=fetcher)


def _by_key(result: Any) -> dict[str, Any]:
    return {outcome.key: outcome for outcome in result.outcomes}


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


class TestPlanning:
    def test_every_manifest_row_appears_in_the_summary(self, roots: tuple[Path, Path]) -> None:
        """A silent partial install must be structurally impossible."""
        result = _run(roots, RecordingFetcher(roots[0]), dry_run=True)

        assert set(_by_key(result)) == set(ASSETS_BY_KEY)


class TestPreflight:
    def test_running_as_root_is_a_usage_error(
        self, roots: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(install_mod, "_geteuid", lambda: 0)
        plan, options = _plan(roots)

        with pytest.raises(PlanError) as exc:
            install_mod.preflight(plan, options)

        assert exc.value.exit_code == 2
        assert "Refusing to run as root" in str(exc.value)

    def test_unwritable_write_root_is_fatal_before_any_fetch(
        self, roots: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan, options = _plan(roots)
        monkeypatch.setattr(
            Path, "write_bytes", lambda *_a, **_k: (_ for _ in ()).throw(OSError("EROFS"))
        )

        with pytest.raises(PlanError) as exc:
            install_mod.preflight(plan, options)

        assert exc.value.exit_code == 1
        assert "EKCHAIN_ASSET_ROOT" in str(exc.value)


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


class TestProvisioning:
    def test_dry_run_writes_nothing_and_requests_nothing(self, roots: tuple[Path, Path]) -> None:
        fetcher = RecordingFetcher(roots[0])
        result = _run(roots, fetcher, dry_run=True)

        assert fetcher.urls == []
        assert list(roots[0].iterdir()) == []
        assert result.exit_code == 0

    def test_fresh_install_then_idempotent_rerun(self, roots: tuple[Path, Path]) -> None:
        fetcher = RecordingFetcher(roots[0])
        first = _run(roots, fetcher, {"attack"})
        assert _by_key(first)["attack"].status == "installed"
        assert (roots[0] / "attack" / "enterprise-attack.json").exists()

        fetched = len(fetcher.urls)
        second = _run(roots, fetcher, {"attack"})

        assert _by_key(second)["attack"].status == "up-to-date"
        assert len(fetcher.urls) == fetched

    def test_a_failed_fetch_leaves_the_previous_good_copy_intact(
        self, roots: tuple[Path, Path]
    ) -> None:
        """The whole point of staging: a bad download never reaches the reader."""
        fetcher = RecordingFetcher(roots[0])
        _run(roots, fetcher, {"attack"})
        good = (roots[0] / "attack" / "enterprise-attack.json").read_bytes()

        fetcher.corrupt.add("enterprise-attack.json")
        # A re-fetch now happens only because the recorded version no longer
        # matches the manifest -- the same path a real upgrade takes.
        state = AssetState.load(roots[0])
        state.assets["attack"].version = "stale"
        state.save(roots[0])
        result = _run(roots, fetcher, {"attack"})

        assert _by_key(result)["attack"].failed
        assert result.exit_code == 3
        assert (roots[0] / "attack" / "enterprise-attack.json").read_bytes() == good
        assert json.loads(good)["objects"] == []


class TestVerify:
    def test_verify_reports_missing_and_exits_4(self, roots: tuple[Path, Path]) -> None:
        from ekchain.assets.fetch import OfflineFetcher

        plan, options = _plan(roots, verify=True)
        result = provision(plan, options, fetch=OfflineFetcher())

        assert _by_key(result)["attack"].status == "missing"
        assert result.exit_code == 4

    def test_verify_exits_0_when_everything_is_present(self, roots: tuple[Path, Path]) -> None:
        from ekchain.assets.fetch import OfflineFetcher

        _run(roots, RecordingFetcher(roots[0]), {"attack"})
        plan, options = _plan(roots, {"attack"}, verify=True)
        result = provision(plan, options, fetch=OfflineFetcher())

        assert result.exit_code == 0


class TestShadowing:
    def test_a_higher_priority_copy_is_reported_not_hidden(
        self, roots: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SIFT case: a hand-made /opt copy outranks what setup just wrote.

        Without this, setup prints "All N assets present" while every read still
        resolves to the older -- possibly truncated -- copy.
        """
        from ekchain.assets import paths

        write_root, bin_dir = roots
        _run(roots, RecordingFetcher(write_root), {"attack"})

        stale = tmp_path / "opt"
        (stale / "attack").mkdir(parents=True)
        (stale / "attack" / "enterprise-attack.json").write_text('{"objects": []}')
        monkeypatch.delenv("EKCHAIN_ASSET_ROOT")
        monkeypatch.setattr(paths, "SYSTEM_ROOT", stale)
        monkeypatch.setattr(Path, "home", lambda: write_root.parent / "fake-home")
        monkeypatch.setattr(paths, "user_root", lambda: write_root)
        paths.reset_asset_caches()

        options = SetupOptions(verify=True)
        plan = build_plan(options, write_root, bin_dir)
        plan.selected = [a for a in plan.selected if a.key == "attack"]
        from ekchain.assets.fetch import OfflineFetcher

        result = provision(plan, options, fetch=OfflineFetcher())

        assert _by_key(result)["attack"].shadowed_by == str(stale / "attack")
        assert result.exit_code == 4
        assert "shadowed" in result.remedies[0]

    def test_verify_accepts_an_asset_provided_only_by_a_higher_priority_root(
        self, roots: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The stock SIFT box, which SPEC 1.6 promises is unaffected.

        A hand-made /opt clone and no 'ekchain setup' run is a working install.
        Deciding presence from the write root alone reported it 'missing' with
        exit 4, sending the user looking for a problem that is not there.
        """
        from ekchain.assets import paths
        from ekchain.assets.fetch import OfflineFetcher

        write_root, bin_dir = roots
        theirs = tmp_path / "opt"
        (theirs / "attack").mkdir(parents=True)
        (theirs / "attack" / "enterprise-attack.json").write_text('{"objects": []}')

        monkeypatch.delenv("EKCHAIN_ASSET_ROOT")
        monkeypatch.setattr(paths, "SYSTEM_ROOT", theirs)
        monkeypatch.setattr(Path, "home", lambda: write_root.parent / "fake-home")
        monkeypatch.setattr(paths, "user_root", lambda: write_root)
        paths.reset_asset_caches()

        options = SetupOptions(verify=True)
        plan = build_plan(options, write_root, bin_dir)
        plan.selected = [a for a in plan.selected if a.key == "attack"]
        result = provision(plan, options, fetch=OfflineFetcher())

        attack = _by_key(result)["attack"]
        assert attack.status == "up-to-date (unmanaged)"
        assert not attack.missing
        assert str(theirs / "attack") in attack.detail
        assert result.exit_code == 0


class TestArchiveSafety:
    def test_a_tar_member_escaping_the_destination_is_rejected(self, tmp_path: Path) -> None:
        archive = tmp_path / "evil.tar.gz"
        payload = tmp_path / "payload"
        payload.write_text("pwned")
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(payload, arcname="../../escaped")

        with pytest.raises(FetchError, match="escapes destination"):
            install_mod.extract_archive(archive, tmp_path / "dest")

        assert not (tmp_path.parent / "escaped").exists()

    def test_absolute_member_paths_are_rejected(self, tmp_path: Path) -> None:
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("/etc/passwd", "pwned")

        with pytest.raises(FetchError, match="escapes destination"):
            install_mod.extract_archive(archive, tmp_path / "dest")


class TestSummaryLine:
    """The last line is the one a human actually reads."""

    def _render(
        self, roots: tuple[Path, Path], keys: set[str] | None = None, **kwargs: Any
    ) -> str:
        from io import StringIO

        from rich.console import Console

        from ekchain.assets.fetch import OfflineFetcher
        from ekchain.cli import _print_summary

        plan, options = _plan(roots, keys, **kwargs)
        result = provision(plan, options, fetch=OfflineFetcher())
        buffer = StringIO()
        _print_summary(Console(file=buffer, width=200, no_color=True), result, plan, options)
        return buffer.getvalue()

    def test_a_verify_run_that_found_nothing_does_not_claim_everything_is_present(
        self, roots: tuple[Path, Path]
    ) -> None:
        """It used to print 'All 8 selected assets present.' under a table of
        eight 'missing' rows and a Consequences block listing what broke."""
        output = self._render(roots, verify=True)

        assert "missing - run 'ekchain setup' to install them" in output
        assert "selected assets present" not in output
