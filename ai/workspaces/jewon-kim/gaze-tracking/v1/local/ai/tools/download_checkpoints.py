"""Fetch and verify the pretrained gaze checkpoints (doc 3-2 / doc 16).

``mediapipe_geom`` needs nothing from here.  ``l2cs`` and ``gazetr`` need weights
that the authors publish outside PyPI, so this CLI does three separate jobs and
keeps them separate on purpose:

1. say where each file comes from, in copy-pasteable form;
2. download it when -- and only when -- a stable direct URL exists;
3. verify whatever ended up on disk before anything trusts it.

Step 3 is the point.  Every "download from Google Drive" one-liner in this corner
of the ecosystem eventually saves a 2 kB HTML interstitial under a ``.pkl`` name
and fails hours later inside ``torch.load``.  Here a file is only accepted after
its length, its magic bytes and (where a digest is pinned) its SHA-256 match, and
optionally after torch has actually parsed the state dict.

    python ai/tools/download_checkpoints.py --list
    python ai/tools/download_checkpoints.py l2cs
    python ai/tools/download_checkpoints.py gazetr --from-file ~/Downloads/GazeTR-H-ETH.pt
    python ai/tools/download_checkpoints.py all --verify-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "ai" / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "ai" / "src"))

#: torch.save writes a zip archive (torch >= 1.6) or, for older releases, a raw
#: pickle.  Anything else under a .pkl/.pt name is an error page.
PICKLE_MAGIC = bytes([0x80])
ZIP_MAGIC = b"PK" + bytes([0x03, 0x04])
HTML_PREFIXES = (b"<!DO", b"<htm", b"<HTM", b"<?xm")

CHUNK = 1 << 20
USER_AGENT = "pitchcoach-vision/0.1 (+checkpoint downloader)"


@dataclass(frozen=True)
class CheckpointSpec:
    """One published checkpoint and everything needed to trust a copy of it."""

    key: str
    backbone: str
    #: Repo-relative destination; matches the backbone module's DEFAULT_CHECKPOINT.
    dest: str
    trained_on: str
    #: Exact byte length of the official artefact, or None when unverified.
    size_bytes: Optional[int]
    #: Pinned digest, or None when no trustworthy digest is published.
    sha256: Optional[str]
    #: Direct, non-interactive URL, or None when only a Drive/Baidu page exists.
    url: Optional[str]
    url_note: str
    manual: str
    #: State-dict key prefixes the matching backbone's forward pass needs.
    required_prefixes: Tuple[str, ...] = ()


CHECKPOINTS: Dict[str, CheckpointSpec] = {
    "l2cs": CheckpointSpec(
        key="l2cs",
        backbone="l2cs",
        dest="ai/models/gaze/backbone/L2CSNet_gaze360.pkl",
        trained_on="Gaze360",
        size_bytes=95849977,
        sha256="8a7f3480d868dd48261e1d59f915b0ef0bb33ea12ea00938fb2168f212080665",
        url=(
            "https://huggingface.co/dorni/SpeakerVid-5M-data-curation-models/resolve/"
            "5c6e04d7fa3321e6228e79162f8ec98466bf308a/L2CSNet_gaze360.pkl"
        ),
        url_note=(
            "third-party HuggingFace mirror, pinned to one commit. The authors publish\n"
            "    only through a Google Drive folder, which cannot be fetched non-interactively:\n"
            "    https://drive.google.com/drive/folders/17p6ORr-JQJcw-eYtG2WGNiuS_qVKwdWd"
        ),
        manual=(
            "Download L2CSNet_gaze360.pkl from the Drive folder above and re-run with\n"
            "  --from-file <path>"
        ),
        required_prefixes=("conv1", "bn1", "layer", "fc_yaw_gaze", "fc_pitch_gaze"),
    ),
    "gazetr": CheckpointSpec(
        key="gazetr",
        backbone="gazetr",
        dest="ai/models/gaze/backbone/GazeTR-H-ETH.pt",
        trained_on="ETH-XGaze",
        size_bytes=None,
        sha256=None,
        url=None,
        url_note=(
            "no stable direct URL. The authors publish only via Google Drive and Baidu,\n"
            "    and a scripted Drive fetch returns an HTML consent page that looks like a\n"
            "    checkpoint until torch.load fails, so this tool refuses to guess."
        ),
        manual=(
            "Download GazeTR-H-ETH.pt from\n"
            "  https://drive.google.com/file/d/1WEiKZ8Ga0foNmxM7xFabI4D5ajThWAWj/view\n"
            "  (or pan.baidu.com/s/1GEbjbNgXvVkisVWGtTJm7g, code 1234)\n"
            "then re-run with --from-file <path>."
        ),
        required_prefixes=("base_model", "encoder", "cls_token", "pos_embedding", "feed"),
    ),
}


class VerificationError(RuntimeError):
    """A candidate file failed a check; it is never left in the destination."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def check_magic(path: Path) -> None:
    """Reject anything that is not a torch archive, naming the likely cause."""
    head = path.read_bytes()[:4]
    if head.startswith(HTML_PREFIXES):
        raise VerificationError(
            f"{path.name} starts with {head!r}: this is an HTML page, not a checkpoint. "
            "A Google-Drive or login-walled URL returns exactly this."
        )
    if head[:1] != PICKLE_MAGIC and head != ZIP_MAGIC:
        raise VerificationError(
            f"{path.name} starts with {head!r}, which is neither a torch zip archive "
            f"({ZIP_MAGIC!r}) nor a pickle stream ({PICKLE_MAGIC!r})."
        )


