// BBD Akademi — ödev İÇERİĞİ alanları (ders, müfredat konuları, kaynak, açıklama).
//
// Üç yerde aynı alanlar kullanılır: tek ödev formu (Düzenle), toplu ödev
// sihirbazının "Ne?" adımı ve ödev şablonu formu (Şablonlar). Hepsi
// `formDialog`un özel alanlarıdır (`type: 'custom'`); seçimler fabrikanın
// kendi durumunda tutulur, `content(draft)` sözleşmenin `HomeworkContent`
// alanlarını (başlık hariç kalanlar dahil) üretir.
//
// HIZ: konu ve kaynak seçicileri program / kaynak ayrıntısını yalnız
// seçildiğinde ve önbellekten (`getCached`) çeker.

import { button, h } from '../../ui-kit/kit.js';
import { skeletonRows } from '../../ui-kit/layout.js';
import {
  errorBox, gradeText, multiChips, num, problem, searchBox, selectBox, TRACKS,
} from './common.js';

const PROGRAMS = '/v1/admin/curricula/programs';

export const RESOURCE_KINDS = { topic_book: 'Konu anlatımlı', question_bank: 'Soru bankası', fascicle: 'Fasikül',
  practice_exam: 'Deneme', other: 'Diğer' };
export const SECTION_KINDS = { test: 'Test', chapter: 'Bölüm', unit_test: 'Ünite testi', review: 'Tekrar',
  other: 'Diğer' };

/** "TYT · 12. sınıf" — konu seçicideki program etiketi (Müfredat ekranındakiyle aynı dil). */
export function programLevel(program) {
  const parts = [];
  if (program?.exam) parts.push(program.exam);
  if (program?.grade !== null && program?.grade !== undefined && program?.grade !== '') {
    parts.push(gradeText(program.grade));
  }
  if (program?.tracks?.length) parts.push(program.tracks.map((track) => TRACKS[track] || track).join(' / '));
  return parts.join(' · ') || 'Genel';
}

export function stack(main, sub) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', main || '—'));
  if (sub) cell.append(h('small', '', sub));
  return cell;
}

export function removableChip(text, onRemove, title = 'Kaldır') {
  const chip = h('button', 'kit-chip on', `${text} ✕`);
  chip.type = 'button';
  chip.title = title;
  chip.setAttribute('aria-label', `${text} — ${title}`);
  chip.addEventListener('click', onRemove);
  return chip;
}

export function topicsText(topics, max = 3) {
  const list = (topics || []).map((topic) => topic.name).filter(Boolean);
  if (!list.length) return '';
  return list.length > max ? `${list.slice(0, max).join(', ')} +${num(list.length - max)}` : list.join(', ');
}

/**
 * İçerik alanları fabrikası. `current`: düzenlenen ödev / şablon (ya da null).
 * `subjects`: ders listesi (`app.lookups.subjects()`).
 */
