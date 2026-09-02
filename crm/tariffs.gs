/* ========================================================================
 *  ТАРИФНЫЙ ДВИЖОК
 *
 *  Источник правды — tariffs.json в корне репозитория. Этот файл его
 *  читает через GitHub Contents API и кэширует на 6 часов, поэтому
 *  править тарифы нужно ТОЛЬКО в репозитории — код трогать не надо.
 *
 *  Если GitHub недоступен или JSON битый, работает встроенная копия
 *  TARIFFS_FALLBACK. Она обязана существовать: без реестра не работает
 *  ни CRM, ни приём брифов.
 *
 *  После правки tariffs.json в репозитории — запустить tariffsRefresh()
 *  или подождать 6 часов.
 * ==================================================================== */

var TARIFFS_PATH      = 'tariffs.json';
var TARIFFS_CACHE_KEY = 'tariffs_registry_v1';
var TARIFFS_CACHE_SEC = 21600;   // 6 часов
var TARIFFS_MEMO_     = null;    // память в пределах одного запуска скрипта

/**
 * Встроенная копия реестра. Обновлять при смене схемы, а не при каждой
 * правке цен — цены подтянутся из репозитория сами.
 */
var TARIFFS_FALLBACK = {
  aliases: {
    'СТАРТ': 'start', 'START': 'start',
    'ПРО': 'pro', 'ПРОФИ': 'pro', 'PRO': 'pro',
    'БИЗНЕС': 'business', 'BUSINESS': 'business',
    'ПРЕМИУМ': 'premium', 'PREMIUM': 'premium',
    'МАКСИМУМ': 'max', 'MAX': 'max', 'ENTERPRISE': 'max'
  },
  platforms_default: ['tg', 'vk', 'max'],
  enums: {
    style_mode: {
      template_brief: 'Шаблон ниши + данные брифа',
      tov_brief:      'Tone of Voice собирается из брифа',
      tov_samples:    'Tone of Voice по текстам клиента',
      tov_tuned:      'Tone of Voice по текстам + донастройка'
    },
    visual_mode: {
      brand_colors:  'Карточка в фирменных цветах',
      brand_logo:    'Карточка с логотипом и шрифтом',
      niche_photos:  'Логотип + нишевая фотобиблиотека',
      custom_photos: 'Логотип + индивидуальные фото'
    },
    report: {
      none:     'нет',
      pdf:      'PDF раз в месяц',
      pdf_call: 'PDF раз в месяц + созвон'
    }
  },
  tariffs: {
    start: {
      id: 'start', name: 'СТАРТ', order: 1, price: 2400, public: true,
      flexible: false, inherits: null,
      limits: {
        posts_per_week: 3, slots_per_day: 1, rubrics_max: 2, custom_rubrics_max: 0,
        holidays_enabled: false, holidays_extra: false, holiday_posts: 0, holiday_promo: false,
        platforms: ['tg', 'vk', 'max'], style_mode: 'template_brief', visual_mode: 'brand_colors',
        content_plan: false, preapprove_first_posts: 3, rubric_edits_per_month: 1,
        report: 'none', support_sla_hours: 24
      }
    },
    pro: {
      id: 'pro', name: 'ПРОФИ', order: 2, price: 3900, public: false,
      flexible: false, inherits: null,
      limits: {
        posts_per_week: 5, slots_per_day: 1, rubrics_max: 3, custom_rubrics_max: 0,
        holidays_enabled: true, holidays_extra: false, holiday_posts: 1, holiday_promo: false,
        platforms: ['tg', 'vk', 'max'], style_mode: 'template_brief', visual_mode: 'brand_colors',
        content_plan: false, preapprove_first_posts: 3, rubric_edits_per_month: 1,
        report: 'none', support_sla_hours: 24
      }
    },
    business: {
      id: 'business', name: 'БИЗНЕС', order: 3, price: 6900, public: true,
      flexible: false, inherits: null,
      limits: {
        posts_per_week: 7, slots_per_day: 1, rubrics_max: 4, custom_rubrics_max: 0,
        holidays_enabled: true, holidays_extra: false, holiday_posts: 2, holiday_promo: false,
        platforms: ['tg', 'vk', 'max'], style_mode: 'tov_brief', visual_mode: 'brand_logo',
        content_plan: true, preapprove_first_posts: 0, rubric_edits_per_month: 2,
        report: 'none', support_sla_hours: 12
      }
    },
    premium: {
      id: 'premium', name: 'ПРЕМИУМ', order: 4, price: 11900, public: true,
      flexible: false, inherits: null,
      limits: {
        posts_per_week: 10, slots_per_day: 2, rubrics_max: 5, custom_rubrics_max: 0,
        holidays_enabled: true, holidays_extra: true, holiday_posts: 3, holiday_promo: false,
        platforms: ['tg', 'vk', 'max'], style_mode: 'tov_samples', visual_mode: 'niche_photos',
        content_plan: true, preapprove_first_posts: 0, rubric_edits_per_month: 4,
        report: 'pdf', support_sla_hours: 4
      }
    },
    max: {
      id: 'max', name: 'МАКСИМУМ', order: 5, price: 19900, price_from: true, public: true,
      flexible: true, inherits: 'premium',
      limits: {
        posts_per_week: 14, slots_per_day: 2, rubrics_max: 5, custom_rubrics_max: 2,
        holidays_enabled: true, holidays_extra: true, holiday_posts: 3, holiday_promo: true,
        platforms: ['tg', 'vk', 'max'], style_mode: 'tov_tuned', visual_mode: 'custom_photos',
        content_plan: true, preapprove_first_posts: 0, rubric_edits_per_month: 8,
        report: 'pdf_call', support_sla_hours: 0
      }
    }
  }
};

