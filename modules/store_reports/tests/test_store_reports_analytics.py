"""Rapor hesapları — saf mantık. Ağa çıkmaz, dosya yazmaz."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from store_reports_backend import analytics as an
from store_reports_backend import builders, tree

GUN = "2026-08-13"
MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"


def _order(**extra: Any) -> dict[str, Any]:
    payload = {"id": 1, "increment_id": "S0001", "created_at": f"{GUN} 09:30:00",
               "status": "processing", "grand_total": "1234.56", "sub_total": "1000.00",
               "shipping_amount": "20.00", "discount_amount": "0.00", "tax_amount": "214.56",
               "channel_name": "Varsayılan", "payment_title": "Havale",
               "customer_id": 7, "customer_first_name": "Ayşe", "customer_last_name": "Şahin",
               "shipping_address": {"city": "Şanlıurfa"}}
    payload.update(extra)
    return payload


# ==================================================== para ve alan çevirisi

def test_ondalik_para_kurusa_cevrilir_yuvarlama_yariyi_yukari() -> None:
    assert an.to_kurus("1234.56") == 123_456
    assert an.to_kurus("0.005") == 1          # yarım kuruş yukarı
    assert an.to_kurus("") == 0
    assert an.to_kurus(None) == 0


def test_alan_adi_camel_ya_da_snake_olabilir() -> None:
    assert an.pick({"grandTotal": "5"}, "grand_total", "grandTotal") == "5"
    assert an.pick({"grand_total": "5"}, "grand_total", "grandTotal") == "5"
    # Boş değer DOLU sayılmaz: bir sonrakine geçilir.
    assert an.pick({"grand_total": "", "grandTotal": "9"}, "grand_total", "grandTotal") == "9"


def test_siparis_satiri_para_alanlarini_kurus_yapar() -> None:
    row = an.order_row(_order())
    assert row["grandTotal"] == 123_456
    assert row["day"] == GUN
    assert row["hour"] == 9
    assert row["city"] == "Şanlıurfa"
    assert row["customerName"] == "Ayşe Şahin"
    assert row["statusLabel"] == "Hazırlanıyor"


def test_iptal_siparis_ciroya_katilmaz() -> None:
    rows = [an.order_row(_order()), an.order_row(_order(id=2, status="canceled"))]
    assert len(an.live(rows)) == 1
    assert rows[1]["void"] is True


# ================================================================ kırılım

def test_bos_gunler_seride_sifir_olarak_girer() -> None:
    rows = [{"day": "2026-08-01", "grandTotal": 100}]
    points = an.series(rows, start="2026-08-01", end="2026-08-04", granularity="day")
    assert [item["value"] for item in points] == [100, 0, 0, 0]
    assert points[1]["label"] == "02.08"


def test_ay_kirilimi_turkce_kisa_ay_adiyla_etiketlenir() -> None:
    key, label = an.bucket("2026-08-13", "month")
    assert key == "2026-08"
    assert label == "Ağu 2026"


def test_hafta_kirilimi_iso_hafta_kullanir() -> None:
    key, _ = an.bucket("2026-01-01", "week")
    assert key.startswith(("2026-W", "2025-W"))


def test_onceki_donem_ayni_uzunlukta_ve_bitisik_olur() -> None:
    start, end = an.shift_range("2026-08-08", "2026-08-14", "previous")
    assert (start, end) == ("2026-08-01", "2026-08-07")


def test_gecen_yil_ayni_donem_29_subatta_patlamaz() -> None:
    start, end = an.shift_range("2024-02-29", "2024-02-29", "year")
    assert start and end                      # 365 gün geriye düşer, hata vermez


def test_onceki_donem_sifirsa_yuzde_tanimsizdir() -> None:
    assert an.delta(100, 0) is None
    assert an.delta(150, 100) == {"percent": 50.0}


def test_pareto_kumulatif_paya_gore_abc_verir() -> None:
    rows = [{"label": "A", "total": 800}, {"label": "B", "total": 150},
            {"label": "C", "total": 50}]
    ranked = an.abc(rows)
    assert [item["abc"] for item in ranked] == ["A", "B", "C"]
    assert ranked[0]["cumulativeShare"] == 80.0


# ============================================================ yaprak hesabı

def _params(**extra: Any) -> dict[str, Any]:
    base = {"start": "2026-08-01", "end": "2026-08-31", "prevStart": "", "prevEnd": "",
            "granularity": "day", "compare": "", "channel": "", "customerGroup": "",
            "category": "", "carrier": "", "churnDays": 180, "stockoutDays": 30,
            "deadStockDays": 90}
    base.update(extra)
    return base


def test_satis_ozeti_ciroyu_ve_ortalama_sepeti_hesaplar() -> None:
    data = {"orders": [an.order_row(_order()), an.order_row(_order(id=2))]}
    result = builders.build("sales_summary", data, _params())
    labels = {item["label"]: item["value"] for item in result["kpis"]}
    assert labels["Sipariş"] == "2"
    assert labels["Ciro"].startswith("2.469,12")


def test_her_agac_yapragi_ya_hesaba_ya_gerekceye_sahiptir() -> None:
    """Ağaçta olup hesabı olmayan yaprak, ekranda sessizce boş dönerdi."""
    for leaf in tree.LEAVES:
        if leaf["available"]:
            assert leaf["key"] in builders.BUILDERS, leaf["key"]
        else:
            assert leaf["reason"], leaf["key"]


def test_bilinmeyen_yaprak_patlamaz_not_dondurur() -> None:
    result = builders.build("olmayan_rapor", {}, _params())
    assert result["kpis"] == []
    assert "tanımlı değil" in result["notes"][0]


def test_kdv_icmali_orana_gore_matrah_ayirir() -> None:
    lines = [{"day": "2026-08-05", "channel": "", "void": False, "productId": 1,
              "sku": "A", "name": "Kalem", "qty": 2, "price": 0, "total": 10_000,
              "discount": 0, "tax": 2_000, "taxPercent": 20.0, "orderId": 1},
             {"day": "2026-08-05", "channel": "", "void": False, "productId": 2,
              "sku": "B", "name": "Kitap", "qty": 1, "price": 0, "total": 5_000,
              "discount": 0, "tax": 50, "taxPercent": 1.0, "orderId": 1}]
    result = builders.build("finance_vat", {"lines": lines, "orders": []}, _params())
    rates = [row[0] for row in result["table"]["rows"]]
    assert "%20" in rates
    assert "%1" in rates


def test_olu_stok_donemde_satilmayan_stoklu_urunu_listeler() -> None:
    data = {
        "orders": [], "lines": [],
        "products": [
            {"id": 1, "sku": "A", "name": "Satılan", "type": "simple", "status": 1,
             "price": 1_000, "cost": 0, "stock": 5, "categories": []},
            {"id": 2, "sku": "B", "name": "Ölü", "type": "simple", "status": 1,
             "price": 2_000, "cost": 0, "stock": 3, "categories": []},
        ],
    }
    data["lines"] = [{"day": "2026-08-05", "channel": "", "void": False, "productId": 1,
                      "sku": "A", "name": "Satılan", "qty": 1, "price": 0, "total": 1_000,
                      "discount": 0, "tax": 0, "taxPercent": 0, "orderId": 1}]
    result = builders.build("product_dead", data, _params())
    assert [row[0] for row in result["table"]["rows"]] == ["B"]


def test_tukenme_tahmini_ufkun_disindaki_urunu_gostermez() -> None:
    lines = [{"day": "2026-08-05", "channel": "", "void": False, "productId": 1,
              "sku": "HIZLI", "name": "Hızlı", "qty": 31, "price": 0, "total": 100,
              "discount": 0, "tax": 0, "taxPercent": 0, "orderId": 1},
             {"day": "2026-08-05", "channel": "", "void": False, "productId": 2,
              "sku": "YAVAS", "name": "Yavaş", "qty": 1, "price": 0, "total": 100,
              "discount": 0, "tax": 0, "taxPercent": 0, "orderId": 1}]
    products = [{"id": 1, "sku": "HIZLI", "name": "Hızlı", "type": "simple", "status": 1,
                 "price": 0, "cost": 0, "stock": 5, "categories": []},
                {"id": 2, "sku": "YAVAS", "name": "Yavaş", "type": "simple", "status": 1,
                 "price": 0, "cost": 0, "stock": 900, "categories": []}]
    result = builders.build("product_stockout", {"lines": lines, "products": products,
                                                 "orders": []}, _params())
    assert [row[0] for row in result["table"]["rows"]] == ["HIZLI"]


def test_pdf_bolumleri_ekrandaki_sonuctan_uretilir() -> None:
    data = {"orders": [an.order_row(_order())]}
    result = builders.build("sales_channel", data, _params())
    sections = builders.pdf_sections(result, title="Satış · Kanal bazlı")
    kinds = [section["kind"] for section in sections]
    assert kinds[0] == "tiles"
    assert "table" in kinds


# =================================================================== ağaç

def test_agacta_yedi_dal_ve_otuz_ustu_yaprak_var() -> None:
    branches = tree.tree()
    assert len(branches) == 7
    assert sum(branch["count"] for branch in branches) == len(tree.LEAVES)
    assert len(tree.LEAVES) >= 32


def test_paket_veri_ihtiyaci_tekillestirilir() -> None:
    """On yaprak seçilse de sipariş listesi bir kez çekilmeli."""
    keys = ["sales_summary", "sales_channel", "product_top"]
    assert tree.datasets_for(keys) == ["orders", "lines"]


def test_ucu_olmayan_yaprak_gizlenmez_gerekce_tasir() -> None:
    leaf = tree.leaf("sales_abandoned")
    assert leaf is not None
    assert leaf["available"] is False
    assert "abandoned_carts" in leaf["reason"]


def test_tam_etiket_dal_adiyla_birlikte_verilir() -> None:
    assert tree.full_label("finance_vat") == "Mali · KDV icmali"


# =================================================================== göçler

def test_her_tablo_ve_indeks_adi_modul_onekiyle_baslar() -> None:
    """K5: modül yalnız kendi göçleriyle açtığı tablolara yazar.

    Öneksiz bir ad başka bir modülün tablosuyla çakışabilir ve iki modül aynı
    satırlara yazmaya başlar; hangisinin bozduğu da anlaşılmaz.
    """
    names: list[str] = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        names += re.findall(r"CREATE\s+(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+(\w+)",
                            path.read_text(encoding="utf-8"), flags=re.IGNORECASE)
    assert names, "göç dosyası okunamadı"
    assert all(name.startswith("mod_store_reports_") for name in names), names


# ================================================ üst/alt liste örtüşmesi

def test_en_cok_ve_en_az_satan_listeleri_ortusmez() -> None:
    """25 ürünlü dönemde tablo 40 satır yazıyordu: 15 ürün İKİ KEZ.

    `grouped[-20:]` ilk 20'nin son 15'ini geri getiriyordu; aynı ürün iki ayrı
    sıra numarasıyla hem "en çok satan" hem "en az satan" tarafında duruyor,
    toplamlar okuyanın gözünde iki katına çıkıyordu.
    """
    lines = [{"day": "2026-08-05", "channel": "", "void": False, "productId": pid,
              "sku": f"SKU-{pid}", "name": f"Ürün {pid}", "qty": 1, "price": 0,
              "total": 100 * (26 - pid), "discount": 0, "tax": 0, "taxPercent": 0,
              "orderId": 1} for pid in range(1, 26)]
    result = builders.build("product_top", {"orders": [], "lines": lines}, _params())
    skus = [row[1] for row in result["table"]["rows"]]
    assert len(skus) == 25                      # 40 değil
    assert len(set(skus)) == len(skus)          # hiçbir ürün iki kez yok


def test_yirmiden_az_urunde_kuyruk_listesi_hic_cizilmez() -> None:
    lines = [{"day": "2026-08-05", "channel": "", "void": False, "productId": pid,
              "sku": f"SKU-{pid}", "name": f"Ürün {pid}", "qty": 1, "price": 0,
              "total": 100 * pid, "discount": 0, "tax": 0, "taxPercent": 0,
              "orderId": 1} for pid in range(1, 6)]
    result = builders.build("product_top", {"orders": [], "lines": lines}, _params())
    assert len(result["table"]["rows"]) == 5
    assert result["notes"] == []                # "önce ilk 20, sonra son 20" yazmaz


def test_kirkin_ustunde_urunde_ilk_yirmi_ve_son_yirmi_ayri_durur() -> None:
    lines = [{"day": "2026-08-05", "channel": "", "void": False, "productId": pid,
              "sku": f"SKU-{pid}", "name": f"Ürün {pid}", "qty": 1, "price": 0,
              "total": 100 * (61 - pid), "discount": 0, "tax": 0, "taxPercent": 0,
              "orderId": 1} for pid in range(1, 61)]
    result = builders.build("product_top", {"orders": [], "lines": lines}, _params())
    skus = [row[1] for row in result["table"]["rows"]]
    assert len(skus) == 40
    assert len(set(skus)) == 40
    assert skus[0] == "SKU-1"                   # en çok satan
    assert skus[-1] == "SKU-41"                 # kuyruğun sonu = 41. sıra