def verify_file(
    path: Path, spec: CheckpointSpec, *, check_hash: bool = True
) -> Tuple[int, str]:
    """Length, magic bytes and digest.  Returns ``(size, sha256)``."""
    size = path.stat().st_size
    if spec.size_bytes is not None and size != spec.size_bytes:
        raise VerificationError(
            f"{path.name} is {size} bytes, expected {spec.size_bytes}. "
            "Truncated download or a different build."
        )
    check_magic(path)
    digest = sha256_of(path)
    if check_hash and spec.sha256 and digest != spec.sha256:
        raise VerificationError(
            f"{path.name} sha256 is\n  {digest}\nbut the pinned digest is\n  {spec.sha256}\n"
            "Refusing to install it. Pass --no-verify-hash only if you know the build differs."
        )
    return size, digest


def verify_loadable(path: Path, spec: CheckpointSpec) -> str:
    """Parse the state dict with torch and confirm the model's own keys are there.

    Optional because the point of this tool is to work on a machine that has not
    yet installed torch; when torch *is* present this is the only check that
    proves the file is the right architecture and not merely a valid archive.
    """
    try:
        from vision.backbones.base import load_checkpoint_state_dict
    except ImportError as exc:  # pragma: no cover - path bootstrap failure
        return f"skipped (cannot import vision.backbones.base: {exc})"
    try:
        import torch  # noqa: F401
    except ImportError:
        return "skipped (torch not installed)"

    state = load_checkpoint_state_dict(path, spec.backbone)
    missing = [p for p in spec.required_prefixes if not any(k.startswith(p) for k in state)]
    if missing:
        raise VerificationError(
            f"{path.name} parsed but has no parameters under {missing}; "
            f"this is not a {spec.backbone} checkpoint."
        )
    total = sum(int(getattr(v, "numel", lambda: 0)()) for v in state.values())
    return f"ok ({len(state)} tensors, {total:,} parameters)"


