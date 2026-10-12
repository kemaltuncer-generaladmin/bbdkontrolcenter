"""Vekil izin listesinin satır türü ve yol eşleştirme.

Her satır sözleşmedeki TEK bir işlemdir: yöntem + yol şablonu + Akademi izni.
Satırlar `allowlist.py` içinde ÜRETİLİR (`tools/gen_allowlist.py`); burada
yalnız eşleştirme kuralı durur.

YOL PARAMETRESİ DARALTILIR. Şablondaki `{studentId}` bir yol parçasıdır ve
yalnız `[A-Za-z0-9][A-Za-z0-9._:~-]*` kabul edilir: `..`, boş parça, kodlanmış
`/` ya da `?` vekilin arkasında başka bir Akademi yoluna kaçmanın yoludur.
Akademi kimlikleri (UUID, `STU-…`, `custom-…`, `roleKey`, `YYYY-MM-DD`) bu
kalıba sığar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote

SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._:~-]{0,159}"
_PARAM = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)\}")


@dataclass(frozen=True, slots=True)
class Operation:
    method: str
    path: str
    operation_id: str
    permissions: tuple[str, ...]
    surface: str
    step_up: bool = False
    money: bool = False
    multipart: bool = False
    feature_flag: str | None = None
    features: tuple[str, ...] = ()
    pattern: re.Pattern[str] = field(init=False, repr=False, compare=False)
    literal_segments: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        regex = "^" + _PARAM.sub(lambda m: f"(?P<{m.group(1)}>{SEGMENT})", re.escape(self.path)
                                 .replace(r"\{", "{").replace(r"\}", "}")) + "$"
        object.__setattr__(self, "pattern", re.compile(regex))
        object.__setattr__(self, "literal_segments",
                           sum(1 for part in self.path.split("/") if part and "{" not in part))

    @property
    def is_write(self) -> bool:
        return self.method != "GET"

    def match(self, path: str) -> dict[str, str] | None:
        found = self.pattern.match(path)
        return found.groupdict() if found else None

    def build(self, params: dict[str, str]) -> str:
        """Şablonu parametrelerle doldurur — parçalar yeniden kodlanır."""
        return _PARAM.sub(lambda m: quote(params[m.group(1)], safe=":~-._"), self.path)


@dataclass(frozen=True, slots=True)
class Resolved:
    operation: Operation
    params: dict[str, str]
    upstream_path: str


class Allowlist:
    """Yöntem başına dizinlenmiş liste. Sabit parçası çok olan şablon kazanır.

    `/v1/admin/resources/isbn-lookup` ile `/v1/admin/resources/{resourceId}`
    aynı yöntemi paylaşsaydı sabit yol öne geçerdi; sıra dosya sırasına
    bırakılmaz.
    """

    def __init__(self, operations: tuple[Operation, ...]) -> None:
        self.operations = operations
        self._by_method: dict[str, list[Operation]] = {}
        for operation in operations:
            self._by_method.setdefault(operation.method, []).append(operation)
        for items in self._by_method.values():
            items.sort(key=lambda op: (-op.literal_segments, op.path))
        self._by_id = {operation.operation_id: operation for operation in operations}

    def resolve(self, method: str, path: str) -> Resolved | None:
        clean = "/" + path.strip("/")
        for operation in self._by_method.get(method.upper(), ()):
            params = operation.match(clean)
            if params is not None:
                return Resolved(operation, params, operation.build(params))
        return None

    def by_id(self, operation_id: str) -> Operation | None:
        return self._by_id.get(operation_id)

    def __len__(self) -> int:
        return len(self.operations)