/* ---------------------------------------------------------------- *
 *  Загрузка реестра
 * ---------------------------------------------------------------- */

function tariffRegistry_() {
  if (TARIFFS_MEMO_) return TARIFFS_MEMO_;

  var cache = null;
  try { cache = CacheService.getScriptCache(); } catch (e) { cache = null; }

  if (cache) {
    var raw = null;
    try { raw = cache.get(TARIFFS_CACHE_KEY); } catch (e2) { raw = null; }
    if (raw) {
      var cached = null;
      try { cached = JSON.parse(raw); } catch (e3) { cached = null; }
      if (cached && cached.tariffs) { TARIFFS_MEMO_ = cached; return cached; }
    }
  }

  var fetched = tariffsFetch_();
  if (fetched && cache) {
    try { cache.put(TARIFFS_CACHE_KEY, JSON.stringify(fetched), TARIFFS_CACHE_SEC); } catch (e4) {}
  }

  TARIFFS_MEMO_ = fetched || TARIFFS_FALLBACK;
  return TARIFFS_MEMO_;
}

/** Тянет tariffs.json из репозитория. null при любой проблеме. */
function tariffsFetch_() {
  try {
    var repo = ghRepo_();
    var url = 'https://api.github.com/repos/' + repo + '/contents/' + TARIFFS_PATH;
    var res = gh_(url, 'get', null);
    if (res.code !== 200 || !res.json || !res.json.content) return null;

    var b64 = String(res.json.content).replace(/\s/g, '');
    var text = Utilities.newBlob(Utilities.base64Decode(b64)).getDataAsString('UTF-8');
    var obj = JSON.parse(text);
    if (!obj || !obj.tariffs || !Object.keys(obj.tariffs).length) return null;
    return obj;
  } catch (e) {
    return null;
  }
}

/**
 * Сбросить кэш после правки tariffs.json. Запускать вручную из редактора.
 * Без подчёркивания на конце сознательно: Apps Script прячет из списка
 * запуска всё, что кончается на «_», и функцию было не выбрать мышкой.
 */
function tariffsRefresh() {
  TARIFFS_MEMO_ = null;
  try { CacheService.getScriptCache().remove(TARIFFS_CACHE_KEY); } catch (e) {}
  var reg = tariffRegistry_();
  var src = (reg === TARIFFS_FALLBACK) ? 'встроенная копия' : 'репозиторий';
  return { source: src, tariffs: tariffIds_(), count: tariffIds_().length };
}

