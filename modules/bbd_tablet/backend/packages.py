"""Uploaded APK or split bundle → stored release files.

Runs in a worker thread (`asyncio.to_thread`): hashing and unpacking a 4 GB
package must never block the event loop the tablets' sync shares.

- A single APK is hashed in place, then moved (not copied) to `base.apk`; no
  second copy on disk. A restart right after the move is recovered by the
  caller (`recover_single`), so a single APK is never lost to a restart.
- A bundle (`.apks`, `.xapk`, `.apkm`: a zip holding several APKs, detected by
  content, not by extension) is unpacked entry by entry, streaming; every APK
  becomes its own file. Exactly one base (no `split` attribute) is required
  and all parts must share the package name and versionCode.
- OBB data, encrypted entries and archives without an APK are rejected.
- Stored split names fit the tablet's file name rule
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$` (at most 100 characters).
- `stop` is polled between 1 MiB blocks: a discarded draft stops unpacking
  within one block instead of hashing gigabytes for nothing.

Every stored file gets its SHA-256; the tablet checks it before committing
the install session.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from km_sdk import ensure_private_dir

from .apk_meta import ApkFormatError, ApkManifest, read_apk_manifest

SELF_PACKAGE = "com.veykemtu.tabletkontrol"
COPY_BYTES = 1024 * 1024
MAX_PARTS = 100
FILE_MODE = 0o600
# bundletool puts pre-Lollipop standalone APKs here; tablets never need them.
STANDALONES = "standalones/"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
# Tablet rule: whole name at most 100 characters → "split_" + 90 + ".apk".
MAX_SPLIT_STEM = 90
SPLIT_HASH = 8


class PackageError(ValueError):
    """The upload is not a package that can be distributed (Turkish message)."""


class ProcessingStopped(PackageError):
    """The draft was discarded while it was being unpacked."""


Stop = Callable[[], bool]


def _never() -> bool:
    return False


def _check(stop: Stop) -> None:
    if stop():
        raise ProcessingStopped("Paket işlenirken taslak atıldı.")


@dataclass(frozen=True)
class StoredFile:
    name: str
    size_bytes: int
    sha256: str
    split_name: str = ""


@dataclass(frozen=True)
class PreparedRelease:
    package_name: str
    version_code: int
    version_name: str
    min_sdk: int | None
    label: str
    files: list[StoredFile] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(item.size_bytes for item in self.files)

    @property
    def kind(self) -> str:
        return "self" if self.package_name == SELF_PACKAGE else "app"


def sha256_of(path: Path, stop: Stop = _never) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(COPY_BYTES):
            _check(stop)
            digest.update(block)
    return digest.hexdigest()


def recover_single(upload: Path, target: Path) -> bool:
    """Undo a single APK's move after a restart: `base.apk` → the upload file.

    `_single` moves the upload to `target/base.apk`; if the process stopped
    before the draft was recorded, the upload file is gone and processing
    could not start again. A bundle never leaves `target` holding exactly one
    `base.apk` without its upload file (the caller removes the upload only
    after the draft is recorded), so the move back is unambiguous.
    """
    stored = target / "base.apk"
    if upload.exists() or not stored.is_file():
        return False
    if [entry.name for entry in target.iterdir()] != ["base.apk"]:
        return False
    os.replace(stored, upload)
    return True


def free_bytes(path: Path) -> int:
    """Free space on the disk holding `path` (or its nearest existing parent)."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def prepare_release(upload: Path, target: Path, *, reserve_bytes: int = 0,
                    free: Callable[[Path], int] = free_bytes,
                    stop: Stop = _never) -> PreparedRelease:
    """Turn the finished upload into release files under `target`.

    `target` is emptied first, so a retried run starts clean. A single APK is
    moved into `target`; a bundle's upload file stays for the caller to remove
    once the draft is recorded (a restart before that unpacks it again).
    """
    if target.exists():
        shutil.rmtree(target)
    ensure_private_dir(target)
    try:
        with zipfile.ZipFile(upload) as archive:
            infos = archive.infolist()
            if any(info.flag_bits & 0x1 for info in infos):
                raise PackageError("Paket şifreli. Şifresiz bir APK ya da APK paketi yükleyin.")
            if "AndroidManifest.xml" not in {info.filename for info in infos}:
                return _bundle(archive, infos, target, reserve_bytes=reserve_bytes,
                               free=free, stop=stop)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError) as exc:
        raise PackageError("Dosya bir APK ya da APK paketi (.apks, .xapk, .apkm) değil.") from exc
    # Moved after the archive is closed: Windows cannot rename an open file.
    return _single(upload, target, stop)


