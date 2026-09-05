const { source, googleStubs } = require('./gs');
// Токены берутся из Script Properties, в файле их нет — тестовые значения
// задаются ниже через PROPS, до new Function: const TELEGRAM_BOT_TOKEN
// вычисляется один раз при загрузке скрипта.
const code = source();

/* ---- мок Google Таблицы ---- */
class Sheet {
  constructor(name, head) { this.name = name; this.data = [head.slice()]; this.frozen = 0; }
  getName() { return this.name; }
  getSheetId() { return this.gid || (this.gid = Math.floor(Math.random() * 1e6)); }
  setNumberFormat() { return this; }
  getLastRow() { let n = 0; this.data.forEach((r, i) => { if (r.some(v => v !== '' && v !== null && v !== undefined)) n = i + 1; }); return n; }
  getLastColumn() { return Math.max(...this.data.map(r => r.length)); }
  getMaxRows() { return this.data.length; }
  setFrozenRows(n) { this.frozen = n; return this; }
  insertRowsAfter(row, n) { for (let i = 0; i < n; i++) this.data.splice(row, 0, []); return this; }
  getDataRange() { return this.getRange(1, 1, this.getLastRow() || 1, this.getLastColumn()); }
  getRange(r, c, nr, nc) {
    nr = nr === undefined ? 1 : nr; nc = nc === undefined ? 1 : nc;
    const sh = this;
    return {
      getValues() {
        const out = [];
        for (let i = 0; i < nr; i++) {
          const row = sh.data[r - 1 + i] || (sh.data[r - 1 + i] = []);
          const line = [];
          for (let j = 0; j < nc; j++) { const v = row[c - 1 + j]; line.push(v === undefined ? '' : v); }
          out.push(line);
        }
        return out;
      },
      setValues(vals) {
        vals.forEach((line, i) => {
          const idx = r - 1 + i;
          if (!sh.data[idx]) sh.data[idx] = [];
          line.forEach((v, j) => { sh.data[idx][c - 1 + j] = v; });
        });
        return this;
      },
      setValue(v) { return this.setValues([[v]]); },
      getValue() { return this.getValues()[0][0]; },
      getRow() { return r; },
      getColumn() { return c; },
      getSheet() { return sh; },
      setFontWeight() { return this; },
      setNumberFormat() { return this; },
      setBackground() { return this; },
      setNote(n) { sh.notes = sh.notes || {}; sh.notes[r + ':' + c] = n; return this; },
      clearNote() { if (sh.notes) delete sh.notes[r + ':' + c]; return this; },
      setDataValidation() { return this; },
      clearContent() {
        for (let i = 0; i < nr; i++) {
          for (let j = 0; j < nc; j++) {
            if (sh.data[r - 1 + i]) sh.data[r - 1 + i][c - 1 + j] = '';
          }
        }
        return this;
      }
    };
  }
}
class Book {
  constructor() { this.sheets = {}; }
  getSheetByName(n) { return this.sheets[n] || null; }
  insertSheet(n) { return (this.sheets[n] = new Sheet(n, [])); }
  getUrl() { return 'https://docs.google.com/spreadsheets/d/TESTID/edit'; }
}
const book = new Book();
const SpreadsheetApp = { getActive: () => book, getActiveSpreadsheet: () => book, flush: () => {} };
const LockService = { getScriptLock: () => ({ tryLock: () => true, releaseLock: () => {} }) };
const Utilities = {
  formatDate: (dt, tz, f) => { const p = n => String(n).padStart(2, '0');
    return f.replace('yyyy', dt.getFullYear()).replace('MM', p(dt.getMonth() + 1)).replace('dd', p(dt.getDate())).replace('HH', p(dt.getHours())).replace('mm', p(dt.getMinutes())); },
  base64Encode: s => Buffer.from(s, 'utf8').toString('base64'), Charset: { UTF_8: 'utf8' }, sleep: () => {}
};
const Session = { getScriptTimeZone: () => 'Europe/Moscow' };
const PROPS = {};
// имена свойств должны совпадать с Code.gs, и задать их надо до new Function:
// const TELEGRAM_BOT_TOKEN = ... вычисляется один раз при загрузке скрипта
PROPS.TELEGRAM_BOT_TOKEN = '123:AA';
PROPS.TELEGRAM_CHAT_ID = '555';
// setProperty обязателен: nextClientNumber_ ведёт счётчик CLIENT_NUMBER_SEQ,
// и без него весь набор падал на «props.setProperty is not a function»
const { PropertiesService, CacheService } = googleStubs(PROPS);
const sent = [];
const UrlFetchApp = { fetch: (url, o) => { sent.push({ url, o }); return { getResponseCode: () => 200, getContentText: () => JSON.stringify({ ok: true, result: { username: 'bot' } }) }; } };
const ScriptApp = { getProjectTriggers: () => [] };
const Logger = { log: () => {} };
const ContentService = { MimeType: { JSON: 'json' }, createTextOutput: t => ({ setMimeType: () => ({ body: t }) }) };