/* ---------------------------------------------------------------- *
 *  Доступ к тарифам
 * ---------------------------------------------------------------- */

/** Все id по возрастанию order: ['start','pro','business','premium','max'] */
function tariffIds_() {
  var T = tariffRegistry_().tariffs;
  var ids = [];
  for (var id in T) if (T.hasOwnProperty(id)) ids.push(id);
  ids.sort(function (a, b) { return (T[a].order || 0) - (T[b].order || 0); });
  return ids;
}

/** Только те, что показываются клиенту (public !== false). */
function tariffIdsPublic_() {
  var T = tariffRegistry_().tariffs;
  return tariffIds_().filter(function (id) { return T[id].public !== false; });
}

function tariffDef_(id) {
  var T = tariffRegistry_().tariffs;
  return T[id] || T[defaultTariffId_()] || null;
}

function tariffName_(id) {
  var d = tariffDef_(id);
  return d ? d.name : '';
}

function tariffPrice_(id) {
  var d = tariffDef_(id);
  return d ? (d.price || 0) : 0;
}

function tariffOrder_(id) {
  var d = tariffDef_(id);
  return d ? (d.order || 0) : 0;
}

function defaultTariffId_() {
  var fromProp = null;
  try { fromProp = prop_('DEFAULT_TARIFF'); } catch (e) { fromProp = null; }
  var T = tariffRegistry_().tariffs;
  if (fromProp && T[fromProp]) return fromProp;

  var ids = [];
  for (var id in T) if (T.hasOwnProperty(id)) ids.push(id);
  ids.sort(function (a, b) { return (T[a].order || 0) - (T[b].order || 0); });
  return ids[0] || 'start';
}

/**
 * Любое написание тарифа -> стабильный id.
 * Понимает id, отображаемые имена, алиасы из реестра и вхождение
 * названия в строку («БИЗНЕС — 2 поста в день»).
 */
function normalizeTariff_(v) {
  var reg = tariffRegistry_();
  var T = reg.tariffs;
  var A = reg.aliases || {};
  var s = norm_(v);
  if (!s) return defaultTariffId_();

  if (T[s]) return s;

  for (var a in A) {
    if (A.hasOwnProperty(a) && norm_(a) === s && T[A[a]]) return A[a];
  }

  for (var id in T) {
    if (T.hasOwnProperty(id) && norm_(T[id].name) === s) return id;
  }

  // вхождение — от старшего тарифа к младшему, чтобы «ПРЕМИУМ» не
  // проиграл более короткому совпадению
  var desc = tariffIds_().slice().reverse();
  for (var i = 0; i < desc.length; i++) {
    var d = T[desc[i]];
    if (s.indexOf(norm_(d.name)) >= 0 || s.indexOf(desc[i]) >= 0) return desc[i];
  }

  for (var a2 in A) {
    if (A.hasOwnProperty(a2) && T[A[a2]] && s.indexOf(norm_(a2)) >= 0) return A[a2];
  }

  return defaultTariffId_();
}

/**
 * СОВМЕСТИМОСТЬ. Возвращает отображаемое имя, как раньше, — чтобы
 * в таблице и письмах ничего не поехало до миграции на id.
 */
function tariffIn_(v) {
  return tariffName_(normalizeTariff_(v));
}

/**
 * Маркеры разделов формы: {'ПРОФИ': ['ПРОФИ','ПРО'], ...}.
 *
 * Кроме отображаемого имени берутся ЗАГЛАВНЫЕ алиасы из реестра: в живой
 * Google Форме раздел до сих пор называется «ПРО — 2 поста в день», и если
 * оставить только имя «ПРОФИ», такой бриф молча уедет в СТАРТ.
 * Внутри тарифа маркеры отсортированы от длинного к короткому, чтобы
 * «ПРЕМИУМ» не проигрывал более короткому совпадению.
 */
