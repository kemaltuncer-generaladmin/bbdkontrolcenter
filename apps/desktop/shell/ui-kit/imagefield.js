// Görsel alanı — dosya seç / sürükle-bırak / önizle, seçim anında ön denetim.
//
// GERÇEK KAPI SUNUCUDADIR. Buradaki tür, boyut ve çözünürlük denetimi
// KULLANICI DENEYİMİDİR, güvenlik değil: 30 MB'lık bir .mov dosyasını
// gönderip sunucudan ret beklemek hem hız kovasından pay yer hem de
// kullanıcıya "istek doğrulanamadı" gibi bir şey söylemeyen metin gösterir.
// Sunucu tarafındaki asıl denetim `modules/store_api/backend/upload.py`
// içindedir ve burada ne yazarsa yazsın orası kendi kararını verir (K9).
// Bu dosyadaki bir denetimi gevşetmek sunucuyu gevşetmez; sıkmak da
// sunucunun kabul edeceği bir dosyayı reddetmemelidir.
//
// TEK KOPYA (ADR 0011). Bu bileşen üç ayrı panelde üç ayrı biçimde yazılıydı:
//
//   store_products      · en olgunu. Dosya başına inceleme (`inspectFile`),
//                         ret sebebini kendi satırında yazan günlük, kapak
//                         sırası, nesne URL'lerini bırakan temizlik.
//   store_home_media    · tek dosya + SUNUCUDA ölçüm (`/image/check`) ve iki
//                         kareli önizleme (vitrindeki kırpılmış hâl + gerçek
//                         oran). Ölçümü sunucuya sormak modüle özeldir ve
//                         genelleştirilemez; İKİ KARE genelleştirilebilir ve
//                         `frameRatio` seçeneğine bağlandı.
//   bbd_canteen_products· en yalını. Tür ve boyut denetimi tek `toast`,
//                         önizleme tek kare, sürükle-bırak var.
//
// Ortak payda store_products sürümüdür; öteki ikisinin fazlası seçeneğe
// bağlıdır. Hiçbiri modül adı, uç adresi ya da iş kuralı bilmez: bu bileşen
// dosyayı SEÇER ve ANLATIR, göndermez. Gönderme çağıranın işidir.
//
// NEDEN BASE64: kabuk Tauri ve fs eklentisi yok; dosya tarayıcıda
// `FileReader` ile okunup JSON gövdede taşınıyor. `payload()` bu yüzden var
// ve BİLEREK GEÇ çalışır — altı dosyanın base64'ünü form açık dururken
// bellekte tutmanın anlamı yok.

import { badge, alertBox } from './layout.js';
import { bytes, button, h, num } from './kit.js';

export const IMAGEFIELD_VERSION = '1.0.0';

/** Uzantı → mime. Tarayıcı bazı sürüklemelerde `file.type` boş bırakıyor. */
const EXT_MIME = {
  bmp: 'image/bmp',
  gif: 'image/gif',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
};

/** `File` → `data:` URI. Tauri kabuğunda fs eklentisi yok; tek yol budur. */
export function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`\`${file.name}\` okunamadı; dosya taşınmış ya da `
      + 'erişim kapanmış olabilir.'));
    reader.onload = () => resolve(String(reader.result || ''));
    reader.readAsDataURL(file);
  });
}

/** Ölçüyü tarayıcıya ölçtürür. Okunamazsa `null` — sıfır UYDURULMAZ. */
export function measureImage(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const probe = new Image();
    const finish = (size) => { URL.revokeObjectURL(url); resolve(size); };
    probe.onload = () => finish({ width: probe.naturalWidth, height: probe.naturalHeight });
    probe.onerror = () => finish(null);
    probe.src = url;
  });
}

