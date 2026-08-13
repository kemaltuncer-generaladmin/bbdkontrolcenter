"""Deneme Kulübü dönüşümleri — saf mantık, ağa çıkmaz.

Buradaki her test bir TUZAĞA karşılık gelir; testin adı tuzağın kendisidir.
"""

from __future__ import annotations

from store_trial_club_backend import trial

# ================================================================== metin

def test_turkce_harfler_karsilastirma_anahtarinda_bozulmaz() -> None:
    # `unicodedata` ile normalleştirmek `ı`yı boşa çıkarır ve `Işıl` → `sl`
    # olurdu; Türkçe harfler ÖNCE eşlenir.
    assert trial.fold("Ayşe ÖZTÜRK") == "ayse ozturk"
    assert trial.fold("Işıl") == "isil"
    assert trial.fold("  Ali   Veli ") == "ali veli"


def test_telefon_anahtari_yalniz_gecerli_cep_numarasi_uretir() -> None:
    assert trial.phone_key("0532 123 45 67") == "5321234567"
    assert trial.phone_key("+90 532 123 45 67") == "5321234567"
    assert trial.phone_key("5321234567") == "5321234567"
    # Sabit hat ve eksik numara BOŞ anahtar döner: yanlış kişiye sonuç
    # yazmaktansa eşleşmemek doğrudur.
    assert trial.phone_key("03121234567") == ""
    assert trial.phone_key("123") == ""


def test_maskeli_telefon_son_iki_haneyi_birakir() -> None:
    assert trial.mask_phone("05321234567") == "0532 *** ** 67"


# =================================================================== para

def test_ondalik_ucret_kurusa_cevrilirken_bir_kurus_kaybolmaz() -> None:
    assert trial.to_kurus("150.35") == 15035
    assert trial.to_kurus("1.250,00") == 125000
    assert trial.to_kurus("8.615") == 862


def test_bos_ucret_sifir_degil_none_dondurur() -> None:
    # 0 ile "ücret girilmemiş" farklı şeylerdir: birincisi ücretsiz deneme.
    assert trial.to_kurus(None) is None
    assert trial.to_kurus("") is None
    assert trial.to_kurus("0") == 0


# ============================================================== kontenjan

def test_kontenjan_sifirsa_sinirsiz_sayilir_yuzde_uydurulmaz() -> None:
    view = trial.capacity_view(12, 0)
    assert view["state"] == "unlimited"
    assert view["percent"] is None
    assert view["remaining"] is None


def test_kontenjan_durumlari_dolulukla_degisir() -> None:
    assert trial.capacity_view(0, 40)["state"] == "empty"
    assert trial.capacity_view(10, 40)["state"] == "filling"
    assert trial.capacity_view(36, 40)["state"] == "near"
    assert trial.capacity_view(40, 40)["state"] == "full"
    assert trial.capacity_view(42, 40)["state"] == "over"


def test_kontenjan_kayitlinin_altina_cekilemez() -> None:
    # Kabul edilseydi fazla kayıtlar asılı kalır, kimin sınava gireceği
    # belirsizleşir ve yoklama çizelgesi kontenjandan uzun çıkardı.
    problem = trial.capacity_change_error(20, 30)
    assert "30 kayıtlı" in problem
    assert trial.capacity_change_error(30, 30) == ""
    assert trial.capacity_change_error(0, 30) == ""      # 0 = sınırsız
    assert "negatif" in trial.capacity_change_error(-1, 0)


# =========================================================== deneme satırı

def test_kayit_penceresi_kapandiysa_kayit_kapali_gorunur() -> None:
    row = trial.exam_row({"id": 1, "name": "TYT", "exam_date": "2026-09-20",
                          "registration_start": "2026-08-01",
                          "registration_end": "2026-08-10", "capacity": 40, "enrolled": 5},
                         today="2026-08-13")
    assert row["registrationState"] == "closed"


def test_kontenjan_dolduysa_kayit_penceresi_acik_olsa_bile_dolu_gorunur() -> None:
    row = trial.exam_row({"id": 1, "name": "TYT", "exam_date": "2026-09-20",
                          "registration_end": "2026-09-15", "capacity": 40, "enrolled": 40},
                         today="2026-08-13")
    assert row["registrationState"] == "full"