function tariffMarkers_() {
  var override = null;
  try { override = jsonCell_(prop_('TARIFF_MARKERS'), null); } catch (e) { override = null; }
  if (override) return override;

  var reg = tariffRegistry_();
  var T = reg.tariffs;
  var A = reg.aliases || {};
  var out = {};

  tariffIds_().forEach(function (id) {
    var name = T[id].name;
    var list = [name];
    for (var alias in A) {
      if (!A.hasOwnProperty(alias) || A[alias] !== id) continue;
      // маркер ищется в заголовке вопроса как отдельное слово ЗАГЛАВНЫМИ,
      // поэтому строчные написания алиасов сюда не годятся
      if (alias !== alias.toUpperCase()) continue;
      if (list.indexOf(alias) < 0) list.push(alias);
    }
    list.sort(function (a, b) { return b.length - a.length; });
    out[name] = list;
  });
  return out;
}

/* ---------------------------------------------------------------- *
 *  Лимиты
 * ---------------------------------------------------------------- */

/**
 * Итоговые лимиты. Принимает id тарифа ИЛИ объект клиента.
 * Порядок наложения: inherits -> собственные limits -> limitsOverride клиента.
 */
function tariffLimits_(tariffOrClient) {
  var id, override = null;

  if (tariffOrClient && typeof tariffOrClient === 'object' && !Array.isArray(tariffOrClient)) {
    id = normalizeTariff_(tariffOrClient.tariffId || tariffOrClient.tariff);
    override = tariffOrClient.limitsOverride || tariffOrClient.limits_override || null;
    if (typeof override === 'string') override = jsonCell_(override, null);
  } else {
    id = normalizeTariff_(tariffOrClient);
  }

  var def = tariffDef_(id);
  var out = {};
  if (!def) return out;

  if (def.inherits) {
    var base = tariffDef_(def.inherits);
    if (base && base.limits) copyInto_(out, base.limits);
  }
  if (def.limits) copyInto_(out, def.limits);
  if (override) copyInto_(out, override);

  return out;
}

function copyInto_(dst, src) {
  for (var k in src) {
    if (!src.hasOwnProperty(k)) continue;
    if (k.charAt(0) === '_') continue;   // служебные поля вроде _note
    dst[k] = src[k];
  }
  return dst;
}

/* ---------------------------------------------------------------- *
 *  Метаданные лимитов: как сравнивать и как называть
 * ---------------------------------------------------------------- */

var LIMIT_META = {
  posts_per_week:         { title: 'Постов в неделю',        cmp: 'more' },
  slots_per_day:          { title: 'Слотов в день',          cmp: 'more' },
  rubrics_max:            { title: 'Рубрик',                 cmp: 'more' },
  custom_rubrics_max:     { title: 'Своих рубрик',           cmp: 'more' },
  holidays_enabled:       { title: 'Праздничные посты',      cmp: 'bool' },
  holidays_extra:         { title: 'Свои праздничные даты',  cmp: 'bool' },
  holiday_posts:          { title: 'Постов на праздник',     cmp: 'more' },
  holiday_promo:          { title: 'Промо-серии к праздникам', cmp: 'bool' },
  platforms:              { title: 'Площадки',               cmp: 'list' },
  style_mode:             { title: 'Стиль',                  cmp: 'rank',
                            rank: ['template_brief', 'tov_brief', 'tov_samples', 'tov_tuned'] },
  visual_mode:            { title: 'Визуал',                 cmp: 'rank',
                            rank: ['brand_colors', 'brand_logo', 'niche_photos', 'custom_photos'] },
  content_plan:           { title: 'Согласование контент-плана', cmp: 'bool' },
  preapprove_first_posts: { title: 'Первые посты на подтверждение', cmp: 'neutral' },
  rubric_edits_per_month: { title: 'Правок рубрик в месяц',  cmp: 'more' },
  report:                 { title: 'Отчёт',                  cmp: 'rank',
                            rank: ['none', 'pdf', 'pdf_call'] },
  support_sla_hours:      { title: 'Ответ поддержки',        cmp: 'sla' }
};

