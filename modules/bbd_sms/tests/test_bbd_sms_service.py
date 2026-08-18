"""SMS Sistemi — iş kuralları ve segment hesabı.

Modülün üç güvencesi var (`backend/service.py` başlığı) ve bu dosyanın işi
üçünü de kendi kendine kanıtlamak:

1. **Alıcı daima veli.** Velisi olmayan öğrenciye SMS gitmez ve bu bir sessiz
   düşme değildir — atlanan satır kayda geçer.
2. **Kör gönderim yok.** Kuru provada kantine TEK BİR çağrı bile gitmez.
3. **Geçmiş bizde.** Kantin gönderdiğini hiçbir yere yazmıyor; kaydın tek
   yeri bu modülün deposu.

HİÇBİR TEST GERÇEK SMS GÖNDERMEZ: kantin geçidi bütünüyle taklit edilir ve
taklidin gönderim metodu yalnız çağrıyı kaydeder.
"""

from __future__ import annotations

import pytest
from bbd_sms_backend import segments as seg
from bbd_sms_backend.service import SmsService
from bbd_sms_fakes import (
    STUDENT,
    STUDENT_NO_PHONE,
    FakeCanteen,
    FakeLog,
    FakeStore,
)

AKTOR = "Ayşe Yılmaz"


def kur(canteen: FakeCanteen | None = None, store: FakeStore | None = None,
        **config: object) -> tuple[SmsService, FakeCanteen, FakeStore]:
    canteen = canteen or FakeCanteen()
    store = store or FakeStore()
    service = SmsService(canteen=canteen, store=store, log=FakeLog(),
                         config={"school_name": "Deneme Koleji", **config})
    return service, canteen, store


def gonderim(**fields: object) -> dict[str, object]:
    """Panelin gönderdiği gövdenin tam şekli (`SendBody.model_dump`)."""
    return {
        "students": ["101"], "body": "Merhaba {ad}", "title": "", "includeDebt": False,
        "includeDaily": False, "simplify": False, "classes": {}, "dryRun": False,
        **fields,
    }


# ======================================================== segment hesabı
#
# SEGMENT SAYISI DOĞRUDAN PARADIR. Türkçe bir harf metni UCS-2'ye düşürür ve
# tek segment 160 değil 70 karakter olur; 500 veliye giden bir duyuruda fark
# bin SMS'tir. Aşağıdaki testler sınırın hangi karakterde döndüğünü sabitler.

def test_gsm7_metin_160_karakterde_tek_segment() -> None:
    assert seg.measure("A" * 160)["segments"] == 1
    assert seg.measure("A" * 161)["segments"] == 2


def test_tek_turkce_harf_metni_ucs2ye_dusurur() -> None:
    # `ş` GSM-7 tablosunda YOK: 100 karakterlik masum bir metin, tek harf
    # yüzünden 1 kredi yerine 2 krediye mal olur.
    olcum = seg.measure("A" * 99 + "ş")
    assert olcum["gsm7"] is False
    assert olcum["segments"] == 2
    assert olcum["offending"] == ["ş"]


def test_sadelestirme_kazanci_olcude_gorunur() -> None:
    # Ekran "sadeleştirirsen şu kadar düşer" diyebilsin diye ölçü kendi
    # sadeleştirilmiş karşılığını da taşır.
    olcum = seg.measure("A" * 99 + "ş")
    assert olcum["simplifiedSegments"] == 1
    assert seg.simplify("İşçi ğaz") == "Isci gaz"


def test_gsm7_genisletme_karakteri_iki_birim_sayilir() -> None:
    # `{` ve `}` GSM-7'de ESC öneki alır; sayacın bunu gizlemesi maliyet
    # ekranını olduğundan iyimser gösterirdi.
    assert seg.measure("{}")["units"] == 4
    assert seg.measure("{}")["chars"] == 2


def test_bos_metin_sifir_segment() -> None:
    # Sıfır DEĞİL bir demek, boş bir gönderimi ücretli göstermek olurdu.
    assert seg.measure("")["segments"] == 0


# ============================================================== önizleme

@pytest.mark.asyncio
async def test_onizleme_yer_tutuculari_cozer_ve_maliyeti_yazar() -> None:
    service, _, _ = kur()
    cevap = await service.preview(gonderim(body="Sayın veli, {ad} ({okul}) borcu {borc}"))

    assert cevap["ok"] is True
    satir = cevap["rows"][0]
    assert satir["verdict"] == "ready"
    assert "Ali Demir" in satir["text"]
    assert "Deneme Koleji" in satir["text"]
    # `{borc}` kuruştan TL'ye çevrilir; ham kuruş göstermek veliye anlamsız
    # bir sayı yollamak olurdu.
    assert "45,50 TL" in satir["text"]
    assert cevap["summary"]["eligible"] == 1
    assert cevap["summary"]["credits"] == satir["segments"]


