const fs = require('fs');
const code = fs.readFileSync(require('path').join(__dirname, '..', 'Code.gs'), 'utf8');
const Utilities = { formatDate: (d,t,f)=>'24.08 · 12:00', base64Encode: (s)=>Buffer.from(s,'utf8').toString('base64'), Charset:{UTF_8:'utf8'}, sleep:()=>{} };
const Session = { getScriptTimeZone: () => 'Europe/Moscow' };
const PROPS = { GITHUB_TOKEN: 'ghp_x', GITHUB_REPO: 'malkhanow/vk-autopost', GITHUB_BRANCH: 'main', ROUTERAI_KEY: 'sk-or-x' };
const PropertiesService = { getScriptProperties: () => ({ getProperty: k => (k in PROPS ? PROPS[k] : null) }) };
let route = {};
const log = [];
const UrlFetchApp = { fetch: (url, o) => {
  log.push({ url, method: (o.method||'get').toLowerCase(), payload: o.payload ? JSON.parse(o.payload) : null, headers: o.headers });
  const key = Object.keys(route).find(k => url.indexOf(k) >= 0);
  const r = route[key] || { code: 200, body: {} };
  return { getResponseCode: () => r.code, getContentText: () => JSON.stringify(r.body) };
} };
const stub = new Proxy({}, { get: () => () => { throw new Error('нет мока'); } });
const api = new Function('Utilities','Session','PropertiesService','UrlFetchApp','SpreadsheetApp','LockService','ScriptApp','ContentService','console',
  code + '\nreturn {ghPushConfig_, ai_, parseJsonLoose_, buildConfig_, pingAll_};')
  (Utilities, Session, PropertiesService, UrlFetchApp, stub, stub, stub, stub, console);

let fails = 0;
const ok = (n,c,i)=>{ if(c) console.log('ok  ',n); else { console.log('FAIL',n,i===undefined?'':JSON.stringify(i)); fails++; } };
const CL = { id:'beauty-bar', business:'Beauty BAR', city:'СПб', tariff:'ПРО', networks:['VK','Telegram'],
  slots:['morning','evening'], stylePrompt:'Коротко', tone:'T', holidays:'H', limits:['Цены и стоимость услуг'],
  limitsText:'', cta:'Записаться', faq:'a\nb', rubrics:[{name:'Фото работ',days:'пн, чт',prompt:'P'}], iterations:1 };

/* обновление существующего файла */
log.length = 0;
route = {
  '/contents/clients/beauty-bar.json?ref=': { code: 200, body: { sha: 'old111' } },
  '/contents/clients/beauty-bar.json':      { code: 200, body: { commit: { sha: 'newsha1234567' } } },
  '/dispatches':                            { code: 204, body: {} }
};
let res = api.ghPushConfig_(CL, true);
ok('GET sha -> PUT -> dispatch', log.length === 3 && log[0].method === 'get' && log[1].method === 'put' && log[2].method === 'post', log.map(l=>l.method));
ok('sha предыдущего файла передан', log[1].payload.sha === 'old111');
ok('ветка в теле PUT', log[1].payload.branch === 'main');
ok('заголовок авторизации', log[1].headers.Authorization === 'Bearer ghp_x');
const decoded = JSON.parse(Buffer.from(log[1].payload.content, 'base64').toString('utf8'));
ok('base64 -> валидный JSON конфига', decoded.client_id === 'beauty-bar' && decoded.slots[1].cron === '16:00', decoded.slots);
ok('кириллица в base64 не побилась', decoded.business === 'Beauty BAR' && decoded.cta === 'Записаться');
ok('dispatch с client_id', log[2].payload.inputs.client_id === 'beauty-bar' && log[2].payload.ref === 'main');
ok('результат: sha и статус', res.sha === 'newsha1' && res.prevSha === 'old111' && res.dispatched === true, res);