/** Человеческие подписи значений. Берутся из enums реестра, если есть. */
function limitLabel_(key, value) {
  var enums = tariffRegistry_().enums || {};
  if (enums[key] && enums[key][value]) return enums[key][value];

  if (value === true)  return 'да';
  if (value === false) return 'нет';
  if (Array.isArray(value)) return value.join(', ').toUpperCase();

  if (key === 'support_sla_hours') {
    return Number(value) === 0 ? 'прямой контакт' : 'до ' + value + ' ч';
  }
  return String(value);
}

/**
 * Сравнение двух значений одного лимита.
 * 1 — стало лучше, -1 — хуже, 0 — без изменений или нейтрально.
 */
function limitCompare_(key, from, to) {
  var meta = LIMIT_META[key] || { cmp: 'neutral' };

  if (meta.cmp === 'more') {
    var a = Number(from) || 0, b = Number(to) || 0;
    return b > a ? 1 : (b < a ? -1 : 0);
  }
  if (meta.cmp === 'bool') {
    var x = !!from, y = !!to;
    return (y && !x) ? 1 : ((!y && x) ? -1 : 0);
  }
  if (meta.cmp === 'rank') {
    var i = meta.rank.indexOf(from), j = meta.rank.indexOf(to);
    if (i < 0 || j < 0) return 0;
    return j > i ? 1 : (j < i ? -1 : 0);
  }
  if (meta.cmp === 'sla') {
    // меньше — лучше, 0 значит «прямой контакт» и это лучше всех
    var p = Number(from) === 0 ? -1 : Number(from);
    var q = Number(to)   === 0 ? -1 : Number(to);
    return q < p ? 1 : (q > p ? -1 : 0);
  }
  if (meta.cmp === 'list') {
    var la = (from || []).length, lb = (to || []).length;
    return lb > la ? 1 : (lb < la ? -1 : 0);
  }
  return 0;
}

/* ---------------------------------------------------------------- *
 *  Рубрики клиента
 * ---------------------------------------------------------------- */

function rubricsOf_(c) {
  var r = (c && c.rubrics) || [];
  if (typeof r === 'string') r = jsonCell_(r, []);
  return Array.isArray(r) ? r : [];
}

function rubricActive_(r) {
  return !!r && r.dormant !== true && r.enabled !== false;
}

/** Сколько дней в неделю занимает рубрика. */
function rubricDaysCount_(r) {
  var d = r && r.days;
  if (Array.isArray(d)) return d.filter(String).length;
  return String(d || '').split(',').map(function (x) { return x.trim(); }).filter(String).length;
}

function rubricIsCustom_(r) {
  return !!(r && (r.custom === true || r.isCustom === true));
}

/* ---------------------------------------------------------------- *
 *  Валидация конфига против тарифа
 * ---------------------------------------------------------------- */

/**
 * Возвращает массив нарушений. Пустой массив — конфиг валиден.
 * level: 'error' блокирует сохранение, 'warn' только подсвечивается.
 */
function validateAgainstTariff_(c) {
  var lim = tariffLimits_(c);
  var id  = normalizeTariff_(c && (c.tariffId || c.tariff));
  var out = [];

  var all     = rubricsOf_(c);
  var active  = all.filter(rubricActive_);
  var custom  = active.filter(rubricIsCustom_);
  var regular = active.filter(function (r) { return !rubricIsCustom_(r); });

  if (lim.rubrics_max !== undefined && regular.length > lim.rubrics_max) {
    out.push({
      field: 'rubrics', level: 'error',
      message: 'Активных рубрик ' + regular.length + ', тариф ' + tariffName_(id) +
               ' допускает ' + lim.rubrics_max
    });
  }

  if (lim.custom_rubrics_max !== undefined && custom.length > lim.custom_rubrics_max) {
    out.push({
      field: 'rubrics', level: 'error',
      message: 'Своих рубрик ' + custom.length + ', допускается ' + lim.custom_rubrics_max
    });
  }

  var slots = (c && c.slots) || [];
  if (typeof slots === 'string') slots = slotsIn_(slots);
  if (lim.slots_per_day !== undefined && slots.length > lim.slots_per_day) {
    out.push({
      field: 'slots', level: 'error',
      message: 'Выбрано слотов ' + slots.length + ', тариф допускает ' + lim.slots_per_day
    });
  }

  var week = 0;
  active.forEach(function (r) { week += rubricDaysCount_(r); });
  if (lim.posts_per_week !== undefined && week > lim.posts_per_week) {
    out.push({
      field: 'rubrics', level: 'error',
      message: 'Постов в неделю по рубрикам ' + week + ', тариф допускает ' + lim.posts_per_week
    });
  }

  var holidaysWanted = norm_(c && c.holidays).indexOf('не нужн') !== 0 && !!str_(c && c.holidays);
  if (holidaysWanted && lim.holidays_enabled === false) {
    out.push({
      field: 'holidays', level: 'warn',
      message: 'Праздничные посты не входят в тариф ' + tariffName_(id) + ' — публиковаться не будут'
    });
  }

  if (str_(c && c.holidaysExtra) && lim.holidays_extra === false) {
    out.push({
      field: 'holidaysExtra', level: 'warn',
      message: 'Свои праздничные даты доступны с тарифа ПРЕМИУМ — сохранятся, но не будут использоваться'
    });
  }

  return out;
}

