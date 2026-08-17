"""SQLite → PostgreSQL çevirmeni.

Bu çevirmen yanlış çevirdiğinde belirti merkezde, sebepten çok uzakta çıkar:
satır sessizce yanlış tabloya/sütuna gider ya da hiç gitmez. Bu yüzden testler
iki yönlüdür — doğru çevirdiğini VE tanımadığını reddettiğini denetler.

En değerli test en sondadır: deponun GERÇEK 51 göç dosyası ve çekirdek şeması
çevrilir. Yeni bir modül çevrilemeyen bir SQL yazarsa kapı burada kapanır.
"""

from __future__ import annotations

import pathlib

import pytest

from km_core.store.db import CORE_SCHEMA
from km_core.store.dialect import UnsupportedDialect, to_postgres, translate_ddl

REPO = pathlib.Path(__file__).resolve().parents[2]


# --------------------------------------------------------------- yer tutucu

def test_soru_isaretleri_numaralanir() -> None:
    sql = "INSERT INTO users (id, name) VALUES (?, ?)"
    assert to_postgres(sql) == "INSERT INTO users (id, name) VALUES ($1, $2)"


def test_dize_sabitindeki_soru_isareti_korunur() -> None:
    """`'Kayıt var mı?'` bir yer tutucu DEĞİLDİR."""
    sql = "UPDATE t SET note = 'Kayıt var mı?' WHERE id = ?"
    assert to_postgres(sql) == "UPDATE t SET note = 'Kayıt var mı?' WHERE id = $1"


def test_ikilenen_tirnak_sabiti_bitirmez() -> None:
    sql = "UPDATE t SET note = 'iki '' tırnak ?' WHERE id = ?"
    assert to_postgres(sql).endswith("WHERE id = $1")
    assert "'iki '' tırnak ?'" in to_postgres(sql)


def test_yorumdaki_soru_isareti_korunur() -> None:
    sql = "SELECT 1 -- gerçekten? evet\nWHERE id = ?"
    result = to_postgres(sql)
    assert "-- gerçekten? evet" in result
    assert "WHERE id = $1" in result


def test_blok_yorumu_atlanir() -> None:
    sql = "/* soru? */ SELECT * FROM t WHERE a = ? AND b = ?"
    result = to_postgres(sql)
    assert "/* soru? */" in result
    assert "a = $1 AND b = $2" in result


def test_baslangic_numarasi_verilebilir() -> None:
    """`execute_many` ikinci satırdan devam ederken numaralandırma kayar."""
    assert to_postgres("VALUES (?, ?)", start=3) == "VALUES ($3, $4)"


# ------------------------------------------------------------------ zaman

def test_datetime_now_cevrilir() -> None:
    sql = "INSERT INTO t (at) VALUES (datetime('now'))"
    assert to_postgres(sql) == "INSERT INTO t (at) VALUES (now())"


def test_datetime_now_bosluklu_yazim() -> None:
    assert "now()" in to_postgres("SELECT datetime( 'now' )")


# ---------------------------------------------------------------- upsert

def test_insert_or_ignore() -> None:
    sql = "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)"
    assert to_postgres(sql) == (
        "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING"
    )


def test_insert_or_replace_birincil_anahtarla() -> None:
    sql = (
        "INSERT OR REPLACE INTO mod_bbd_lunch_roster (kantin_id, updated_at) "
        "VALUES (?, ?)"
    )
    result = to_postgres(sql, pk_of=lambda _table: ["kantin_id"])
    assert result.startswith("INSERT INTO mod_bbd_lunch_roster")
    assert 'ON CONFLICT ("kantin_id") DO UPDATE SET "updated_at" = EXCLUDED."updated_at"' in result


def test_insert_or_replace_cok_sutunlu_anahtar() -> None:
    sql = "INSERT OR REPLACE INTO t (a, b, c) VALUES (?, ?, ?)"
    result = to_postgres(sql, pk_of=lambda _t: ["a", "b"])
    assert 'ON CONFLICT ("a", "b") DO UPDATE SET "c" = EXCLUDED."c"' in result


