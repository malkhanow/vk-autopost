/**
 * Собранная страница не должна отставать от холста.
 *
 * index.html генерируется из «Клиенты.dc.html» скриптом build_page.py.
 * Если холст поправили, а сборку не запустили, на GitHub Pages уедет
 * старая версия — этот набор ловит такой случай.
 */
const fs = require('fs');
const path = require('path');

const CRM = path.join(__dirname, '..');
const canvas = fs.readFileSync(path.join(CRM, 'Клиенты.dc.html'), 'utf8');
const page = fs.readFileSync(path.join(CRM, 'index.html'), 'utf8');
const runtime = fs.readFileSync(path.join(CRM, 'page_runtime.js'), 'utf8');

let fails = 0;
const ok = (name, cond, info) => {
  if (cond) console.log('ok  ', name);
  else { console.log('FAIL', name, info === undefined ? '' : JSON.stringify(info)); fails++; }
};

const logic = /<script type="text\/x-dc"[^>]*>\n([\s\S]*)\n<\/script>/.exec(canvas)[1];
const markup = canvas.slice(canvas.indexOf('</helmet>') + 9, canvas.indexOf('</x-dc>')).trim();

ok('логика холста попала в страницу целиком', page.includes(logic),
   'запустите python3 crm/build_page.py');
ok('разметка холста попала в страницу целиком', page.includes(markup),
   'запустите python3 crm/build_page.py');
ok('рантайм встроен', page.includes(runtime.trim()),
   'запустите python3 crm/build_page.py');

/*
 * Каждое поле {{ c.X }} из разметки должно быть в объекте c, который
 * renderVals отдаёт шаблону. Объект собирается вручную, поле за полем, и
 * забытый ключ выглядит как молча пустой инпут: пользователь печатает,
 * значение уходит в состояние, но обратно в поле не возвращается и
 * стирается на ближайшей перерисовке. На этом уже теряли tgChannel и
 * styleSourceText, поэтому проверяем списком.
 */
const cObject = /\n      c: \{\n([\s\S]*?)\n      \},/.exec(logic);
ok('объект c найден в renderVals', !!cObject);
if (cObject) {
  const declared = new Set(
    (cObject[1].match(/(\w+)\s*:/g) || []).map(m => m.replace(/\s*:$/, ''))
  );
  const used = new Set(
    (markup.match(/\{\{\s*c\.(\w+)/g) || []).map(m => m.replace(/\{\{\s*c\./, ''))
  );
  const missing = [...used].filter(k => !declared.has(k)).sort();
  ok('все поля {{ c.* }} из разметки объявлены в объекте c',
     missing.length === 0, missing);
}

ok('от формата холста ничего не осталось',
   !/<x-dc|<helmet|data-dc-script|src="\.\/support\.js"/.test(page));
ok('разметка лежит в <template>', page.includes('<template id="crm-template">'));
ok('страница монтируется', /mountPage\(\s*\n\s*document\.getElementById\('crm-template'\)/.test(page));
ok('DCLogic определён рантаймом', runtime.includes('global.DCLogic = Logic'));
ok('внешних скриптов нет', !/<script[^>]+src=/.test(page));

console.log(fails ? '\n=== сборка страницы: провалов ' + fails
                  : '\n=== сборка страницы: все проверки прошли');
process.exit(fails ? 1 : 0);
