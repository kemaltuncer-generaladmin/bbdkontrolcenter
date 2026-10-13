"""Minimal reader for the binary AndroidManifest.xml inside an APK.

Only what app distribution needs is read: package name, versionCode
(including versionCodeMajor), versionName, split name, minSdk and a literal
application label. A label given as a resource reference needs
resources.arsc and is left empty; the panel then falls back to other names.
Checked against `aapt2 dump badging` on a real APK.
"""

from __future__ import annotations

import struct
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import IO

_RES_STRING_POOL = 0x0001
_RES_XML = 0x0003
_RES_XML_START_ELEMENT = 0x0102
_RES_XML_RESOURCE_MAP = 0x0180
_UTF8_FLAG = 0x100

_TYPE_REFERENCE = 0x01
_TYPE_STRING = 0x03
_TYPE_INT_DEC = 0x10
_TYPE_INT_HEX = 0x11

_ATTR_LABEL = 0x01010001
_ATTR_MIN_SDK = 0x0101020C
_ATTR_VERSION_CODE = 0x0101021B
_ATTR_VERSION_NAME = 0x0101021C
_ATTR_VERSION_CODE_MAJOR = 0x01010576

_MAX_MANIFEST_BYTES = 4 * 1024 * 1024


class ApkFormatError(ValueError):
    """The file is not an APK whose manifest can be read."""


@dataclass(frozen=True)
class ApkManifest:
    package_name: str
    version_code: int
    version_name: str
    split_name: str
    min_sdk: int | None
    label: str


def read_apk_manifest(source: str | Path | IO[bytes]) -> ApkManifest:
    try:
        with zipfile.ZipFile(source) as archive:
            try:
                info = archive.getinfo("AndroidManifest.xml")
            except KeyError as exc:
                raise ApkFormatError("AndroidManifest.xml yok") from exc
            if info.file_size > _MAX_MANIFEST_BYTES:
                raise ApkFormatError("AndroidManifest.xml çok büyük")
            data = archive.read(info)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError) as exc:
        raise ApkFormatError("Dosya bir APK (zip) değil") from exc
    except (NotImplementedError, RuntimeError) as exc:
        # Unsupported compression or an encrypted entry.
        raise ApkFormatError("AndroidManifest.xml açılamadı") from exc
    return parse_binary_manifest(data)


def parse_binary_manifest(data: bytes) -> ApkManifest:
    try:
        return _parse(data)
    except ApkFormatError:
        raise
    except (struct.error, IndexError, UnicodeDecodeError, ValueError, OverflowError) as exc:
        raise ApkFormatError("AndroidManifest.xml okunamadı") from exc


def _parse(data: bytes) -> ApkManifest:
    xml_type, header_size, _ = struct.unpack_from("<HHI", data, 0)
    if xml_type != _RES_XML:
        raise ApkFormatError("AndroidManifest.xml ikili XML değil")
    strings: list[str] = []
    resource_ids: list[int] = []
    manifest: dict[str | int, object] = {}
    uses_sdk: dict[str | int, object] = {}
    application: dict[str | int, object] = {}
    offset = header_size
    while offset + 8 <= len(data):
        chunk_type, chunk_header, chunk_size = struct.unpack_from("<HHI", data, offset)
        if chunk_size < 8:
            raise ApkFormatError("Bozuk XML parçası")
        if chunk_type == _RES_STRING_POOL:
            strings = _string_pool(data, offset)
        elif chunk_type == _RES_XML_RESOURCE_MAP:
            count = (chunk_size - chunk_header) // 4
            resource_ids = list(struct.unpack_from(f"<{count}I", data, offset + chunk_header))
        elif chunk_type == _RES_XML_START_ELEMENT:
            name, attrs = _start_element(data, offset, chunk_header, strings, resource_ids)
            if name == "manifest" and not manifest:
                manifest = attrs
            elif name == "uses-sdk" and not uses_sdk:
                uses_sdk = attrs
            elif name == "application" and not application:
                application = attrs
        offset += chunk_size

    package_name = manifest.get("package")
    if not isinstance(package_name, str) or not package_name:
        raise ApkFormatError("Paket adı okunamadı")
    code = manifest.get(_ATTR_VERSION_CODE)
    if not isinstance(code, int):
        raise ApkFormatError("versionCode okunamadı")
    major = manifest.get(_ATTR_VERSION_CODE_MAJOR)
    version_code = ((major if isinstance(major, int) else 0) << 32) | (code & 0xFFFFFFFF)
    version_name = manifest.get(_ATTR_VERSION_NAME)
    split = manifest.get("split")
    min_sdk = uses_sdk.get(_ATTR_MIN_SDK)
    label = application.get(_ATTR_LABEL)
    return ApkManifest(
        package_name=package_name,
        version_code=version_code,
        version_name=version_name if isinstance(version_name, str) else "",
        split_name=split if isinstance(split, str) else "",
        min_sdk=min_sdk if isinstance(min_sdk, int) else None,
        # A resource reference is not resolvable without resources.arsc.
        label=label if isinstance(label, str) else "",
    )


def _string_pool(data: bytes, offset: int) -> list[str]:
    (_, header_size, _, count, _, flags, strings_start, _) = struct.unpack_from(
        "<HHIIIIII", data, offset
    )
    utf8 = bool(flags & _UTF8_FLAG)
    offsets = struct.unpack_from(f"<{count}I", data, offset + header_size)
    base = offset + strings_start
    result = []
    for item in offsets:
        position = base + item
        if utf8:
            _, position = _utf8_length(data, position)
            size, position = _utf8_length(data, position)
            result.append(data[position : position + size].decode("utf-8", "replace"))
        else:
            size = struct.unpack_from("<H", data, position)[0]
            position += 2
            if size & 0x8000:
                low = struct.unpack_from("<H", data, position)[0]
                size = ((size & 0x7FFF) << 16) | low
                position += 2
            result.append(data[position : position + size * 2].decode("utf-16-le", "replace"))
    return result


def _utf8_length(data: bytes, position: int) -> tuple[int, int]:
    size = data[position]
    position += 1
    if size & 0x80:
        size = ((size & 0x7F) << 8) | data[position]
        position += 1
    return size, position


def _string(strings: list[str], index: int) -> str:
    return strings[index] if 0 <= index < len(strings) else ""


def _start_element(
    data: bytes,
    offset: int,
    header_size: int,
    strings: list[str],
    resource_ids: list[int],
) -> tuple[str, dict[str | int, object]]:
    ext = offset + header_size
    _, name_index, attr_start, attr_size, attr_count = struct.unpack_from("<IIHHH", data, ext)
    attrs: dict[str | int, object] = {}
    for index in range(attr_count):
        position = ext + attr_start + index * attr_size
        _, attr_name, raw_value, _, _, data_type, value = struct.unpack_from(
            "<IIIHBBI", data, position
        )
        if data_type == _TYPE_STRING:
            parsed: object = _string(strings, value)
        elif data_type in (_TYPE_INT_DEC, _TYPE_INT_HEX):
            parsed = value
        elif data_type == _TYPE_REFERENCE:
            parsed = None
        elif raw_value != 0xFFFFFFFF:
            parsed = _string(strings, raw_value)
        else:
            parsed = value
        # Framework attributes are keyed by resource id (names may be
        # obfuscated); the rest (package, split) by their plain name.
        if attr_name < len(resource_ids) and resource_ids[attr_name] >> 24 == 0x01:
            attrs[resource_ids[attr_name]] = parsed
        attrs.setdefault(_string(strings, attr_name), parsed)
    return _string(strings, name_index), attrs