def _single(upload: Path, target: Path, stop: Stop) -> PreparedRelease:
    manifest = _manifest(upload, "APK")
    if manifest.split_name:
        raise PackageError("Bu dosya bölünmüş bir paketin yalnız bir parçası. Paketin tamamını "
                           "(.apks, .xapk ya da .apkm) yükleyin.")
    # Hashed before the move: until the move the upload file is intact, after
    # it `recover_single` can put it back.
    digest, size = sha256_of(upload, stop), upload.stat().st_size
    stored = target / "base.apk"
    os.replace(upload, stored)
    stored.chmod(FILE_MODE)
    return _release(manifest, [StoredFile("base.apk", size, digest)])


def split_file_name(split_name: str) -> str:
    """`split_<safe>.apk`, at most 100 characters; a long name keeps a hash suffix."""
    stem = _UNSAFE.sub("_", split_name).strip("._-") or "part"
    if len(stem) > MAX_SPLIT_STEM:
        suffix = hashlib.sha256(split_name.encode("utf-8")).hexdigest()[:SPLIT_HASH]
        stem = f"{stem[:MAX_SPLIT_STEM - SPLIT_HASH - 1]}_{suffix}"
    return f"split_{stem}.apk"


def _bundle(archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo], target: Path, *,
            reserve_bytes: int, free: Callable[[Path], int], stop: Stop) -> PreparedRelease:
    lowered = [info.filename.replace("\\", "/").lower() for info in infos]
    if any(name.endswith(".obb") or "android/obb/" in name for name in lowered):
        raise PackageError("Paket OBB veri dosyası içeriyor. OBB'li uygulamalar dağıtılamaz.")
    parts = [info for info, name in zip(infos, lowered, strict=True)
             if not info.is_dir() and name.endswith(".apk") and not name.startswith(STANDALONES)]
    if not parts:
        raise PackageError("Dosya bir APK ya da içinde APK bulunan bir paket değil.")
    if len(parts) > MAX_PARTS:
        raise PackageError(f"Pakette {len(parts)} APK var; en fazla {MAX_PARTS} olabilir.")
    needed = sum(info.file_size for info in parts)
    if free(target) - needed < reserve_bytes:
        raise PackageError("Sunucuda paketi açacak kadar boş yer yok.")

    unpacked: list[tuple[ApkManifest, StoredFile, Path]] = []
    for index, info in enumerate(parts):
        temporary = target / f"part-{index}.tmp"
        size, digest = _extract(archive, info, temporary, stop)
        manifest = _manifest(temporary, info.filename)
        unpacked.append((manifest, StoredFile("", size, digest, manifest.split_name), temporary))

    bases = [item for item in unpacked if not item[0].split_name]
    if len(bases) != 1:
        raise PackageError("Pakette tek bir temel APK olmalı; "
                           f"{len(bases)} tane bulundu.")
    base = bases[0][0]
    for manifest, _, _ in unpacked:
        if manifest.package_name != base.package_name:
            raise PackageError("Paketteki APK'lar farklı uygulamalara ait.")
        if manifest.version_code != base.version_code:
            raise PackageError("Paketteki APK'ların sürüm numaraları (versionCode) farklı.")

    files: list[StoredFile] = []
    taken: set[str] = set()
    splits: set[str] = set()
    ordered = sorted(unpacked, key=lambda item: (bool(item[0].split_name), item[0].split_name))
    for manifest, stored, temporary in ordered:
        if manifest.split_name in splits:
            raise PackageError(f"Pakette aynı parça iki kez var: {manifest.split_name}.")
        splits.add(manifest.split_name)
        name = "base.apk" if not manifest.split_name else split_file_name(manifest.split_name)
        if name in taken:
            # Two split names that differ only in unsafe characters.
            digest = hashlib.sha256(manifest.split_name.encode("utf-8")).hexdigest()
            name = f"{name[:-4][:100 - 4 - SPLIT_HASH - 1]}_{digest[:SPLIT_HASH]}.apk"
        taken.add(name)
        os.replace(temporary, target / name)
        files.append(StoredFile(name, stored.size_bytes, stored.sha256, manifest.split_name))
    return _release(base, files)


def _extract(archive: zipfile.ZipFile, info: zipfile.ZipInfo, path: Path,
             stop: Stop) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        with os.fdopen(handle, "wb") as sink, archive.open(info) as source:
            while block := source.read(COPY_BYTES):
                _check(stop)
                sink.write(block)
                digest.update(block)
                size += len(block)
    except (NotImplementedError, RuntimeError) as exc:
        raise PackageError(f"{info.filename} açılamadı (desteklenmeyen sıkıştırma).") from exc
    return size, digest.hexdigest()


def _manifest(path: Path, label: str) -> ApkManifest:
    try:
        return read_apk_manifest(path)
    except ApkFormatError as exc:
        raise PackageError(f"{label} okunamadı: {exc}.") from exc


def _release(manifest: ApkManifest, files: list[StoredFile]) -> PreparedRelease:
    return PreparedRelease(
        package_name=manifest.package_name, version_code=manifest.version_code,
        version_name=manifest.version_name, min_sdk=manifest.min_sdk,
        label=manifest.label.strip(), files=files,
    )