def test_insert_or_replace_cozucusuz_reddedilir() -> None:
    """Çakışma hedefi tahmin EDİLMEZ; bilinmiyorsa ifade reddedilir."""
    with pytest.raises(UnsupportedDialect, match="birincil anahtar"):
        to_postgres("INSERT OR REPLACE INTO t (a) VALUES (?)")


def test_insert_or_replace_anahtar_sutunu_eksikse_reddedilir() -> None:
    with pytest.raises(UnsupportedDialect, match="taşımıyor"):
        to_postgres("INSERT OR REPLACE INTO t (b) VALUES (?)", pk_of=lambda _t: ["a"])


# ------------------------------------------------------------------- DDL

def test_autoincrement_bigserial_olur() -> None:
    ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL);"
    assert "id BIGSERIAL PRIMARY KEY" in translate_ddl(ddl)
    assert "AUTOINCREMENT" not in translate_ddl(ddl)


def test_ddl_yer_tutucu_cevirmez() -> None:
    """Göçte `?` olmaz; olsaydı yazım hatasıdır ve fark edilsin diye kalır."""
    assert "?" in translate_ddl("CREATE TABLE t (a TEXT DEFAULT '?');")


def test_ddl_insert_or_ignore_cevrilir() -> None:
    ddl = "INSERT OR IGNORE INTO roster_meta (id, revision) VALUES (1, 1);"
    result = translate_ddl(ddl)
    assert "ON CONFLICT DO NOTHING" in result
    assert "INSERT OR IGNORE" not in result


def test_ddl_ifade_ayirmada_dize_sabiti_korunur() -> None:
    ddl = "INSERT INTO t (a) VALUES ('nokta;virgül'); CREATE INDEX i ON t (a);"
    result = translate_ddl(ddl)
    assert "'nokta;virgül'" in result
    assert result.count("CREATE INDEX") == 1


# --------------------------------------------------------------- reddetme

@pytest.mark.parametrize("sql", [
    "SELECT strftime('%Y', at) FROM t",
    "SELECT julianday(at) FROM t",
    "SELECT json_extract(v, '$.a') FROM t",
    "SELECT GROUP_CONCAT(a) FROM t",
    "SELECT IFNULL(a, 0) FROM t",
    "PRAGMA journal_mode=WAL",
    "CREATE TABLE t (a TEXT COLLATE NOCASE)",
    "CREATE TABLE t (a TEXT) WITHOUT ROWID",
])
def test_tanimadigini_reddeder(sql: str) -> None:
    """Sessizce yanlış çevirmektense gürültülü patlamak yeğdir."""
    with pytest.raises(UnsupportedDialect):
        to_postgres(sql)


def test_reddetme_dize_sabitine_bakmaz() -> None:
    """`'strftime'` bir METİN olabilir; onun için ifade reddedilmez."""
    sql = "INSERT INTO t (note) VALUES ('strftime( kullanmayın')"
    assert to_postgres(sql) == sql


# ------------------------------------------------- deponun gerçek göçleri

def test_depodaki_tum_goc_dosyalari_cevrilir() -> None:
    """51 göç dosyasının hepsi PostgreSQL'e çevrilebilmeli.

    Yeni bir modül çevrilemeyen SQL yazarsa kapı BURADA kapanır — merkezde
    yarım kalan bir göçle değil.
    """
    files = sorted(REPO.glob("modules/*/backend/migrations/*.sql"))
    assert files, "göç dosyası bulunamadı — testin yolu bozulmuş"

    hatalar: list[str] = []
    for path in files:
        try:
            translate_ddl(path.read_text(encoding="utf-8"))
        except UnsupportedDialect as error:
            hatalar.append(f"{path.relative_to(REPO)}: {error}")
    assert not hatalar, "çevrilemeyen göç dosyaları:\n" + "\n".join(hatalar)


def test_cekirdek_semasi_cevrilir() -> None:
    result = translate_ddl(CORE_SCHEMA)
    assert "BIGSERIAL PRIMARY KEY" in result  # audit_log.id
    assert "AUTOINCREMENT" not in result
    assert "CREATE TABLE IF NOT EXISTS users" in result