/* первый коммит: файла ещё нет */
log.length = 0;
route['/contents/clients/beauty-bar.json?ref='] = { code: 404, body: { message: 'Not Found' } };
route['/contents/clients/beauty-bar.json'] = { code: 201, body: { commit: { sha: 'first99999' } } };
res = api.ghPushConfig_(CL, true);
ok('404 -> создание без sha', log[1].payload.sha === undefined && res.created === true);

/* воркфлоу без input client_id */
log.length = 0;
let dispatchCalls = 0;
route['/dispatches'] = { code: 422, body: { message: 'Unexpected inputs' } };
const realFetch = UrlFetchApp.fetch;
UrlFetchApp.fetch = (url, o) => {
  if (url.indexOf('/dispatches') >= 0) { dispatchCalls++; route['/dispatches'] = { code: dispatchCalls === 1 ? 422 : 204, body: {} }; }
  return realFetch(url, o);
};
res = api.ghPushConfig_(CL, true);
ok('422 -> повтор без inputs', dispatchCalls === 2 && res.dispatched === true, { dispatchCalls, res });
UrlFetchApp.fetch = realFetch;

/* воркфлоу не найден — коммит всё равно успешен */
log.length = 0;
route['/dispatches'] = { code: 404, body: { message: 'Not Found' } };
res = api.ghPushConfig_(CL, true);
ok('404 воркфлоу не рушит коммит', res.sha === 'first99' && res.dispatched === false && /не найден/.test(res.workflowNote), res.workflowNote);

/* ошибка PUT -> исключение с текстом GitHub */
route['/contents/clients/beauty-bar.json'] = { code: 409, body: { message: 'is at 111 but expected 222' } };
try { api.ghPushConfig_(CL, false); ok('конфликт PUT -> ошибка', false); }
catch (e) { ok('конфликт PUT -> ошибка', /HTTP 409/.test(e.message) && /expected 222/.test(e.message), e.message); }

/* RouterAI */
log.length = 0;
route = { 'routerai.ru': { code: 200, body: { choices: [{ message: { content: '```json\n{"style_prompt":"Коротко и по делу"}\n```' } }] } } };
const out = api.ai_([{ role: 'user', content: 'привет' }], { json: true });
ok('RouterAI: модель по умолчанию', log[0].payload.model === 'google/gemini-3.1-flash-lite', log[0].payload.model);
ok('RouterAI: адрес', log[0].url === 'https://routerai.ru/api/v1/chat/completions');
ok('RouterAI: ключ в заголовке, не в теле', log[0].headers.Authorization === 'Bearer sk-or-x' && !JSON.stringify(log[0].payload).includes('sk-or-x'));
ok('RouterAI: json-режим', log[0].payload.response_format.type === 'json_object');
ok('разбор ответа в ```json', api.parseJsonLoose_(out).style_prompt === 'Коротко и по делу');

/* повтор при 429 */
log.length = 0;
let n = 0;
UrlFetchApp.fetch = (url, o) => { n++; log.push({url}); return { getResponseCode: () => (n < 3 ? 429 : 200), getContentText: () => JSON.stringify({ choices: [{ message: { content: 'готово' } }] }) }; };
ok('429 -> повтор до успеха', api.ai_([{role:'user',content:'x'}]) === 'готово' && n === 3, n);

/* 401 -> сразу ошибка с текстом */
n = 0;
UrlFetchApp.fetch = () => ({ getResponseCode: () => 401, getContentText: () => JSON.stringify({ error: { message: 'Invalid API key' } }) });
try { api.ai_([{role:'user',content:'x'}]); ok('401 -> ошибка', false); }
catch (e) { ok('401 -> понятная ошибка', /HTTP 401/.test(e.message) && /Invalid API key/.test(e.message), e.message); }

console.log(fails ? '\n=== ПРОВАЛОВ: ' + fails : '\n=== GitHub и RouterAI: все проверки прошли');
process.exit(fails ? 1 : 0);