const api = new Function('SpreadsheetApp', 'LockService', 'Utilities', 'Session', 'PropertiesService', 'CacheService', 'UrlFetchApp', 'ScriptApp', 'ContentService', 'Logger', 'console',
  code + '\nreturn {setupSheets,GET_ACTIONS,POST_ACTIONS,doGet,doPost,onFormSubmit,HEAD_CLIENTS,HEAD_CLIENTS_EXTRA,clientsTable_,F,route_,onEdit,checkPayments,missingClientColumns_,PAY_OPTIONS,ST_CANCEL,ST_OVERDUE};')
  (SpreadsheetApp, LockService, Utilities, Session, PropertiesService, CacheService, UrlFetchApp, ScriptApp, ContentService, Logger, console);

let fails = 0;
const ok = (n, c, info) => { if (c) console.log('ok  ', n); else { console.log('FAIL', n, info === undefined ? '' : JSON.stringify(info)); fails++; } };

/* ---- 1. CRM садится на рабочий лист учёта оплат ---- */
// как в продакшне: A–G, оплаты ведёт дропдаун в F
const prod = new Sheet('Клиенты', ['№', 'Клиент', 'Тариф', 'Цена', 'Дата оплаты', 'Статус', 'Контакт']);
prod.data.push([1, 'Игорь Демин', 'БИЗНЕС', 6900, new Date(2026, 8, 9), 'Оплачено 3 мес', '@praktika_service']);
book.sheets['Клиенты'] = prod;
const before = prod.data[1].slice(0, 7);

console.log(api.setupSheets());
const clientsSheet = prod;
ok('колонки A–G не тронуты', JSON.stringify(clientsSheet.data[1].slice(0, 7)) === JSON.stringify(before));
ok('шапка учёта оплат цела', clientsSheet.data[0].slice(0, 7).join('|') === '№|Клиент|Тариф|Цена|Дата оплаты|Статус|Контакт');
ok('колонки CRM дописаны справа от G', clientsSheet.data[0].indexOf('client_id') >= 7);
ok('«Тариф» не продублирован', clientsSheet.data[0].filter(h => h === 'Тариф').length === 1);
ok('«Дата оплаты» не продублирована', clientsSheet.data[0].filter(h => h === 'Дата оплаты').length === 1);
ok('«Статус оплаты» не добавлен — есть «Статус»', clientsSheet.data[0].indexOf('Статус оплаты') < 0, clientsSheet.data[0].filter(h => /Статус/.test(h)));
ok('«ФИО» не добавлено — есть «Клиент»', clientsSheet.data[0].indexOf('ФИО') < 0);
ok('повторный setupSheets ничего не добавляет', api.missingClientColumns_(clientsSheet).length === 0);

