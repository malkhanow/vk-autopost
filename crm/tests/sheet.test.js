const fs = require('fs');
const code = fs.readFileSync(require('path').join(__dirname, '..', 'Code.gs'), 'utf8');

/* ---- мок Google Таблицы ---- */
class Sheet {
  constructor(name, head) { this.name = name; this.data = [head.slice()]; this.frozen = 0; }
  getName() { return this.name; }
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
      setFontWeight() { return this; }
    };
  }
}
class Book {
  constructor() { this.sheets = {}; }
  getSheetByName(n) { return this.sheets[n] || null; }
  insertSheet(n) { return (this.sheets[n] = new Sheet(n, [])); }
}
const book = new Book();
const SpreadsheetApp = { getActive: () => book, flush: () => {} };
const LockService = { getScriptLock: () => ({ tryLock: () => true, releaseLock: () => {} }) };
const Utilities = {
  formatDate: (dt, tz, f) => { const p = n => String(n).padStart(2, '0');
    return f.replace('yyyy', dt.getFullYear()).replace('MM', p(dt.getMonth() + 1)).replace('dd', p(dt.getDate())).replace('HH', p(dt.getHours())).replace('mm', p(dt.getMinutes())); },
  base64Encode: s => Buffer.from(s, 'utf8').toString('base64'), Charset: { UTF_8: 'utf8' }, sleep: () => {}
};
const Session = { getScriptTimeZone: () => 'Europe/Moscow' };
const PROPS = {};
const PropertiesService = { getScriptProperties: () => ({ getProperty: k => (k in PROPS ? PROPS[k] : null) }) };
const sent = [];
const UrlFetchApp = { fetch: (url, o) => { sent.push({ url, o }); return { getResponseCode: () => 200, getContentText: () => JSON.stringify({ ok: true, result: { username: 'bot' } }) }; } };
const ScriptApp = { getProjectTriggers: () => [] };
const ContentService = { MimeType: { JSON: 'json' }, createTextOutput: t => ({ setMimeType: () => ({ body: t }) }) };

const api = new Function('SpreadsheetApp', 'LockService', 'Utilities', 'Session', 'PropertiesService', 'UrlFetchApp', 'ScriptApp', 'ContentService', 'console',
  code + '\nreturn {setupSheets,GET_ACTIONS,POST_ACTIONS,doGet,doPost,onFormSubmit,HEAD_CLIENTS,HEAD_CLIENTS_EXTRA,clientsTable_,F,dailyPaymentReminders,tgNotify_,route_};')
  (SpreadsheetApp, LockService, Utilities, Session, PropertiesService, UrlFetchApp, ScriptApp, ContentService, console);

let fails = 0;
const ok = (n, c, info) => { if (c) console.log('ok  ', n); else { console.log('FAIL', n, info === undefined ? '' : JSON.stringify(info)); fails++; } };

/* ---- 1. подготовка листов ---- */
console.log(api.setupSheets());
const clientsSheet = book.getSheetByName('Клиенты');
ok('28 обязательных колонок на месте', api.HEAD_CLIENTS.every((h, i) => clientsSheet.data[0][i] === h), clientsSheet.data[0].slice(0, 3));
ok('служебные колонки дописаны справа', api.HEAD_CLIENTS_EXTRA.every(h => clientsSheet.data[0].indexOf(h) >= api.HEAD_CLIENTS.length));

/* ---- 2. создание клиента ---- */
const created = api.POST_ACTIONS['new']({ client: {
  business: 'Пекарня «Тесто и дело»', name: 'Данила Кириллов', phone: '+7 921 000-00-00',
  tg: '@testoidelo', email: 'd@ya.ru', city: 'Санкт-Петербург', tariff: 'ПРО', niche: 'пекарня',
  about: 'Хлеб на закваске', audience: 'Жители района', networks: ['VK', 'Telegram'],
  slots: ['morning', 'evening'], limits: ['Цены и стоимость услуг', 'Проценты, ставки, доходность'],
  topics: ['Фото работ', 'Отзывы и результаты'], hasPhoto: true, faq: 'Вопрос? — Ответ.', cta: 'Пишите'
} }).client;
ok('client_id из названия', created.id === 'pekarnya-testo-i-delo', created.id);
ok('статус — новый бриф', created.pay === 'brief');
ok('дата подключения — сегодня', created.startedAt === new Date().toISOString().slice(0, 10), created.startedAt);

const cell = (row, title) => clientsSheet.data[row - 1][clientsSheet.data[0].indexOf(title)];
ok('слоты по-русски в ячейке', cell(2, 'Слоты') === 'Утро, Вечер', cell(2, 'Слоты'));
ok('статус оплаты словами', cell(2, 'Статус оплаты') === 'Новый бриф', cell(2, 'Статус оплаты'));
ok('ограничения с запятыми целы', cell(2, 'Ограничения список') === 'Цены и стоимость услуг\nПроценты, ставки, доходность', cell(2, 'Ограничения список'));
ok('«Активен» — булево', cell(2, 'Активен') === true);
ok('«Есть фото» — Да', cell(2, 'Есть фото') === 'Да');

/* ---- 3. чтение обратно ---- */
const list = api.GET_ACTIONS.clients().clients;
ok('в списке один клиент', list.length === 1);
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
ok('рубрики в ячейке — JSON', JSON.parse(cell(2, 'Рубрики (JSON)'))[0].name === 'Фото работ');
ok('style_prompt в своей колонке', cell(2, 'style_prompt') === 'Короткие фразы');