/** 1920×640 → "3:1". Sadeleşmiyorsa ondalık yazılır ("1,8:1"). */
export function ratioText(width, height) {
  if (!width || !height) return '';
  const divisor = (function gcd(a, b) { return b ? gcd(b, a % b) : a; }(width, height)) || 1;
  const shortW = Math.round(width / divisor);
  const shortH = Math.round(height / divisor);
  if (shortW <= 20 && shortH <= 20) return `${shortW}:${shortH}`;
  return width >= height
    ? `${(width / height).toFixed(1).replace('.', ',')}:1`
    : `1:${(height / width).toFixed(1).replace('.', ',')}`;
}

/** Küçük harfli uzantı; yoksa boş metin. */
function extensionOf(name) {
  const clean = String(name || '').trim();
  return clean.includes('.') ? clean.split('.').pop().toLowerCase() : '';
}

/**
 * Tek dosyanın kararı: listeye alınır mı, alınırsa kullanıcı neyi bilmeli.
 * `{file, ok, error, warnings, width, height}` döner ve ASLA istek atmaz.
 *
 * TÜR DENETİMİ MİME'E TAKILI DEĞİL: WebKitGTK bazı sürükle-bırak
 * işlemlerinde `file.type` alanını boş bırakıyor ve mime'e bakan bir denetim
 * o dosyayı "tanınmayan tür" diye reddederdi. Sunucu da (`upload.py`) "mime
 * uygun YA DA uzantı uygun" diyor; buradaki kural aynısıdır — daha katı
 * davranıp sunucunun kabul edeceği dosyayı reddetmek yanlış olurdu.
 */
export async function inspectFile(file, rules = {}) {
  const accept = rules.accept || [];
  const maxBytes = Number(rules.maxBytes) || 0;
  const mime = String(file.type || '').toLowerCase();
  const guessed = mime || EXT_MIME[extensionOf(file.name)] || '';

  if (accept.length && !accept.includes(guessed)) {
    const label = guessed ? guessed.replace('image/', '').toUpperCase() : 'tanınmayan tür';
    return {
      file,
      ok: false,
      warnings: [],
      error: `${label} kabul edilmiyor; kabul edilenler `
        + `${accept.map((item) => item.replace('image/', '').toUpperCase()).join(', ')}. `
        + 'Görseli bu biçimlerden birine çevirip yeniden seçin.',
    };
  }
  if (maxBytes && file.size > maxBytes) {
    return {
      file,
      ok: false,
      warnings: [],
      error: `Dosya ${bytes(file.size)}; sınır ${bytes(maxBytes)}. Görseli küçültüp yeniden `
        + 'deneyin — dosya listeye alınmadı.',
    };
  }
  if (!file.size) {
    return { file, ok: false, warnings: [], error: 'Dosya boş.' };
  }

  const size = await measureImage(file);
  if (!size) {
    return {
      file,
      ok: true,
      width: 0,
      height: 0,
      warnings: ['Görselin ölçüsü tarayıcıda okunamadı; dosya yarım inmiş olabilir. '
        + 'Gönderdikten sonra sonucu gözle doğrulayın.'],
    };
  }

  const { width, height } = size;
  const warnings = [];
  const minWidth = Number(rules.minWidth) || 0;
  const minHeight = Number(rules.minHeight) || 0;
  const maxRatio = Number(rules.maxRatio) || 0;
  if ((minWidth && width < minWidth) || (minHeight && height < minHeight)) {
    warnings.push(`Önerilen en az ${minWidth}×${minHeight}; yüklenen ${width}×${height} — `
      + 'büyütülünce bulanık görünür.');
  }
  const longSide = Math.max(width, height);
  const shortSide = Math.min(width, height);
  if (maxRatio && longSide > shortSide * maxRatio) {
    warnings.push(`Görsel ${ratioText(width, height)} oranında (${width}×${height}); `
      + 'kareye yakın bekleyen bir ızgarada kenarlardan kırpılır.');
  }
  return { file, ok: true, width, height, warnings };
}