export function homeworkContent(app, current, subjects = []) {
  const disposers = [];
  const topics = new Map((current?.topics || []).map((topic) => [topic.id, topic.name]));
  const resource = {
    id: current?.resourceId || null,
    sectionIds: new Set(current?.sectionIds || []),
    pageFrom: current?.pageFrom ?? null,
    pageTo: current?.pageTo ?? null,
  };
  const subjectOptions = [{ value: '', label: 'Ders seçilmedi' },
    ...subjects.map((item) => ({ value: item.id, label: item.name }))];
  if (current?.subject && !subjects.some((item) => item.id === current.subject.id)) {
    subjectOptions.push({ value: current.subject.id, label: current.subject.name });
  }

  // ------------------------------------------------------------- konular
  const topicField = (draft) => {
    const wrap = h('div', 'ak-stack');
    const picked = h('div', 'ak-chips');
    const programSelect = selectBox([{ value: '', label: 'Program seçin' }], '', (programId) => loadProgram(programId),
      { label: 'Program' });
    const topicSlot = h('div', 'ak-stack ak-hw-topics');
    let subjectShown;
    let unitChips = [];

    const refreshChips = () => {
      for (const { chips, live } of unitChips) chips.set(live.filter((topic) => topics.has(topic.id)).map((topic) => topic.id));
    };
    const paintPicked = () => {
      picked.replaceChildren();
      for (const [topicId, name] of topics) {
        picked.append(removableChip(name, () => {
          topics.delete(topicId);
          paintPicked();
          refreshChips();
        }));
      }
      if (!topics.size) picked.append(h('span', 'ak-muted', 'Konu seçilmedi.'));
    };

    async function loadSubject(subjectId) {
      if (subjectId === subjectShown) return;
      subjectShown = subjectId;
      unitChips = [];
      topicSlot.replaceChildren();
      programSelect.node.hidden = !subjectId;
      if (!subjectId) {
        topicSlot.append(h('span', 'ak-muted', 'Konu seçmek için önce ders seçin.'));
        return;
      }
      programSelect.options([{ value: '', label: 'Programlar yükleniyor…' }]);
      try {
        const data = await app.api.getCached(PROGRAMS, { subject: subjectId });
        if (subjectShown !== subjectId) return;
        const programs = data?.items || [];
        programSelect.options([{ value: '', label: programs.length ? 'Program seçin (seviye / sınav)'
          : 'Bu derste program yok' },
        ...programs.map((item) => ({ value: item.id, label: `${programLevel(item)} — ${item.title}` }))]);
        programSelect.set(programs.length === 1 ? programs[0].id : '');
        if (programs.length === 1) loadProgram(programs[0].id);
      } catch (failure) {
        topicSlot.replaceChildren(errorBox(failure));
      }
    }

    async function loadProgram(programId) {
      unitChips = [];
      topicSlot.replaceChildren();
      if (!programId) return;
      topicSlot.append(skeletonRows(3, 3));
      try {
        const program = await app.api.getCached(`/v1/admin/curricula/programs/${programId}`);
        if (programSelect.value !== programId) return;
        topicSlot.replaceChildren();
        const units = [...(program.units || [])].filter((unit) => !unit.retired)
          .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
        for (const unit of units) {
          const live = (unit.topics || []).filter((topic) => !topic.retired)
            .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
          if (!live.length) continue;
          const block = h('div', 'ak-hw-unit');
          const chips = multiChips(live.map((topic) => ({ value: topic.id, label: topic.title })),
            live.filter((topic) => topics.has(topic.id)).map((topic) => topic.id), (ids) => {
              const chosen = new Set(ids);
              for (const topic of live) {
                if (chosen.has(topic.id)) topics.set(topic.id, topic.title); else topics.delete(topic.id);
              }
              paintPicked();
            });
          unitChips.push({ chips, live });
          block.append(h('div', 'ak-hw-unit-title', unit.title), chips.node);
          topicSlot.append(block);
        }
        if (!unitChips.length) topicSlot.append(h('span', 'ak-muted', 'Bu programda konu yok.'));
      } catch (failure) {
        topicSlot.replaceChildren(errorBox(failure));
      }
    }

    paintPicked();
    wrap.append(picked, programSelect.node, topicSlot);
    loadSubject(draft.subjectId || '');
    return { node: wrap, update: (next) => loadSubject(next.subjectId || '') };
  };

  // ------------------------------------------------------------- kaynak
  const resourceField = (draft) => {
    const wrap = h('div', 'ak-stack');
    const chosen = h('div', 'ak-stack');
    const finder = h('div', 'ak-picker');
    const results = h('div', 'ak-picker-list');
    results.hidden = true;
    let token = 0;
    const box = searchBox({ placeholder: 'Kaynak ara (kitap adı, yayınevi ya da ISBN)', onSearch: async (text) => {
      const mine = ++token;
      if (text.length < 2) { results.hidden = true; return; }
      const digits = text.replace(/[\s-]/g, '');
      const query = /^\d{10}(\d{3})?$/.test(digits) ? { isbn: digits } : { q: text };
      try {
        const data = await app.api.get('/v1/catalog/library', { ...query,
          subject: draft.subjectId || undefined, limit: 12 });
        if (mine !== token) return;
        results.replaceChildren();
        const items = data?.items || [];
        if (!items.length) {
          results.append(h('div', 'ak-picker-empty', draft.subjectId
            ? 'Bu derste eşleşen kaynak yok. Kaynaklar ekranından eklenebilir.' : 'Eşleşen kaynak yok.'));
        }
        for (const item of items) {
          const row = h('button', 'ak-picker-row');
          row.type = 'button';
          row.append(h('b', '', item.title), h('span', '', [item.publisher, RESOURCE_KINDS[item.kind],
            `${num(item.sectionCount)} bölüm`, item.scope === 'tenant' ? 'kuruma özel' : ''].filter(Boolean).join(' · ')));
          row.addEventListener('click', () => {
            results.hidden = true;
            if (resource.id !== item.id) {
              resource.id = item.id;
              resource.sectionIds.clear();
              resource.pageFrom = null;
              resource.pageTo = null;
            }
            paintChosen();
          });
          results.append(row);
        }
        results.hidden = false;
      } catch (failure) {
        if (mine !== token) return;
        results.replaceChildren(h('div', 'ak-picker-empty', problem(failure)));
        results.hidden = false;
      }
    } });
    disposers.push(() => box.destroy());
    finder.append(box.node, results);

    async function paintChosen() {
      chosen.replaceChildren();
      finder.hidden = Boolean(resource.id);
      if (!resource.id) {
        chosen.append(h('span', 'ak-muted', 'Kaynak seçilmedi. Hazır kütüphaneden ya da kurumun kaynaklarından '
          + 'arayın.'));
        return;
      }
      const forId = resource.id;
      chosen.append(skeletonRows(2, 3));
      let detail;
      try {
        detail = await app.api.getCached(`/v1/catalog/library/${forId}`);
      } catch (failure) {
        if (resource.id !== forId) return;
        chosen.replaceChildren(errorBox(failure, () => paintChosen()), clearButton());
        return;
      }
      if (resource.id !== forId) return;
      const head = h('div', 'ak-inline');
      head.append(stack(detail.title, [detail.publisher, RESOURCE_KINDS[detail.kind], detail.isbn]
        .filter(Boolean).join(' · ')), h('span', 'kit-spacer'), clearButton());

      const toc = h('div', 'ak-hw-toc');
      for (const unit of [...(detail.toc || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))) {
        const sections = [...(unit.sections || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
        if (!sections.length) continue;
        toc.append(h('div', 'ak-hw-unit-title', unit.title));
        for (const item of sections) {
          const row = h('label', 'ak-hw-section');
          const check = h('input', 'kit-check');
          check.type = 'checkbox';
          check.checked = resource.sectionIds.has(item.id);
          check.addEventListener('change', () => {
            if (check.checked) resource.sectionIds.add(item.id); else resource.sectionIds.delete(item.id);
          });
          const pages = item.pageFrom || item.pageTo ? `s. ${item.pageFrom ?? '…'}–${item.pageTo ?? '…'}` : '';
          row.append(check, h('span', 'ak-hw-section-title', item.title),
            h('small', 'ak-muted', [SECTION_KINDS[item.kind], pages,
              item.questionCount ? `${num(item.questionCount)} soru` : ''].filter(Boolean).join(' · ')));
          toc.append(row);
        }
      }
      if (!toc.childElementCount) toc.append(h('span', 'ak-muted', 'Bu kaynağın içindekiler bilgisi yok.'));

      const pageRow = h('div', 'ak-inline ak-hw-pages');
      const pageInput = (value, onValue, label) => {
        const input = h('input', 'kit-input ak-hw-page');
        input.inputMode = 'numeric';
        input.placeholder = label;
        input.setAttribute('aria-label', label);
        input.value = value ?? '';
        input.addEventListener('input', () => {
          const parsed = Number.parseInt(input.value, 10);
          onValue(Number.isFinite(parsed) && parsed > 0 ? parsed : null);
        });
        return input;
      };
      pageRow.append(h('span', 'ak-muted', 'Sayfa aralığı (isteğe bağlı):'),
        pageInput(resource.pageFrom, (value) => { resource.pageFrom = value; }, 'ilk sayfa'),
        h('span', '', '–'),
        pageInput(resource.pageTo, (value) => { resource.pageTo = value; }, 'son sayfa'));
      chosen.replaceChildren(head, toc, pageRow);
    }

    function clearButton() {
      return button('Kaynağı kaldır', { variant: 'ghost', onClick: () => {
        resource.id = null;
        resource.sectionIds.clear();
        resource.pageFrom = null;
        resource.pageTo = null;
        paintChosen();
      } });
    }

    paintChosen();
    wrap.append(chosen, finder);
    return wrap;
  };

  // ----------------------------------------------------- açıklama / bildirim
  const descriptionField = (draft, set) => {
    const area = h('textarea', 'kit-textarea');
    area.maxLength = 1000;
    area.placeholder = 'Ör. “Testleri çözüp yanlışlarınızın fotoğrafını yükleyin.”';
    area.value = draft.description || '';
    area.addEventListener('input', () => set(area.value));
    return area;
  };

  const resourceValidate = () => (resource.id && resource.pageFrom && resource.pageTo
    && resource.pageFrom > resource.pageTo ? 'İlk sayfa son sayfadan büyük olamaz.' : null);

  /** Sözleşmenin `HomeworkContent` alanları (başlık / açıklama `draft`tan). */
  const content = (draft) => ({
    title: String(draft.title || '').trim(),
    description: String(draft.description || '').trim() || null,
    subjectId: draft.subjectId || null,
    resourceId: resource.id || null,
    sectionIds: resource.id ? [...resource.sectionIds] : [],
    pageFrom: resource.id ? resource.pageFrom : null,
    pageTo: resource.id ? resource.pageTo : null,
    topicIds: [...topics.keys()],
  });

  /** Seçili konu adları (özet satırı için). */
  const topicNames = () => [...topics.values()];

  return {
    topics, resource, subjectOptions, topicField, resourceField, resourceValidate, descriptionField,
    content, topicNames,
    dispose: () => { for (const fn of disposers.splice(0)) fn(); },
  };
}
