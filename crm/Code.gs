/**
 * CRM подписного SMM-сервиса — бэкенд на Google Apps Script.
 *
 * Web App отдаёт данные интерфейсу «Клиенты.dc.html» и является
 * единственным местом, где живут секреты: GitHub PAT, ключ RouterAI и
 * токен Telegram-бота хранятся в Script Properties и никогда не уходят
 * в браузер.
 *
 * Развёртывание, свойства скрипта и триггеры — см. crm/README.md.
 */

/* ========================================================================
 *  НАСТРОЙКИ
 * ==================================================================== */

var SHEET_CLIENTS = 'Клиенты';
var SHEET_BRIEFS  = 'Брифы';
var SHEET_LOG     = 'Лог';

/** Обязательные колонки листа «Клиенты» — ровно в этом порядке. */
var HEAD_CLIENTS = [
  'client_id', 'ФИО', 'Телефон', 'Email', 'Telegram', 'Тариф',
  'Название бизнеса', 'О бизнесе', 'Аудитория', 'Город', 'Соцсети', 'Ссылки',
  'Тон', 'Слоты', 'Есть фото', 'Рубрики (JSON)', 'Праздники', 'FAQ',
  'Ограничения список', 'Ограничения текст', 'Заглушка', 'Источник',
  'Дата подключения', 'Дата оплаты', 'Статус оплаты', 'Активен',
  'style_prompt', 'Конфиг закоммичен'
];

/** Служебные колонки CRM. Дописываются справа, если их ещё нет. */
var HEAD_CLIENTS_EXTRA = [
  'Ниша', 'Темы', 'Последняя оплата', 'Предыдущая дата оплаты',
  'Ответы на задания (JSON)', 'Чек-лист (JSON)', 'Итерации', 'Фото в очереди',
  'Последний пост', 'Статус последнего поста', 'Обновлено'
];

var HEAD_LOG = ['client_id', 'Дата', 'Время', 'Рубрика', 'Статус', 'Ошибка'];

/** Служебные колонки листа «Брифы» (лист ответов Google Формы). */
var BRIEF_CLIENT_COL = 'client_id';
var BRIEF_STATE_COL  = 'Обработан';

/** Поле объекта клиента -> заголовок колонки. */
var F = {
  id: 'client_id', name: 'ФИО', phone: 'Телефон', email: 'Email', tg: 'Telegram',
  tariff: 'Тариф', business: 'Название бизнеса', about: 'О бизнесе',
  audience: 'Аудитория', city: 'Город', networks: 'Соцсети', links: 'Ссылки',
  tone: 'Тон', slots: 'Слоты', hasPhoto: 'Есть фото', rubrics: 'Рубрики (JSON)',
  holidays: 'Праздники', faq: 'FAQ', limits: 'Ограничения список',
  limitsText: 'Ограничения текст', cta: 'Заглушка', source: 'Источник',
  startedAt: 'Дата подключения', nextPay: 'Дата оплаты', pay: 'Статус оплаты',
  active: 'Активен', stylePrompt: 'style_prompt', pushed: 'Конфиг закоммичен',
  niche: 'Ниша', topics: 'Темы', paidAt: 'Последняя оплата',
  prevPay: 'Предыдущая дата оплаты',
  styleAnswers: 'Ответы на задания (JSON)', checks: 'Чек-лист (JSON)',
  iterations: 'Итерации', photoQueue: 'Фото в очереди',
  lastPostDate: 'Последний пост', lastPostStatus: 'Статус последнего поста',
  updatedAt: 'Обновлено'
};

var PAY_LABELS = {
  paid: 'Оплачено', due: 'Ждёт оплаты', overdue: 'Просрочка',
  test: 'Тест без оплаты', brief: 'Новый бриф'
};

var SLOT_DEF = [
  { key: 'morning', label: 'Утро',  cron: '07:00' },
  { key: 'midday',  label: 'День',  cron: '11:00' },
  { key: 'evening', label: 'Вечер', cron: '16:00' }
];

var TARIFFS = ['СТАРТ', 'ПРО', 'БИЗНЕС'];
var TARIFF_SLOTS = { 'СТАРТ': 1, 'ПРО': 2, 'БИЗНЕС': 2 };

/**
 * Заголовки вопросов Google Формы. Ответ ищется по вхождению любой из
 * строк в заголовок вопроса (регистр и «ё» не важны) — не по индексу
 * колонки, поэтому вопросы в форме можно переставлять.
 */
var FORM_Q = {
  name:       ['фио', 'ваше имя', 'как вас зовут', 'имя и фамилия'],
  phone:      ['телефон'],
  email:      ['email', 'почта', 'e-mail'],
  tg:         ['telegram', 'телеграм'],
  business:   ['название бизнеса', 'название компании', 'название проекта', 'название'],
  niche:      ['ниша', 'сфера', 'чем занимаетесь', 'вид деятельности'],
  city:       ['город', 'регион'],
  about:      ['о бизнесе', 'расскажите о бизнес', 'опишите бизнес', 'о вашем бизнес'],
  audience:   ['аудитори', 'кто ваши клиенты', 'кто ваш клиент', 'целев'],
  networks:   ['соцсет', 'социальн', 'где публиковать'],
  links:      ['ссылк'],
  tone:       ['тон', 'стиль общения'],
  slots:      ['слот', 'время публикаци', 'когда публиковать'],
  hasPhoto:   ['есть фото', 'фото работ', 'фотографи', 'фото'],
  holidays:   ['праздник'],
  faq:        ['faq', 'частые вопросы', 'вопросы клиентов'],
  limits:     ['о чём нельзя', 'о чем нельзя', 'нельзя писать', 'запрещ', 'ограничения список'],
  limitsText: ['что ещё нельзя', 'что еще нельзя', 'ограничения текст',
               'дополнительные ограничени', 'другие ограничени'],
  cta:        ['заглушка', 'призыв'],
  tariff:     ['тариф'],
  source:     ['откуда', 'источник', 'как узнали', 'как вы о нас']
};


/**
 * Разделы формы под конкретный тариф. Тариф определяется по тому,
 * в каком разделе клиент оставил ответы: заголовок вопроса должен
 * содержать название тарифа ЗАГЛАВНЫМИ буквами («СТАРТ — 1 пост в день»).
 * Строчное «про» в обычном вопросе («расскажите про бизнес») не считается.
 * Можно переопределить свойством скрипта TARIFF_MARKERS:
 *   {"СТАРТ":["раздел а"],"ПРО":["раздел б"],"БИЗНЕС":["раздел в"]}
 */
var TARIFF_MARKERS = { 'СТАРТ': ['СТАРТ'], 'ПРО': ['ПРО'], 'БИЗНЕС': ['БИЗНЕС'] };
var DEFAULT_TARIFF = 'СТАРТ';

var ROUTERAI_URL = 'https://routerai.ru/api/v1/chat/completions';
var ROUTERAI_MODEL_DEFAULT = 'google/gemini-3.1-pro-preview';

/* ========================================================================
 *  СВОЙСТВА СКРИПТА
 * ==================================================================== */

function prop_(key, required) {
  var v = PropertiesService.getScriptProperties().getProperty(key);
  v = v === null || v === undefined ? '' : String(v).trim();
  if (!v && required) throw new Error('Не задано свойство скрипта ' + key);
  return v;
}

function tz_() {
  return Session.getScriptTimeZone() || 'Europe/Moscow';
}

/* ========================================================================
 *  РОУТЕР WEB APP
 * ==================================================================== */

function doGet(e) {
  return route_('GET', e);
}

function doPost(e) {
  return route_('POST', e);
}

function route_(method, e) {
  var req;
  try {
    req = readRequest_(e);
  } catch (err) {
    return json_({ ok: false, error: String(err.message || err) });
  }

  try {
    checkToken_(req);
    var action = String(req.action || '').toLowerCase();
    var out = (method === 'GET' ? GET_ACTIONS : POST_ACTIONS)[action];
    if (!out) throw new Error('Неизвестное действие: ' + (action || '(пусто)') + ' (' + method + ')');
    var body = out(req);
    body = body || {};
    body.ok = true;
    body.at = Utilities.formatDate(new Date(), tz_(), 'dd.MM · HH:mm');
    return json_(body);
  } catch (err) {
    var msg = String((err && err.message) || err);
    console.error(method + ' ' + req.action + ': ' + msg + '\n' + ((err && err.stack) || ''));
    return json_({ ok: false, error: msg });
  }
}

