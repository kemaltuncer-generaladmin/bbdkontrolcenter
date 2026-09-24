// Öğrenci Tablet Yönetimi — profiller, fiziksel tabletler ve öğrenci ek süresi.
const h = (tag, className, content) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
};

function field(label, input) {
  const box = h('label', 'tb-field');
  box.append(h('span', null, label), input);
  return box;
}

function input(value = '', type = 'text') {
  const node = h('input', 'tb-input');
  node.type = type;
  node.value = value;
  return node;
}

function button(label, onClick, primary = false) {
  const node = h('button', `tb-button${primary ? ' primary' : ''}`, label);
  node.type = 'button';
  node.addEventListener('click', onClick);
  return node;
}

function option(value, label) {
  return new Option(label, value);
}

function displayDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('tr-TR', {
    dateStyle: 'short', timeStyle: 'short',
  });
}

// Sunucunun kabul ettiği Android SystemPolicyControl.storageKey() alt kümesi.
// Boş seçim, tabletteki mevcut varsayılan davranışı korur.
const SYSTEM_CONTROLS = [
  ['usb_file_transfer', 'USB dosya aktarımı', 'Tabletin USB üzerinden depolama olarak kullanılmasını engeller.'],
  ['unknown_source_installs', 'Bilinmeyen kaynaklardan kurulum', 'Mağaza dışı uygulama kurulumunu engeller.'],
  ['app_stores_and_installers', 'Uygulama mağazaları ve yükleyiciler', 'Uygulama kurulumunu ve tablet uygulamasının güvenli güncellemesini de engelleyebilir.'],
  ['account_modification', 'Hesap değişiklikleri', 'Cihazda hesap ekleme ve kaldırmayı engeller.'],
  ['vpn_configuration', 'VPN yapılandırması', 'VPN ayarlarının değiştirilmesini engeller.'],
  ['network_reset', 'Ağ ayarlarını sıfırlama', 'Ağ ayarlarını sıfırlamayı engeller.'],
  ['tethering', 'İnternet paylaşımı', 'Hotspot ve bağlantı paylaşımı ayarlarını engeller.'],
  ['wifi_configuration', 'Wi-Fi yapılandırması', 'Ağ seçimini kısıtlar; yeni bir Wi-Fi ağı veya şifresi tanımlamaz. Cihaz üreticisine göre sonuç değişebilir.'],
  ['private_dns', 'Özel DNS', 'Özel DNS ayarlarının değiştirilmesini engeller.'],
  ['date_and_time', 'Tarih ve saat', 'Sistem saati ve saat dilimi değişikliklerini engeller.'],
  ['safe_boot', 'Güvenli mod', 'Cihazın güvenli modda başlatılmasını engeller.'],
  ['factory_reset', 'Fabrika ayarlarına sıfırlama', 'Ayarlar üzerinden cihazı sıfırlamayı engeller.'],
  ['user_and_profile_creation', 'Kullanıcı ve profil oluşturma', 'Yeni kullanıcı/profil oluşturmayı ve kullanıcı değiştirmeyi engeller.'],
  ['app_uninstall', 'Uygulama kaldırma', 'Uygulamaların kaldırılmasını engeller.'],
  ['app_control_settings', 'Uygulama kontrol ayarları', 'Uygulamaları durdurma ve verilerini silme ayarlarını engeller.'],
];