/* ---- 5. оплата ---- */
api.POST_ACTIONS.save({ client: { id: created.id, nextPay: '2026-08-26', pay: 'due' } });
const paid = api.POST_ACTIONS.pay({ id: created.id });
ok('дата оплаты +1 месяц', paid.nextPay === '2026-09-26', paid.nextPay);
ok('последняя оплата — прежняя дата', paid.paidAt === '2026-08-26', paid.paidAt);
ok('статус «Оплачено»', paid.client.pay === 'paid' && cell(2, 'Статус оплаты') === 'Оплачено');
ok('чек-лист: оплата отмечена', paid.client.checks.paid === true);

/* ---- 5b. откат оплаты ---- */
ok('дата до сдвига сохранена', cell(2, 'Предыдущая дата оплаты') instanceof Date && api.GET_ACTIONS.client({ id: created.id }).client.prevPay === '2026-08-26', cell(2, 'Предыдущая дата оплаты'));
try { api.POST_ACTIONS.pay({ id: created.id }); ok('повторное подтверждение отклонено', false); }
catch (e) { ok('повторное подтверждение отклонено', /уже подтверждена/.test(e.message), e.message); }
ok('дата не сдвинулась от лишнего клика', api.GET_ACTIONS.client({ id: created.id }).client.nextPay === '2026-09-26');

const back = api.POST_ACTIONS.unpay({ id: created.id });
ok('откат вернул дату', back.nextPay === '2026-08-26' && back.client.nextPay === '2026-08-26', back.nextPay);
ok('статус — ждёт оплаты', back.client.pay === 'due' && cell(2, 'Статус оплаты') === 'Ждёт оплаты', cell(2, 'Статус оплаты'));
ok('галочка paid снята', back.client.checks.paid === false);
ok('колонка отката очищена', back.client.prevPay === '' && back.client.paidAt === '');
try { api.POST_ACTIONS.unpay({ id: created.id }); ok('повторный откат отклонён', false); }
catch (e) { ok('повторный откат отклонён', /отменять нечего/.test(e.message), e.message); }

api.POST_ACTIONS.pay({ id: created.id });
ok('после отката можно подтвердить снова', api.GET_ACTIONS.client({ id: created.id }).client.nextPay === '2026-09-26');

// строка, оплаченная до появления колонки отката
api.POST_ACTIONS.save({ client: { id: created.id, prevPay: '' } });
ok('запасной вариант — по «Последней оплате»', api.POST_ACTIONS.unpay({ id: created.id }).nextPay === '2026-08-26');
api.POST_ACTIONS.save({ client: { id: created.id, nextPay: '2026-08-26', pay: 'due' } });
api.POST_ACTIONS.pay({ id: created.id });

/* ---- 6. просрочка считается на лету ---- */
api.POST_ACTIONS.save({ client: { id: created.id, nextPay: '2020-01-01' } });
ok('просрочка по дате', api.GET_ACTIONS.client({ id: created.id }).client.pay === 'overdue');

/* ---- 7. одинаковые названия -> разные id ---- */
const second = api.POST_ACTIONS['new']({ client: { business: 'Пекарня «Тесто и дело»', name: 'Кто-то' } }).client;
ok('второй клиент получил -2', second.id === 'pekarnya-testo-i-delo-2', second.id);

/* ---- 8. форма -> клиент + Telegram ---- */
PROPS.TG_BOT_TOKEN = '123:AA'; PROPS.TG_CHAT_ID = '555';
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
ok('карточка из формы', fromForm.id === 'studiya-keramika' && fromForm.name === 'Алиса Ремизова', fromForm.id);
ok('тариф по разделу формы', fromForm.tariff === 'ПРО', fromForm.tariff);
ok('слоты из формы', JSON.stringify(fromForm.slots) === JSON.stringify(['morning', 'evening']), fromForm.slots);
ok('ограничения из формы', fromForm.limits.length === 2, fromForm.limits);
ok('источник — Google Форма', fromForm.source === 'Google Форма');
const tg = sent.find(s => /sendMessage/.test(s.url));
const msg = JSON.parse(tg.o.payload).text;
ok('Telegram: нужная первая строка', msg.split('\n')[0] === 'Новый бриф: Алиса Ремизова, ПРО, керамическая студия', msg.split('\n')[0]);
ok('строка брифа помечена', briefSheet.data[1][12] === 'studiya-keramika' && briefSheet.data[1][13] === 'создан клиент');
ok('обработанный бриф ушёл из списка', api.GET_ACTIONS.briefs().briefs.length === 0);

/* ---- 9. необработанный бриф виден ---- */
briefSheet.data[2] = [new Date(), 'Пётр Смирнов', '+7 900 000-00-00', '@petr', 'Автосервис «Практика»',
  'автосервис', 'Колпино', 'Ремонт авто', 'Мужчины 30–55', '', '', 'Нет', '', ''];
const pending = api.GET_ACTIONS.briefs().briefs;
ok('новый бриф в списке', pending.length === 1 && pending[0].business === 'Автосервис «Практика»', pending.length);
ok('ответы брифа доступны интерфейсу', pending[0].answers.niche === 'автосервис');
api.POST_ACTIONS.brief_dismiss({ row: pending[0].row });
ok('отложенный бриф скрыт', api.GET_ACTIONS.briefs().briefs.length === 0);

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