/** Параметры GET-запроса и JSON-тело POST приводятся к одному объекту. */
function readRequest_(e) {
  var req = {};
  if (e && e.parameter) {
    for (var k in e.parameter) req[k] = e.parameter[k];
  }
  if (e && e.postData && e.postData.contents) {
    var raw = String(e.postData.contents).trim();
    if (raw.charAt(0) === '{') {
      var parsed = JSON.parse(raw);
      for (var k2 in parsed) req[k2] = parsed[k2];
    }
  }
  return req;
}

/**
 * Общий токен доступа. Web App развёрнут «для всех, у кого есть ссылка»,
 * поэтому одной ссылки для доступа к данным быть не должно.
 */
function checkToken_(req) {
  var expected = prop_('API_TOKEN');
  if (!expected) return; // не задан — проверка выключена (см. README)
  if (String(req.token || '') !== expected) throw new Error('Неверный токен доступа');
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/* ========================================================================
 *  ДОСТУП К ТАБЛИЦЕ
 * ==================================================================== */

function ss_() {
  var id = prop_('SPREADSHEET_ID');
  if (id) return SpreadsheetApp.openById(id);
  var active = SpreadsheetApp.getActive();
  if (!active) throw new Error('Скрипт не привязан к таблице — задайте SPREADSHEET_ID');
  return active;
}

function sheet_(name, headers) {
  var book = ss_();
  var sh = book.getSheetByName(name);
  if (!sh) {
    if (!headers) throw new Error('В таблице нет листа «' + name + '»');
    sh = book.insertSheet(name);
    sh.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
    sh.setFrozenRows(1);
  }
  return sh;
}

/** Приводит заголовок к виду, по которому его можно сравнивать. */
function norm_(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/ /g, ' ').toLowerCase().replace(/ё/g, 'е')
    .replace(/\s+/g, ' ').trim();
}

/**
 * Читает лист целиком и строит индекс «нормализованный заголовок -> номер
 * колонки». Порядок колонок в таблице значения не имеет.
 */
function table_(name, headers) {
  var sh = sheet_(name, headers);
  var last = sh.getLastRow();
  var width = Math.max(sh.getLastColumn(), 1);
  var values = last ? sh.getRange(1, 1, last, width).getValues() : [[]];
  var head = (values.shift() || []).map(function (h) { return String(h); });
  var idx = {};
  head.forEach(function (h, i) {
    var key = norm_(h);
    if (key && !(key in idx)) idx[key] = i;
  });
  return {
    sheet: sh, head: head, idx: idx, rows: values, width: width,
    has: function (title) { return norm_(title) in this.idx; },
    at: function (row, title) {
      var i = this.idx[norm_(title)];
      return i === undefined ? '' : row[i];
    }
  };
}

/** Дописывает недостающие служебные колонки справа. */
function ensureHeaders_(sh, headers) {
  var width = Math.max(sh.getLastColumn(), 1);
  var head = sh.getRange(1, 1, 1, width).getValues()[0].map(norm_);
  var missing = headers.filter(function (h) { return head.indexOf(norm_(h)) < 0; });
  if (!missing.length) return false;
  sh.getRange(1, width + 1, 1, missing.length).setValues([missing]).setFontWeight('bold');
  return true;
}

/** Однократная подготовка таблицы: три листа с нужными заголовками. */
function setupSheets() {
  var clients = sheet_(SHEET_CLIENTS, HEAD_CLIENTS);
  ensureHeaders_(clients, HEAD_CLIENTS.concat(HEAD_CLIENTS_EXTRA));
  clients.setFrozenRows(1);

  var briefs = sheet_(SHEET_BRIEFS, ['Отметка времени']);
  ensureHeaders_(briefs, [BRIEF_CLIENT_COL, BRIEF_STATE_COL]);

  var log = sheet_(SHEET_LOG, HEAD_LOG);
  ensureHeaders_(log, HEAD_LOG);
  log.setFrozenRows(1);

  return 'Готово: листы «' + SHEET_CLIENTS + '», «' + SHEET_BRIEFS + '», «' + SHEET_LOG + '» на месте';
}

function clientsTable_() {
  var sh = sheet_(SHEET_CLIENTS, HEAD_CLIENTS);
  if (ensureHeaders_(sh, HEAD_CLIENTS.concat(HEAD_CLIENTS_EXTRA))) SpreadsheetApp.flush();
  return table_(SHEET_CLIENTS, HEAD_CLIENTS);
}

/* ========================================================================
 *  ПРЕОБРАЗОВАНИЕ ЗНАЧЕНИЙ
 * ==================================================================== */

function str_(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function bool_(v) {
  if (typeof v === 'boolean') return v;
  var s = norm_(v);
  if (!s) return false;
  return ['да', 'true', '1', 'yes', 'y', 'есть', '+', 'v', 'истина'].indexOf(s) >= 0;
}

function num_(v) {
  if (typeof v === 'number') return v;
  var m = String(v === null || v === undefined ? '' : v).replace(',', '.').match(/-?\d+(\.\d+)?/);
  return m ? Number(m[0]) : 0;
}

function list_(v) {
  if (Array.isArray(v)) return v.map(str_).filter(String);
  return str_(v).split(/[,;\n]/).map(function (x) { return x.trim(); }).filter(String);
}

function jsonCell_(v, fallback) {
  var s = str_(v);
  if (!s) return fallback;
  try {
    var parsed = JSON.parse(s);
    return parsed === null ? fallback : parsed;
  } catch (e) {
    return fallback;
  }
}

/** Дата ячейки -> 'yyyy-MM-dd' (в браузер уходят только строки). */
function dateOut_(v) {
  if (!v) return '';
  var dt = v instanceof Date ? v : parseDate_(v);
  if (!dt) return '';
  return Utilities.formatDate(dt, tz_(), 'yyyy-MM-dd');
}

function parseDate_(v) {
  if (!v) return null;
  if (v instanceof Date) return isNaN(v.getTime()) ? null : v;
  var s = str_(v);
  var iso = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (iso) return new Date(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3]));
  var ru = s.match(/^(\d{1,2})[.\/](\d{1,2})[.\/](\d{4})/);
  if (ru) return new Date(Number(ru[3]), Number(ru[2]) - 1, Number(ru[1]));
  var d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

function today_() {
  var now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function addMonth_(dt) {
  var n = new Date(dt.getTime());
  var day = n.getDate();
  n.setDate(1);
  n.setMonth(n.getMonth() + 1);
  var last = new Date(n.getFullYear(), n.getMonth() + 1, 0).getDate();
  n.setDate(Math.min(day, last));
  return n;
}

/** «Утро, Вечер» / «morning,evening» -> ['morning','evening'] */
function slotsIn_(v) {
  return list_(v).map(function (item) {
    var s = norm_(item);
    var hit = null;
    SLOT_DEF.forEach(function (sl) {
      if (s === sl.key || s === norm_(sl.label) || s.indexOf(norm_(sl.label)) === 0) hit = sl.key;
    });
    return hit;
  }).filter(Boolean).filter(function (k, i, arr) { return arr.indexOf(k) === i; });
}

function slotsOut_(keys) {
  return (keys || []).map(function (k) {
    var sl = slotDef_(k);
    return sl ? sl.label : '';
  }).filter(String).join(', ');
}

function slotDef_(key) {
  for (var i = 0; i < SLOT_DEF.length; i++) if (SLOT_DEF[i].key === key) return SLOT_DEF[i];
  return null;
}

function payIn_(v) {
  var s = norm_(v);
  for (var key in PAY_LABELS) if (norm_(PAY_LABELS[key]) === s || key === s) return key;
  if (!s) return 'brief';
  if (s.indexOf('оплач') === 0) return 'paid';
  if (s.indexOf('просроч') === 0) return 'overdue';
  if (s.indexOf('тест') === 0) return 'test';
  if (s.indexOf('бриф') >= 0 || s.indexOf('нов') === 0) return 'brief';
  return 'due';
}

function tariffIn_(v) {
  var s = norm_(v);
  for (var i = 0; i < TARIFFS.length; i++) if (norm_(TARIFFS[i]) === s) return TARIFFS[i];
  for (var j = 0; j < TARIFFS.length; j++) if (s.indexOf(norm_(TARIFFS[j])) >= 0) return TARIFFS[j];
  return DEFAULT_TARIFF;
}

/** Транслитерация названия бизнеса в client_id. */
function translit_(s) {
  var M = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
  };
  return String(s || '').toLowerCase().split('').map(function (ch) {
    if (M[ch] !== undefined) return M[ch];
    return /[a-z0-9]/.test(ch) ? ch : '-';
  }).join('').replace(/-+/g, '-').replace(/^-|-$/g, '').slice(0, 24);
}

