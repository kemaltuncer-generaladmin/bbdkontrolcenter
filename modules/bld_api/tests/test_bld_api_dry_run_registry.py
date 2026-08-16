"""Kuru prova kayıt defterinin KAPSAM testi.

NEDEN BU TEST VAR — KORUDUĞU ARIZA

    Sözleşmede olmayan bir yola kuru prova ile yazılırsa Laravel `dry_run`
    alanını sessizce yok sayar ve "prova" sanılan istek GERÇEK yazma olur.
    Geçit bunu, yolu bir kayıt defteriyle karşılaştırarak engelliyor: defterde
    olmayan yola kuru provada istek HİÇ GÖNDERİLMEZ.

    Kilit tersten de bozulabilir: yeni bir alan eklenip deseni kaydedilmezse o
    alanın bütün yazmaları sessiz bir no-op'a döner ve ekran "yazıldı" derken
    hiçbir şey değişmez. On üç alan ve yetmiş beş yazma metoduyla bu, unutması
    en kolay adımdır.

İKİ TEST, İKİ AYRI SORU

    1. Her yazma metodu kuru provada gerçekten istek gönderiyor mu (yani yolu
       deftere düşüyor mu).
    2. Aşağıdaki liste eksiksiz mi — `BldApi` üzerindeki BÜTÜN yazma metotları
       (imzasında `reason` ve `actor` taşıyanlar) burada sayılı mı. İkincisi
       olmadan birincisi kendi kendini kandırırdı: listeye eklenmemiş bir
       metot hiç sınanmazdı.

Hiçbir test ağa çıkmaz: `httpx.MockTransport`.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest
from bld_api_backend.client import MENU, BldApi
from bld_api_backend.errors import BldApiError
from bld_api_fakes import gateway

GEREKCE = "Kuru prova kapsam testi için yazılmış gerekçe"
AKTOR = "Ayşe Yılmaz"

#: `sniff_mime` yalnız sihirli bayta bakıyor; geçerli bir PNG başlığı yeter.
GORSEL = b"\x89PNG\r\n\x1a\n" + b"deneme-icerik"

Cagri = Callable[[BldApi], Coroutine[Any, Any, Any]]

#: Her yazma metodu için bir çağrı. Sıra alan dosyalarının sırasıdır.
WRITES: tuple[tuple[str, Cagri], ...] = (
    # --- KDS ailesi (K-21) ---
    ("create_device",
     lambda api: api.create_device(name="Kasa", reason=GEREKCE, actor=AKTOR)),
    ("rename_device",
     lambda api: api.rename_device(3, name="Kasa 2", reason=GEREKCE, actor=AKTOR)),
    ("new_pairing_code",
     lambda api: api.new_pairing_code(3, reason=GEREKCE, actor=AKTOR)),
    ("revoke_device",
     lambda api: api.revoke_device(3, reason=GEREKCE, actor=AKTOR)),
    ("update_device_settings",
     lambda api: api.update_device_settings(3, settings={"poll_seconds": 5},
                                            reason=GEREKCE, actor=AKTOR)),
    ("send_command",
     lambda api: api.send_command(3, command="restart", reason=GEREKCE, actor=AKTOR)),
    ("create_order_revision",
     lambda api: api.create_order_revision(8421, items=[{"menu_id": 88, "quantity": 2}],
                                           reason=GEREKCE, actor=AKTOR)),
    ("set_order_status",
     lambda api: api.set_order_status(8421, status="hazir", reason=GEREKCE, actor=AKTOR)),

    # --- menu ---
    ("create_menu_day",
     lambda api: api.create_menu_day(date="2026-08-17", reason=GEREKCE, actor=AKTOR)),
    ("update_menu_day",
     lambda api: api.update_menu_day("2026-08-17", package_price_kurus=19000,
                                     reason=GEREKCE, actor=AKTOR)),
    ("delete_menu_day",
     lambda api: api.delete_menu_day("2026-08-17", reason=GEREKCE, actor=AKTOR)),
    ("publish_menu_day",
     lambda api: api.publish_menu_day("2026-08-17", reason=GEREKCE, actor=AKTOR)),
    ("unpublish_menu_day",
     lambda api: api.unpublish_menu_day("2026-08-17", reason=GEREKCE, actor=AKTOR)),
    ("create_menu_item",
     lambda api: api.create_menu_item("2026-08-17", menu_id=27, reason=GEREKCE,
                                      actor=AKTOR)),
    ("update_menu_item",
     lambda api: api.update_menu_item("2026-08-17", 902, quantity=2, reason=GEREKCE,
                                      actor=AKTOR)),
    ("delete_menu_item",
     lambda api: api.delete_menu_item("2026-08-17", 902, reason=GEREKCE, actor=AKTOR)),
    ("set_menu_stock",
     lambda api: api.set_menu_stock("2026-08-17", capacity_total=120,
                                    items=[{"item_id": 902, "capacity": 60}],
                                    reason=GEREKCE, actor=AKTOR)),
    ("duplicate_menu_day",
     lambda api: api.duplicate_menu_day("2026-08-17", target_date="2026-08-24",
                                        reason=GEREKCE, actor=AKTOR)),

    # --- products ---
    ("create_product",
     lambda api: api.create_product(name="Karnıyarık", price_kurus=9500, reason=GEREKCE,
                                    actor=AKTOR)),
    ("update_product",
     lambda api: api.update_product(27, price_kurus=10000, reason=GEREKCE, actor=AKTOR)),
    ("delete_product",
     lambda api: api.delete_product(27, reason=GEREKCE, actor=AKTOR)),
    ("set_product_image",
     lambda api: api.set_product_image(27, content=GORSEL, filename="urun.png",
                                       reason=GEREKCE, actor=AKTOR)),
    ("delete_product_image",
     lambda api: api.delete_product_image(27, reason=GEREKCE, actor=AKTOR)),
    ("mark_product_sold_out",
     lambda api: api.mark_product_sold_out(27, reason=GEREKCE, actor=AKTOR)),
    ("clear_product_sold_out",
     lambda api: api.clear_product_sold_out(27, reason=GEREKCE, actor=AKTOR)),
    ("create_category",
     lambda api: api.create_category(name="Tatlı", reason=GEREKCE, actor=AKTOR)),
    ("update_category",
     lambda api: api.update_category(3, priority=20, reason=GEREKCE, actor=AKTOR)),

    # --- settings ---
    ("update_sales_settings",
     lambda api: api.update_sales_settings(order_cutoff="08:00", reason=GEREKCE,
                                           actor=AKTOR)),
    ("pause_ordering",
     lambda api: api.pause_ordering(reason=GEREKCE, actor=AKTOR)),
    ("resume_ordering",
     lambda api: api.resume_ordering(reason=GEREKCE, actor=AKTOR)),
    ("create_closed_day",
     lambda api: api.create_closed_day(date="2026-08-30", reason=GEREKCE, actor=AKTOR)),
    ("delete_closed_day",
     lambda api: api.delete_closed_day("2026-08-30", reason=GEREKCE, actor=AKTOR)),

    # --- orders (panel yolu) ---
    ("revise_order",
     lambda api: api.revise_order(8421, items=[{"menu_id": 88, "quantity": 10}],
                                  reason=GEREKCE, actor=AKTOR)),
    ("change_order_status",
     lambda api: api.change_order_status(8421, status="yolda", reason=GEREKCE,
                                         actor=AKTOR)),
    ("cancel_order",
     lambda api: api.cancel_order(8421, reason=GEREKCE, actor=AKTOR)),

    # --- subscriptions ---
    ("create_subscription",
     lambda api: api.create_subscription(customer_id=312, start_date="2026-09-01",
                                         service_days=[1, 2, 3], default_quantity=20,
                                         reason=GEREKCE, actor=AKTOR)),
    ("update_subscription",
     lambda api: api.update_subscription(18, default_quantity=22, reason=GEREKCE,
                                         actor=AKTOR)),
    ("activate_subscription",
     lambda api: api.activate_subscription(18, reason=GEREKCE, actor=AKTOR)),
    ("pause_subscription",
     lambda api: api.pause_subscription(18, start_date="2026-09-01",
                                        end_date="2026-09-14", reason=GEREKCE,
                                        actor=AKTOR)),
    ("resume_subscription",
     lambda api: api.resume_subscription(18, reason=GEREKCE, actor=AKTOR)),
    ("cancel_subscription",
     lambda api: api.cancel_subscription(18, effective_date="2026-08-31",
                                         reason=GEREKCE, actor=AKTOR)),
    ("create_subscription_exception",
     lambda api: api.create_subscription_exception(18, service_date="2026-08-20",
                                                   quantity_override=12, reason=GEREKCE,
                                                   actor=AKTOR)),
    ("delete_subscription_exception",
     lambda api: api.delete_subscription_exception(18, "2026-08-20", reason=GEREKCE,
                                                   actor=AKTOR)),
    ("generate_subscription_orders",
     lambda api: api.generate_subscription_orders(18, service_date="2026-08-17",
                                                  reason=GEREKCE, actor=AKTOR)),
    ("release_subscription_order",
     lambda api: api.release_subscription_order(8455, reason=GEREKCE, actor=AKTOR)),
    ("update_quote_request",
     lambda api: api.update_quote_request(88, status="cevaplandi", reason=GEREKCE,
                                          actor=AKTOR)),
    ("convert_quote_request",
     lambda api: api.convert_quote_request(88, customer_id=312,
                                           subscription={"start_date": "2026-09-01"},
                                           reason=GEREKCE, actor=AKTOR)),
    ("create_subscription_contract",
     lambda api: api.create_subscription_contract(18, phone="5321234567", reason=GEREKCE,
                                                  actor=AKTOR)),
    ("resend_subscription_contract",
     lambda api: api.resend_subscription_contract(7, reason=GEREKCE, actor=AKTOR)),
    ("cancel_subscription_contract",
     lambda api: api.cancel_subscription_contract(7, reason=GEREKCE, actor=AKTOR)),
    ("create_subscription_payment",
     lambda api: api.create_subscription_payment(18, period_start="2026-08-01",
                                                 period_end="2026-08-31",
                                                 due_date="2026-09-05", reason=GEREKCE,
                                                 actor=AKTOR)),
    ("mark_subscription_payment_paid",
     lambda api: api.mark_subscription_payment_paid(41, method="online", reason=GEREKCE,
                                                    actor=AKTOR)),

    # --- customers ---
    ("update_customer",
     lambda api: api.update_customer(312, telephone="5329876543", reason=GEREKCE,
                                     actor=AKTOR)),
    ("disable_customer",
     lambda api: api.disable_customer(312, reason=GEREKCE, actor=AKTOR)),
    ("enable_customer",
     lambda api: api.enable_customer(312, reason=GEREKCE, actor=AKTOR)),

    # --- invoices ---
    ("create_invoice",
     lambda api: api.create_invoice(order_id=8421, reason=GEREKCE, actor=AKTOR)),
    ("void_invoice",
     lambda api: api.void_invoice(44, reason=GEREKCE, actor=AKTOR)),

    # --- cms ---
    ("set_site_content",
     lambda api: api.set_site_content("contact", value={"phone": "3124445566"},
                                      reason=GEREKCE, actor=AKTOR)),
    ("create_site_service",
     lambda api: api.create_site_service(slug="etkinlik-catering", title="Etkinlik",
                                         fields={"summary": "..."}, reason=GEREKCE,
                                         actor=AKTOR)),
    ("update_site_service",
     lambda api: api.update_site_service(3, fields={"sort_order": 20}, reason=GEREKCE,
                                         actor=AKTOR)),
    ("delete_site_service",
     lambda api: api.delete_site_service(3, reason=GEREKCE, actor=AKTOR)),
    ("create_site_post",
     lambda api: api.create_site_post(slug="soguk-zincir", title="Soğuk zincir",
                                      body_html="<p>…</p>", reason=GEREKCE, actor=AKTOR)),
    ("update_site_post",
     lambda api: api.update_site_post(21, fields={"is_published": True}, reason=GEREKCE,
                                      actor=AKTOR)),
    ("delete_site_post",
     lambda api: api.delete_site_post(21, reason=GEREKCE, actor=AKTOR)),
    ("revalidate_site",
     lambda api: api.revalidate_site(reason=GEREKCE, actor=AKTOR)),

    # --- sms ---
    ("update_sms_template",
     lambda api: api.update_sms_template("order_created", enabled=True, reason=GEREKCE,
                                         actor=AKTOR)),
    ("preview_sms_template",
     lambda api: api.preview_sms_template("order_created", reason=GEREKCE, actor=AKTOR)),
    ("send_test_sms",
     lambda api: api.send_test_sms(phone="5321234567", template_key="order_created",
                                   reason=GEREKCE, actor=AKTOR)),
    ("set_sms_announcement",
     lambda api: api.set_sms_announcement(body="Duyuru", audience="active_customers",
                                          reason=GEREKCE, actor=AKTOR)),
    ("run_sms_announcement",
     lambda api: api.run_sms_announcement(confirm_recipients=186, reason=GEREKCE,
                                          actor=AKTOR)),

    # --- notifications ---
    ("create_notification",
     lambda api: api.create_notification(title="Kapalıyız", body="Metin", reason=GEREKCE,
                                         actor=AKTOR)),
    ("update_notification",
     lambda api: api.update_notification(12, title="Yeni başlık", reason=GEREKCE,
                                         actor=AKTOR)),
    ("publish_notification",
     lambda api: api.publish_notification(12, reason=GEREKCE, actor=AKTOR)),
    ("archive_notification",
     lambda api: api.archive_notification(12, reason=GEREKCE, actor=AKTOR)),

    # --- monitor ---
    ("resolve_monitor_event",
     lambda api: api.resolve_monitor_event(3311, reason=GEREKCE, actor=AKTOR)),
)


def _write_methods() -> set[str]:
    """`BldApi` üzerindeki yazma metotları: imzasında `reason` VE `actor` olanlar.

    Ölçüt kod yapısından okunur, elle tutulan bir listeden değil: yeni bir
    yazma metodu eklendiği anda bu küme büyür ve aşağıdaki eşitlik testi
    kırılır.
    """
    found: set[str] = set()
    for name in dir(BldApi):
        if name.startswith("_"):
            continue
        attr = getattr(BldApi, name)
        if not callable(attr):
            continue
        try:
            parameters = inspect.signature(attr).parameters
        except (TypeError, ValueError):  # pragma: no cover - imzasız yerleşikler
            continue
        if "reason" in parameters and "actor" in parameters:
            found.add(name)
    return found


def test_kapsam_listesi_butun_yazma_metotlarini_sayiyor() -> None:
    """Liste eksik kalırsa aşağıdaki kapsam testi kendi kendini kandırırdı."""
    listelenen = {name for name, _ in WRITES}
    assert len(listelenen) == len(WRITES), "listede aynı metot iki kez var"
    assert _write_methods() == listelenen


@pytest.mark.parametrize(("ad", "cagri"), WRITES, ids=[name for name, _ in WRITES])
async def test_her_yazma_yolu_kuru_prova_defterinde(ad: str, cagri: Cagri) -> None:
    """Kuru provada istek GERÇEKTEN gidiyorsa yol deftere kayıtlı demektir."""
    gonderilen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gonderilen.append(request)
        return httpx.Response(200, json={"ok": True, "dry_run": True, "would": {}})

    api, _, _, _ = gateway(handler, dry_run_default=True)
    sonuc = await cagri(api)

    assert gonderilen, (
        f"{ad}: kuru provada istek hiç gönderilmedi — yolu `register_dry_run` "
        "defterinde değil. Kaydedilmezse gerçek yazmalar da sessizce no-op olur."
    )
    assert sonuc.get("sent") is not False
    govde = json.loads(gonderilen[0].content)
    assert govde["dry_run"] is True
    assert govde["reason"] == GEREKCE
    assert govde["actor"] == AKTOR


async def test_defterde_olmayan_panel_yoluna_kuru_provada_istek_gitmez() -> None:
    """Kilidin ters yönü: kaydı olmayan bir yol için istek HİÇ çıkmamalı."""
    gonderilen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        gonderilen.append(request)
        return httpx.Response(200, json={})

    api, depo, _, _ = gateway(handler, dry_run_default=True)
    sonuc = await api._request(
        "POST", f"{MENU}/uydurma-uc", body={"x": 1}, reason=GEREKCE, actor=AKTOR,
        action="deneme",
    )

    assert gonderilen == []
    assert sonuc["sent"] is False
    assert depo.audit[0]["result"] == "dry_run"


async def test_uzun_aktor_adi_istek_cikmadan_reddedilir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.publish_menu_day("2026-08-17", reason=GEREKCE, actor="A" * 121)

    assert hata.value.code == "actor_required"
