"""Menü Yönetimi — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ İZİN, İKİ DEĞİL:

    bld_menu.view     takvim, gün ayrıntısı, stok sayaçları, ürün seçicisi
    bld_menu.manage   gün kurma ve düzenleme, kalem ekleme/güncelleme,
                      yayınlama ve taslağa çekme, tavan yazma, kopyalama
    bld_menu.remove   GERİ ALINAMAZ silme: gün (kalemleriyle) ve tek kalem

Üçüncüsünün ayrı durmasının nedeni şudur: yayından çekmek bir şalterdir ve
tersi tek tıktır — gün, kalemleri ve tavanlarıyla taslak olarak yerinde durur.
Silmek geri alınamaz: gün kaydı ve TÜM kalemleri (fiyat geçersiz kılmaları,
tavanları, etiketleri) birlikte gider ve yeniden yazmanın tek yolu hepsini elle
girmek. Sunucu iki kapı daha tutuyor (yayınlanmış gün silinemez, siparişi olan
gün ve bugün pişen kalem silinemez) ama taslak bir haftalık menü tek çağrıda
yok edilebilir.

GEREKÇE UÇ BAŞINA İSTENİR, HER YAZMADA DEĞİL. Ölçüt tek cümledir: işlem
müşteriye GÖRÜNÜR HÂLE GELİYOR mu ve GERİ ALINMASI ZOR mu.

    reason İSTER    POST publish · POST unpublish · DELETE days/{date} ·
                    POST duplicate                       → `ReasonBody`
    reason İSTEMEZ  POST days · PATCH days/{date} · POST/PATCH/DELETE items ·
                    POST stock/preview · PUT stock       → `WriteBody`

Taslak kurmak bir taahhüt değil, YAYINLAMAK taahhüttür. Her kalem eklemede on
karakter istemek "düzeltme", "ok", "asdasd" üretiyordu; az yerde istenen gerekçe
çok yerde istenenden değerlidir. `actor` HER yazmada kalır ve denetim satırı her
yazmada açılır — seyrekleşen tek şey "neden" sorusudur.

AYNI POLİTİKA DÖRT KATMANDA BİRDEN GEVŞETİLDİ (panel · bu dosya · geçit ·
sunucu). Yalnız birini gevşetmek 422 üretirdi: gerekçesiz bir gövde bu uçtan
geçse bile geçit ya da sunucu geri çevirirdi.

KURU PROVA VARSAYILANI YOK — VE BİLEREK YOK. Uç `dryRun` alanını kabul eder
(sözleşme additive) ama gönderilmezse `False` uygulanır ve bu karar AYARDAN
OKUNMAZ. Ayardan okunan bir varsayılan tam da geçidin uyardığı tuzaktır:
`config/local.yaml` git dışıdır, orada `true` yazabilir ve panelden yapılan her
yazma sessizce bir provaya döner — ekran "kaydedildi" der, BLD'de hiçbir şey
değişmez. Servise giden `dry_run` her çağrıda AÇIKÇA verilir.

STOK İKİ UÇTAN GEÇER ve bu bir kolaylık değil bir emniyet: `PUT stock` TAM
LİSTE yazar, gönderilmeyen kalemin tavanı kalkar. Önce `POST .../stock/preview`
kuru provayı koşar ve uyarılarla birlikte bir jeton döndürür; `PUT .../stock`
yalnız o jetonla gelir ve temel çizgiyi doğrular. Tek adımlı bir tavan yazma
ucu, yöneticinin onayladığı tablonun uygulanan tablo olduğunu kanıtlayamazdı.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran mesajı
gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..menu import MAX_REASON, MIN_REASON
from ..service import MenuService

#: İzin anahtarları tek yerde durur: uç noktalar ve servis aynı dizeyi okur,
#: yazım hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bld_menu.view"
MANAGE = "bld_menu.manage"
REMOVE = "bld_menu.remove"

router = APIRouter()
_service: MenuService | None = None


def bind(service: MenuService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> MenuService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class WriteBody(BaseModel):
    """Her yazma gövdesinin ORTAK tabanı. Gerekçe TAŞIMAZ.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. İstemcinin aktör adını
    yazabilmesi, denetim izini imzalanmamış bir deftere çevirirdi: silinmeyen
    bir satıra istediği adı yazan biri, işi başkasının üstüne bırakabilirdi.
    Aktör HER yazmada kayda geçer; seyrekleşen soru "neden", "kim" değil.

    `dryRun` camelCase'tir ve TEK KABUL EDİLEN addır. Panel→Kontrol Merkezi
    sınırında `store_orders` deseni geçerli; tele giden `dry_run` adına çeviriyi
    geçit yapar. `dry_run` da kabul edilseydi, bir yazım hatası ("dryrun",
    "dry_Run") sessizce düşer ve alan hiç gönderilmemiş sayılırdı. Tek ad +
    `extra="forbid"` sayesinde yanlış yazılan alan 422 ile geri döner.

    `extra="forbid"` GEREKÇE İÇİN DE GEÇERLİDİR: bu tabandan türeyen bir uca
    `reason` göndermek 422 verir. Sessizce yok saymak, gerekçe yazdığını sanan
    ve hiçbir yere yazılmayan bir metin bırakırdı; ekranın o alanı hiç
    sormaması gerektiğini söyleyen tek gürültü budur.
    """

    #: `ClassVar` ile işaretli, çünkü `km_sdk` pydantic'in `ConfigDict` tipini
    #: dışa vurmuyor ve modül pydantic'i doğrudan import etmiyor (K2). Düz
    #: sözlük pydantic için yeterli; işaret yalnız "bu bir alan değil" der.
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    dryRun: bool | None = None

    def dry(self) -> bool:
        """Kuru prova bayrağı. Alan gelmediyse GERÇEK yazma.

        Varsayılan ayardan OKUNMAZ (modül başlığındaki gerekçe): bir kurulumun
        `local.yaml` dosyası panelden yapılan her yazmayı sessiz bir provaya
        çeviremesin.
        """
        return bool(self.dryRun)

    def changes(self) -> dict[str, Any]:
        """İstemcinin GERÇEKTEN gönderdiği alanlar.

        `exclude_unset` kısmi yazmanın bel kemiği: alanı hiç göndermemek
        "dokunma", `null` göndermek "boşalt" demek ve varsayılanı `None` olan
        bir modelde bu iki niyet ancak böyle ayrılabilir. Ayrım geçide kadar
        korunur — orada `UNSET` nöbetçisi gönderilmeyen alanı gövdeye koymaz.

        `reason` de düşürülür: `ReasonBody` alt sınıfları aynı yardımcıyı
        kullanıyor ve gerekçe bir ALAN DEĞİŞİKLİĞİ değil, işlemin kaydıdır.
        """
        data = self.model_dump(exclude_unset=True)
        data.pop("reason", None)
        data.pop("dryRun", None)
        return data


class ReasonBody(WriteBody):
    """Gerekçe İSTEYEN uçların gövdesi.

    DÖRT UÇ: yayınlama, yayından çekme, gün silme, kopyalama. Ölçüt tek
    cümledir — işlem müşteriye GÖRÜNÜR HÂLE GELİYOR mu ve GERİ ALINMASI ZOR mu.
    Taslak kurmak ikisi de değildir; yayınlamak ikisi de.

    NEDEN ARTIK HER YAZMADA DEĞİL: bir güne beş ürün koymak beş çağrıdır ve her
    birinde on karakter istemek "düzeltme", "ok", "asdasd" üretiyordu. Böyle bir
    denetim izi, altı ay sonra hiçbir soruyu cevaplamıyor. Az yerde istenen
    gerekçe, çok yerde istenenden daha değerlidir.

    SINIRLAR DEĞİŞMEDİ: istendiği yerde hâlâ 10–500 karakter.
    """

    reason: str = Field(min_length=MIN_REASON, max_length=MAX_REASON)


# ================================================================== okuma

@router.get("/calendar")
async def calendar(
    date_from: str = Query("", max_length=10),
    date_to: str = Query("", max_length=10),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Takvim ızgarası — aralıktaki HER GÜN, menüsü olmayanlar dâhil.

    Parametre adları `date_from` / `date_to`. Sözleşmedeki `from` / `to`
    adlarını buraya taşımak, `from` Python'da ayrılmış bir sözcük olduğu için
    her uçta takma ad gerektirirdi; çeviri geçidin işidir ve orada tek satır.

    Aralık tavanı (92 gün) servis tarafında denetlenir ve AŞILDIĞINDA SESSİZCE
    KIRPILMAZ: kırpılmış bir aralık, menüsü olan günleri "menü girilmemiş" diye
    gösterirdi.
    """
    return await service().calendar(date_from=date_from, date_to=date_to)