function uniqueId_(base, taken) {
  var id = base || 'client';
  var n = 2;
  while (taken.indexOf(id) >= 0) { id = (base || 'client') + '-' + n; n++; }
  return id;
}

var LIMITS_KNOWN = [
  'Цены и стоимость услуг', 'Конкретные сроки выполнения',
  'Проценты, ставки, доходность', 'Гарантии результата',
  'Медицинские советы и диагнозы'
];
var TOPICS_KNOWN = [
  'Фото работ', 'Советы и полезное', 'Частые вопросы',
  'Идеи и вдохновение', 'Отзывы и результаты'
];
var NETWORKS_KNOWN = ['VK', 'Telegram', 'MAX'];

/** Значения из нескольких строк (пункты могут содержать запятые). */
function lines_(v) {
  if (Array.isArray(v)) return v.map(str_).filter(String);
  return str_(v).split(/\r?\n|\s*\|\s*/).map(function (x) { return x.trim(); }).filter(String);
}

/**
 * Разбирает ответ-мультивыбор: сначала ищет известные варианты целиком
 * (они сами содержат запятые), и только потом делит текст по строкам.
 */
function pickList_(v, known) {
  if (Array.isArray(v)) return v.map(str_).filter(String);
  var s = norm_(v);
  if (!s) return [];
  var hits = known.filter(function (k) { return s.indexOf(norm_(k)) >= 0; });
  return hits.length ? hits : lines_(v);
}

/* ========================================================================
 *  КЛИЕНТ: СТРОКА <-> ОБЪЕКТ
 * ==================================================================== */

/** Строка листа «Клиенты» -> объект в том виде, в каком его ждёт интерфейс. */
function rowToClient_(t, row, rowNumber) {
  var startedAt = parseDate_(t.at(row, F.startedAt));
  var nextPay = parseDate_(t.at(row, F.nextPay));
  var paidAt = parseDate_(t.at(row, F.paidAt));
  var stored = payIn_(t.at(row, F.pay));
  var photo = t.at(row, F.photoQueue);

  return {
    row: rowNumber,
    id: str_(t.at(row, F.id)),
    name: str_(t.at(row, F.name)),
    phone: str_(t.at(row, F.phone)),
    email: str_(t.at(row, F.email)),
    tg: str_(t.at(row, F.tg)),
    tariff: tariffIn_(t.at(row, F.tariff)),
    business: str_(t.at(row, F.business)),
    about: str_(t.at(row, F.about)),
    audience: str_(t.at(row, F.audience)),
    city: str_(t.at(row, F.city)),
    networks: pickList_(t.at(row, F.networks), NETWORKS_KNOWN),
    links: str_(t.at(row, F.links)),
    tone: str_(t.at(row, F.tone)),
    slots: slotsIn_(t.at(row, F.slots)),
    hasPhoto: bool_(t.at(row, F.hasPhoto)),
    rubrics: normRubrics_(jsonCell_(t.at(row, F.rubrics), [])),
    holidays: str_(t.at(row, F.holidays)),
    faq: str_(t.at(row, F.faq)),
    limits: pickList_(t.at(row, F.limits), LIMITS_KNOWN),
    limitsText: str_(t.at(row, F.limitsText)),
    cta: str_(t.at(row, F.cta)),
    source: str_(t.at(row, F.source)),
    startedAt: dateOut_(startedAt),
    nextPay: dateOut_(nextPay),
    paidAt: dateOut_(paidAt),
    prevPay: dateOut_(parseDate_(t.at(row, F.prevPay))),
    pay: payState_(stored, nextPay),
    payStored: stored,
    active: t.has(F.active) ? bool_(t.at(row, F.active)) : true,
    stylePrompt: str_(t.at(row, F.stylePrompt)),
    configPushed: str_(t.at(row, F.pushed)),
    niche: str_(t.at(row, F.niche)),
    topics: pickList_(t.at(row, F.topics), TOPICS_KNOWN),
    styleAnswers: padAnswers_(jsonCell_(t.at(row, F.styleAnswers), [])),
    checks: jsonCell_(t.at(row, F.checks), {}),
    iterations: num_(t.at(row, F.iterations)),
    photoQueue: num_(photo),
    lastPostDate: str_(t.at(row, F.lastPostDate)) || '—',
    lastPostStatus: str_(t.at(row, F.lastPostStatus)) || 'Не запущен'
  };
}

/** Просрочка считается на лету: дата оплаты в прошлом и оплата не подтверждена. */
function payState_(stored, nextPay) {
  if (stored === 'test') return 'test';
  if (!nextPay) return stored === 'paid' ? 'paid' : 'brief';
  if (nextPay < today_()) return 'overdue';
  return stored === 'paid' ? 'paid' : 'due';
}

function normRubrics_(v) {
  if (!Array.isArray(v)) return [];
  return v.map(function (r) {
    r = r || {};
    var days = Array.isArray(r.days) ? r.days.join(', ') : str_(r.days);
    return {
      name: str_(r.name) || 'Рубрика',
      days: days || 'пн',
      prompt: str_(r.prompt),
      example: str_(r.example)
    };
  });
}

function padAnswers_(v) {
  var arr = Array.isArray(v) ? v.map(str_) : [];
  while (arr.length < 5) arr.push('');
  return arr.slice(0, 5);
}

/** Поля объекта -> значения ячеек. Пишутся только присланные поля. */
var TO_CELL = {
  id:            function (v) { return str_(v); },
  name:          function (v) { return str_(v); },
  phone:         function (v) { return str_(v); },
  email:         function (v) { return str_(v); },
  tg:            function (v) { return str_(v); },
  tariff:        function (v) { return tariffIn_(v); },
  business:      function (v) { return str_(v); },
  about:         function (v) { return str_(v); },
  audience:      function (v) { return str_(v); },
  city:          function (v) { return str_(v); },
  networks:      function (v) { return pickList_(v, NETWORKS_KNOWN).join(', '); },
  links:         function (v) { return str_(v); },
  tone:          function (v) { return str_(v); },
  slots:         function (v) { return slotsOut_(Array.isArray(v) ? v : slotsIn_(v)); },
  hasPhoto:      function (v) { return bool_(v) ? 'Да' : 'Нет'; },
  rubrics:       function (v) { return JSON.stringify(normRubrics_(v)); },
  holidays:      function (v) { return str_(v); },
  faq:           function (v) { return str_(v); },
  limits:        function (v) { return pickList_(v, LIMITS_KNOWN).join('\n'); },
  limitsText:    function (v) { return str_(v); },
  cta:           function (v) { return str_(v); },
  source:        function (v) { return str_(v); },
  startedAt:     function (v) { return parseDate_(v); },
  nextPay:       function (v) { return parseDate_(v); },
  paidAt:        function (v) { return parseDate_(v); },
  prevPay:       function (v) { return parseDate_(v); },
  pay:           function (v) { return PAY_LABELS[v] || PAY_LABELS[payIn_(v)] || v; },
  active:        function (v) { return bool_(v); },
  stylePrompt:   function (v) { return str_(v); },
  configPushed:  function (v) { return str_(v); },
  niche:         function (v) { return str_(v); },
  topics:        function (v) { return pickList_(v, TOPICS_KNOWN).join('\n'); },
  styleAnswers:  function (v) { return JSON.stringify(padAnswers_(v)); },
  checks:        function (v) { return JSON.stringify(v || {}); },
  iterations:    function (v) { return num_(v); },
  photoQueue:    function (v) { return num_(v); },
  lastPostDate:  function (v) { return str_(v); },
  lastPostStatus:function (v) { return str_(v); }
};

/** Ключ поля -> имя колонки (configPushed живёт в «Конфиг закоммичен»). */
function colOf_(field) {
  if (field === 'configPushed') return F.pushed;
  return F[field];
}

/**
 * Записывает переданные поля в строку клиента. Поля, которых нет в
 * patch, остаются как есть — интерфейс может слать частичный объект.
 */
function writeRow_(t, rowNumber, patch) {
  var width = Math.max(t.width, t.head.length);
  var range = t.sheet.getRange(rowNumber, 1, 1, width);
  var row = range.getValues()[0];
  var touched = false;

  Object.keys(patch).forEach(function (field) {
    var col = colOf_(field);
    if (!col) return;
    var i = t.idx[norm_(col)];
    if (i === undefined) return;
    var conv = TO_CELL[field];
    if (!conv) return;
    var value = conv(patch[field]);
    row[i] = value === null || value === undefined ? '' : value;
    touched = true;
  });

  if (t.has(F.updatedAt)) {
    row[t.idx[norm_(F.updatedAt)]] = new Date();
    touched = true;
  }
  if (touched) range.setValues([row]);
  return touched;
}