/** Dosya başına tek satır: ad · boyut · ölçü · karar. Renk tek başına konuşmaz. */
function fileLine(report, tone, text) {
  const line = h('div', `kit-img-line ${tone}`);
  const size = report.width ? `${report.width}×${report.height}` : 'ölçü okunamadı';
  line.append(
    h('b', undefined, report.file.name),
    h('span', 'kit-img-meta', `${bytes(report.file.size)} · ${size}`),
    badge({ bad: 'Alınmadı', warn: 'Uyarılı', good: 'Gönderildi', dim: 'Seçildi' }[tone] || tone,
      tone),
    h('span', 'kit-img-why', text),
  );
  return line;
}

/** "1920x640" → {width, height}. Çözülemezse null. */
function parseFrame(raw) {
  const match = String(raw || '').trim().toLowerCase().match(/^(\d+)\s*[x×]\s*(\d+)$/);
  if (!match) return null;
  const width = Number(match[1]);
  const height = Number(match[2]);
  return width > 0 && height > 0 ? { width, height } : null;
}

/**
 * Görsel alanı.
 *
 * @param {object} spec
 * @param {object} [spec.rules]      — {accept:string[], maxBytes, minWidth, minHeight, maxRatio}
 * @param {number} [spec.limit]      — en çok kaç dosya; 0 = sınırsız
 * @param {boolean} [spec.multiple]  — çoklu seçim (varsayılan açık)
 * @param {boolean} [spec.reorder]   — kapak sırası düğmeleri (çoklu seçimde anlamlı)
 * @param {string} [spec.frameRatio] — "1920x640": vitrindeki KIRPILMIŞ hâli de çizer
 * @param {string} [spec.label]      — seçme düğmesinin yazısı
 * @param {string} [spec.dropText]
 * @param {string} [spec.emptyText]  — hiç dosya yokken ızgarada duran cümle
 * @param {(entries:object[])=>void} [spec.onChange]
 * @returns {{node:HTMLElement, files:()=>object[], payload:()=>Promise<object[]>,
 *            count:()=>number, clear:()=>void, destroy:()=>void}}
 */