def test_tarihi_gecmis_ve_yayinlanmamis_deneme_sonuc_bekliyor_der() -> None:
    row = trial.exam_row({"id": 1, "name": "TYT", "exam_date": "2026-08-01",
                          "results_published": False}, today="2026-08-13")
    assert row["resultsState"] == "pending"


def test_camelcase_alan_adlari_da_okunur() -> None:
    # BBD uçları yazılırken alan adları henüz sabitlenmedi; tek yazıma
    # bağlanmak uç yayınlandığında boş sütun göstermek olurdu.
    row = trial.exam_row({"id": 3, "name": "AYT", "examDate": "2026-10-01", "capacity": 20,
                          "enrolled": 4}, today="2026-08-13")
    assert row["examDate"] == "2026-10-01"
    assert row["capacity"]["label"] == "4/20"


def test_kayit_zamani_camelcase_gelirse_de_okunur() -> None:
    # CANLIDA DOĞRULANDI (2026-08-13, /api/admin/orders): mağaza alan adlarını
    # camelCase veriyor — `createdAt`. Düz `raw.get("created_at")` istisna
    # ATMAZ, sessizce boş döner ve ekranda "—" görünür; en sinsi hata budur.
    camel = trial.exam_row({"id": 4, "name": "TYT", "createdAt": "2026-08-13 18:27:17"},
                           today="2026-08-13")
    snake = trial.exam_row({"id": 4, "name": "TYT", "created_at": "2026-08-13 18:27:17"},
                           today="2026-08-13")
    assert camel["createdAt"] == "2026-08-13 18:27:17"
    assert snake["createdAt"] == camel["createdAt"]


# ==================================================================== CSV

def test_ayrac_baslik_satirindan_secilir() -> None:
    assert trial.detect_delimiter("Ad Soyad;Net;Puan") == ";"
    assert trial.detect_delimiter("Ad Soyad\tNet\tPuan") == "\t"
    assert trial.detect_delimiter("Ad Soyad,Net,Puan") == ","
    assert trial.detect_delimiter("tek sütun") == ";"


def test_sutun_adlari_esanlamlariyla_bulunur() -> None:
    columns = trial.map_columns(["Adı Soyadı", "E-Posta", "Cep", "Net", "Sıra"])
    assert columns == {"name": 0, "email": 1, "phone": 2, "net": 3, "rank": 4}


def test_turkce_ondalik_okunur_ve_cozulemeyen_sayi_sifir_olmaz() -> None:
    assert trial.parse_number("12,5") == 12.5
    assert trial.parse_number("12.5") == 12.5
    assert trial.parse_number("") is None
    assert trial.parse_number("yok") is None
    assert trial.parse_number("0") == 0.0


def test_kimlik_sutunu_olmayan_dosya_reddedilir() -> None:
    parsed = trial.parse_result_csv("Net;Puan\n12,5;340\n")
    assert parsed["ok"] is False
    assert "Kimlik sütunu" in parsed["error"]


def test_sonuc_sutunu_olmayan_dosya_reddedilir() -> None:
    parsed = trial.parse_result_csv("Ad Soyad;Şehir\nAli Veli;Ankara\n")
    assert parsed["ok"] is False
    assert "Sonuç sütunu" in parsed["error"]


def test_bom_ve_bos_satirlar_dosyayi_bozmaz() -> None:
    parsed = trial.parse_result_csv("﻿Ad Soyad;Net\n\nAli Veli;12,5\n\n")
    assert parsed["ok"] is True
    assert len(parsed["rows"]) == 1
    assert parsed["rows"][0]["net"] == 12.5
    assert parsed["rows"][0]["line"] == 2


def test_virgul_ayracli_dosya_uyari_uretir() -> None:
    # Türkçe ondalık virgüldür; virgülle ayrılmış dosyada `12,5` bölünmüş
    # olabilir ve bunu sessizce yutmak yanlış net yazmak demekti.
    parsed = trial.parse_result_csv("Ad Soyad,Net\nAli Veli,12\n")
    assert parsed["ok"] is True
    assert any("virgül" in note for note in parsed["warnings"])