function findRow_(t, id) {
  var needle = str_(id);
  for (var i = 0; i < t.rows.length; i++) {
    if (str_(t.at(t.rows[i], F.id)) === needle) return i + 2; // +1 заголовок, +1 нумерация с 1
  }
  return 0;
}

function readClient_(t, id) {
  var rowNumber = findRow_(t, id);
  if (!rowNumber) throw new Error('Клиент ' + id + ' не найден в листе «' + SHEET_CLIENTS + '»');
  return rowToClient_(t, t.rows[rowNumber - 2], rowNumber);
}

/** Перечитывает одну строку прямо из листа (после записи). */
function refetchClient_(id) {
  var t = clientsTable_();
  return readClient_(t, id);
}

/* ========================================================================
 *  ДЕЙСТВИЯ: ЧТЕНИЕ (doGet)
 * ==================================================================== */

var GET_ACTIONS = {

  /** action=clients — все клиенты из листа «Клиенты». */
  clients: function () {
    var t = clientsTable_();
    var out = [];
    for (var i = 0; i < t.rows.length; i++) {
      if (!str_(t.at(t.rows[i], F.id))) continue;
      out.push(rowToClient_(t, t.rows[i], i + 2));
    }
    return { clients: out };
  },

  /** action=client&id=X — один клиент. */
  client: function (req) {
    var id = str_(req.id);
    if (!id) throw new Error('Не передан id');
    return { client: readClient_(clientsTable_(), id) };
  },

  /** action=briefs — необработанные ответы формы из листа «Брифы». */
  briefs: function () {
    return { briefs: readBriefs_() };
  },

  /** action=log — записи из листа «Лог» (свежие сверху). */
  log: function (req) {
    var limit = num_(req.limit) || 200;
    var t = table_(SHEET_LOG, HEAD_LOG);
    var out = [];
    for (var i = t.rows.length - 1; i >= 0 && out.length < limit; i--) {
      var row = t.rows[i];
      var id = str_(t.at(row, 'client_id'));
      var rubric = str_(t.at(row, 'Рубрика'));
      if (!id && !rubric) continue;
      var status = str_(t.at(row, 'Статус'));
      var err = str_(t.at(row, 'Ошибка'));
      var ok = err ? false : norm_(status).indexOf('ошибк') < 0 && norm_(status) !== 'error';
      var dt = parseDate_(t.at(row, 'Дата'));
      out.push({
        id: id,
        date: dt ? Utilities.formatDate(dt, tz_(), 'dd.MM.yyyy') : str_(t.at(row, 'Дата')),
        time: timeCell_(t.at(row, 'Время')),
        rubric: rubric,
        ok: ok,
        err: err || (ok ? '' : status)
      });
    }
    return { log: out };
  },

  /** action=settings — какие свойства скрипта заданы (без значений). */
  settings: function () {
    return { props: propsState_() };
  },

  /** action=ping — проверка подключений: таблица, GitHub, RouterAI, Telegram. */
  ping: function () {
    return { checks: pingAll_() };
  }
};

function timeCell_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, tz_(), 'HH:mm');
  return str_(v);
}

/**
 * Лист «Брифы» — это лист ответов Google Формы. Триггер onFormSubmit
 * проставляет в служебную колонку client_id созданной карточки; сюда
 * попадают только те ответы, по которым карточки ещё нет (например,
 * пришли, когда триггер был выключен).
 */
function readBriefs_() {
  var sh = sheet_(SHEET_BRIEFS, ['Отметка времени']);
  ensureHeaders_(sh, [BRIEF_CLIENT_COL, BRIEF_STATE_COL]);
  SpreadsheetApp.flush();
  var t = table_(SHEET_BRIEFS);
  var out = [];
  for (var i = 0; i < t.rows.length; i++) {
    var row = t.rows[i];
    var answers = answersOfRow_(t, row);
    if (!Object.keys(answers).length) continue;
    if (str_(t.at(row, BRIEF_CLIENT_COL))) continue;      // карточка уже создана
    if (norm_(t.at(row, BRIEF_STATE_COL)) === 'отложен') continue;
    var stamp = parseDate_(row[0]);
    var brief = briefFromAnswers_(answers);
    if (!brief.business && !brief.name) continue;
    out.push({
      fid: 'row-' + (i + 2),
      row: i + 2,
      business: brief.business || brief.name,
      name: brief.name,
      niche: brief.niche || 'не указана',
      city: brief.city,
      tariff: brief.tariff,
      tg: brief.tg,
      about: brief.about,
      at: stamp ? Utilities.formatDate(stamp, tz_(), 'dd.MM · HH:mm') : '',
      tag: stamp && (today_() - stamp) / 86400000 > 1
        ? 'ждёт ' + Math.round((today_() - stamp) / 86400000) + ' дн.'
        : 'новый',
      answers: brief
    });
  }
  return out;
}

/** Строка листа ответов -> { нормализованный заголовок: ответ }. */
function answersOfRow_(t, row) {
  var answers = {};
  t.head.forEach(function (title, i) {
    var key = norm_(title);
    if (!key) return;
    var v = row[i];
    if (v instanceof Date) v = Utilities.formatDate(v, tz_(), 'yyyy-MM-dd HH:mm');
    v = str_(v);
    if (v) answers[key] = v;
  });
  return answers;
}

/* ========================================================================
 *  ДЕЙСТВИЯ: ЗАПИСЬ (doPost)
 * ==================================================================== */

var POST_ACTIONS = {

  /** action=save — сохранить карточку клиента в таблицу. */
  save: function (req) {
    var patch = req.client || req;
    var id = str_(patch.id);
    if (!id) throw new Error('Не передан client_id');
    return withLock_(function () {
      var t = clientsTable_();
      var rowNumber = findRow_(t, id);
      if (!rowNumber) throw new Error('Клиент ' + id + ' не найден');
      writeRow_(t, rowNumber, cleanPatch_(patch));
      SpreadsheetApp.flush();
      return { client: refetchClient_(id) };
    });
  },

  /**
   * action=pay — подтвердить оплату и сдвинуть дату платежа на месяц.
   * Дата до сдвига уходит в «Предыдущая дата оплаты» — по ней работает
   * откат. Повторное подтверждение отклоняется: иначе каждый лишний клик
   * сдвигал бы дату ещё на месяц.
   */
  pay: function (req) {
    var id = str_(req.id || (req.client && req.client.id));
    if (!id) throw new Error('Не передан client_id');
    return withLock_(function () {
      var t = clientsTable_();
      var rowNumber = findRow_(t, id);
      if (!rowNumber) throw new Error('Клиент ' + id + ' не найден');
      var c = rowToClient_(t, t.rows[rowNumber - 2], rowNumber);
      if (c.pay === 'paid') {
        throw new Error('Оплата уже подтверждена до ' + (c.nextPay || '—') + ' — сначала отмените её');
      }
      var base = parseDate_(c.nextPay) || today_();
      var next = addMonth_(base);
      var checks = c.checks || {};
      checks.paid = true;
      writeRow_(t, rowNumber, {
        pay: 'paid', paidAt: base, prevPay: base, nextPay: next, checks: checks
      });
      SpreadsheetApp.flush();
      return {
        client: refetchClient_(id),
        paidAt: dateOut_(base),
        nextPay: dateOut_(next)
      };
    });
  },

  /**
   * action=unpay — откат ошибочного подтверждения: дата возвращается из
   * «Предыдущая дата оплаты», статус — «Ждёт оплаты», галочка «Оплата
   * получена» снимается.
   */
  unpay: function (req) {
    var id = str_(req.id || (req.client && req.client.id));
    if (!id) throw new Error('Не передан client_id');
    return withLock_(function () {
      var t = clientsTable_();
      var rowNumber = findRow_(t, id);
      if (!rowNumber) throw new Error('Клиент ' + id + ' не найден');
      var c = rowToClient_(t, t.rows[rowNumber - 2], rowNumber);
      if (c.payStored !== 'paid') throw new Error('Оплата не подтверждена — отменять нечего');
      // строки, оплаченные до появления колонки отката, чинит запасной вариант
      var back = parseDate_(c.prevPay) || parseDate_(c.paidAt);
      if (!back) throw new Error('Не сохранена предыдущая дата оплаты — поправьте дату в таблице руками');
      var checks = c.checks || {};
      checks.paid = false;
      writeRow_(t, rowNumber, {
        pay: 'due', paidAt: '', prevPay: '', nextPay: back, checks: checks
      });
      SpreadsheetApp.flush();
      return { client: refetchClient_(id), nextPay: dateOut_(back) };
    });
  },

  /** action=new — создать клиента вручную (или из брифа). */
  'new': function (req) {
    var src = req.client || req;
    var business = str_(src.business);
    var name = str_(src.name);
    if (!business && !name) throw new Error('Нужны название бизнеса и ФИО');
    return withLock_(function () {
      var created = createClient_(src);
      if (req.briefRow) markBrief_(num_(req.briefRow), created.id, 'создан клиент');
      return { client: created };
    });
  },

  /** action=brief_dismiss — отложить бриф, чтобы он не висел в списке. */
  brief_dismiss: function (req) {
    var row = num_(req.row);
    if (!row) throw new Error('Не передан номер строки брифа');
    markBrief_(row, '', 'отложен');
    return { row: row };
  },

  /** action=push — коммит конфига в GitHub и запуск тестового прогона. */
  push: function (req) {
    if (req.client) POST_ACTIONS.save({ client: req.client });
    var id = str_(req.id || (req.client && req.client.id));
    if (!id) throw new Error('Не передан client_id');
    var c = refetchClient_(id);
    var res = ghPushConfig_(c, req.dispatch !== false);
    withLock_(function () {
      var t = clientsTable_();
      var rowNumber = findRow_(t, id);
      var checks = c.checks || {};
      checks.config = true;
      if (res.dispatched) checks.test = true;
      writeRow_(t, rowNumber, {
        configPushed: res.sha + ' · ' + res.at,
        checks: checks
      });
      SpreadsheetApp.flush();
      return true;
    });
    res.client = refetchClient_(id);
    return res;
  },

  /** action=ping — то же, что GET ping (кнопка «Проверить подключения»). */
  ping: function () {
    return { checks: pingAll_() };
  },

  analyze_style: function (req) { return aiAnalyzeStyle_(req); },
  build_plan:    function (req) { return aiBuildPlan_(req); },
  gen_examples:  function (req) { return aiGenExamples_(req); },
  apply_edits:   function (req) { return aiApplyEdits_(req); }
};

