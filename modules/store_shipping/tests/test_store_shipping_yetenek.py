"""Paylaşılan yetenek yüzeyi — `store.shipment.byOrder` (panel tarafı).

NEDEN BU DOSYA VAR. Manifest yeteneği ilan ediyordu, backend sağlıyordu, ama
panel `capabilities()` DIŞA VURMUYORDU. Kabuk bunu sessizce boş sayıyor
(`ui-kernel.js`: `typeof module.capabilities !== 'function' → {}`), yani
Siparişler ekranının Kargo sekmesi hiç açılmıyor ve kullanıcıya "veriyi veren
ekran bu kurulumda yok" deniyordu — oysa ekran iki dosya ötedeydi. Doğru yolu
deneyen kişi duvara çarpıp kendi kopyasını yazdı; ÜÇ AYRI "kargoya ver"
düğmesinin kökeni tam olarak budur.

Buradaki testler o dikişin takılı KALDIĞINI ve yüzeyin DAR kaldığını sınar.
Kaynak metni üzerinden okunur: depoda JS koşucusu yok ve bu kuralların hiçbiri
çalışma zamanında hata vermez — panel açılır, yalnızca yanlış çalışır.
"""

from __future__ import annotations

from pathlib import Path

import yaml

MODULE = Path(__file__).resolve().parents[1]
PANEL = (MODULE / "ui" / "panel" / "index.js").read_text(encoding="utf-8")
CSS = (MODULE / "ui" / "panel" / "panel.css").read_text(encoding="utf-8")
MANIFEST = yaml.safe_load((MODULE / "module.yaml").read_text(encoding="utf-8"))


def _capabilities_body() -> str:
    """`capabilities()` dışa vurumundan dosya sonuna kadarki metin."""
    return PANEL[PANEL.index("export function capabilities("):]


# ==================================================== dikiş takılı mı

def test_panel_capabilities_disa_vuruyor() -> None:
    # Bu satır silinirse Siparişler'in Kargo sekmesi SESSİZCE boşalır.
    assert "export function capabilities(" in PANEL


def test_manifestte_ilan_edilen_her_yetenek_PANELDE_de_ilan_ediliyor() -> None:
    """Kabuk manifestte olmayanı atlar (`manifestte ilan edilmemiş, atlandı`);
    manifestte olup panelde olmayan ise HİÇ çözülmez ve tüketen ekran
    "bu kurulumda yok" der. İki liste ayrışamaz."""
    ilan = {item["capability"] for item in (MANIFEST.get("provides") or [])}
    govde = _capabilities_body()
    for ad in ilan:
        assert f"'{ad}'" in govde, f"{ad} manifestte var, panelde yok"


def test_yetenek_govdesi_manifest_disina_cikmiyor() -> None:
    ilan = {item["capability"] for item in (MANIFEST.get("provides") or [])}
    assert ilan == {"store.shipment.byOrder"}


# ==================================================== yüzey DAR mı (K3)

def test_yetenek_SALT_OKUMA_yazma_cagrisi_yok() -> None:
    """Yetenek başka ekranın içinde çalışır; oradan gönderi açılamaz, etiket
    alınamaz, iptal edilemez. Dar yüzey bunu kaza eseri bozmayı imkânsız
    kılar."""
    govde = _capabilities_body()
    for yasak in ("method: 'POST'", "method: 'PUT'", "method: 'DELETE'",
                  "/dispatch", "/purchase", "/cancel"):
        assert yasak not in govde, f"yetenek yüzeyinde yazma izi: {yasak}"


def test_yetenek_kartinda_KARGOYA_VEREN_dugme_yok() -> None:
    """KULLANICININ KURALI: "farklı yerde 'kargoya ver' olmasın."

    Sipariş künyesinde gönderinin DURUMU görünür, eylemi görünmez. Buraya bir
    gönderme düğmesi eklemek, kaldırdığımız üçüncü kapıyı geri açardı.
    """
    govde = _capabilities_body()
    # DÜĞME ETİKETİ aranır, serbest metin değil: yorum satırında geçen
    # "kargoya verme YOK" cümlesi yanlış eşleşme üretiyordu.
    for yasak in ("button('Kargoya ver", "button('Seçilenleri kargoya",
                  "button('Etiket", "button('İptal"):
        assert yasak not in govde, f"yetenek kartında eylem düğmesi: {yasak}"
    # Tek eylem: asıl evinde açmak.
    assert "button('Kargo Yönetimi’nde aç'" in govde


def test_yetenek_dugmeleri_GECERLI_secenek_kullaniyor() -> None:
    """`button(label, {variant, title, onClick, disabled})` — `subtle` diye bir
    seçenek YOK ve verilirse sessizce yok sayılır. Sessizce yok sayılan bir
    görünüm seçeneği, "neden bu düğme diğerleri gibi görünmüyor" diye
    aranırken bulunması en zor şeydir."""
    assert "subtle:" not in _capabilities_body()


def test_baglanti_yoksa_GONDERI_YOK_denmiyor() -> None:
    """"Bu sipariş kargoya verilmedi" ile "kargo bilgisine ulaşamıyoruz" çok
    farklı iki cümledir. İkincisini birincisi gibi göstermek, kargolanmış bir
    siparişi kargosuz sanmaya ve İKİNCİ KEZ göndermeye yol açar."""
    govde = _capabilities_body()
    assert "connected === false" in govde
    assert "ulaşılamıyor" in govde


# ==================================================== stil sızıntısı

def test_yetenek_stili_TEMBEL_yukleniyor() -> None:
    """Kabuk yetenek çözümü sırasında HİÇ AÇILMAYAN panelleri de import eder;
    dosya tepesindeki `loadStyles()` kullanılmayan stilleri `document.head`'e
    sızdırır ve orada hiç kaldırılmaz (kit.js:399-401).

    Bu panelde İKİ meşru çağrı vardır — `mount()` ve yetenek kartı — ve
    ikisi de tembeldir. Üçüncüsü eklenirse gerekçesi sorulmalıdır.
    """
    assert PANEL.count("loadStyles(") == 2
    assert "loadStyles(import.meta.url)" in _capabilities_body()
    # Dosya tepesinde (ilk import bloğundan sonra, ilk fonksiyondan önce) OLMAMALI.
    tepe = PANEL[: PANEL.index("const BASE =")]
    assert "loadStyles(" not in tepe


def test_yetenek_karti_kendi_panel_yerlesimine_dayanmiyor() -> None:
    """Kart BAŞKA panelin içinde çizilir; orada `sh-body` / `sh-pane`
    kapsayıcıları YOKTUR. Kartın stilleri onlara dayanırsa yerleşim bozulur."""
    assert ".sh-cap {" in CSS
    for kural in CSS.splitlines():
        if ".sh-cap" in kural and ("sh-body" in kural or "sh-pane" in kural):
            raise AssertionError(f"yetenek kartı panel yerleşimine dayanıyor: {kural}")
