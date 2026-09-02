const { source, googleStubs } = require('./gs');
const code = source();

const PROPS = {};
const { Utilities, Session, PropertiesService, CacheService } = googleStubs(PROPS);
const stub = new Proxy({}, { get: () => () => { throw new Error('not stubbed'); } });

const api = new Function('Utilities','Session','PropertiesService','CacheService','SpreadsheetApp','UrlFetchApp','LockService','ScriptApp','ContentService','console',
  code + '\nreturn {norm_,pickList_,slotsIn_,slotsOut_,payIn_,payState_,tariffIn_,translit_,uniqueId_,addMonth_,parseDate_,dateOut_,parseJsonLoose_,detectTariff_,briefFromAnswers_,findAnswer_,buildConfig_,normRubrics_,padAnswers_,bool_,num_,list_,lines_,today_,ghError_,mdEscape_,LIMITS_KNOWN,TOPICS_KNOWN,textIsComplete_,trimToSentence_,stripModelNoise_,rubricKind_,rubricFormat_,normalizeTariff_,tariffName_,tariffPrice_};')
  (Utilities, Session, PropertiesService, CacheService, stub, stub, stub, stub, stub, console);

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
eq('статусы дропдауна', ['Оплачено 1 мес','Оплачено 12 мес','Ожидает оплаты','Просрочено','Отменить оплату'].map(api.payIn_),
   ['paid','paid','due','overdue','due']);
eq('лист главнее даты: просрочено', api.payState_('overdue', new Date(2999,0,1)), 'overdue');
eq('оплачено, но дата прошла', api.payState_('paid', new Date(2000,0,1)), 'overdue');
eq('пустой статус без даты', api.payState_('brief', null), 'brief');
eq('markdown экранируется', api.mdEscape_('Новый бриф: @olga_spb_realty *тест*'), 'Новый бриф: @olga\\_spb\\_realty \\*тест\\*');
// «ПРО» — алиас: реестр нормализует его в отображаемое имя тарифа «ПРОФИ»
eq('tariff in', [api.tariffIn_('ПРО'), api.tariffIn_('тариф БИЗНЕС'), api.tariffIn_('')], ['ПРОФИ','БИЗНЕС','СТАРТ']);
eq('ПРОФИ и ПРО — один тариф', api.normalizeTariff_('ПРО'), api.normalizeTariff_('ПРОФИ'));
eq('новые тарифы понимаются', [api.tariffIn_('ПРЕМИУМ'), api.tariffIn_('МАКСИМУМ')], ['ПРЕМИУМ','МАКСИМУМ']);
eq('цена берётся из реестра', api.tariffPrice_(api.normalizeTariff_('СТАРТ')), 2400);
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
// старая Google Форма до сих пор называет раздел «ПРО» — алиас обязан работать
eq('tariff by section ПРО -> ПРОФИ', api.detectTariff_(raw1, ans1), 'ПРОФИ');

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
eq('cfg канал клиента', cfg.tg_channel, null);

// без active скрипт постинга считает клиента включённым по умолчанию:
// тумблер в CRM тогда не остановил бы публикации
const off = api.buildConfig_({ id: 'x', tariff: 'ПРО', active: false });
eq('cfg выключенный клиент', off.active, false);
const on = api.buildConfig_({ id: 'x', tariff: 'ПРО', active: true });
eq('cfg включённый клиент', on.active, true);

// поля, которые читает clients_post.py
const full = api.buildConfig_({ id: 'x', tariff: 'ПРО', active: true,
  tgChannel: '@ch', morningPhoto: true, holidaysExtra: '08.02 День риелтора' });
eq('cfg tg_channel', full.tg_channel, '@ch');
eq('cfg morning_photo', full.morning_photo, true);
eq('cfg holidays_extra', full.holidays_extra, '08.02 День риелтора');

console.log(fails ? '\n=== ПРОВАЛОВ: ' + fails : '\n=== все проверки прошли');
process.exit(fails ? 1 : 0);