def test_satir_sayisi_tavana_dayanirsa_soylenir() -> None:
    body = "Ad Soyad;Net\n" + "".join(f"Kisi {i};{i}\n" for i in range(10))
    parsed = trial.parse_result_csv(body, max_rows=4)
    assert parsed["truncated"] is True
    assert len(parsed["rows"]) == 4
    assert any("kesildi" in note for note in parsed["warnings"])


# ============================================================ eşleştirme

def _members() -> list[dict[str, object]]:
    return [
        {"id": 1, "name": "Ali Veli", "email": "ali@ornek.com", "phone": "05321234501",
         "code": "KY-001"},
        {"id": 2, "name": "Ayşe Öztürk", "email": "ayse@ornek.com", "phone": "05321234502",
         "code": "KY-002"},
        {"id": 3, "name": "Ali Veli", "email": "ali2@ornek.com", "phone": "05321234503",
         "code": "KY-003"},
    ]


def _rows(body: str) -> list[dict[str, object]]:
    parsed = trial.parse_result_csv(body)
    assert parsed["ok"] is True, parsed.get("error")
    return parsed["rows"]


def test_eslesme_sirasi_kayit_no_eposta_telefon_ad() -> None:
    rows = _rows("Kayıt No;E-posta;Telefon;Ad Soyad;Net\n"
                 "KY-002;yanlis@ornek.com;;Yanlış Ad;30\n")
    matches = trial.match_results(rows, _members())
    assert matches[0]["state"] == "matched"
    assert matches[0]["memberId"] == 2
    assert matches[0]["matchedBy"] == "kayıt no"


def test_ad_belirsizse_hicbiri_secilmez() -> None:
    # Aynı ada sahip iki katılımcı var; sessizce ilkini seçmek kardeşlerin
    # sonucunu birbirine yazmak demekti.
    rows = _rows("Ad Soyad;Net\nAli Veli;35\n")
    matches = trial.match_results(rows, _members())
    assert matches[0]["state"] == "ambiguous"
    assert matches[0]["memberId"] == 0
    assert "2 katılımcı" in matches[0]["note"]


def test_ayni_kisi_iki_satirda_gelirse_ikincisi_mukerrer_sayilir() -> None:
    rows = _rows("E-posta;Net\nayse@ornek.com;30\nayse@ornek.com;42\n")
    matches = trial.match_results(rows, _members())
    assert matches[0]["state"] == "matched"
    assert matches[1]["state"] == "duplicate"
    assert "2. satırda" in matches[1]["note"]


def test_listede_olmayan_kisi_sessizce_atlanmaz() -> None:
    rows = _rows("E-posta;Net\nyabanci@ornek.com;30\n")
    matches = trial.match_results(rows, _members())
    assert matches[0]["state"] == "unmatched"
    assert "bulunamadı" in matches[0]["note"]


def test_eslesse_bile_sonuc_degeri_yoksa_yazilacak_sey_yok() -> None:
    rows = _rows("E-posta;Net;Puan\nayse@ornek.com;;\n")
    matches = trial.match_results(rows, _members())
    assert matches[0]["state"] == "empty"


def test_yalniz_eslesen_satirlar_magazaya_gider() -> None:
    rows = _rows("E-posta;Net;Puan\n"
                 "ayse@ornek.com;42,5;380\n"
                 "yabanci@ornek.com;10;100\n"
                 "ayse@ornek.com;12;120\n")
    matches = trial.match_results(rows, _members())
    body = trial.upload_rows(matches)
    assert body == [{"memberId": 2, "net": 42.5, "score": 380.0}]


def test_dosyada_karsiligi_olmayan_katilimcilar_listelenir() -> None:
    rows = _rows("E-posta;Net\nayse@ornek.com;42\n")
    matches = trial.match_results(rows, _members())
    missing = trial.missing_members(matches, _members())
    assert [item["id"] for item in missing] == [1, 3]