def download(url: str, target: Path, *, timeout: float, stream: bool = True) -> None:
    """Stream a URL to ``target`` via a ``.part`` file, with a coarse progress line.

    The partial file is removed on any failure so a later run cannot mistake a
    half-written download for a complete one.
    """
    part = target.with_suffix(target.suffix + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if content_type.startswith("text/html"):
                raise VerificationError(
                    f"{url} served Content-Type {content_type!r}: a consent or error page, "
                    "not a file."
                )
            expected = response.headers.get("Content-Length")
            total = int(expected) if expected and expected.isdigit() else 0
            done = 0
            with part.open("wb") as handle:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    handle.write(block)
                    done += len(block)
                    if stream:
                        pct = f" ({done * 100 // total}%)" if total else ""
                        print(f"\r    {done / 1e6:7.1f} MB{pct}", end="", flush=True)
        if stream:
            print()
    except (urllib.error.URLError, OSError, VerificationError):
        part.unlink(missing_ok=True)
        raise
    part.replace(target)


def install(
    spec: CheckpointSpec,
    *,
    dest_root: Path,
    source_url: Optional[str],
    from_file: Optional[Path],
    force: bool,
    check_hash: bool,
    check_load: bool,
    timeout: float,
    now_ms: int,
) -> bool:
    """Put one checkpoint in place and verify it.  Returns True on success."""
    target = dest_root / spec.dest
    print(f"[{spec.key}] -> {target}")

    if target.is_file() and not force:
        try:
            size, digest = verify_file(target, spec, check_hash=check_hash)
        except VerificationError as exc:
            print(f"    present but INVALID: {exc}")
            print("    re-run with --force to replace it")
            return False
        print(f"    already present, verified ({size:,} bytes)")
        if check_load:
            print(f"    torch load: {verify_loadable(target, spec)}")
        _write_sidecar(target, spec, size, digest, source="existing", now_ms=now_ms)
        return True

    if from_file is not None:
        source = Path(from_file).expanduser().resolve()
        if not source.is_file():
            print(f"    --from-file {source} does not exist")
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        origin = str(source)
    else:
        url = source_url or spec.url
        if not url:
            print(f"    no direct URL: {spec.url_note}")
            for line in spec.manual.splitlines():
                print(f"    {line}")
            return False
        print(f"    downloading {url}")
        try:
            download(url, target, timeout=timeout)
        except (urllib.error.URLError, OSError, VerificationError) as exc:
            print(f"    download failed: {exc}")
            for line in spec.manual.splitlines():
                print(f"    {line}")
            return False
        origin = url

    try:
        size, digest = verify_file(target, spec, check_hash=check_hash)
        if check_load:
            load_status = verify_loadable(target, spec)
        else:
            load_status = "skipped (--no-verify-load)"
    except VerificationError as exc:
        target.unlink(missing_ok=True)
        print(f"    REJECTED and deleted: {exc}")
        return False

    print(f"    verified {size:,} bytes  sha256 {digest}")
    print(f"    torch load: {load_status}")
    if spec.sha256 is None:
        print("    note: no digest is published for this file; the observed one is")
        print("          recorded next to it so another machine can compare.")
    _write_sidecar(target, spec, size, digest, source=origin, now_ms=now_ms)
    return True


def _write_sidecar(
    target: Path, spec: CheckpointSpec, size: int, digest: str, *, source: str, now_ms: int
) -> None:
    """Record provenance next to the weights (doc 18: a run must be reconstructable)."""
    payload = {
        "key": spec.key,
        "backbone": spec.backbone,
        "trained_on": spec.trained_on,
        "filename": target.name,
        "size_bytes": size,
        "sha256": digest,
        "pinned_sha256": spec.sha256,
        "source": source,
        "installed_at_ms": now_ms,
    }
    target.with_suffix(target.suffix + ".json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def print_listing(dest_root: Path) -> None:
    print("gaze backbone checkpoints (doc 3-2)\n")
    print("  mediapipe_geom  no checkpoint: it is pure geometry over MediaPipe landmarks.\n")
    for spec in CHECKPOINTS.values():
        target = dest_root / spec.dest
        if target.is_file():
            status = f"present, {target.stat().st_size:,} bytes"
        else:
            status = "MISSING"
        print(f"  {spec.key}  ({spec.backbone}, trained on {spec.trained_on})")
        print(f"    path    {spec.dest}")
        print(f"    status  {status}")
        print(f"    size    {spec.size_bytes:,} bytes" if spec.size_bytes else "    size    unpublished")
        print(f"    sha256  {spec.sha256 or 'not published -- recorded on install'}")
        print(f"    url     {spec.url or 'none (manual download)'}")
        print(f"    note    {spec.url_note}")
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="download_checkpoints.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "keys",
        nargs="*",
        metavar="KEY",
        help=f"checkpoints to install: {', '.join(CHECKPOINTS)}, or 'all'",
    )
    parser.add_argument("--list", action="store_true", help="show every checkpoint and exit")
    parser.add_argument(
        "--dest-root",
        type=Path,
        default=REPO_ROOT,
        help="repo root the destination paths are relative to (default: this repo)",
    )
    parser.add_argument("--url", help="override the download URL (single KEY only)")
    parser.add_argument(
        "--from-file",
        type=Path,
        help="install an already-downloaded file instead of fetching (single KEY only)",
    )
    parser.add_argument("--force", action="store_true", help="replace a file that is already there")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="check what is on disk; never download or copy",
    )
    parser.add_argument(
        "--no-verify-hash",
        action="store_true",
        help="accept a digest mismatch (only for a knowingly different build)",
    )
    parser.add_argument(
        "--no-verify-load",
        action="store_true",
        help="skip the torch state-dict parse (faster, and works without torch)",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="per-request timeout, seconds")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    dest_root = args.dest_root.resolve()

    if args.list or not args.keys:
        print_listing(dest_root)
        return 0 if args.list else 1

    keys: List[str] = []
    for key in args.keys:
        if key == "all":
            keys.extend(CHECKPOINTS)
        elif key in CHECKPOINTS:
            keys.append(key)
        else:
            print(f"unknown checkpoint {key!r}; known: {', '.join(CHECKPOINTS)}, all")
            return 2
    if (args.url or args.from_file) and len(keys) != 1:
        print("--url / --from-file apply to exactly one KEY")
        return 2

    # Timestamps enter library code as arguments; this is the entry point, so
    # this is the one place allowed to read the clock.
    now_ms = int(time.time() * 1000)
    failures = 0
    for key in keys:
        spec = CHECKPOINTS[key]
        if args.verify_only:
            target = dest_root / spec.dest
            print(f"[{spec.key}] -> {target}")
            if not target.is_file():
                print("    MISSING")
                failures += 1
                continue
            try:
                size, digest = verify_file(target, spec, check_hash=not args.no_verify_hash)
                print(f"    verified {size:,} bytes  sha256 {digest}")
                if not args.no_verify_load:
                    print(f"    torch load: {verify_loadable(target, spec)}")
            except VerificationError as exc:
                print(f"    INVALID: {exc}")
                failures += 1
            continue

        ok = install(
            spec,
            dest_root=dest_root,
            source_url=args.url,
            from_file=args.from_file,
            force=args.force,
            check_hash=not args.no_verify_hash,
            check_load=not args.no_verify_load,
            timeout=args.timeout,
            now_ms=now_ms,
        )
        failures += 0 if ok else 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