/* ---------------------------------------------------------------- *
 *  План перехода между тарифами
 * ---------------------------------------------------------------- */

/**
 * Что произойдёт при переводе клиента на тариф toId.
 * Ничего не меняет — только считает. Результат идёт в диалог CRM.
 *
 * conflicts — то, что оператор обязан разрешить руками (выбрать,
 * какие рубрики оставить). Пока они есть, подтверждать переход нельзя.
 */
function planTariffChange_(c, toId) {
  var fromId = normalizeTariff_(c && (c.tariffId || c.tariff));
  toId = normalizeTariff_(toId);

  var limFrom = tariffLimits_(c);
  var limTo   = tariffLimits_({ tariff: toId });

  var direction = tariffOrder_(toId) > tariffOrder_(fromId) ? 'up'
                : (tariffOrder_(toId) < tariffOrder_(fromId) ? 'down' : 'same');

  var gains = [], losses = [];
  for (var key in LIMIT_META) {
    if (!LIMIT_META.hasOwnProperty(key)) continue;
    if (limFrom[key] === undefined && limTo[key] === undefined) continue;
    var cmp = limitCompare_(key, limFrom[key], limTo[key]);
    if (cmp === 0) continue;
    var item = {
      key: key,
      title: LIMIT_META[key].title,
      from: limitLabel_(key, limFrom[key]),
      to:   limitLabel_(key, limTo[key])
    };
    (cmp > 0 ? gains : losses).push(item);
  }

  var all      = rubricsOf_(c);
  var active   = all.filter(rubricActive_);
  var dormant  = all.filter(function (r) { return r && r.dormant === true; });
  var custom   = active.filter(rubricIsCustom_);
  var regular  = active.filter(function (r) { return !rubricIsCustom_(r); });

  var conflicts = [];

  if (limTo.rubrics_max !== undefined && regular.length > limTo.rubrics_max) {
    conflicts.push({
      type: 'rubrics_over_limit',
      keep: limTo.rubrics_max,
      candidates: regular.map(function (r) { return r.name; }),
      message: 'Оставьте ' + limTo.rubrics_max + ' из ' + regular.length +
               ' рубрик. Остальные уснут — настройки сохранятся.'
    });
  }

  if (limTo.custom_rubrics_max !== undefined && custom.length > limTo.custom_rubrics_max) {
    conflicts.push({
      type: 'custom_rubrics_over_limit',
      keep: limTo.custom_rubrics_max,
      candidates: custom.map(function (r) { return r.name; }),
      message: limTo.custom_rubrics_max === 0
        ? 'Свои рубрики не входят в тариф ' + tariffName_(toId) + ' — уснут все ' + custom.length
        : 'Оставьте ' + limTo.custom_rubrics_max + ' из ' + custom.length + ' своих рубрик'
    });
  }

  var slots = (c && c.slots) || [];
  if (typeof slots === 'string') slots = slotsIn_(slots);
  if (limTo.slots_per_day !== undefined && slots.length > limTo.slots_per_day) {
    conflicts.push({
      type: 'slots_over_limit',
      keep: limTo.slots_per_day,
      candidates: slots.map(function (k) { var d = slotDef_(k); return d ? d.label : k; }),
      message: 'Оставьте ' + limTo.slots_per_day + ' слот(а) из ' + slots.length
    });
  }

  var week = 0;
  active.forEach(function (r) { week += rubricDaysCount_(r); });
  if (limTo.posts_per_week !== undefined && week > limTo.posts_per_week) {
    conflicts.push({
      type: 'week_over_limit',
      keep: limTo.posts_per_week,
      current: week,
      message: 'Сейчас ' + week + ' постов в неделю, тариф даёт ' + limTo.posts_per_week +
               '. Уберите дни в рубриках.'
    });
  }

  // что можно разбудить обратно при апгрейде
  var canWake = [];
  if (dormant.length && limTo.rubrics_max !== undefined) {
    var room = limTo.rubrics_max - regular.length;
    if (room > 0) {
      canWake = dormant.slice(0, room).map(function (r) { return r.name; });
    }
  }

  return {
    from: { id: fromId, name: tariffName_(fromId), price: tariffPrice_(fromId) },
    to:   { id: toId,   name: tariffName_(toId),   price: tariffPrice_(toId) },
    direction: direction,
    gains: gains,
    losses: losses,
    keepRubrics: regular.map(function (r) { return r.name; }),
    dormantRubrics: dormant.map(function (r) { return r.name; }),
    canWake: canWake,
    conflicts: conflicts,
    canApply: conflicts.length === 0
  };
}