@router.get("/days/{date}")
async def day(
    date: str,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Tek günün tam menüsü. Menü yoksa `exists: False` — hata değil."""
    return await service().day(date)


@router.get("/days/{date}/stock")
async def stock(
    date: str,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Gün ve kalem stok durumu. `sold` REZERVEDİR, teslim edilmiş değil."""
    return await service().stock(date)


@router.get("/products")
async def products(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Kalem eklerken açılan seçicinin ürün kataloğu.

    `bld_menu.view` YETER: ürün adını görmek, menüye kalem eklemek demek
    değildir. Yazma kapısı `POST /days/{date}/items` üzerindedir.
    """
    return await service().products()


# =============================================================== menü günü

class DayCreateBody(WriteBody):
    """Yeni gün. GEREKÇE İSTEMEZ — taslak kurmak bir taahhüt değildir.

    `status` YOKTUR — gün her zaman `draft` doğar ve yayın ayrı bir eylemdir;
    gövdeden durum kabul etmek, yarım girilmiş bir günü tek istekte müşteriye
    açardı. Gerekçe de o yayın adımında istenir."""

    date: str = Field(min_length=10, max_length=10)
    title: str | None = None
    description: str | None = None
    internal_note: str | None = None
    package_price_kurus: int | None = None
    components_sellable: bool = True
    cutoff_time: str | None = None
    capacity_total: int | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/days")
async def create_day(
    body: DayCreateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    fields = body.changes()
    fields.pop("date", None)
    fields.pop("items", None)
    return await service().create_day(date=body.date, fields=fields, items=body.items,
                                      actor=user.full_name, dry_run=body.dry())


class DayPatchBody(WriteBody):
    """Kısmi gün güncellemesi. GEREKÇE İSTEMEZ.

    Aynı yolun `DELETE` fiili gerekçe İSTER; ayrım şurada: başlığı düzeltmek
    geri alınabilir, günü kalemleriyle silmek değil.

    `date`, `location_id` ve `status` BİLEREK YOK: günü taşımak yeni gün kurup
    eskisini silmektir ve durum değişimi kendi uçlarındadır. `extra="forbid"`
    sayesinde bunları göndermeye çalışan istek 422 alır — sessizce yok
    sayılsalardı yönetici günü taşıdığını sanırdı.
    """

    title: str | None = None
    description: str | None = None
    internal_note: str | None = None
    package_price_kurus: int | None = None
    components_sellable: bool | None = None
    cutoff_time: str | None = None
    capacity_total: int | None = None
    image_path: str | None = None


@router.patch("/days/{date}")
async def update_day(
    date: str,
    body: DayPatchBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Gönderilmeyen alan değişmez, `null` gönderilen alan temizlenir."""
    return await service().update_day(date, fields=body.changes(),
                                      actor=user.full_name, dry_run=body.dry())


@router.delete("/days/{date}")
async def delete_day(
    date: str,
    body: ReasonBody,
    user: CurrentUser = requires(REMOVE),
) -> dict[str, Any]:
    """GERİ ALINAMAZ. Gün ve TÜM kalemleri birlikte gider.

    GEREKÇE İSTER: geri getirmenin tek yolu her kalemi, her tavanı ve her
    etiketi elle yeniden girmektir.

    İzin kapısı burada ve serviste iki kez denetlenir (K9 — çift kapı).
    """
    return await service().delete_day(date, reason=body.reason, actor=user.full_name,
                                      dry_run=body.dry(),
                                      allow_remove=user.has_permission(REMOVE))


@router.post("/days/{date}/publish")
async def publish(
    date: str,
    body: ReasonBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Yayına alır. GEREKÇE İSTER — politikanın kalbi bu satırdır: gün bu
    çağrıyla müşteriye görünür hâle gelir ve sipariş almaya başlar.

    Ön denetimler kuru provada da koşar (sözleşme)."""
    return await service().publish(date, reason=body.reason, actor=user.full_name,
                                   dry_run=body.dry())


@router.post("/days/{date}/unpublish")
async def unpublish(
    date: str,
    body: ReasonBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Taslağa çeker — GERİ ALINABİLİR, bu yüzden `remove` istemez.

    GEREKÇE YİNE DE İSTER: izin ile gerekçe ayrı sorulardır. Şalter geri
    alınabilir ama etkisi müşteride anında görünür ve "neden kapattık" sorusu
    ertesi gün mutlaka soruluyor."""
    return await service().unpublish(date, reason=body.reason, actor=user.full_name,
                                     dry_run=body.dry())


@router.post("/days/{date}/open-sale")
async def open_sale(
    date: str,
    body: WriteBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Günü SATIŞA AÇAR: eksik "zorunlu kalem" işaretlerini kurar, sonra
    yayınlar. Tek çağrıda iki adım — ayrıntı `MenuService.open_sale`.

    GEREKÇE İSTEMEZ ve bu, yukarıdaki dört uçluk listenin İSTİSNASI DEĞİL
    GENİŞLEMESİ: gerekçeyi burada AYAR veriyor (`mn.AUTO_OPEN_REASON`) ve
    denetim izine ayarın adıyla düşüyor. Ölçüt "müşteriye görünür + geri
    alınması zor" hâlâ geçerli; değişen, cevabı kimin yazdığı. Kullanıcıdan her
    fiyat kaydında on karakter istemek tam da seyreltilmek istenen gürültüydü
    ve üreteceği metin ("fiyat girdim") denetim izine hiçbir şey katmazdı.

    YAYINDAN ÇEKMEK BU UÇTAN YAPILMAZ: geri almanın yolu `unpublish` ve ORASI
    gerekçe ister. Otomatikleşen yalnız açma yönüdür.
    """
    return await service().open_sale(date, actor=user.full_name, dry_run=body.dry())


class OpenSaleRangeBody(WriteBody):
    """Toplu satışa açma aralığı. GEREKÇE İSTEMEZ — tek günlük kardeşiyle aynı
    sebeple (`open_sale`).

    ARALIK ZORUNLU ve varsayılanı YOK: "bugünden ileriye" gibi bir varsayılan,
    ekranın gösterdiği listeyle sunucunun dokunduğu günlerin ayrışabileceği tek
    yerdir. Panel hangi günleri saydıysa aynı aralığı yollar."""

    date_from: str
    date_to: str


@router.post("/open-sale")
async def open_sale_range(
    body: OpenSaleRangeBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Aralıktaki bütün uygun günleri satışa açar — ayrıntı
    `MenuService.open_sale_range`.

    `dryRun: true` ile çağrıldığında hiçbir şey yazmaz ve ne olacağını gün gün
    döner; panel önce onu koşturup kullanıcıya sayıları gösteriyor."""
    return await service().open_sale_range(date_from=body.date_from,
                                           date_to=body.date_to,
                                           actor=user.full_name, dry_run=body.dry())


# ================================================================= kalemler

class ItemCreateBody(WriteBody):
    """Güne kalem ekler. GEREKÇE İSTEMEZ — şikâyetin çıkış noktası burasıydı:
    bir güne beş ürün koymak beş kez ürün seçici + form + gerekçe diyaloğu
    demekti ve üretilen metinler ("düzeltme", "ok") izi işe yaramaz kılıyordu."""

    menu_id: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1)
    sort_order: int | None = None
    label: str | None = None
    price_override_kurus: int | None = None
    is_required: bool = False
    sellable_alone: bool = False
    capacity: int | None = None


@router.post("/days/{date}/items")
async def create_item(
    date: str,
    body: ItemCreateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """`sort_order` verilmezse sunucu mevcut en büyüğe 10 ekler."""
    return await service().add_item(date, fields=body.changes(),
                                    actor=user.full_name, dry_run=body.dry())


class ItemPatchBody(WriteBody):
    """Kısmi kalem güncellemesi. GEREKÇE İSTEMEZ.

    YAYINDAKİ GÜNÜN KALEMİ İÇİN DE İSTEMEZ ve bu bilinçli bir karardır (sahip
    böyle istedi). Politikanın kendi ölçütüyle gerilimli: yayındaki bir günde
    fiyat ya da etiket değiştirmek müşteriye görünen şeyi değiştirir.
    Sıkılaştırmak ucuz — geçitteki muafiyet defterinden `PATCH items` satırını
    kaldırmak ve burada `ReasonBody`ye dönmek yeter — ama BUGÜN İSTENMEDİ.

    `menu_id` YOK: ürünü değiştirmek kalemi silip yenisini eklemektir ve
    denetim izinde iki ayrı satır olmalıdır."""

    quantity: int | None = Field(default=None, ge=1)
    sort_order: int | None = None
    label: str | None = None
    price_override_kurus: int | None = None
    is_required: bool | None = None
    sellable_alone: bool | None = None
    capacity: int | None = None


@router.patch("/days/{date}/items/{item_id}")
async def update_item(
    date: str,
    item_id: int,
    body: ItemPatchBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    return await service().update_item(date, item_id, fields=body.changes(),
                                       actor=user.full_name, dry_run=body.dry())


@router.delete("/days/{date}/items/{item_id}")
async def delete_item(
    date: str,
    item_id: int,
    body: WriteBody,
    user: CurrentUser = requires(REMOVE),
) -> dict[str, Any]:
    """GERİ ALINAMAZ ama GEREKÇE İSTEMEZ — gün silmenin aksine.

    İZİN İLE GEREKÇE AYRI SORULARDIR ve bu uç ikisinin ayrıştığı yerdir:
    `bld_menu.remove` anahtarı DURUYOR (kimin silebileceği değişmedi), yalnız
    "neden" sorusu kalktı. Ayrımın dayanağı ölçek: gün silmek gün kaydını ve
    TÜM kalemlerini birlikte götürür, tek kalem silmek ise menü kurarken
    yapılan olağan bir düzeltmedir ve geri almanın maliyeti bir seçicidir.

    Sunucu bugünün siparişlerinde kullanılmış kalemi ayrıca engeller; izin
    kapısı burada ve serviste iki kez denetlenir (K9)."""
    return await service().delete_item(date, item_id,
                                       actor=user.full_name, dry_run=body.dry(),
                                       allow_remove=user.has_permission(REMOVE))


# ===================================================================== stok

class StockPreviewBody(WriteBody):
    """Tavan tablosunun TAMAMI. Fark değil. GEREKÇE İSTEMEZ.

    Emniyet gerekçede değil AKIŞTADIR: önizleme uyarıları hesaplar, uygulama
    jetonla gelir ve temel çizgiyi doğrular. Tavan düşürmek gün içinde
    tekrarlanan bir iştir (malzeme bitti) ve her tekrarda on karakter istemek
    tam da bu turda kaldırılan sürtünmedir.

    `dryRun` alanı miras kalır ama BU UÇTA KULLANILMAZ: önizleme zaten kuru
    provanın kendisidir ve servis geçide her zaman `dry_run=True` geçer.
    """

    capacity_total: int | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/days/{date}/stock/preview")
async def preview_stock(
    date: str,
    body: StockPreviewBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Kuru prova: uyarıları hesaplar ve bir jeton döndürür.

    `bld_menu.manage` ister çünkü sunucuda bir denetim satırı açar
    (`00-genel.md` §8: kuru prova satırı da yazılır) ve aktör taşır.
    """
    return await service().preview_stock(date, capacity_total=body.capacity_total,
                                         items=body.items, actor=user.full_name)


class StockApplyBody(WriteBody):
    """Önizlenen tabloyu uygular. TABLO GÖVDEDE YOK, jetonun arkasında.

    Tabloyu burada tekrar göndermek, onaylanan tablo ile uygulanan tablonun
    aynı olduğunu kanıtlamayı imkânsız kılardı: ikisi arasındaki tek bağ
    kullanıcının hafızası olurdu.
    """

    token: str = Field(min_length=8, max_length=64)


@router.put("/days/{date}/stock")
async def apply_stock(
    date: str,
    body: StockApplyBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Jetonla gelir, temel çizgiyi doğrular, tavanları yazar."""
    return await service().apply_stock(date, token=body.token, actor=user.full_name)


# ================================================================ kopyalama

class DuplicateBody(ReasonBody):
    """GEREKÇE İSTER: tek çağrıda bir günün tamamını yazar ve `overwrite` ile
    mevcut bir taslağın üzerine geçebilir — tek tıkla en çok veri değiştiren
    menü işlemi budur."""

    target_date: str = Field(min_length=10, max_length=10)
    #: `true` yalnız TASLAK bir hedefin üzerine yazabilir; yayınlanmış günün
    #: üzerine kopyalamayı sunucu 409 ile reddeder.
    overwrite: bool = False


@router.post("/days/{date}/duplicate")
async def duplicate(
    date: str,
    body: DuplicateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Hedef gün HER ZAMAN `draft` doğar; görsel ve iç not kopyalanmaz."""
    return await service().duplicate(date, target_date=body.target_date,
                                     overwrite=body.overwrite, reason=body.reason,
                                     actor=user.full_name, dry_run=body.dry())
