/**
 * Проверки бэкенда и интерфейса без Google и без сети:
 * Apps Script и браузерное окружение подменяются заглушками.
 *
 *   node crm/tests/run.js
 */
const { execFileSync } = require('child_process');
const path = require('path');

const suites = [
  ['pure.test.js', 'разбор значений, тариф по разделу формы, конфиг'],
  ['sheet.test.js', 'таблица: создание, сохранение, оплата, форма, лог'],
  ['github-ai.test.js', 'GitHub Contents API и RouterAI'],
  ['ui.test.js', 'интерфейс: загрузка и все действия через Web App'],
  ['build.test.js', 'собранная страница не отстаёт от холста']
];

let failed = 0;
suites.forEach(([file, title]) => {
  console.log('\n──── ' + title + ' (' + file + ')');
  try {
    execFileSync(process.execPath, [path.join(__dirname, file)], { stdio: 'inherit' });
  } catch (e) {
    failed++;
  }
});
console.log(failed ? '\nПровалено наборов: ' + failed : '\nВсе наборы прошли');
process.exit(failed ? 1 : 0);
