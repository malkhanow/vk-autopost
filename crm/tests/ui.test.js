const fs = require('fs');
const html = fs.readFileSync(require('path').join(__dirname, '..', 'Клиенты.dc.html'), 'utf8');
const src = /<script type="text\/x-dc"[^>]*>\n([\s\S]*)\n<\/script>/.exec(html)[1];

class DCLogic {
  constructor() { this.props = {}; }
  setState(u) {
    const patch = typeof u === 'function' ? u(this.state) : u;
    this.state = Object.assign({}, this.state, patch);
    this.renders = (this.renders || 0) + 1;
  }
}
const store = {};
const window = { localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } } };
const navigator = { clipboard: { writeText: () => {} } };

let calls = [];
let reply = {};
const fetchStub = (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  const action = body ? body.action : /action=([^&]+)/.exec(url)[1];
  calls.push({ url, method: (opts && opts.method) || 'GET', action, body, ct: opts && opts.headers && opts.headers['Content-Type'] });
  const data = Object.assign({ ok: true, at: '24.08 · 12:00' }, reply[action] || {});
  return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(data)) });
};

const Component = new Function('DCLogic', 'window', 'navigator', 'fetch', 'Blob', 'setTimeout', 'clearTimeout',
  src + '\nreturn Component;')(DCLogic, window, navigator, fetchStub, Blob, setTimeout, clearTimeout);

let fails = 0;
const ok = (name, cond, info) => { if (cond) console.log('ok  ', name); else { console.log('FAIL', name, info === undefined ? '' : info); fails++; } };
const later = ms => new Promise(r => setTimeout(r, ms));

const SERVER_CLIENT = {
  row: 2, id: 'beauty-bar', name: 'Екатерина Лаврова', phone: '+7 921 344-18-02', email: 'b@ya.ru',
  tg: '@beautybar_spb', tariff: 'ПРО', business: 'Beauty BAR', about: 'Салон', audience: 'Женщины',
  city: 'СПб', networks: ['VK', 'Telegram'], links: '', tone: 'Простой и дружелюбный — как друг',
  slots: ['morning', 'evening'], hasPhoto: true, rubrics: [{ name: 'Фото работ', days: 'пн, чт', prompt: 'P', example: '' }],
  holidays: 'Да', faq: 'a\nb', limits: ['Цены и стоимость услуг'], limitsText: '', cta: 'Записаться',
  source: 'Avito', startedAt: '2026-05-12', nextPay: '2026-09-01', paidAt: '', pay: 'test',
  active: true, stylePrompt: '', configPushed: '', niche: 'салон красоты', topics: ['Фото работ'],
  styleAnswers: ['1', '2', '3', '4', '5'], checks: { disk: true }, iterations: 0, photoQueue: 14,
  lastPostDate: '24.08.2026 10:00', lastPostStatus: 'Опубликован'
};