/** Оставляет только известные поля — лишнее из браузера в таблицу не попадёт. */
function cleanPatch_(src) {
  var patch = {};
  Object.keys(src || {}).forEach(function (k) {
    if (k === 'id' || k === 'row') return;
    if (TO_CELL[k] && src[k] !== undefined && src[k] !== null) patch[k] = src[k];
  });
  return patch;
}

function withLock_(fn) {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) throw new Error('Таблица занята другим запросом, повторите');
  try {
    return fn();
  } finally {
    lock.releaseLock();
  }
}

/** Создаёт строку в листе «Клиенты» и возвращает готовый объект клиента. */
function createClient_(src) {
  var t = clientsTable_();
  var taken = t.rows.map(function (r) { return str_(t.at(r, F.id)); }).filter(String);
  var id = str_(src.id) && taken.indexOf(str_(src.id)) < 0
    ? str_(src.id)
    : uniqueId_(translit_(src.business || src.name) || 'client', taken);

  var data = {
    id: id,
    name: str_(src.name),
    phone: str_(src.phone),
    email: str_(src.email),
    tg: str_(src.tg),
    tariff: tariffIn_(src.tariff),
    business: str_(src.business) || str_(src.name),
    about: str_(src.about),
    audience: str_(src.audience),
    city: str_(src.city),
    networks: src.networks || NETWORKS_KNOWN,
    links: str_(src.links),
    tone: str_(src.tone) || 'Простой и дружелюбный — как друг',
    slots: src.slots || [],
    hasPhoto: src.hasPhoto === undefined ? false : src.hasPhoto,
    rubrics: src.rubrics || [],
    holidays: str_(src.holidays) || 'Да, но без упоминания скидок',
    faq: str_(src.faq),
    limits: src.limits || [],
    limitsText: str_(src.limitsText),
    cta: str_(src.cta),
    source: str_(src.source) || 'Вручную',
    startedAt: src.startedAt ? parseDate_(src.startedAt) : today_(),
    nextPay: src.nextPay ? parseDate_(src.nextPay) : '',
    paidAt: src.paidAt ? parseDate_(src.paidAt) : '',
    pay: src.pay ? payIn_(src.pay) : 'brief',
    active: src.active === undefined ? true : bool_(src.active),
    stylePrompt: str_(src.stylePrompt),
    configPushed: '',
    niche: str_(src.niche) || 'не указана',
    topics: src.topics || [],
    styleAnswers: padAnswers_(src.styleAnswers || []),
    checks: src.checks || {},
    iterations: num_(src.iterations),
    photoQueue: num_(src.photoQueue),
    lastPostDate: '—',
    lastPostStatus: 'Не запущен'
  };

  var rowNumber = t.sheet.getLastRow() + 1;
  if (rowNumber > t.sheet.getMaxRows()) t.sheet.insertRowsAfter(t.sheet.getMaxRows(), 1);
  writeRow_(t, rowNumber, data);
  SpreadsheetApp.flush();
  return refetchClient_(id);
}

/** Помечает строку брифа: создан клиент или отложен. */
function markBrief_(rowNumber, clientId, state) {
  var sh = sheet_(SHEET_BRIEFS, ['Отметка времени']);
  ensureHeaders_(sh, [BRIEF_CLIENT_COL, BRIEF_STATE_COL]);
  SpreadsheetApp.flush();
  var t = table_(SHEET_BRIEFS);
  var ci = t.idx[norm_(BRIEF_CLIENT_COL)];
  var si = t.idx[norm_(BRIEF_STATE_COL)];
  if (clientId && ci !== undefined) sh.getRange(rowNumber, ci + 1).setValue(clientId);
  if (si !== undefined) sh.getRange(rowNumber, si + 1).setValue(state || '');
}

/* ========================================================================
 *  GITHUB CONTENTS API
 * ==================================================================== */

/** Конфиг клиента — ровно то, что показывает интерфейс в предпросмотре. */
function buildConfig_(c) {
  return {
    client_id: c.id,
    business: c.business,
    city: c.city,
    tariff: c.tariff,
    networks: (c.networks || []).map(function (n) { return String(n).toLowerCase(); }),
    slots: (c.slots || []).map(function (k) {
      var sl = slotDef_(k);
      return { name: k, cron: sl ? sl.cron : '' };
    }),
    style_prompt: c.stylePrompt || null,
    tone: c.tone,
    holidays: c.holidays,
    forbidden: (c.limits || []).concat(c.limitsText ? [c.limitsText] : []),
    cta: c.cta,
    faq: c.faq ? c.faq.split('\n') : [],
    rubrics: (c.rubrics || []).map(function (r) {
      return {
        name: r.name,
        days: String(r.days || '').split(',').map(function (d) { return d.trim(); }).filter(String),
        prompt: r.prompt
      };
    }),
    yandex_folder: 'clients/' + c.id,
    iterations: c.iterations || 0
  };
}

function gh_(url, method, payload) {
  var token = prop_('GITHUB_TOKEN', true);
  var opts = {
    method: method,
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    muteHttpExceptions: true
  };
  if (payload) {
    opts.contentType = 'application/json';
    opts.payload = JSON.stringify(payload);
  }
  var res = UrlFetchApp.fetch(url, opts);
  var code = res.getResponseCode();
  var text = res.getContentText();
  var body = null;
  try { body = text ? JSON.parse(text) : null; } catch (e) { body = null; }
  return { code: code, json: body, text: text };
}

function ghRepo_() {
  var repo = prop_('GITHUB_REPO', true);
  if (!/^[^\/\s]+\/[^\/\s]+$/.test(repo)) {
    throw new Error('GITHUB_REPO должен быть в виде owner/repo, сейчас: ' + repo);
  }
  return repo;
}

/**
 * GET sha текущего файла -> PUT clients/{id}.json с base64-содержимым.
 * После коммита — workflow_dispatch для тестового прогона.
 */