@pytest.mark.asyncio
async def test_velisi_olmayan_ogrenci_sessizce_dusmez_atlanir() -> None:
    # ALICI DAİMA VELİDİR. Numarası olmayan satır listeden çıkarılsaydı,
    # yönetici gönderdiğini sandığından az kişiye ulaşırdı.
    canteen = FakeCanteen([dict(STUDENT), dict(STUDENT_NO_PHONE)])
    service, _, _ = kur(canteen)

    cevap = await service.preview(gonderim(students=["101", "102"]))
    kararlar = {row["kantinId"]: row["verdict"] for row in cevap["rows"]}
    assert kararlar == {"101": "ready", "102": "skipped"}
    assert cevap["summary"]["eligible"] == 1
    assert cevap["summary"]["skipped"] == 1


@pytest.mark.asyncio
async def test_kantinde_olmayan_ogrenci_atlanir() -> None:
    service, _, _ = kur()
    cevap = await service.preview(gonderim(students=["999"]))
    assert cevap["rows"][0]["verdict"] == "skipped"
    assert cevap["summary"]["eligible"] == 0


@pytest.mark.asyncio
async def test_bos_mesaj_reddedilir() -> None:
    service, canteen, _ = kur()
    cevap = await service.preview(gonderim(body="   "))
    assert cevap["ok"] is False
    # İSTEK HİÇ ÇIKMADI: boş bir metin için kantini yormanın anlamı yok.
    assert canteen.sent == []


@pytest.mark.asyncio
async def test_kantin_dusunce_onizleme_patlamaz() -> None:
    canteen = FakeCanteen()
    canteen.fail.add("students")
    service, _, _ = kur(canteen)

    cevap = await service.preview(gonderim())
    # K7: ekran beyaz hata sayfası değil, sebebi yazan bir kutu görür.
    assert cevap["ok"] is False
    assert cevap["rows"] == []


# ================================================================ gönderim

@pytest.mark.asyncio
async def test_kuru_provada_kantine_tek_cagri_bile_gitmez() -> None:
    service, canteen, store = kur()
    cevap = await service.send(gonderim(dryRun=True), actor=AKTOR)

    assert cevap["dryRun"] is True
    # SAYIYA DEĞİL LİSTENİN BOŞLUĞUNA BAKILIR: "sıfır gönderildi" ile "hiç
    # denenmedi" farklı şeyler ve müşterinin gördüğü ikincisidir.
    assert canteen.sent == []
    assert store.batches == {}


@pytest.mark.asyncio
async def test_gonderim_kaydi_bizde_tutulur() -> None:
    # ÜÇÜNCÜ GÜVENCE: kantinin `POST /api/sms/send` ucu gönderdiğini hiçbir
    # yere yazmıyor. Ne gönderdiğimizin tek kaydı burada.
    service, canteen, store = kur()
    cevap = await service.send(gonderim(), actor=AKTOR)

    assert cevap["sent"] is True
    assert cevap["sentCount"] == 1
    assert len(canteen.sent) == 1
    obek = store.batches[cevap["batchRef"]]
    assert obek["created_by"] == AKTOR
    assert obek["sent_count"] == 1
    assert obek["fail_count"] == 0
    assert store.statuses() == ["sent"]


@pytest.mark.asyncio
async def test_atlanan_alicilar_da_kayda_gecer() -> None:
    # "GÖNDERMEDİK" İLE "GÖNDEREMEDİK" AYRIMI: atlanan satır yazılmasaydı
    # "bu veliye haber verildi mi" sorusunun cevabı "kayıt yok" olurdu.
    canteen = FakeCanteen([dict(STUDENT), dict(STUDENT_NO_PHONE)])
    service, _, store = kur(canteen)

    await service.send(gonderim(students=["101", "102"]), actor=AKTOR)
    assert sorted(store.statuses()) == ["sent", "skipped"]


@pytest.mark.asyncio
async def test_saglayici_reddederse_diger_aliciler_surer() -> None:
    canteen = FakeCanteen([dict(STUDENT), {**STUDENT, "id": "103",
                                           "displayName": "Can Ak"}])
    canteen.reject_after = 1
    service, _, store = kur(canteen)

    cevap = await service.send(gonderim(students=["101", "103"]), actor=AKTOR)
    # Biri düştü, öteki gitti: tek bir hata bütün gönderimi durdurmuyor.
    assert cevap["sentCount"] == 1
    assert cevap["failCount"] == 1
    assert sorted(store.statuses()) == ["failed", "sent"]


