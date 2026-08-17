"""SQLite lehçesini PostgreSQL'e çevirir.

NEDEN VAR. Yerel ayna SQLite kalır ve modüllerin yazdığı SQL değişmez; aynı
ifade merkezdeki PostgreSQL'e de gitmek zorundadır. Çevrim tek yerde, burada
yapılır — 49 modülün 383 SQL ifadesine ve 51 göç dosyasına dokunulmaz (K6).

## Kapsam ölçüldü, geniş tutulmadı

Bu çevirmen GENEL AMAÇLI DEĞİLDİR. Depodaki SQL tarandı ve yalnız şunlar
bulundu; başkası çıkarsa `UnsupportedDialect` ile GÜRÜLTÜLÜ patlar, sessizce
yanlış çevirmez:

    ?  yer tutucu               → $1..$n
    INTEGER PRIMARY KEY AUTOINCREMENT → BIGSERIAL PRIMARY KEY
    INSERT OR IGNORE            → INSERT ... ON CONFLICT DO NOTHING
    INSERT OR REPLACE           → INSERT ... ON CONFLICT (pk) DO UPDATE SET ...
    datetime('now')             → now()

Göç dosyalarındaki sütun tipleri yalnız TEXT/INTEGER/REAL; yabancı anahtar,
COLLATE, WITHOUT ROWID ve GENERATED hiç kullanılmıyor. `strftime` ve
`json_extract` SQL'de değil, Python tarafında.

## Dize sabitleri atlanır

Tarayıcı tek geçişlidir ve `'...'` sabitlerinin, `"..."` tanımlayıcılarının,
`--` ve `/* */` yorumlarının içine GİRMEZ. Bunu yapmayan bir "?" değiştirici,
`'soru? var'` gibi bir metni bozardı.

TEK İSTİSNA `datetime('now')`: kendisi bir dize sabiti İÇERİR, bu yüzden
tarayıcıdan önce ön geçişte değiştirilir. Bir kullanıcı metni birebir
`datetime('now')` yazısını taşısaydı yanlış çevrilirdi — depoda öyle bir metin
yok ve olması da anlamsız.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

__all__ = ["UnsupportedDialect", "to_postgres", "translate_ddl"]


class UnsupportedDialect(ValueError):
    """Çevirmenin tanımadığı bir SQLite yapısı görüldü.

    Sessizce yanlış çevirmektense reddetmek yeğdir: yanlış çeviri merkezde
    yanlış veri demektir ve belirtisi sebebinden çok uzakta çıkar.
    """


# `datetime('now')` — boşluk ve büyük/küçük harf serbest.
_DATETIME_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)

# ISO-8601 UTC damgası. Çekirdek izin göçleri (0008, 0009) denetim izine bunu
# yazar. Kalıp TEK VE KAPALIDIR: yalnız bu birebir biçim çevrilir, başka her
# `strftime` kullanımı reddedilmeye devam eder. Göç metnini değiştirmek yerine
# çevirmene kural eklendi — koşmuş bir göçün gövdesi tarihtir, düzeltilmez.
_STRFTIME_ISO_UTC = re.compile(
    r"strftime\(\s*'%Y-%m-%dT%H:%M:%S\+00:00'\s*,\s*'now'\s*\)", re.IGNORECASE
)
_STRFTIME_ISO_UTC_PG = (
    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00')"
)

# `INTEGER PRIMARY KEY AUTOINCREMENT` — SQLite'ın kendi kendine artan anahtarı.
_AUTOINCREMENT = re.compile(
    r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE
)

# SQLite'ın `INTEGER`'ı 64 BİTTİR, PostgreSQL'inki 32. Milisaniyelik zaman
# damgası ya da büyük sayaç taşan bir sütuna düşerse hata YAZMA ANINDA çıkar ve
# sebebi ("int4 aralığı") veriyle ilgisiz görünür. Bu yüzden tip BIGINT'e
# genişletilir — SQLite tarafında karşılığı değişmez.
_INTEGER_COLUMN = re.compile(r"\bINTEGER\b(?!\s+PRIMARY\s+KEY\s+AUTOINCREMENT)", re.IGNORECASE)

# Başka bir yerde geçen yalnız `AUTOINCREMENT` — yukarıdaki kalıba uymuyorsa
# çeviremeyiz.
_STRAY_AUTOINCREMENT = re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE)

_INSERT_OR = re.compile(r"^\s*INSERT\s+OR\s+(IGNORE|REPLACE)\s+INTO\s+", re.IGNORECASE)

# NULL-GÜVENLİ KARŞILAŞTIRMA. SQLite `x IS ?` / `x IS NOT ?` yazar; PostgreSQL
# bunu `IS [NOT] DISTINCT FROM` ile yapar ve `IS NOT $2` SÖZDİZİMİ HATASIDIR.
# Yalnız yer tutucu gelen biçim çevrilir: `IS NOT NULL` ikisinde de geçerlidir
# ve ELLENMEZ.
_IS_NOT_PLACEHOLDER = re.compile(r"\bIS\s+NOT\s+\?", re.IGNORECASE)
_IS_PLACEHOLDER = re.compile(r"\bIS\s+\?", re.IGNORECASE)

# SESSİZ DAVRANIŞ FARKI — hata vermez, YANLIŞ SONUÇ verir.
# SQLite'ta `LIKE` ASCII harflerde büyük/küçük harf duyarsızdır; PostgreSQL'de
# duyarlıdır. Çevrilmezse "ahmet" araması "Ahmet"i bulmaz ve kimse bunun bir
# lehçe farkı olduğunu anlamaz. `ILIKE` SQLite'ın davranışını korur, üstelik
# Türkçe harflerde SQLite'tan da doğru çalışır.
_LIKE = re.compile(r"\bLIKE\b", re.IGNORECASE)

# `INSERT [OR ...] INTO <tablo> (<sütunlar>)` — tablo ve sütun listesini alır.
_INSERT_TARGET = re.compile(
    r"^\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+([\"'`]?)([a-zA-Z0-9_.]+)\1\s*\(([^)]*)\)",
    re.IGNORECASE,
)

# Çevrilemeyen, ama sessizce geçerse zarar veren SQLite'a özgü yapılar.
# Hem DML hem DDL için geçerli.
_KNOWN_UNSUPPORTED = (
    (re.compile(r"\bWITHOUT\s+ROWID\b", re.IGNORECASE), "WITHOUT ROWID"),
    (re.compile(r"\bCOLLATE\s+NOCASE\b", re.IGNORECASE), "COLLATE NOCASE"),
    (re.compile(r"\bstrftime\s*\(", re.IGNORECASE), "strftime()"),
    (re.compile(r"\bjulianday\s*\(", re.IGNORECASE), "julianday()"),
    (re.compile(r"\bjson_extract\s*\(", re.IGNORECASE), "json_extract()"),
    (re.compile(r"\bGROUP_CONCAT\s*\(", re.IGNORECASE), "GROUP_CONCAT()"),
    (re.compile(r"\bIFNULL\s*\(", re.IGNORECASE), "IFNULL()"),
    (re.compile(r"\bPRAGMA\b", re.IGNORECASE), "PRAGMA"),
    # SQLite'ın örtük satır kimliği. PostgreSQL'de karşılığı YOKTUR ve
    # uydurulamaz: `ctid` fiziksel adrestir, satır güncellenince değişir.
    # `ORDER BY rowid` yazan sorgu gerçek bir sütuna geçirilmelidir
    # (`created_at` + birincil anahtar). Üretimde 500 vermektense burada
    # patlasın.
    (re.compile(r"\browid\b", re.IGNORECASE), "rowid"),
)


def _segments(sql: str) -> list[tuple[str, bool]]:
    """SQL'i (metin, kod_mu) parçalarına böler.

    `kod_mu` False olan parçalar dize sabiti, tırnaklı tanımlayıcı ya da
    yorumdur; onlara dokunulmaz.
    """
    parts: list[tuple[str, bool]] = []
    buffer: list[str] = []
    index = 0
    length = len(sql)

    def flush() -> None:
        if buffer:
            parts.append(("".join(buffer), True))
            buffer.clear()

    while index < length:
        char = sql[index]

        # -- satır yorumu
        if char == "-" and sql.startswith("--", index):
            flush()
            end = sql.find("\n", index)
            end = length if end == -1 else end
            parts.append((sql[index:end], False))
            index = end
            continue

        # /* blok yorumu */
        if char == "/" and sql.startswith("/*", index):
            flush()
            end = sql.find("*/", index + 2)
            end = length if end == -1 else end + 2
            parts.append((sql[index:end], False))
            index = end
            continue

        # 'dize sabiti' — içinde '' ikilemesi kaçış demektir
        if char == "'":
            flush()
            cursor = index + 1
            while cursor < length:
                if sql[cursor] == "'":
                    if cursor + 1 < length and sql[cursor + 1] == "'":
                        cursor += 2
                        continue
                    cursor += 1
                    break
                cursor += 1
            parts.append((sql[index:cursor], False))
            index = cursor
            continue

        # "tırnaklı tanımlayıcı" — PostgreSQL'de de aynı anlama gelir
        if char == '"':
            flush()
            cursor = index + 1
            while cursor < length:
                if sql[cursor] == '"':
                    if cursor + 1 < length and sql[cursor + 1] == '"':
                        cursor += 2
                        continue
                    cursor += 1
                    break
                cursor += 1
            parts.append((sql[index:cursor], False))
            index = cursor
            continue

        buffer.append(char)
        index += 1

    flush()
    return parts


def _renumber_placeholders(sql: str, start: int = 1) -> tuple[str, int]:
    """`?` yer tutucularını `$1..$n` yapar. Dize sabitlerine dokunmaz."""
    counter = start
    output: list[str] = []
    for text, is_code in _segments(sql):
        if not is_code:
            output.append(text)
            continue
        chunk: list[str] = []
        for char in text:
            if char == "?":
                chunk.append(f"${counter}")
                counter += 1
            else:
                chunk.append(char)
        output.append("".join(chunk))
    return "".join(output), counter


def _code_only(sql: str) -> str:
    """Dize sabitleri ve yorumlar boşaltılmış hâli — denetimler bunun üstünde."""
    return "".join(text if is_code else " " * len(text) for text, is_code in _segments(sql))


def _map_code(sql: str, transform: Callable[[str], str]) -> str:
    """Dönüşümü YALNIZ kod parçalarına uygular; sabit ve yorumlara dokunmaz."""
    return "".join(
        transform(text) if is_code else text for text, is_code in _segments(sql)
    )


def _time_functions(sql: str) -> str:
    """SQLite'ın zaman fonksiyonlarını PostgreSQL karşılıklarına çevirir.

    Bu ön geçiş `_reject_unsupported`ten ÖNCE koşar: her ikisi de kendi içinde
    dize sabiti taşır, dolayısıyla tarayıcıya bırakılamaz ve çevrilmeden
    denetime girseler tanınmayan yapı diye reddedilirlerdi.
    """
    text = _DATETIME_NOW.sub("now()", sql)
    # Değiştirme metni lambda ile verilir: düz dize verilseydi `re` içindeki
    # ters bölü/grup kaçışlarını yorumlardı.
    return _STRFTIME_ISO_UTC.sub(lambda _match: _STRFTIME_ISO_UTC_PG, text)


def _operators(sql: str) -> str:
    """Karşılığı farklı yazılan işleçleri çevirir. YALNIZ kod parçalarında."""

    def transform(part: str) -> str:
        part = _IS_NOT_PLACEHOLDER.sub("IS DISTINCT FROM ?", part)
        part = _IS_PLACEHOLDER.sub("IS NOT DISTINCT FROM ?", part)
        return _LIKE.sub("ILIKE", part)

    return _map_code(sql, transform)


def _reject_unsupported(sql: str) -> None:
    code = _code_only(sql)
    for pattern, label in _KNOWN_UNSUPPORTED:
        if pattern.search(code):
            raise UnsupportedDialect(
                f"'{label}' PostgreSQL'e çevrilemiyor; SQL elle uyarlanmalı."
            )


def _rewrite_insert_or(sql: str, pk_of: Callable[[str], Sequence[str]] | None) -> str:
    """`INSERT OR IGNORE/REPLACE` → `ON CONFLICT` biçimine çevirir."""
    match = _INSERT_OR.match(sql)
    if match is None:
        return sql

    kind = match.group(1).upper()
    body = _INSERT_OR.sub("INSERT INTO ", sql, count=1)

    if kind == "IGNORE":
        return f"{body.rstrip().rstrip(';')} ON CONFLICT DO NOTHING"

    # REPLACE: çakışma sütunlarını bilmeden yazılamaz.
    target = _INSERT_TARGET.match(sql)
    if target is None:
        raise UnsupportedDialect(
            "INSERT OR REPLACE sütun listesi olmadan çevrilemez."
        )
    table = target.group(2)
    columns = [name.strip().strip('"').strip("`") for name in target.group(3).split(",")]

    if pk_of is None:
        raise UnsupportedDialect(
            f"'{table}' için INSERT OR REPLACE çevrilemedi: birincil anahtar "
            "çözücüsü verilmedi."
        )
    keys = [str(key) for key in pk_of(table)]
    if not keys:
        raise UnsupportedDialect(
            f"'{table}' tablosunun birincil anahtarı yok; INSERT OR REPLACE "
            "çakışma hedefi belirlenemiyor."
        )
    missing = [key for key in keys if key not in columns]
    if missing:
        raise UnsupportedDialect(
            f"'{table}' INSERT OR REPLACE ifadesi birincil anahtar sütunlarını "
            f"taşımıyor: {', '.join(missing)}."
        )

    updates = [f'"{name}" = EXCLUDED."{name}"' for name in columns if name not in keys]
    conflict = ", ".join(f'"{name}"' for name in keys)
    tail = (
        f" ON CONFLICT ({conflict}) DO UPDATE SET {', '.join(updates)}"
        if updates
        else f" ON CONFLICT ({conflict}) DO NOTHING"
    )
    return f"{body.rstrip().rstrip(';')}{tail}"


def to_postgres(
    sql: str,
    *,
    pk_of: Callable[[str], Sequence[str]] | None = None,
    start: int = 1,
) -> str:
    """Tek bir SQLite DML ifadesini PostgreSQL'e çevirir.

    `pk_of` yalnız `INSERT OR REPLACE` için gerekir: tablonun birincil anahtar
    sütunlarını döndürür, çakışma hedefi ondan yazılır.
    """
    text = _time_functions(sql)
    _reject_unsupported(text)
    if _STRAY_AUTOINCREMENT.search(_code_only(text)):
        raise UnsupportedDialect("AUTOINCREMENT bir DML ifadesinde bulunamaz.")
    text = _operators(text)
    text = _rewrite_insert_or(text, pk_of)
    translated, _ = _renumber_placeholders(text, start)
    return translated


def translate_ddl(sql: str) -> str:
    """Göç betiğini (çok ifadeli DDL) PostgreSQL'e çevirir.

    Göçlerde yer tutucu bulunmaz — parametre alan bir DDL yoktur — bu yüzden
    `?` dönüşümü UYGULANMAZ; göçteki bir `?` yazım hatasıdır ve öyle kalır ki
    fark edilsin.
    """
    text = _operators(_time_functions(sql))
    _reject_unsupported(text)
    text = _AUTOINCREMENT.sub("BIGSERIAL PRIMARY KEY", text)
    text = _map_code(text, lambda part: _INTEGER_COLUMN.sub("BIGINT", part))

    if _STRAY_AUTOINCREMENT.search(_code_only(text)):
        raise UnsupportedDialect(
            "AUTOINCREMENT yalnız 'INTEGER PRIMARY KEY AUTOINCREMENT' biçiminde "
            "çevrilebiliyor."
        )

    # `INSERT OR IGNORE` göçlerde de geçer (çekirdek izin göçleri, roster_meta).
    statements = [chunk for chunk in _split_statements(text) if chunk.strip()]
    rewritten = [
        _rewrite_insert_or(chunk, None) if _INSERT_OR.match(chunk) else chunk
        for chunk in statements
    ]
    return ";\n".join(part.strip().rstrip(";") for part in rewritten) + ";"


def _split_statements(sql: str) -> list[str]:
    """`;` ile ayırır — dize sabiti ve yorum içindeki `;` sayılmaz."""
    statements: list[str] = []
    current: list[str] = []
    for text, is_code in _segments(sql):
        if not is_code:
            current.append(text)
            continue
        chunk: list[str] = []
        for char in text:
            if char == ";":
                current.append("".join(chunk))
                chunk.clear()
                statements.append("".join(current))
                current.clear()
            else:
                chunk.append(char)
        current.append("".join(chunk))
    if current:
        statements.append("".join(current))
    return statements
