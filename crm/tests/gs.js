/**
 * Загрузка бэкенда для тестов.
 *
 * В проекте Apps Script файлы склеиваются в ОДНУ область видимости, поэтому
 * Code.gs свободно зовёт normalizeTariff_, tariffLimits_ и planTariffChange_
 * из tariffs.gs. Тесты же читали только Code.gs — и все наборы падали на
 * «normalizeTariff_ is not defined», хотя в бою всё работало. Здесь порядок
 * тот же, что в редакторе: сначала реестр тарифов, затем основной файл.
 */
const fs = require('fs');
const path = require('path');

const CRM = path.join(__dirname, '..');

/** Исходник бэкенда одной строкой. patch — правки под конкретный набор. */
function source(patch) {
  const tariffs = fs.readFileSync(path.join(CRM, 'tariffs.gs'), 'utf8');
  let code = fs.readFileSync(path.join(CRM, 'Code.gs'), 'utf8');
  if (typeof patch === 'function') code = patch(code);
  return tariffs + '\n' + code;
}

/**
 * Заглушки Google-сервисов, общие для всех наборов.
 * props — объект со значениями Script Properties; тесты его же и правят.
 */
function googleStubs(props) {
  const PROPS = props || {};
  const Utilities = {
    formatDate: (d, tz, f) => {
      const p = n => String(n).padStart(2, '0');
      return f.replace('yyyy', d.getFullYear()).replace('MM', p(d.getMonth() + 1))
              .replace('dd', p(d.getDate())).replace('HH', p(d.getHours()))
              .replace('mm', p(d.getMinutes()));
    },
    base64Encode: (s) => Buffer.from(s, 'utf8').toString('base64'),
    base64Decode: (s) => Buffer.from(s, 'base64'),
    newBlob: (b) => ({ getDataAsString: () => Buffer.from(b).toString('utf8') }),
    Charset: { UTF_8: 'utf8' },
    sleep: () => {}
  };
  const Session = { getScriptTimeZone: () => 'Europe/Moscow' };
  // setProperty нужен nextClientNumber_ (счётчик CLIENT_NUMBER_SEQ): без него
  // набор про таблицу падал на «props.setProperty is not a function».
  const PropertiesService = {
    getScriptProperties: () => ({
      getProperty: k => (k in PROPS ? PROPS[k] : null),
      setProperty: (k, v) => { PROPS[k] = String(v); },
      deleteProperty: k => { delete PROPS[k]; }
    })
  };
  // Кэш реестра тарифов. Пустой: каждый набор должен видеть одно и то же.
  const CacheService = {
    getScriptCache: () => ({ get: () => null, put: () => {}, remove: () => {} })
  };
  return { Utilities, Session, PropertiesService, CacheService, PROPS };
}

/** Заглушка браузерного окружения для холста (Esc вешается на document). */
function domStubs() {
  const listeners = {};
  const document = {
    addEventListener: (type, fn) => {
      (listeners[type] = listeners[type] || []).push(fn);
    },
    removeEventListener: () => {},
    createElement: () => ({ style: {}, click: () => {}, appendChild: () => {} }),
    body: { appendChild: () => {}, removeChild: () => {} },
    getElementById: () => null,
    activeElement: null
  };
  /** Отправить событие в обработчики, навешанные страницей. */
  const fire = (type, event) => (listeners[type] || []).forEach(fn => fn(event));
  return { document, fire };
}

module.exports = { CRM, source, googleStubs, domStubs };