@pytest.mark.asyncio
async def test_alici_siniri_gonderimi_durdurur() -> None:
    # KAZAYLA TÜM OKULA SMS ATMAYI ZORLAŞTIRIR. Sınır ayardan gelir ve
    # aşıldığında istek hiç çıkmaz.
    canteen = FakeCanteen([dict(STUDENT), {**STUDENT, "id": "103"}])
    service, canteen, _ = kur(canteen, max_recipients=1)

    cevap = await service.send(gonderim(students=["101", "103"]), actor=AKTOR)
    assert cevap["sent"] is False
    assert canteen.sent == []


@pytest.mark.asyncio
async def test_uygun_alici_yoksa_gonderim_yapilmaz() -> None:
    canteen = FakeCanteen([dict(STUDENT_NO_PHONE)])
    service, canteen, store = kur(canteen)

    cevap = await service.send(gonderim(students=["102"]), actor=AKTOR)
    assert cevap["sent"] is False
    assert canteen.sent == []
    assert store.batches == {}


@pytest.mark.asyncio
async def test_on_ek_cumleleri_kantine_iki_kez_yazilmaz() -> None:
    """`includeDebt` cümlesini kantin KENDİSİ ekliyor.

    Önizleme onu `text` içinde gösteriyor (velinin göreceği tam metin) ama
    kantine giden `message` alanına KOYMUYOR. İkisi ayrışmasaydı borç cümlesi
    mesajda iki kez yazılır ve segment sayısı da yanlış çıkardı.
    """
    service, canteen, _ = kur()
    await service.send(gonderim(includeDebt=True), actor=AKTOR)

    giden = canteen.sent[0]
    assert giden["include_debt"] is True
    assert "güncel borç" not in str(giden["message"])


# ================================================================ ayarlar

@pytest.mark.asyncio
async def test_bos_parola_mevcut_parolayi_ezmez() -> None:
    # Sunucu parolayı geri vermiyor; panel alanı boş bırakınca "sil" değil
    # "dokunma" anlaşılmalı. Aksi hâlde başlığı düzeltmek parolayı silerdi.
    service, canteen, _ = kur()
    cevap = await service.update_netgsm({"netgsmHeader": "BLEZZETDNYM",
                                         "netgsmPassword": ""})

    assert cevap["ok"] is True
    assert canteen.updated == [{"netgsmHeader": "BLEZZETDNYM"}]


@pytest.mark.asyncio
async def test_degisiklik_yoksa_ayar_yazilmaz() -> None:
    service, canteen, _ = kur()
    cevap = await service.update_netgsm({})
    assert cevap["ok"] is False
    assert canteen.updated == []


@pytest.mark.asyncio
async def test_netgsm_eksikse_acilis_ekrani_bunu_soyler() -> None:
    # ÜÇÜ BİRDEN DOLU DEĞİLSE HİÇBİR SMS GİTMEZ; ekran bunu baştan söylemeli,
    # yoksa "gönderildi" yazan bir satır hiçbir şey göndermemiş olur.
    canteen = FakeCanteen()
    canteen.settings_payload["netgsmHeader"] = ""
    service, _, _ = kur(canteen)

    cevap = await service.workspace()
    assert cevap["netgsm"]["ready"] is False


@pytest.mark.asyncio
async def test_kantin_dusunce_acilis_ekrani_ayakta_kalir() -> None:
    canteen = FakeCanteen()
    canteen.fail.update({"students", "settings", "notifications"})
    service, _, _ = kur(canteen)

    cevap = await service.workspace()
    assert cevap["connected"] is False
    assert cevap["students"] == []
    # Ayar okunamadığında "kurulu" DEMEZ: iyimser varsayım, hiç gitmeyen
    # SMS'lerin fark edilmemesi demekti.
    assert cevap["netgsm"]["ready"] is False


# =========================================================== hazır mesaj

@pytest.mark.asyncio
async def test_hazir_mesaj_yazilir_ve_silinir() -> None:
    service, _, store = kur()
    assert (await service.save_preset("Borç hatırlatma", "Sayın veli…",
                                      actor=AKTOR))["ok"] is True
    assert len(store.presets) == 1

    # Aynı ad ikinci kez yazılınca YENİ SATIR AÇILMAZ, üzerine yazılır.
    await service.save_preset("Borç hatırlatma", "Yeni metin", actor=AKTOR)
    assert len(store.presets) == 1
    assert store.presets[0]["body"] == "Yeni metin"

    await service.delete_preset(store.presets[0]["id"])
    assert store.presets == []


@pytest.mark.asyncio
async def test_bos_hazir_mesaj_kaydedilmez() -> None:
    service, _, store = kur()
    cevap = await service.save_preset("Boş", "   ", actor=AKTOR)
    assert cevap["ok"] is False
    assert store.presets == []