export function mount(root, ctx) {
  const styleHref = new URL('./panel.css', import.meta.url).href;
  if (!document.querySelector(`link[href="${styleHref}"]`)) {
    const link = h('link');
    link.rel = 'stylesheet';
    link.href = styleHref;
    document.head.append(link);
  }

  const state = { profiles: [], devices: [], sessions: [], students: [],
    selectedProfile: '', selectedDevice: '', selectedStudent: '', inventory: [] };
  const view = h('div', 'tb');
  const heading = h('header', 'tb-heading');
  heading.append(h('div', null, 'Öğrenci tabletleri'));
  heading.append(button('Yenile', () => refresh()));
  const notice = h('p', 'tb-notice', 'Yükleniyor…');
  const tabs = h('nav', 'tb-tabs');
  const body = h('div', 'tb-body');
  view.append(heading, notice, tabs, body);
  root.replaceChildren(view);

  let activeTab = 'devices';
  function info(message, bad = false) {
    notice.textContent = message;
    notice.classList.toggle('bad', bad);
  }
  async function call(path, options) {
    return ctx.api(`/api/bbd_tablet${path}`, options);
  }
  async function refresh() {
    try {
      const overview = await call('/overview');
      let students = [];
      let canteenOnline = true;
      try { students = await call('/students'); }
      catch { canteenOnline = false; }
      state.profiles = overview.profiles || [];
      state.devices = overview.devices || [];
      state.sessions = overview.sessions || [];
      state.students = students || [];
      if (!state.selectedProfile) state.selectedProfile = state.profiles[0]?.id || '';
      if (!state.selectedDevice) state.selectedDevice = state.devices[0]?.id || '';
      if (!state.selectedStudent) state.selectedStudent = state.students[0]?.id || '';
      if (state.selectedDevice) {
        try { state.inventory = await call(`/devices/${encodeURIComponent(state.selectedDevice)}/apps`); }
        catch { state.inventory = []; }
      }
      info(`${state.devices.length} tablet · ${state.profiles.length} profil · ${state.sessions.length} açık öğrenci oturumu${canteenOnline ? '' : ' · Kantin öğrenci listesine ulaşılamadı'}`);
      render();
    } catch (error) {
      info(`Veriler alınamadı: ${error.message}`, true);
    }
  }
  function render() {
    tabs.replaceChildren();
    for (const [key, label] of [
      ['devices', 'Tabletler'], ['profiles', 'Profiller'], ['students', 'Kullanım ve ek süre'],
    ]) {
      const tab = button(label, () => { activeTab = key; render(); });
      tab.classList.toggle('active', key === activeTab);
      tabs.append(tab);
    }
    body.replaceChildren();
    if (activeTab === 'devices') renderDevices();
    if (activeTab === 'profiles') renderProfiles();
    if (activeTab === 'students') renderStudents();
  }
  function profilePicker(value) {
    const select = h('select', 'tb-input');
    for (const profile of state.profiles) select.append(option(profile.id, profile.name));
    select.value = value || '';
    return select;
  }
  function renderDevices() {
    const columns = h('div', 'tb-columns');
    const list = h('section', 'tb-card');
    list.append(h('h3', null, 'Kayıtlı tabletler'));
    if (!state.devices.length) list.append(h('p', 'tb-muted', 'Henüz tablet kaydı yok.'));
    for (const device of state.devices) {
      const row = h('button', `tb-list-row${state.selectedDevice === device.id ? ' selected' : ''}`);
      row.type = 'button';
      row.append(h('strong', null, device.name),
        h('small', null, device.last_seen_at
          ? `Son bağlantı: ${displayDateTime(device.last_seen_at)}` : 'Henüz eşlenmedi'));
      row.addEventListener('click', async () => {
        state.selectedDevice = device.id;
        try { state.inventory = await call(`/devices/${encodeURIComponent(device.id)}/apps`); }
        catch { state.inventory = []; }
        render();
      });
      list.append(row);
    }
    const detail = h('section', 'tb-card');
    const device = state.devices.find((item) => item.id === state.selectedDevice);
    detail.append(h('h3', null, device ? device.name : 'Yeni tablet'));
    if (device) {
      detail.append(h('p', 'tb-muted', `${device.manufacturer || ''} ${device.model || ''} · ${device.app_version || 'Sürüm bilgisi yok'}`));
      detail.append(h('p', 'tb-muted', `İlke durumu: ${device.policy_health || 'Henüz bildirilmedi'}`));
      const picker = profilePicker(device.profile_id);
      detail.append(field('Atanmış profil', picker));
      detail.append(button('Profili ata', async () => {
        try {
          await call(`/devices/${encodeURIComponent(device.id)}/assign-profile`, {
            method: 'POST', body: { profileId: picker.value },
          });
          await refresh();
          info('Profil atandı. Tablet yeni profili bir sonraki eşitlemede alacak.');
        } catch (error) { info(error.message, true); }
      }, true));
      const inventory = h('div', 'tb-inventory');
      inventory.append(h('h4', null, `Yüklü uygulamalar (${state.inventory.length})`));
      for (const app of state.inventory) inventory.append(h('p', null,
        `${app.appName || app.packageName} · ${app.packageName}`));
      detail.append(inventory);
    }
    const form = h('div', 'tb-form');
    form.append(h('h4', null, 'Yeni tablet eşleştirme'));
    const name = input('', 'text');
    const picker = profilePicker(state.selectedProfile);
    form.append(field('Tablet adı', name), field('Profil', picker),
      button('Eşleme kodu oluştur', async () => {
        try {
          const result = await call('/devices', {
            method: 'POST', body: { name: name.value.trim(), profileId: picker.value },
          });
          await refresh();
          const codeOut = h('p', 'tb-code',
            `Tek kullanımlık eşleme kodu: ${result.enrollmentCode} · Geçerlilik sonu: ${displayDateTime(result.expiresAt)}`);
          body.querySelector('.tb-card:last-child')?.append(codeOut);
        } catch (error) { info(error.message, true); }
      }, true));
    detail.append(form);
    columns.append(list, detail);
    body.append(columns);
  }
  function renderProfiles() {
    const columns = h('div', 'tb-columns');
    const list = h('section', 'tb-card');
    list.append(h('h3', null, 'Ortak profiller'));
    for (const profile of state.profiles) {
      const row = h('button', `tb-list-row${state.selectedProfile === profile.id ? ' selected' : ''}`);
      row.type = 'button';
      row.append(h('strong', null, profile.name), h('small', null, `Sürüm ${profile.revision} · ${profile.apps.length} uygulama`));
      row.addEventListener('click', () => { state.selectedProfile = profile.id; render(); });
      list.append(row);
    }
    list.append(button('+ Yeni profil', () => { state.selectedProfile = ''; render(); }));
    const detail = h('section', 'tb-card');
    const profile = state.profiles.find((item) => item.id === state.selectedProfile);
    detail.append(h('h3', null, profile ? `Profil · sürüm ${profile.revision}` : 'Yeni profil'));
    const name = input(profile?.name || '');
    const timezone = input(profile?.timezone || 'Europe/Istanbul');
    detail.append(field('Profil adı', name), field('Saat dilimi', timezone));
    const controls = h('section', 'tb-controls');
    controls.append(h('h4', null, 'Uzaktan sistem kontrolleri'),
      h('p', 'tb-muted', 'Açık: kısıtlamayı uygula · Kapalı: kısıtlamayı kaldır · Cihaz varsayılanı: mevcut davranışı koru. Tablet, desteklemediği kısıtlamayı uygulayamayabilir. Geliştirici seçenekleri ve USB hata ayıklama uzaktan değiştirilemez; kurtarma erişimi korunur.'));
    const controlChoices = new Map();
    for (const [key, label, description] of SYSTEM_CONTROLS) {
      const row = h('div', 'tb-control-row');
      const descriptionBox = h('div');
      descriptionBox.append(h('strong', null, label), h('small', null, description));
      const choice = h('select', 'tb-input');
      choice.append(option('', 'Cihaz varsayılanı'), option('true', 'Açık'), option('false', 'Kapalı'));
      if (Object.hasOwn(profile?.systemControls || {}, key)) {
        choice.value = profile.systemControls[key] ? 'true' : 'false';
      }
      controlChoices.set(key, choice);
      row.append(descriptionBox, choice);
      controls.append(row);
    }
    detail.append(controls);
    const appList = h('div', 'tb-apps');
    const appRows = [];
    function addApp(app = {}) {
      const row = h('div', 'tb-app-row');
      const pkg = input(app.packageName || '');
      pkg.placeholder = 'com.example.app';
      const label = input(app.appName || '');
      label.placeholder = 'Uygulama adı';
      const allowed = input('', 'checkbox');
      allowed.checked = Boolean(app.allowed);
      const unlimited = input('', 'checkbox');
      unlimited.checked = Boolean(app.unlimited);
      const minutes = input(String(Math.floor((app.dailyLimitSeconds || 0) / 60)), 'number');
      minutes.min = '0';
      minutes.max = '1440';
      row.append(field('Uygulama paketi', pkg), field('Uygulama adı', label), field('İzin ver', allowed),
        field('Sınırsız', unlimited), field('Günlük süre (dk)', minutes));
      row.append(button('Kaldır', () => { row.remove(); appRows.splice(appRows.indexOf(item), 1); }));
      const item = { row, pkg, label, allowed, unlimited, minutes };
      appRows.push(item);
      appList.append(row);
    }
    for (const app of profile?.apps || []) addApp(app);
    const inventorySelect = h('select', 'tb-input');
    for (const app of state.inventory.filter((item) => item.isLaunchable)) {
      inventorySelect.append(option(app.packageName,
        `${app.appName || app.packageName} · ${app.packageName}`));
    }
    detail.append(h('h4', null, 'Uygulama kuralları'), appList,
      field('Seçili tabletteki uygulamalar', inventorySelect),
      button('Tabletten ekle', () => {
        const app = state.inventory.find((item) => item.packageName === inventorySelect.value);
        if (app && !appRows.some((item) => item.pkg.value === app.packageName)) {
          addApp({ ...app, allowed: true, unlimited: false, dailyLimitSeconds: 3600 });
        }
      }),
      button('+ Uygulama ekle', () => addApp()),
      button('Profili kaydet', async () => {
        const apps = appRows.map((item) => ({
          packageName: item.pkg.value.trim(), appName: item.label.value.trim(),
          allowed: item.allowed.checked, unlimited: item.unlimited.checked,
          dailyLimitSeconds: Number(item.minutes.value) * 60,
        })).filter((app) => app.packageName);
        const systemControls = {};
        for (const [key, choice] of controlChoices) {
          if (choice.value) systemControls[key] = choice.value === 'true';
        }
        try {
          const result = await call(profile ? `/profiles/${encodeURIComponent(profile.id)}` : '/profiles', {
            method: profile ? 'PUT' : 'POST',
            body: { name: name.value.trim(), timezone: timezone.value.trim(), apps, systemControls },
          });
          state.selectedProfile = result.id;
          await refresh();
          info(`Profil kaydedildi · sürüm ${result.revision}`);
        } catch (error) { info(error.message, true); }
      }, true));
    columns.append(list, detail);
    body.append(columns);
  }
  async function renderStudentDetail(detail, studentId, date) {
    try {
      const result = await call(`/students/${encodeURIComponent(studentId)}/usage?localDate=${date}`);
      const student = state.students.find((item) => item.id === studentId);
      detail.replaceChildren(h('h3', null, student?.name || 'Öğrenci'),
        h('p', 'tb-muted', `${date} · Günlük kullanım ve ek süre`));
      const map = new Map();
      for (const row of result.usage || []) map.set(row.packageName, { used: row.usedSeconds, extra: 0 });
      for (const row of result.grants || []) {
        const item = map.get(row.packageName) || { used: 0, extra: 0 };
        item.extra += row.extraSeconds;
        map.set(row.packageName, item);
      }
      if (!map.size) detail.append(h('p', 'tb-muted', 'Bugün için kullanım veya ek süre kaydı yok.'));
      for (const [pkg, value] of map) detail.append(h('p', 'tb-usage',
        `${pkg} · Kullanılan: ${Math.floor(value.used / 60)} dk · Ek süre: ${Math.floor(value.extra / 60)} dk`));
      const allowedApps = new Map();
      for (const profile of state.profiles) for (const app of profile.apps) {
        if (app.allowed) allowedApps.set(app.packageName, app.appName || app.packageName);
      }
      const select = h('select', 'tb-input');
      for (const [pkg, label] of allowedApps) select.append(option(pkg, `${label} · ${pkg}`));
      const minutes = input('10', 'number');
      minutes.min = '1';
      minutes.max = '1440';
      const reason = input('');
      reason.placeholder = 'Gerekçe (isteğe bağlı)';
      detail.append(field('Uygulama', select), field('Ek süre (dk)', minutes),
        field('Gerekçe', reason), button('Ek süre ver', async () => {
          try {
            await call(`/students/${encodeURIComponent(studentId)}/apps/${encodeURIComponent(select.value)}/grants`, {
              method: 'POST', body: { localDate: date, extraSeconds: Number(minutes.value) * 60,
                reason: reason.value.trim() },
            });
            await renderStudentDetail(detail, studentId, date);
            info('Ek süre öğrenci hesabına eklendi. Tablet bir sonraki eşitlemede görecek.');
          } catch (error) { info(error.message, true); }
        }, true));
    } catch (error) { detail.replaceChildren(h('p', 'tb-error', error.message)); }
  }
  function renderStudents() {
    const columns = h('div', 'tb-columns');
    const list = h('section', 'tb-card');
    list.append(h('h3', null, 'Öğrenciler'), h('p', 'tb-muted',
      'Öğrenciler tabletlerde, kantin kiosklarında kullandıkları şifreyle giriş yapar.'));
    const search = input('', 'search');
    search.placeholder = 'Öğrenci ara';
    const rows = h('div', 'tb-students');
    function paintStudents() {
      rows.replaceChildren();
      const q = search.value.toLocaleLowerCase('tr');
      for (const student of state.students.filter((item) => item.name.toLocaleLowerCase('tr').includes(q))) {
        const row = button(student.name || student.id, () => {
          state.selectedStudent = student.id;
          renderStudents();
        });
        row.classList.add('tb-list-row');
        row.classList.toggle('selected', student.id === state.selectedStudent);
        rows.append(row);
      }
    }
    search.addEventListener('input', paintStudents);
    list.append(search, rows);
    paintStudents();
    const detail = h('section', 'tb-card');
    const selected = state.students.find((item) => item.id === state.selectedStudent);
    const session = state.sessions.find((item) => item.student_id === selected?.id);
    const device = state.devices.find((item) => item.id === session?.device_id);
    const profile = state.profiles.find((item) => item.id === device?.profile_id)
      || state.profiles.find((item) => item.id === state.selectedProfile);
    const zone = profile?.timezone || 'Europe/Istanbul';
    const date = new Date().toLocaleDateString('sv-SE', { timeZone: zone });
    if (selected) {
      detail.append(h('h3', null, selected.name), h('p', 'tb-muted', `${date} · ${zone}`));
      renderStudentDetail(detail, selected.id, date);
    }
    columns.append(list, detail);
    body.append(columns);
  }
  refresh();
  return () => root.replaceChildren();
}