def test_ozet_her_durumu_ayri_sayar() -> None:
    rows = _rows("E-posta;Net\nayse@ornek.com;30\nayse@ornek.com;31\nyok@ornek.com;5\n")
    matches = trial.match_results(rows, _members())
    summary = trial.match_summary(matches, member_count=3)
    assert summary["matched"] == 1
    assert summary["duplicate"] == 1
    assert summary["unmatched"] == 1
    assert summary["writable"] == 1


def test_telefonu_bozuk_satir_telefondan_eslesmez() -> None:
    rows = _rows("Telefon;Net\n0312 555 44 33;30\n")
    matches = trial.match_results(rows, _members())
    assert matches[0]["state"] == "unmatched"


# ================================================================= sonuç

def test_sira_gelmediyse_puandan_uretilir_ve_beraberlik_ayni_siradir() -> None:
    rows = trial.derive_ranks([
        {"id": 1, "score": 380.0, "net": 42.0, "rank": None},
        {"id": 2, "score": 380.0, "net": 42.0, "rank": None},
        {"id": 3, "score": 300.0, "net": 30.0, "rank": None},
    ])
    assert [row["rank"] for row in rows] == [1, 1, 3]


def test_gelen_sira_uretilenle_ezilmez() -> None:
    rows = trial.derive_ranks([{"id": 1, "score": 100.0, "rank": 7}])
    assert rows[0]["rank"] == 7


def test_istatistik_bos_veriden_sifir_uydurmaz() -> None:
    stats = trial.score_stats([{"id": 1, "net": None, "score": None, "attendance": "absent"}])
    assert stats["netAverage"] is None
    assert stats["scoreBest"] is None
    assert stats["attended"] == 0


# ============================================================= yoklama

def test_yoklama_cizelgesinde_telefon_ve_eposta_basilmaz() -> None:
    # Çizelge sınıfta elden ele dolaşır; iletişim bilgisi orada işe yaramaz
    # ama kişisel veriyi kâğıda çıkarır.
    rows = trial.attendance_rows([
        {"name": "Zeynep Ak", "grade": "12", "code": "KY-9", "phone": "05321234501",
         "email": "z@ornek.com"},
    ])
    assert rows == [["1", "Zeynep Ak", "12", "KY-9", ""]]


def test_yoklama_alfabetik_siralanir_ve_bos_satir_eklenir() -> None:
    rows = trial.attendance_rows([{"name": "Zeynep"}, {"name": "Ali"}], spare=2)
    assert [row[1] for row in rows] == ["Ali", "Zeynep", "", ""]
    assert [row[0] for row in rows] == ["1", "2", "3", "4"]


# ============================================================== bildirim

def test_kitle_secimi_katilimciyi_dogru_suzer() -> None:
    people = [
        {"id": 1, "payment": "pending", "attendance": "absent"},
        {"id": 2, "payment": "paid", "attendance": "attended"},
    ]
    assert [row["id"] for row in trial.audience_members(people, "unpaid")] == [1]
    assert [row["id"] for row in trial.audience_members(people, "attended")] == [2]
    assert [row["id"] for row in trial.audience_members(people, "absent")] == [1]
    assert len(trial.audience_members(people, "all")) == 2
    assert trial.audience_members(people, "uydurma") == []


def test_ulasilamayan_alici_gonderildi_sayilmaz() -> None:
    people = [{"id": 1, "name": "A", "phone": "05321234501", "email": "a@ornek.com"},
              {"id": 2, "name": "B", "phone": "0312 555 44 33", "email": ""}]
    split = trial.recipients(people, "sms")
    assert [row["memberId"] for row in split["reachable"]] == [1]
    assert split["unreachable"][0]["reason"] == "Telefon numarası geçersiz"

    by_mail = trial.recipients(people, "email")
    assert [row["memberId"] for row in by_mail["reachable"]] == [1]


# ================================================================ gerekçe

def test_kisa_gerekce_reddedilir() -> None:
    assert trial.reason_error("ok") != ""
    assert trial.reason_error("Sonuçlar kuruma göre düzeltildi") == ""
