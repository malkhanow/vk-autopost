const fs = require('fs');
const code = fs.readFileSync(require('path').join(__dirname, '..', 'Code.gs'), 'utf8');

const Utilities = {
  formatDate: (d, tz, f) => {
    const p = n => String(n).padStart(2, '0');
    return f.replace('yyyy', d.getFullYear()).replace('MM', p(d.getMonth() + 1))
            .replace('dd', p(d.getDate())).replace('HH', p(d.getHours())).replace('mm', p(d.getMinutes()));
  },
  base64Encode: (s) => Buffer.from(s, 'utf8').toString('base64'),
  Charset: { UTF_8: 'utf8' },
  sleep: () => {}
};
const Session = { getScriptTimeZone: () => 'Europe/Moscow' };
const PROPS = {};
const PropertiesService = { getScriptProperties: () => ({ getProperty: k => PROPS[k] || null }) };
const stub = new Proxy({}, { get: () => () => { throw new Error('not stubbed'); } });

const api = new Function('Utilities','Session','PropertiesService','SpreadsheetApp','UrlFetchApp','LockService','ScriptApp','ContentService','console',
  code + '\nreturn {norm_,pickList_,slotsIn_,slotsOut_,payIn_,payState_,tariffIn_,translit_,uniqueId_,addMonth_,parseDate_,dateOut_,parseJsonLoose_,detectTariff_,briefFromAnswers_,findAnswer_,buildConfig_,normRubrics_,padAnswers_,bool_,num_,list_,lines_,today_,ghError_,LIMITS_KNOWN,TOPICS_KNOWN};')
  (Utilities, Session, PropertiesService, stub, stub, stub, stub, stub, console);

let fails = 0;
const eq = (name, a, b) => {
  const A = JSON.stringify(a), B = JSON.stringify(b);
  if (A !== B) { console.log('FAIL', name, '\n  got     ', A, '\n  expected', B); fails++; }
  else console.log('ok  ', name, '=', A);
};

eq('norm ё', api.norm_(' Ёлка  Дом '), 'елка дом');
eq('slots in labels', api.slotsIn_('Утро, Вечер'), ['morning','evening']);
eq('slots in keys', api.slotsIn_('morning,midday'), ['morning','midday']);
eq('slots out', api.slotsOut_(['morning','evening']), 'Утро, Вечер');
eq('limits with commas', api.pickList_('Цены и стоимость услуг, Проценты, ставки, доходность', api.LIMITS_KNOWN),
   ['Цены и стоимость услуг','Проценты, ставки, доходность']);
eq('limits newline', api.pickList_('Своё ограничение\nЕщё одно', api.LIMITS_KNOWN), ['Своё ограничение','Ещё одно']);
eq('topics', api.pickList_('Фото работ, Отзывы и результаты', api.TOPICS_KNOWN), ['Фото работ','Отзывы и результаты']);
eq('pay in', [api.payIn_('Оплачено'), api.payIn_('Просрочка'), api.payIn_('paid'), api.payIn_('')], ['paid','overdue','paid','brief']);
eq('tariff in', [api.tariffIn_('ПРО'), api.tariffIn_('тариф БИЗНЕС'), api.tariffIn_('')], ['ПРО','БИЗНЕС','СТАРТ']);
eq('translit', api.translit_('Пекарня «Тесто и дело»'), 'pekarnya-testo-i-delo');
eq('uniqueId', api.uniqueId_('beauty-bar', ['beauty-bar','beauty-bar-2']), 'beauty-bar-3');
eq('addMonth 31', api.dateOut_(api.addMonth_(new Date(2026,0,31))), '2026-02-28');
eq('addMonth normal', api.dateOut_(api.addMonth_(new Date(2026,7,26))), '2026-09-26');
eq('parseDate ru', api.dateOut_(api.parseDate_('26.08.2026')), '2026-08-26');
eq('parseDate iso', api.dateOut_(api.parseDate_('2026-08-26')), '2026-08-26');
eq('json loose fence', api.parseJsonLoose_('```json\n{"a":1}\n```').a, 1);
eq('json loose prose', api.parseJsonLoose_('Вот результат: {"rubrics":[{"name":"X"}]} — готово').rubrics[0].name, 'X');
eq('payState overdue', api.payState_('due', new Date(2000,0,1)), 'overdue');
eq('payState brief', api.payState_('brief', null), 'brief');
eq('payState paid future', api.payState_('paid', new Date(2999,0,1)), 'paid');