(async () => {
  /* --- 1. Демо-режим без WEBAPP_URL --- */
  let comp = new Component();
  let v = comp.renderVals();
  ok('рендер без данных не падает', v.rows.length === 0 && v.c.id === '—', v.c.id);
  ok('заглушка «нет клиентов»', v.c.business === 'Клиентов пока нет');
  await later(20);
  v = comp.renderVals();
  ok('демо-данные подхватились', v.rows.length === 6 && v.syncedAt === 'демо-данные', v.rows.length);
  ok('демо: сеть не трогали', calls.length === 0);
  v.onSave();
  await later(600);
  ok('демо onSave без запроса', calls.length === 0 && comp.state.saved === true);

  /* --- 2. Реальный режим --- */
  store['smm-crm.connection'] = JSON.stringify({ WEBAPP_URL: 'https://script.google.com/macros/s/AKfy/exec', API_TOKEN: 'secret-123' });
  calls = [];
  reply = {
    clients: { clients: [SERVER_CLIENT] },
    briefs: { briefs: [{ fid: 'row-2', row: 2, business: 'Пекарня', name: 'Данила', niche: 'пекарня', city: 'СПб', tariff: 'ПРО', tg: '@t', about: 'Хлеб', at: '24.08 · 18:22', tag: 'новый', answers: { name: 'Данила', business: 'Пекарня' } }] },
    log: { log: [{ id: 'beauty-bar', date: '24.08.2026', time: '10:00', rubric: 'Фото работ', ok: true, err: '' }] },
    settings: { props: { GITHUB_TOKEN: true, ROUTERAI_KEY: true, TG_BOT_TOKEN: false, GITHUB_REPO: true, TG_CHAT_ID: false } }
  };
  comp = new Component();
  comp.renderVals();
  await later(30);
  v = comp.renderVals();
  ok('загрузка четырьмя GET', calls.filter(c => c.method === 'GET').length === 4, calls.map(c => c.action).join(','));
  ok('токен в query', calls[0].url.indexOf('token=secret-123') > 0);
  ok('клиент из таблицы', v.rows.length === 1 && v.rows[0].business === 'Beauty BAR');
  ok('даты разобраны', v.c.startedAt === '12 мая 2026' && v.c.nextPay === '1 сентября 2026', v.c.startedAt + ' / ' + v.c.nextPay);
  ok('статус оплаты', v.rows[0].payLabel === 'Тест без оплаты', v.rows[0].payLabel);
  ok('слоты', v.rows[0].slots === 'Утро + Вечер', v.rows[0].slots);
  ok('брифы', v.briefs.length === 1 && v.briefs[0].business === 'Пекарня');
  ok('лог', v.logRows.length === 1 && v.logRows[0].business === 'Beauty BAR');
  ok('секреты: статус, не значение', v.settingFields[2].value === '' && v.settingFields[2].state === 'задан', JSON.stringify(v.settingFields[2].value));
  ok('WEBAPP_URL виден', v.settingFields[0].value.endsWith('/exec'));
  ok('TG_CHAT_ID не задан', v.settingFields[6].state === 'не задан', v.settingFields[6].state);
  ok('конфиг в предпросмотре', JSON.parse(v.configJson).slots[0].cron === '07:00');

  /* --- 3. onSave --- */
  calls = [];
  reply.save = { client: Object.assign({}, SERVER_CLIENT, { about: 'Обновлено' }) };
  v.onSave();
  await later(30);
  const save = calls.find(c => c.action === 'save');
  ok('onSave -> POST save', save && save.method === 'POST');
  ok('POST без preflight (text/plain)', save.ct === 'text/plain;charset=utf-8', save.ct);
  ok('в теле весь клиент', save.body.client.id === 'beauty-bar' && save.body.client.slots.length === 2);
  ok('даты уходят строкой', save.body.client.startedAt === '2026-05-12', save.body.client.startedAt);
  ok('токен в теле', save.body.token === 'secret-123');
  ok('ответ применён', comp.renderVals().c.about === 'Обновлено');

  /* --- 4. confirmPayment --- */
  calls = [];
  reply.pay = { client: Object.assign({}, SERVER_CLIENT, { pay: 'paid', paidAt: '2026-09-01', nextPay: '2026-10-01' }), nextPay: '2026-10-01' };
  comp.renderVals().confirmPayment();
  await later(30);
  ok('confirmPayment -> POST pay', calls[0].action === 'pay' && calls[0].body.id === 'beauty-bar');
  v = comp.renderVals();
  ok('оплата применена', v.c.payLabel === 'Оплачено' && v.c.nextPay === '1 октября 2026', v.c.nextPay);

  /* --- 5. onPush --- */
  calls = [];
  reply.push = { client: SERVER_CLIENT, sha: 'a1b2c3d', prevSha: '9f8e7d6', path: 'clients/beauty-bar.json', dispatched: true, at: '24.08 · 12:05', workflowNote: 'запущен' };
  comp.renderVals().onPush();
  await later(30);
  ok('onPush -> POST push', calls[0].action === 'push' && calls[0].body.id === 'beauty-bar');
  v = comp.renderVals();
  ok('шаги деплоя обновились', v.deployLabel === 'закоммичен · a1b2c3d', v.deployLabel);
  ok('sha предыдущего файла', v.deploySteps[1].detail === 'sha 9f8e7d6', v.deploySteps[1].detail);
  ok('workflow_dispatch отмечен', v.deploySteps[3].detail === 'запущен', v.deploySteps[3].detail);

  /* --- 6. RouterAI --- */
  calls = [];
  reply.analyze_style = { client: Object.assign({}, SERVER_CLIENT, { stylePrompt: 'Короткие фразы.' }), stylePrompt: 'Короткие фразы.' };
  comp.renderVals().analyzeStyle();
  await later(30);
  ok('analyzeStyle -> POST analyze_style', calls[0].action === 'analyze_style' && calls[0].body.answers.length === 5);
  ok('style_prompt в интерфейсе', comp.renderVals().c.stylePrompt === 'Короткие фразы.');

  calls = [];
  reply.build_plan = { client: Object.assign({}, SERVER_CLIENT, { rubrics: [{ name: 'A', days: 'пн', prompt: 'p', example: '' }, { name: 'B', days: 'вт', prompt: 'p', example: '' }] }), rubrics: [1, 2] };
  comp.renderVals().buildPlan();
  await later(30);
  ok('buildPlan -> POST build_plan', calls[0].action === 'build_plan');
  ok('рубрики применены', comp.renderVals().rubrics.length === 2);

  calls = [];
  reply.gen_examples = { client: Object.assign({}, SERVER_CLIENT, { rubrics: [{ name: 'A', days: 'пн', prompt: 'p', example: 'Пример поста' }] }) };
  comp.renderVals().genExamples();
  await later(30);
  ok('genExamples -> POST gen_examples', calls[0].action === 'gen_examples');
  ok('пример в рубрике', comp.renderVals().rubrics[0].example === 'Пример поста');

  comp.setState({ clients: comp.state.clients.map(c => Object.assign({}, c, { edits: 'больше про людей' })) });
  calls = [];
  reply.apply_edits = { client: Object.assign({}, SERVER_CLIENT, { iterations: 1 }), iterations: 1 };
  comp.renderVals().applyEdits();
  await later(30);
  ok('applyEdits -> POST apply_edits', calls[0].action === 'apply_edits' && calls[0].body.edits === 'больше про людей');
  ok('поле правок очищено', comp.renderVals().c.edits === '');

  /* --- 7. новый клиент и брифы --- */
  calls = [];
  comp.setState({ form: { business: 'Пекарня «Тесто»', name: 'Данила', phone: '+7', tg: '@t', email: '', city: 'СПб' } });
  reply['new'] = { client: Object.assign({}, SERVER_CLIENT, { id: 'pekarnya-testo', business: 'Пекарня «Тесто»' }) };
  comp.renderVals().createClient();
  await later(30);
  ok('createClient -> POST new', calls[0].action === 'new' && calls[0].body.client.business === 'Пекарня «Тесто»');
  ok('id пришёл от сервера', comp.state.current === 'pekarnya-testo' && comp.state.clients.length === 2);

  calls = [];
  comp.setState({ view: 'briefs' });
  comp.renderVals().briefs[0].onDismiss();
  await later(30);
  ok('onDismiss -> POST brief_dismiss', calls[0].action === 'brief_dismiss' && calls[0].body.row === 2);
  ok('бриф убран из списка', comp.state.briefs.length === 0);

  /* --- 8. переключатели --- */
  calls = [];
  comp.setState({ view: 'list' });
  const row = comp.renderVals().rows.find(r => r.business === 'Beauty BAR');
  row.onToggle({ stopPropagation() {} });
  await later(30);
  ok('переключатель -> POST save c active', calls[0].action === 'save' && calls[0].body.client.active === false);

  calls = [];
  comp.setState({ view: 'card', current: 'beauty-bar', tab: 'onboarding' });
  comp.renderVals().checklist[1].onToggle();
  await later(30);
  ok('чек-лист -> POST save c checks', calls[0].action === 'save' && calls[0].body.client.checks.channel === true, JSON.stringify(calls[0] && calls[0].body.client));

  /* --- 9. ping --- */
  calls = [];
  reply.ping = { checks: { sheet: { ok: true, detail: '1 клиент' }, github: { ok: false, detail: 'HTTP 404' }, routerai: { ok: true, detail: 'ключ принят' }, telegram: { ok: true, detail: '@bot' } } };
  comp.setState({ view: 'settings' });
  comp.renderVals().testSettings();
  await later(40);
  ok('ping -> GET ping', calls[0].action === 'ping' && calls[0].method === 'GET');
  ok('ошибка показана', comp.state.tested === 'Ошибки: GitHub', comp.state.tested);

  /* --- 10. ошибка сети --- */
  calls = [];
  const broken = () => Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve('<!DOCTYPE html><html>ошибка</html>') });
  const Comp2 = new Function('DCLogic', 'window', 'navigator', 'fetch', 'Blob', 'setTimeout', 'clearTimeout', src + '\nreturn Component;')(DCLogic, window, navigator, broken, Blob, setTimeout, clearTimeout);
  const c2 = new Comp2();
  c2.renderVals();
  await later(30);
  const v2 = c2.renderVals();
  ok('сбой -> внятный тост', /не JSON/.test(c2.state.toast || ''), c2.state.toast);
  ok('сбой -> «нет связи»', v2.syncedAt === 'нет связи', v2.syncedAt);
  ok('сбой не рушит рендер', v2.rows.length === 0 && v2.c.id === '—');

  console.log(fails ? '\n=== ПРОВАЛОВ: ' + fails : '\n=== интерфейс: все проверки прошли');
  process.exit(fails ? 1 : 0);
})();