export function imageField({
  rules = {},
  limit = 0,
  multiple = true,
  reorder = true,
  frameRatio = '',
  label = 'Görsel seç',
  dropText = 'Görselleri buraya sürükleyip bırakın',
  emptyText = 'Henüz görsel seçilmedi.',
  onChange,
} = {}) {
  /** @type {{file:File, url:string, report:object}[]} */
  let picked = [];
  let disposed = false;

  const node = h('div', 'kit-img');
  const grid = h('div', 'kit-img-grid');
  // GÜNLÜK PARTİLER ARASINDA BİRİKİR, SIFIRLANMAZ. Kullanıcı önce
  // `kapak.png` + `arka.pdf` bırakıp PDF'in reddedildiğini okuyor, sonra
  // `ic1.png` bırakıyor. Her partide silinseydi PDF'in alınmadığına dair tek
  // iz ekrandan kaybolur ve kullanıcı üç dosya seçtiğini sanırdı.
  const log = h('div', 'kit-img-log');
  const frame = parseFrame(frameRatio);

  // SINIR BİLİNMİYORSA UYDURULMAZ AMA SUSULMAZ DA. Kurallar boş gelirse
  // (`/reference` düşmüşse) `inspectFile` içindeki denetimler sırayla kapanır
  // — hepsi kuralın DOĞRULUK DEĞERİNE bağlı. Ekranın sessizce "hazır"
  // görünmesi, 50 MB'lık bir dosyanın ızgarada durması demekti.
  const unknownRules = !rules || !Number(rules.maxBytes) || !(rules.accept || []).length;

  const publicEntries = () => picked.map((entry) => ({
    file: entry.file,
    name: entry.file.name,
    size: entry.file.size,
    mime: entry.file.type || '',
    url: entry.url,
    width: entry.report.width || 0,
    height: entry.report.height || 0,
    warnings: entry.report.warnings || [],
  }));

  const emit = () => onChange?.(publicEntries());

  const release = () => {
    // Nesne URL'leri ELDE BIRAKILMAZ: her önizleme bir bellek tutamağıdır ve
    // alan kapanınca bırakılmazsa panel açıldıkça birikirler.
    picked.forEach((entry) => URL.revokeObjectURL(entry.url));
  };

  function paint() {
    grid.replaceChildren();
    if (!picked.length) {
      if (emptyText) grid.append(h('div', 'kit-img-empty', emptyText));
      return;
    }
    picked.forEach((entry, index) => {
      const cover = multiple && index === 0;
      const cell = h('div', `kit-img-cell${cover ? ' cover' : ''}`);

      // İKİ KARE — bilerek (store_home_media'dan gelen fikir). Solda görselin
      // ÇERÇEVEYE OTURMUŞ, yani kırpılmış hâli; sağda dosyanın gerçek oranı.
      // Tek kare göstermek tam da uyarmaya çalıştığımız kırpmayı gizlerdi.
      if (frame) {
        const box = h('div', 'kit-img-frame');
        box.style.aspectRatio = `${frame.width} / ${frame.height}`;
        const shown = h('img');
        shown.src = entry.url;
        shown.alt = '';
        box.append(shown);
        cell.append(box, h('span', 'kit-img-cap',
          `Çerçevede böyle görünür (${frame.width}×${frame.height})`));
      }

      const picture = h('img', 'kit-img-thumb');
      picture.src = entry.url;
      picture.alt = '';
      cell.append(picture);

      if (multiple) {
        cell.append(h('span', 'kit-img-tag', cover ? 'Kapak' : `#${index + 1}`));
      }

      const tools = h('div', 'kit-img-tools');
      if (reorder && multiple && picked.length > 1) {
        // Sürükle-bırak tek yol OLAMAZ: ok düğmeleriyle de taşınır (klavye).
        const move = (step) => {
          const target = index + step;
          if (target < 0 || target >= picked.length) return;
          const next = [...picked];
          [next[index], next[target]] = [next[target], next[index]];
          picked = next;
          paint();
          emit();
        };
        tools.append(
          button('◀', { variant: 'ghost', title: 'Sola taşı', onClick: () => move(-1) }),
          button('▶', { variant: 'ghost', title: 'Sağa taşı', onClick: () => move(1) }),
        );
      }
      tools.append(button('Çıkar', {
        variant: 'danger',
        title: 'Listeden çıkarır — hiçbir yere gönderilmez',
        onClick: () => {
          URL.revokeObjectURL(entry.url);
          picked = picked.filter((item) => item !== entry);
          paint();
          emit();
        },
      }));
      cell.append(tools);

      cell.append(h('span', 'kit-img-meta',
        `${entry.file.name} · ${bytes(entry.file.size)}`
        + (entry.report.width ? ` · ${entry.report.width}×${entry.report.height}` : '')));
      grid.append(cell);
    });
  }

  const input = h('input', 'kit-img-file');
  input.type = 'file';
  input.multiple = Boolean(multiple);
  input.accept = (rules.accept || []).join(',');
  input.id = `kit-img-${Math.random().toString(36).slice(2, 8)}`;
  // Görsel olarak gizli ama KLAVYEYLE ULAŞILIR: `display:none` verilseydi
  // sekme tuşuyla erişilemez ve dosya seçmenin klavye yolu hiç kalmazdı.
  const chooser = h('label', 'kit-btn kit-btn-primary kit-img-label', label);
  chooser.setAttribute('for', input.id);

  const drop = h('div', 'kit-img-drop');
  // SINIR BİLİNMİYORSA O CÜMLE HİÇ YAZILMAZ. `bytes(0)` "0 B" döndürüyor ve
  // "dosya başına en çok 0 B" olgusal olarak YANLIŞ bir cümledir — kullanıcı
  // sınırı okuduğunu sanır, oysa ekran sınırı bilmiyordur.
  const hint = unknownRules
    ? 'Görsel kuralları okunamadı; dosyalar ancak sunucuda denetlenecek.'
    : `${(rules.accept || []).map((item) => item.replace('image/', '').toUpperCase()).join(' · ')}`
      + ` · dosya başına en çok ${bytes(rules.maxBytes)}`
      + (rules.minWidth ? ` · önerilen en az ${rules.minWidth}×${rules.minHeight}` : '')
      + (limit ? ` · en çok ${num(limit)} dosya` : '');
  drop.append(h('div', 'kit-img-drop-text', dropText), h('span', 'kit-img-meta', hint));

  if (unknownRules) {
    // Yeşil "hazır" izlenimi verilmez: seçilen dosya burada denetlenmemiştir
    // ve reddi ancak sunucudan dönecektir. Veri kaybı yok — kaybolan şey
    // erken uyarı, ve bunun söylenmesi gerekir.
    node.append(alertBox('Görsel kuralları okunamadı; dosyalar burada denetlenmeden '
      + 'listelenir ve ancak gönderildiğinde reddedilebilir.', 'warn'));
  }

  async function accept(fileList) {
    const files = [...(fileList || [])];
    if (!files.length) return;

    // REDDEDİLEN DOSYA LİSTEYE HİÇ GİRMEZ ve sebebi kendi satırında yazar.
    // Toplu "3 dosya alınmadı" mesajı kullanıcıya hangisini küçülteceğini
    // söylemiyordu.
    for (const file of files) {
      const report = await inspectFile(file, rules);   // eslint-disable-line no-await-in-loop
      if (disposed) return;
      if (!report.ok) {
        log.append(fileLine(report, 'bad', report.error));
        continue;
      }
      if (!multiple) {
        // Tek dosyalı alanda yeni seçim öncekinin YERİNE geçer; önceki
        // tutamak burada bırakılmazsa alan her seçimde bir URL sızdırır.
        picked.forEach((entry) => URL.revokeObjectURL(entry.url));
        picked = [];
      } else if (limit && picked.length >= limit) {
        log.append(fileLine(report, 'bad',
          `En çok ${num(limit)} görsel seçilebilir; bu dosya listeye alınmadı.`));
        continue;
      }
      picked.push({ file, url: URL.createObjectURL(file), report });
      if (report.warnings.length) log.append(fileLine(report, 'warn', report.warnings.join(' ')));
    }
    input.value = '';                 // aynı dosya tekrar seçilebilsin
    paint();
    emit();
  }

  input.addEventListener('change', () => accept(input.files));
  drop.addEventListener('dragover', (event) => {
    event.preventDefault();
    drop.classList.add('over');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (event) => {
    event.preventDefault();
    drop.classList.remove('over');
    accept(event.dataTransfer?.files);
  });

  const actions = h('div', 'kit-img-actions');
  // Sıra önemli: gizli girdi ETİKETTEN ÖNCE gelir, yoksa odak halkasını
  // etikete taşıyan kardeş seçici (.kit-img-file:focus-visible +
  // .kit-img-label) eşleşmez ve klavye kullanıcısı nereye bastığını göremez.
  actions.append(input, chooser);

  node.append(drop, actions, grid, log);
  paint();

  return {
    node,
    /** Seçili dosyaların tanımı — `file` nesnesi ve okunmuş ölçüsüyle. */
    files: publicEntries,
    count: () => picked.length,
    /**
     * Gövdeye girecek dosyalar. Base64 BURADA üretilir, seçim anında değil:
     * altı dosyayı form açık dururken bellekte tutmanın anlamı yok.
     * SIRAYLA okunur — hepsini aynı anda açmak kaçınılmak istenen bellek
     * tepesini üretirdi.
     */
    async payload() {
      const out = [];
      for (const entry of picked) {
        const content = await readAsDataUrl(entry.file);   // eslint-disable-line no-await-in-loop
        out.push({
          filename: entry.file.name,
          mime: entry.file.type || '',
          content,
          width: entry.report.width || 0,
          height: entry.report.height || 0,
        });
      }
      return out;
    },
    clear() {
      release();
      picked = [];
      log.replaceChildren();
      paint();
      emit();
    },
    /** Panel cleanup'ında ÇAĞRILMALI: her önizleme bir nesne URL'i tutuyor. */
    destroy() {
      disposed = true;
      release();
      picked = [];
    },
  };
}