function ghPushConfig_(c, dispatch) {
  var repo = ghRepo_();
  var branch = prop_('GITHUB_BRANCH') || 'main';
  var dir = prop_('CONFIG_DIR') || 'clients';
  var path = dir + '/' + c.id + '.json';
  var base = 'https://api.github.com/repos/' + repo + '/contents/' +
    path.split('/').map(encodeURIComponent).join('/');
  var config = JSON.stringify(buildConfig_(c), null, 2) + '\n';

  var head = gh_(base + '?ref=' + encodeURIComponent(branch), 'get');
  var prevSha = '';
  if (head.code === 200 && head.json && head.json.sha) prevSha = head.json.sha;
  else if (head.code !== 404) throw new Error(ghError_('GET ' + path, head));

  var payload = {
    message: 'crm: конфиг клиента ' + c.id,
    content: Utilities.base64Encode(config, Utilities.Charset.UTF_8),
    branch: branch
  };
  if (prevSha) payload.sha = prevSha;

  var put = gh_(base, 'put', payload);
  if (put.code !== 200 && put.code !== 201) throw new Error(ghError_('PUT ' + path, put));

  var commit = (put.json && put.json.commit && put.json.commit.sha) || '';
  var result = {
    path: path,
    repo: repo,
    branch: branch,
    created: put.code === 201,
    sha: commit.slice(0, 7),
    commitSha: commit,
    prevSha: prevSha ? prevSha.slice(0, 7) : '—',
    at: Utilities.formatDate(new Date(), tz_(), 'dd.MM · HH:mm'),
    bytes: config.length,
    dispatched: false,
    workflow: ''
  };

  if (dispatch) {
    var wf = ghDispatch_(repo, branch, c.id);
    result.dispatched = wf.ok;
    result.workflow = wf.workflow;
    result.workflowNote = wf.note;
  }
  return result;
}

/** Тестовый прогон: workflow_dispatch по имени файла воркфлоу. */
function ghDispatch_(repo, branch, clientId) {
  var wf = prop_('GITHUB_WORKFLOW') || 'post.yml';
  var url = 'https://api.github.com/repos/' + repo + '/actions/workflows/' +
    encodeURIComponent(wf) + '/dispatches';

  var res = gh_(url, 'post', { ref: branch, inputs: { client_id: clientId } });
  if (res.code === 422) {
    // воркфлоу не объявляет input client_id — запускаем без входных данных
    res = gh_(url, 'post', { ref: branch });
  }
  if (res.code === 204) return { ok: true, workflow: wf, note: 'запущен' };
  if (res.code === 404) {
    return { ok: false, workflow: wf, note: 'воркфлоу ' + wf + ' не найден в ветке ' + branch };
  }
  return { ok: false, workflow: wf, note: ghError_('dispatch ' + wf, res) };
}

function ghError_(what, res) {
  var msg = (res.json && (res.json.message || res.json.error)) || String(res.text || '').slice(0, 200);
  return 'GitHub ' + what + ': HTTP ' + res.code + (msg ? ' — ' + msg : '');
}

/* ========================================================================
 *  ROUTERAI (google/gemini-3.1-pro-preview)
 * ==================================================================== */

var STYLE_TASKS = [
  'Напишите пост про то, как вы провели вчерашний вечер',
  'Напишите пост про что-то, что вас недавно удивило или порадовало',
  'Напишите пост про вашу любимую еду или кафе',
  'Напишите пост про то, что вас раздражает в людях',
  'Напишите пост про что-то, чему вы недавно научились или что попробовали впервые'
];

/**
 * Один запрос к RouterAI. Ключ берётся из Script Properties и в браузер
 * не уходит: интерфейс дёргает Web App, а Web App — RouterAI.
 */