/* ---------------------------------------------------------------- *
 *  Самопроверка. Запустить в редакторе Apps Script после установки.
 * ---------------------------------------------------------------- */

function tariffsSelfTest() {
  var log = [];
  var reg = tariffRegistry_();
  log.push('Источник: ' + (reg === TARIFFS_FALLBACK ? 'встроенная копия' : 'репозиторий'));
  log.push('Тарифы: ' + tariffIds_().join(', '));
  log.push('Публичные: ' + tariffIdsPublic_().join(', '));
  log.push('По умолчанию: ' + defaultTariffId_());

  ['СТАРТ', 'ПРО', 'ПРОФИ', ' бизнес ', 'БИЗНЕС — 2 поста в день', 'premium',
   'ENTERPRISE', 'МАКСИМУМ', '', 'ерунда'].forEach(function (v) {
    log.push('  "' + v + '" -> ' + normalizeTariff_(v) + ' (' + tariffIn_(v) + ')');
  });

  var olga = {
    tariff: 'ПРО',
    limitsOverride: { slots_per_day: 2, posts_per_week: 14 },
    slots: ['morning', 'evening'],
    rubrics: [
      { name: 'Советы',  days: 'пн,ср' },
      { name: 'Отзывы',  days: 'вт' },
      { name: 'Вопросы', days: 'чт,сб' }
    ]
  };
  log.push('Ольга, лимиты: слотов ' + tariffLimits_(olga).slots_per_day +
           ', постов/нед ' + tariffLimits_(olga).posts_per_week);
  log.push('Ольга, нарушений: ' + validateAgainstTariff_(olga).length);

  var plan = planTariffChange_(olga, 'start');
  log.push('ПРО -> СТАРТ: направление ' + plan.direction +
           ', потерь ' + plan.losses.length +
           ', конфликтов ' + plan.conflicts.length +
           ', применимо ' + plan.canApply);
  plan.conflicts.forEach(function (x) { log.push('   ! ' + x.message); });

  var up = planTariffChange_(olga, 'premium');
  log.push('ПРО -> ПРЕМИУМ: приобретений ' + up.gains.length +
           ', конфликтов ' + up.conflicts.length);
  up.gains.forEach(function (g) { log.push('   + ' + g.title + ': ' + g.from + ' -> ' + g.to); });

  var text = log.join('\n');
  Logger.log(text);
  return text;
}