/* ---- 1b. существующая строка видна CRM ---- */
const cell = (row, title) => clientsSheet.data[row - 1][clientsSheet.data[0].indexOf(title)];
const idCol = clientsSheet.data[0].indexOf('client_id');
clientsSheet.data[1][idCol] = 'auto-praktika';
let existing = api.GET_ACTIONS.client({ id: 'auto-praktika' }).client;
ok('ФИО из колонки «Клиент»', existing.name === 'Игорь Демин', existing.name);
ok('тариф из колонки «Тариф»', existing.tariff === 'БИЗНЕС');
ok('«Оплачено 3 мес» -> оплачено', existing.pay === 'paid', existing.pay + ' / ' + existing.payRaw);
ok('дата оплаты из колонки E', existing.nextPay === '2026-09-09', existing.nextPay);
ok('ссылка на ячейку статуса', /#gid=\d+&range=F2$/.test(existing.rowUrl), existing.rowUrl);

clientsSheet.data[1][5] = 'Просрочено';
ok('«Просрочено» -> просрочка', api.GET_ACTIONS.client({ id: 'auto-praktika' }).client.pay === 'overdue');
clientsSheet.data[1][5] = 'Ожидает оплаты';
ok('«Ожидает оплаты» -> ждёт', api.GET_ACTIONS.client({ id: 'auto-praktika' }).client.pay === 'due');

/* ---- 1c. CRM не пишет в колонки оплат ---- */
const payBefore = [clientsSheet.data[1][4], clientsSheet.data[1][5]];
api.POST_ACTIONS.save({ client: { id: 'auto-praktika', city: 'Колпино', pay: 'paid', nextPay: '2030-01-01', about: 'Автосервис' } });
ok('дата оплаты не переписана', String(clientsSheet.data[1][4]) === String(payBefore[0]), String(clientsSheet.data[1][4]));
ok('статус не переписан', clientsSheet.data[1][5] === payBefore[1], clientsSheet.data[1][5]);
ok('остальные поля сохранились', api.GET_ACTIONS.client({ id: 'auto-praktika' }).client.city === 'Колпино');

/* ---- 1d. логика учёта оплат не сломана слиянием ---- */
const dateCell = prod.getRange(2, 5);
dateCell.setValue(new Date(2026, 8, 9));
prod.getRange(2, 6).setValue('Оплачено 3 мес');
api.onEdit({ range: prod.getRange(2, 6), oldValue: 'Ожидает оплаты' });
ok('onEdit сдвинул дату на 3 месяца', String(prod.data[1][4]).indexOf('Dec 09 2026') >= 0, String(prod.data[1][4]));
prod.getRange(2, 6).setValue('Отменить оплату');
api.onEdit({ range: prod.getRange(2, 6), oldValue: 'Оплачено 3 мес' });
ok('onEdit откатил дату назад', String(prod.data[1][4]).indexOf('Sep 09 2026') >= 0, String(prod.data[1][4]));

/* ---- 2. создание клиента ---- */
const created = api.POST_ACTIONS['new']({ client: {
  business: 'Пекарня «Тесто и дело»', name: 'Данила Кириллов', phone: '+7 921 000-00-00',
  tg: '@testoidelo', email: 'd@ya.ru', city: 'Санкт-Петербург', tariff: 'ПРО', niche: 'пекарня',
  about: 'Хлеб на закваске', audience: 'Жители района', networks: ['VK', 'Telegram'],
  slots: ['morning', 'evening'], limits: ['Цены и стоимость услуг', 'Проценты, ставки, доходность'],
  topics: ['Фото работ', 'Отзывы и результаты'], hasPhoto: true, faq: 'Вопрос? — Ответ.', cta: 'Пишите'
} }).client;
ok('client_id из названия', created.id === 'pekarnya-testo-i-delo', created.id);
ok('статус пустой — его ставит дропдаун', created.pay === 'brief' && created.payRaw === '', created.payRaw);
ok('дата подключения — сегодня', created.startedAt === new Date().toISOString().slice(0, 10), created.startedAt);

ok('слоты по-русски в ячейке', cell(3, 'Слоты') === 'Утро, Вечер', cell(3, 'Слоты'));
ok('CRM не тронула колонку «Статус»', cell(3, 'Статус') === '', cell(3, 'Статус'));
ok('ограничения с запятыми целы', cell(3, 'Ограничения список') === 'Цены и стоимость услуг\nПроценты, ставки, доходность', cell(3, 'Ограничения список'));
ok('«Активен» — булево', cell(3, 'Активен') === true);
ok('«Есть фото» — Да', cell(3, 'Есть фото') === 'Да');

/* ---- 3. чтение обратно ---- */
const list = api.GET_ACTIONS.clients().clients.filter(c => c.id !== 'auto-praktika');
ok('в списке новый клиент', list.length === 1, list.length);
ok('ограничения разобраны обратно', JSON.stringify(list[0].limits) === JSON.stringify(['Цены и стоимость услуг', 'Проценты, ставки, доходность']), list[0].limits);
ok('слоты разобраны обратно', JSON.stringify(list[0].slots) === JSON.stringify(['morning', 'evening']), list[0].slots);
ok('темы разобраны', JSON.stringify(list[0].topics) === JSON.stringify(['Фото работ', 'Отзывы и результаты']), list[0].topics);
ok('соцсети разобраны', JSON.stringify(list[0].networks) === JSON.stringify(['VK', 'Telegram']));

/* ---- 4. частичное сохранение ---- */
api.POST_ACTIONS.save({ client: { id: created.id, active: false } });
const afterToggle = api.GET_ACTIONS.client({ id: created.id }).client;
ok('частичный save не стёр остальное', afterToggle.active === false && afterToggle.business === 'Пекарня «Тесто и дело»' && afterToggle.slots.length === 2);

const full = api.POST_ACTIONS.save({ client: Object.assign({}, afterToggle, {
  about: 'Новый текст', stylePrompt: 'Короткие фразы', active: true,
  rubrics: [{ name: 'Фото работ', days: 'пн, чт', prompt: 'Пост по фото', example: 'Пример' }],
  styleAnswers: ['a', 'b', 'c', 'd', 'e'], checks: { disk: true, channel: true }, iterations: 2
}) }).client;
ok('полный save вернулся целиком', full.about === 'Новый текст' && full.rubrics.length === 1 && full.checks.channel === true && full.iterations === 2);
ok('рубрики в ячейке — JSON', JSON.parse(cell(3, 'Рубрики (JSON)'))[0].name === 'Фото работ');
ok('style_prompt в своей колонке', cell(3, 'style_prompt') === 'Короткие фразы');

// round-trip для новых полей: сохранил -> перечитал -> значения совпадают
const saved = api.POST_ACTIONS.save({ client: Object.assign({}, full, {
  tgChannel: '@olga_test', morningPhoto: true,
  holidaysExtra: '08.02 День риелтора\n08.07 День семьи'
}) }).client;
ok('tgChannel сохранился и вернулся', saved.tgChannel === '@olga_test', saved.tgChannel);
ok('morningPhoto сохранился и вернулся', saved.morningPhoto === true, saved.morningPhoto);
ok('holidaysExtra сохранился и вернулся',
   saved.holidaysExtra === '08.02 День риелтора\n08.07 День семьи', saved.holidaysExtra);
// конфиг проверяется в pure.test.js — buildConfig_ там экспортируется

/* ---- 5. оплаты только читаются ---- */
ok('в doPost нет action=pay', api.POST_ACTIONS.pay === undefined);
ok('в doPost нет action=unpay', api.POST_ACTIONS.unpay === undefined);

const payCol = clientsSheet.data[0].indexOf('Статус');
const dateCol = clientsSheet.data[0].indexOf('Дата оплаты');
// дата берётся относительно сегодня: с жёстко зашитой тест протухал,
// как только она оказывалась в прошлом
const soon = new Date(); soon.setDate(soon.getDate() + 10);
const soonIso = soon.getFullYear() + '-' +
  String(soon.getMonth() + 1).padStart(2, '0') + '-' +
  String(soon.getDate()).padStart(2, '0');
clientsSheet.data[2][dateCol] = soon;
clientsSheet.data[2][payCol] = 'Ожидает оплаты';
let fresh = api.GET_ACTIONS.client({ id: created.id }).client;
ok('дата и статус читаются из колонок учёта', fresh.nextPay === soonIso && fresh.pay === 'due', fresh.pay);

// полное сохранение карточки не должно задеть колонки оплат
api.POST_ACTIONS.save({ client: Object.assign({}, fresh, { pay: 'paid', nextPay: '2031-01-01', about: 'Ещё раз' }) });
ok('полный save не трогает дату оплаты', String(clientsSheet.data[2][dateCol]) === String(soon), String(clientsSheet.data[2][dateCol]));
ok('полный save не трогает статус', clientsSheet.data[2][payCol] === 'Ожидает оплаты');

/* ---- 6. просрочка, пока лист не пересчитан ---- */
clientsSheet.data[2][dateCol] = new Date(2020, 0, 1);
ok('дата в прошлом -> просрочка', api.GET_ACTIONS.client({ id: created.id }).client.pay === 'overdue');
clientsSheet.data[2][payCol] = 'Оплачено 12 мес';
clientsSheet.data[2][dateCol] = new Date(2030, 0, 1);
ok('оплачено вперёд -> оплачено', api.GET_ACTIONS.client({ id: created.id }).client.pay === 'paid');

/* ---- 7. одинаковые названия -> разные id ---- */
const second = api.POST_ACTIONS['new']({ client: { business: 'Пекарня «Тесто и дело»', name: 'Кто-то' } }).client;
ok('второй клиент получил -2', second.id === 'pekarnya-testo-i-delo-2', second.id);

/* ---- 8. форма -> клиент + Telegram ---- */
const briefSheet = book.getSheetByName('Брифы');
briefSheet.data[0] = ['Отметка времени', 'Ваше имя (ФИО)', 'Телефон', 'Ваш Telegram', 'Название бизнеса',
  'Ниша / сфера деятельности', 'Город', 'Расскажите о бизнесе', 'Кто ваши клиенты',
  'ПРО — 2 поста в день: какие слоты?', 'О чём нельзя писать?', 'Есть фото работ?', 'client_id', 'Обработан'];
briefSheet.data[1] = [new Date(), 'Алиса Ремизова', '+7 900 111-22-33', '@keramika', 'Студия «Керамика»',
  'керамическая студия', 'Санкт-Петербург', 'Мастер-классы', 'Взрослые 25–45', 'Утро, Вечер',
  'Цены и стоимость услуг, Гарантии результата', 'Да', '', ''];
sent.length = 0;
const fromForm = api.onFormSubmit({
  range: { getRow: () => 2, getSheet: () => briefSheet },
  namedValues: (() => { const nv = {}; briefSheet.data[0].forEach((h, i) => { if (h) nv[h] = [briefSheet.data[1][i]]; }); return nv; })()
});
// onFormSubmit карточку не создаёт: бриф ждёт в разделе «Брифы»,
// клиент заводится оттуда кнопкой — id появляется только на этом шаге
ok('бриф из формы разобран', fromForm.business === 'Студия «Керамика»' && fromForm.name === 'Алиса Ремизова', fromForm.business);
ok('id на этапе брифа ещё нет', fromForm.id === undefined, fromForm.id);
// раздел формы называется «ПРО» — это алиас, реестр отдаёт имя тарифа «ПРОФИ»
ok('тариф по разделу формы', fromForm.tariff === 'ПРОФИ', fromForm.tariff);
ok('слоты из формы', JSON.stringify(fromForm.slots) === JSON.stringify(['morning', 'evening']), fromForm.slots);
ok('ограничения из формы', fromForm.limits.length === 2, fromForm.limits);
ok('источник — Google Форма', fromForm.source === 'Google Форма');
const tg = sent.find(s => /sendMessage/.test(s.url));
const msg = JSON.parse(tg.o.payload).text;
// в начале строки — номер клиента из CLIENT_NUMBER_SEQ, экранированный для Markdown
ok('Telegram: нужная первая строка', msg.split('\n')[0] === '\\[0003\\] Новый бриф: Алиса Ремизова, ПРОФИ, керамическая студия', msg.split('\n')[0]);
// строка брифа помечается только при создании клиента кнопкой в CRM,
// сам onFormSubmit её не трогает
ok('строка брифа не помечена', !briefSheet.data[1][12] && !briefSheet.data[1][13]);
ok('бриф ждёт в списке', api.GET_ACTIONS.briefs().briefs.length === 1);

/* ---- 9. необработанный бриф виден ---- */
briefSheet.data[2] = [new Date(), 'Пётр Смирнов', '+7 900 000-00-00', '@petr', 'Автосервис «Практика»',
  'автосервис', 'Колпино', 'Ремонт авто', 'Мужчины 30–55', '', '', 'Нет', '', ''];
// в списке теперь два: керамика из блока 8 никуда не делась
const pending = api.GET_ACTIONS.briefs().briefs;
const auto = pending.filter(b => b.business === 'Автосервис «Практика»');
ok('новый бриф в списке', pending.length === 2 && auto.length === 1, pending.length);
ok('ответы брифа доступны интерфейсу', auto[0].answers.niche === 'автосервис');
api.POST_ACTIONS.brief_dismiss({ row: auto[0].row });
const left = api.GET_ACTIONS.briefs().briefs;
ok('отложенный бриф скрыт', left.length === 1 && left[0].business === 'Студия «Керамика»', left.length);

/* ---- 10. лог ---- */
const logSheet = book.getSheetByName('Лог');
logSheet.data.push(['pekarnya-testo-i-delo', new Date(2026, 7, 24), '10:00', 'Фото работ', 'Опубликован', '']);
logSheet.data.push(['pekarnya-testo-i-delo', new Date(2026, 7, 23), '14:00', 'Советы', 'Ошибка', 'Нет фото в to_post/']);
const log = api.GET_ACTIONS.log({}).log;
ok('лог свежим сверху', log[0].date === '23.08.2026' && log[0].ok === false, log[0]);
ok('ошибка распознана', log[0].err === 'Нет фото в to_post/');
ok('успех распознан', log[1].ok === true);

/* ---- 11. токен доступа ---- */
PROPS.API_TOKEN = 'secret-123';
const denied = JSON.parse(api.route_('GET', { parameter: { action: 'clients' } }).setMimeType ? '{}' : '{}');
const resDenied = api.route_('GET', { parameter: { action: 'clients' } });
ok('без токена — отказ', /Неверный токен/.test(JSON.stringify(resDenied)), JSON.stringify(resDenied).slice(0, 80));
const resOk = api.route_('GET', { parameter: { action: 'clients', token: 'secret-123' } });
ok('с токеном — данные', /pekarnya/.test(JSON.stringify(resOk)));
const resPost = api.route_('POST', { postData: { contents: JSON.stringify({ action: 'save', token: 'secret-123', client: { id: 'pekarnya-testo-i-delo', city: 'Пушкин' } }) } });
ok('POST с JSON-телом работает', api.GET_ACTIONS.client({ id: 'pekarnya-testo-i-delo' }).client.city === 'Пушкин');
const resBad = api.route_('POST', { postData: { contents: JSON.stringify({ action: 'неттакого', token: 'secret-123' }) } });
ok('неизвестное действие -> ok:false', /Неизвестное действие/.test(JSON.stringify(resBad)));

console.log(fails ? '\n=== ПРОВАЛОВ: ' + fails : '\n=== бэкенд: все проверки прошли');
process.exit(fails ? 1 : 0);