// тариф по разделу формы
const raw1 = {'Отметка времени':'…','Расскажите про ваш бизнес':'Пекарня','ПРО — 2 поста в день: какие слоты?':'Утро, Вечер'};
const ans1 = {}; for (const k in raw1) ans1[api.norm_(k)] = raw1[k];
eq('tariff by section ПРО', api.detectTariff_(raw1, ans1), 'ПРО');

const raw2 = {'Расскажите про ваш бизнес':'Столярка','О бизнесе':'мебель'};
const ans2 = {}; for (const k in raw2) ans2[api.norm_(k)] = raw2[k];
eq('строчное «про» не тариф', api.detectTariff_(raw2, ans2), 'СТАРТ');

const raw3 = {'Какой тариф выбираете?':'БИЗНЕС — 6900 ₽','СТАРТ: слот':'Утро'};
const ans3 = {}; for (const k in raw3) ans3[api.norm_(k)] = raw3[k];
eq('прямой вопрос про тариф важнее', api.detectTariff_(raw3, ans3), 'БИЗНЕС');

const raw4 = {'СТАРТ — 1 пост в день: слот':'День','БИЗНЕС — 2 поста: слоты':'Утро, День'};
const ans4 = {}; for (const k in raw4) ans4[api.norm_(k)] = raw4[k];
eq('старший тариф при двух разделах', api.detectTariff_(raw4, ans4), 'БИЗНЕС');

// бриф по заголовкам вопросов
const rawB = {
  'Ваше имя (ФИО)':'Данила Кириллов','Телефон для связи':'+7 921 000-00-00',
  'Ваш Telegram':'@testoidelo','Название бизнеса':'Пекарня «Тесто и дело»',
  'Ниша / сфера деятельности':'пекарня','Город':'Санкт-Петербург',
  'Расскажите о бизнесе':'Хлеб на закваске','Кто ваши клиенты':'Жители района',
  'В каких соцсетях публиковать?':'VK, Telegram',
  'О чём нельзя писать?':'Цены и стоимость услуг, Гарантии результата',
  'Есть фото работ?':'Да','Когда публиковать?':'Утро'
};
const ansB = {}; for (const k in rawB) if (rawB[k]) ansB[api.norm_(k)] = rawB[k];
const b = api.briefFromAnswers_(ansB);
eq('brief name', b.name, 'Данила Кириллов');
eq('brief business', b.business, 'Пекарня «Тесто и дело»');
eq('brief niche', b.niche, 'пекарня');
eq("brief networks", b.networks, ["VK","Telegram"]);
eq('brief limits', b.limits, ['Цены и стоимость услуг','Гарантии результата']);
eq('brief slots', b.slots, ['morning']);
eq('brief hasPhoto', b.hasPhoto, true);

// конфиг
const cfg = api.buildConfig_({id:'x',business:'B',city:'СПб',tariff:'ПРО',networks:['VK','Telegram'],
  slots:['morning','evening'],stylePrompt:'',tone:'T',holidays:'H',limits:['Цены и стоимость услуг'],
  limitsText:'ещё',cta:'C',faq:'a\nb',rubrics:[{name:'R',days:'пн, чт',prompt:'P'}],iterations:2});
eq('cfg networks', cfg.networks, ['vk','telegram']);
eq('cfg slots', cfg.slots, [{name:'morning',cron:'07:00'},{name:'evening',cron:'16:00'}]);
eq('cfg forbidden', cfg.forbidden, ['Цены и стоимость услуг','ещё']);
eq('cfg faq', cfg.faq, ['a','b']);
eq('cfg rubric days', cfg.rubrics[0].days, ['пн','чт']);
eq('cfg style null', cfg.style_prompt, null);

console.log(fails ? '\n=== ПРОВАЛОВ: ' + fails : '\n=== все проверки прошли');
process.exit(fails ? 1 : 0);