function ai_(messages, opts) {
  opts = opts || {};
  var key = prop_('ROUTERAI_KEY', true);
  var model = prop_('ROUTERAI_MODEL') || ROUTERAI_MODEL_DEFAULT;
  var payload = {
    model: model,
    messages: messages,
    temperature: opts.temperature === undefined ? 0.7 : opts.temperature
  };
  if (opts.maxTokens) payload.max_tokens = opts.maxTokens;
  if (opts.json) payload.response_format = { type: 'json_object' };

  var last = '';
  for (var attempt = 0; attempt < 3; attempt++) {
    if (attempt) Utilities.sleep(attempt * 3000);
    var res = UrlFetchApp.fetch(ROUTERAI_URL, {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: 'Bearer ' + key },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
    var code = res.getResponseCode();
    var text = res.getContentText();
    if (code === 429 || code >= 500) { last = 'RouterAI: HTTP ' + code; continue; }
    if (code !== 200) {
      var detail = '';
      try {
        var errJson = JSON.parse(text);
        detail = (errJson.error && (errJson.error.message || errJson.error)) || errJson.message || '';
      } catch (e) { detail = String(text).slice(0, 200); }
      throw new Error('RouterAI: HTTP ' + code + (detail ? ' — ' + detail : ''));
    }
    var body = JSON.parse(text);
    var content = body && body.choices && body.choices[0] && body.choices[0].message &&
      body.choices[0].message.content;
    if (!content) throw new Error('RouterAI вернул пустой ответ');
    return String(content).trim();
  }
  throw new Error(last || 'RouterAI не отвечает');
}

/** Достаёт JSON из ответа модели, даже если он обёрнут в ```json. */
function parseJsonLoose_(text) {
  var s = String(text || '').trim();
  s = s.replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/, '').trim();
  try { return JSON.parse(s); } catch (e) {}
  var start = s.search(/[\[{]/);
  if (start >= 0) {
    var open = s.charAt(start);
    var close = open === '[' ? ']' : '}';
    var end = s.lastIndexOf(close);
    if (end > start) {
      try { return JSON.parse(s.slice(start, end + 1)); } catch (e2) {}
    }
  }
  throw new Error('Модель вернула не JSON: ' + s.slice(0, 200));
}

/** Краткая выжимка брифа — общий контекст для всех промптов. */
function briefContext_(c) {
  var lines = [
    'Бизнес: ' + c.business,
    'Ниша: ' + (c.niche || 'не указана'),
    'Город: ' + c.city,
    'О бизнесе: ' + c.about,
    'Аудитория: ' + c.audience,
    'Тон: ' + c.tone,
    'Праздники: ' + c.holidays,
    'Соцсети: ' + (c.networks || []).join(', '),
    'Темы, выбранные клиентом: ' + ((c.topics || []).join(', ') || 'не выбраны'),
    'Слоты публикаций: ' + ((c.slots || []).map(function (k) {
      var sl = slotDef_(k);
      return sl ? sl.label + ' (' + sl.cron + ' UTC)' : k;
    }).join(', ') || 'не выбраны'),
    'FAQ: ' + (c.faq || 'нет'),
    'Призыв к действию: ' + (c.cta || 'нет'),
    'Фото работ: ' + (c.hasPhoto || c.photoQueue ? 'есть' : 'нет')
  ];
  var forbidden = (c.limits || []).concat(c.limitsText ? [c.limitsText] : []);
  lines.push('Запрещено упоминать: ' + (forbidden.join('; ') || 'ограничений нет'));
  if (c.stylePrompt) lines.push('Стиль клиента (style_prompt): ' + c.stylePrompt);
  return lines.join('\n');
}

/** Сохраняет присланную карточку, чтобы модель работала с актуальным брифом. */
function aiClient_(req) {
  var id = str_(req.id || (req.client && req.client.id));
  if (!id) throw new Error('Не передан client_id');
  if (req.client) POST_ACTIONS.save({ client: req.client });
  return refetchClient_(id);
}

/** analyzeStyle: 5 текстов клиента -> style_prompt. */
function aiAnalyzeStyle_(req) {
  var c = aiClient_(req);
  var answers = padAnswers_(req.answers || c.styleAnswers);
  var filled = answers.filter(function (a) { return str_(a); });
  if (filled.length < 5) throw new Error('Нужны все пять ответов клиента, заполнено ' + filled.length);

  var texts = answers.map(function (a, i) {
    return '### Задание ' + (i + 1) + ': ' + STYLE_TASKS[i] + '\n' + a;
  }).join('\n\n');

  var content = ai_([
    {
      role: 'system',
      content: 'Ты редактор, который описывает манеру письма человека так, ' +
        'чтобы другая нейросеть могла её воспроизвести. Отвечай только JSON.'
    },
    {
      role: 'user',
      content: 'Вот пять текстов одного человека, написанных им самим.\n\n' + texts +
        '\n\nОпиши его манеру письма: средняя длина предложения, обращение (ты/вы), ' +
        'структура текста, лексика, эмодзи, пунктуация, характерные обороты. ' +
        'Пиши инструкцией для нейросети, 3–6 предложений, без оценок и комплиментов, ' +
        'без упоминания конкретных фактов из текстов.\n\n' +
        'Верни JSON: {"style_prompt": "…"}'
    }
  ], { json: true, temperature: 0.4, maxTokens: 800 });

  var stylePrompt = str_(parseJsonLoose_(content).style_prompt);
  if (!stylePrompt) throw new Error('Модель не вернула style_prompt');

  withLock_(function () {
    var t = clientsTable_();
    writeRow_(t, findRow_(t, c.id), { stylePrompt: stylePrompt, styleAnswers: answers });
    SpreadsheetApp.flush();
    return true;
  });
  return { stylePrompt: stylePrompt, client: refetchClient_(c.id) };
}

/** buildPlan: данные брифа -> рубрики с промптами. */
function aiBuildPlan_(req) {
  var c = aiClient_(req);
  var perDay = (c.slots || []).length || 1;

  var content = ai_([
    {
      role: 'system',
      content: 'Ты SMM-стратег. Собираешь контент-план для малого бизнеса ' +
        'и пишешь промпты для нейросети, которая будет генерировать посты. ' +
        'Отвечай только JSON.'
    },
    {
      role: 'user',
      content: briefContext_(c) +
        '\n\nСобери контент-план на неделю: 4–6 рубрик. Для каждой рубрики:\n' +
        '- name — название рубрики по-русски;\n' +
        '- days — дни недели через запятую (пн, вт, ср, чт, пт, сб, вс), ' +
        'всего ' + perDay + ' публикаци(и/й) в день, воскресенье можно оставить пустым;\n' +
        '- prompt — инструкция для нейросети: о чём пост, какая структура, ' +
        'что обязательно упомянуть и чего нельзя касаться (учти запреты выше).\n' +
        'Рубрики про фото работ добавляй, только если у клиента есть фото. ' +
        'Промпт пиши на русском, 1–3 предложения, без цен, сроков и гарантий, ' +
        'если они в запретах.\n\n' +
        'Верни JSON: {"rubrics": [{"name": "…", "days": "пн, чт", "prompt": "…"}]}'
    }
  ], { json: true, temperature: 0.6, maxTokens: 2000 });

  var rubrics = normRubrics_(parseJsonLoose_(content).rubrics || []);
  if (!rubrics.length) throw new Error('Модель не вернула ни одной рубрики');

  saveRubrics_(c.id, rubrics);
  return { rubrics: rubrics, client: refetchClient_(c.id) };
}

/** genExamples: для каждой рубрики — пример поста с учётом style_prompt. */
function aiGenExamples_(req) {
  var c = aiClient_(req);
  var rubrics = normRubrics_(req.rubrics || c.rubrics);
  if (!rubrics.length) throw new Error('Сначала соберите контент-план');
  if (rubrics.length > 8) throw new Error('Слишком много рубрик за один раз (максимум 8)');

  var ctx = briefContext_(c);
  var out = rubrics.map(function (r) {
    if (r.example && !req.force) return r;
    var content = ai_([
      {
        role: 'system',
        content: 'Ты пишешь посты для соцсетей малого бизнеса от лица владельца. ' +
          'Пишешь только текст поста, без заголовков «Пост:» и без пояснений.'
      },
      {
        role: 'user',
        content: ctx +
          '\n\nРубрика: ' + r.name +
          '\nИнструкция рубрики: ' + r.prompt +
          '\n\nНапиши один пример поста по этой рубрике: 60–120 слов, ' +
          (c.stylePrompt
            ? 'строго в стиле клиента, описанном выше в style_prompt. '
            : 'простым разговорным языком. ') +
          'В конце — призыв к действию клиента, если он задан. ' +
          'Не выдумывай цены, сроки и гарантии.'
      }
    ], { temperature: 0.8, maxTokens: 700 });
    return {
      name: r.name, days: r.days, prompt: r.prompt,
      example: String(content).replace(/^```[\s\S]*?\n|```$/g, '').trim()
    };
  });

  saveRubrics_(c.id, out);
  return { rubrics: out, client: refetchClient_(c.id) };
}

/** applyEdits: правки клиента -> обновлённые промпты и примеры рубрик. */
function aiApplyEdits_(req) {
  var c = aiClient_(req);
  var edits = str_(req.edits || c.edits);
  if (!edits) throw new Error('Не переданы правки клиента');
  var rubrics = normRubrics_(req.rubrics || c.rubrics);
  if (!rubrics.length) throw new Error('Сначала соберите контент-план');

  var content = ai_([
    {
      role: 'system',
      content: 'Ты редактор контент-плана. Переписываешь промпты рубрик так, ' +
        'чтобы учесть правки клиента, и обновляешь примеры постов. Отвечай только JSON.'
    },
    {
      role: 'user',
      content: briefContext_(c) +
        '\n\nТекущие рубрики:\n' + JSON.stringify(rubrics.map(function (r) {
          return { name: r.name, days: r.days, prompt: r.prompt, example: r.example };
        }), null, 2) +
        '\n\nПравки клиента (его словами):\n' + edits +
        '\n\nОбнови промпты рубрик так, чтобы правки были учтены навсегда, ' +
        'и перепиши примеры постов по обновлённым промптам. ' +
        'Названия и дни оставь прежними, если клиент не просил их менять. ' +
        'Рубрику можно удалить или добавить, если клиент прямо об этом просит.\n\n' +
        'Верни JSON: {"rubrics": [{"name": "…", "days": "…", "prompt": "…", "example": "…"}]}'
    }
  ], { json: true, temperature: 0.6, maxTokens: 3000 });

  var updated = normRubrics_(parseJsonLoose_(content).rubrics || []);
  if (!updated.length) throw new Error('Модель не вернула обновлённые рубрики');

  var iterations = num_(c.iterations) + 1;
  withLock_(function () {
    var t = clientsTable_();
    writeRow_(t, findRow_(t, c.id), { rubrics: updated, iterations: iterations });
    SpreadsheetApp.flush();
    return true;
  });
  return { rubrics: updated, iterations: iterations, client: refetchClient_(c.id) };
}

function saveRubrics_(id, rubrics) {
  withLock_(function () {
    var t = clientsTable_();
    writeRow_(t, findRow_(t, id), { rubrics: rubrics });
    SpreadsheetApp.flush();
    return true;
  });
}

/* ========================================================================
 *  СВОЙСТВА И ПРОВЕРКА ПОДКЛЮЧЕНИЙ
 * ==================================================================== */

var PROP_KEYS = [
  'API_TOKEN', 'SPREADSHEET_ID', 'GITHUB_TOKEN', 'GITHUB_REPO', 'GITHUB_BRANCH',
  'GITHUB_WORKFLOW', 'CONFIG_DIR', 'ROUTERAI_KEY', 'ROUTERAI_MODEL',
  'TG_BOT_TOKEN', 'TG_CHAT_ID'
];

/** Какие свойства заданы. Значения наружу не отдаются — только да/нет. */
function propsState_() {
  var state = {};
  PROP_KEYS.forEach(function (k) { state[k] = !!prop_(k); });
  return state;
}

function pingAll_() {
  return {
    sheet: check_(function () {
      var t = clientsTable_();
      var n = t.rows.filter(function (r) { return str_(t.at(r, F.id)); }).length;
      return 'лист «' + SHEET_CLIENTS + '»: ' + n + ' клиент(ов)';
    }),
    github: check_(function () {
      var repo = ghRepo_();
      var res = gh_('https://api.github.com/repos/' + repo, 'get');
      if (res.code !== 200) throw new Error(ghError_(repo, res));
      var wf = prop_('GITHUB_WORKFLOW') || 'post.yml';
      var w = gh_('https://api.github.com/repos/' + repo + '/actions/workflows/' +
        encodeURIComponent(wf), 'get');
      return repo + ': доступ есть, воркфлоу ' + wf +
        (w.code === 200 ? ' найден' : ' не найден (HTTP ' + w.code + ')');
    }),
    routerai: check_(function () {
      var key = prop_('ROUTERAI_KEY', true);
      var res = UrlFetchApp.fetch('https://routerai.ru/api/v1/models', {
        method: 'get',
        headers: { Authorization: 'Bearer ' + key },
        muteHttpExceptions: true
      });
      if (res.getResponseCode() !== 200) {
        ai_([{ role: 'user', content: 'ok' }], { maxTokens: 1, temperature: 0 });
      }
      return 'ключ принят, модель ' + (prop_('ROUTERAI_MODEL') || ROUTERAI_MODEL_DEFAULT);
    }),
    telegram: check_(function () {
      var token = prop_('TG_BOT_TOKEN', true);
      var res = UrlFetchApp.fetch('https://api.telegram.org/bot' + token + '/getMe',
        { muteHttpExceptions: true });
      var body = JSON.parse(res.getContentText());
      if (!body.ok) throw new Error(body.description || 'getMe вернул ошибку');
      var chat = prop_('TG_CHAT_ID');
      return '@' + body.result.username + (chat ? ', чат задан' : ', TG_CHAT_ID не задан');
    })
  };
}

function check_(fn) {
  try {
    return { ok: true, detail: String(fn()) };
  } catch (err) {
    return { ok: false, detail: String((err && err.message) || err) };
  }
}

/* ========================================================================
 *  TELEGRAM
 * ==================================================================== */

/** Уведомления владельцу сервиса. Токен и чат — только из Script Properties. */
function tgNotify_(text) {
  var token = prop_('TG_BOT_TOKEN');
  var chats = list_(prop_('TG_CHAT_ID'));
  if (!token || !chats.length) {
    console.warn('Telegram не настроен (TG_BOT_TOKEN / TG_CHAT_ID) — уведомление пропущено');
    return false;
  }
  var sent = 0;
  chats.forEach(function (chat) {
    try {
      var res = UrlFetchApp.fetch('https://api.telegram.org/bot' + token + '/sendMessage', {
        method: 'post',
        contentType: 'application/json',
        payload: JSON.stringify({
          chat_id: chat, text: text, disable_web_page_preview: true
        }),
        muteHttpExceptions: true
      });
      if (res.getResponseCode() === 200) sent++;
      else console.error('Telegram ' + chat + ': ' + res.getContentText().slice(0, 200));
    } catch (err) {
      console.error('Telegram ' + chat + ': ' + err);
    }
  });
  return sent > 0;
}

/* ========================================================================
 *  ТРИГГЕР ФОРМЫ
 * ==================================================================== */

/**
 * Ответ на Google Форму -> строка в листе «Клиенты» + уведомление в Telegram.
 * Ответы читаются по заголовку вопроса, а не по номеру колонки.
 */
function onFormSubmit(e) {
  var raw = {};   // исходный заголовок -> ответ
  if (e && e.namedValues) {
    for (var q in e.namedValues) {
      var v = e.namedValues[q];
      raw[q] = str_(Array.isArray(v) ? v.filter(String).join(', ') : v);
    }
  } else if (e && e.values && e.range) {
    var sh = e.range.getSheet();
    var head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0];
    head.forEach(function (title, i) { raw[String(title)] = str_(e.values[i]); });
  } else {
    throw new Error('onFormSubmit вызван без данных формы');
  }

  var answers = {};
  for (var title in raw) if (raw[title]) answers[norm_(title)] = raw[title];

  var brief = briefFromAnswers_(answers);
  brief.tariff = detectTariff_(raw, answers);

  var created = withLock_(function () { return createClient_(brief); });

  if (e && e.range) {
    try { markBrief_(e.range.getRow(), created.id, 'создан клиент'); } catch (err) {
      console.warn('Не удалось отметить строку брифа: ' + err);
    }
  }

  tgNotify_(
    'Новый бриф: ' + (created.name || '—') + ', ' + created.tariff + ', ' + (created.niche || 'ниша не указана') +
    '\n' + created.business + (created.city ? ' · ' + created.city : '') +
    (created.tg ? '\n' + created.tg : '') + (created.phone ? ' · ' + created.phone : '') +
    '\nclient_id: ' + created.id
  );

  return created;
}

/** Ответы формы -> поля клиента. */
function briefFromAnswers_(answers) {
  var pick = function (field) { return findAnswer_(answers, FORM_Q[field] || []); };
  var networks = pick('networks');
  var limits = pick('limits');
  return {
    name: pick('name'),
    phone: pick('phone'),
    email: pick('email'),
    tg: pick('tg'),
    business: pick('business') || pick('name'),
    niche: pick('niche'),
    city: pick('city'),
    about: pick('about'),
    audience: pick('audience'),
    networks: networks ? pickList_(networks, NETWORKS_KNOWN) : NETWORKS_KNOWN,
    links: pick('links'),
    tone: pick('tone'),
    slots: slotsIn_(pick('slots')),
    hasPhoto: bool_(pick('hasPhoto')) || /\d/.test(pick('hasPhoto')),
    holidays: pick('holidays'),
    faq: pick('faq'),
    limits: limits ? pickList_(limits, LIMITS_KNOWN) : [],
    limitsText: pick('limitsText'),
    cta: pick('cta'),
    source: pick('source') || 'Google Форма',
    tariff: tariffIn_(pick('tariff')),
    pay: 'brief'
  };
}

/** Ищет ответ по вхождению варианта в заголовок вопроса. */
function findAnswer_(answers, variants) {
  for (var i = 0; i < variants.length; i++) {
    var needle = norm_(variants[i]);
    for (var title in answers) {
      if (title.indexOf(needle) >= 0) return answers[title];
    }
  }
  return '';
}

/**
 * Тариф по заполненному разделу формы: ищем вопросы, в заголовке которых
 * название тарифа стоит ЗАГЛАВНЫМИ («БИЗНЕС — 2 поста в день»), и берём
 * самый старший тариф, в разделе которого есть ответы. Если в форме есть
 * прямой вопрос про тариф — он важнее.
 */
function detectTariff_(raw, answers) {
  var direct = findAnswer_(answers, FORM_Q.tariff);
  if (direct) {
    for (var i = TARIFFS.length - 1; i >= 0; i--) {
      if (norm_(direct).indexOf(norm_(TARIFFS[i])) >= 0) return TARIFFS[i];
    }
  }

  var markers = jsonCell_(prop_('TARIFF_MARKERS'), TARIFF_MARKERS);
  for (var j = TARIFFS.length - 1; j >= 0; j--) {
    var tariff = TARIFFS[j];
    var variants = markers[tariff] || [];
    for (var k = 0; k < variants.length; k++) {
      var marker = variants[k];
      var re = new RegExp('(^|[^A-ZА-ЯЁ])' + marker + '([^A-ZА-ЯЁ]|$)');
      for (var title in raw) {
        if (str_(raw[title]) && re.test(String(title))) return tariff;
      }
    }
  }
  return DEFAULT_TARIFF;
}

/* ========================================================================
 *  ТРИГГЕРЫ И НАПОМИНАНИЯ
 * ==================================================================== */

/** Ставит триггеры: ответ формы и ежедневная проверка оплат в 10:00. */
function installTriggers() {
  var book = ss_();
  var existing = ScriptApp.getProjectTriggers();
  existing.forEach(function (t) {
    if (['onFormSubmit', 'dailyPaymentReminders'].indexOf(t.getHandlerFunction()) >= 0) {
      ScriptApp.deleteTrigger(t);
    }
  });
  ScriptApp.newTrigger('onFormSubmit').forSpreadsheet(book).onFormSubmit().create();
  ScriptApp.newTrigger('dailyPaymentReminders').timeBased().atHour(10).everyDays(1).create();
  return 'Триггеры установлены: onFormSubmit, dailyPaymentReminders (10:00)';
}

/**
 * Ежедневная проверка оплат: напоминания за 3 дня, за 1 день, в день
 * оплаты и каждый день просрочки. Просроченным проставляется статус.
 */
function dailyPaymentReminders() {
  var t = clientsTable_();
  var now = today_();
  var soon = [], late = [];

  for (var i = 0; i < t.rows.length; i++) {
    var row = t.rows[i];
    if (!str_(t.at(row, F.id))) continue;
    var c = rowToClient_(t, row, i + 2);
    if (!c.active || !c.nextPay || c.pay === 'test') continue;
    var due = parseDate_(c.nextPay);
    var days = Math.round((due - now) / 86400000);
    var line = c.business + ' · ' + c.tariff + ' · ' + c.tg;
    if (days < 0) {
      late.push(line + ' — просрочка ' + Math.abs(days) + ' дн.');
      if (c.pay !== 'overdue') writeRow_(t, i + 2, { pay: 'overdue' });
    } else if (c.pay !== 'paid' || days <= 3) {
      if (days === 0) soon.push(line + ' — сегодня');
      else if (days === 1) soon.push(line + ' — завтра');
      else if (days === 3) soon.push(line + ' — через 3 дня');
    }
  }

  if (!soon.length && !late.length) return 'Напоминать не о чем';
  var text = 'Оплаты на ' + Utilities.formatDate(now, tz_(), 'dd.MM') + ':';
  if (late.length) text += '\n\nПросрочка:\n' + late.join('\n');
  if (soon.length) text += '\n\nСкоро:\n' + soon.join('\n');
  tgNotify_(text);
  return text;
}
