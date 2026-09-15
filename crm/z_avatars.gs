// ═══════════════════════════════════════════════════════════════
//  АВАТАРКИ КЛИЕНТОВ — без Google Drive
//  Файл: z_avatars.gs
//  (z_ обеспечивает выполнение ПОСЛЕ Code.gs, где объявлен POST_ACTIONS)
//
//  Фото хранится data-URL'ом прямо в ячейке «Аватар» листа «Клиенты».
//  Никаких разрешений на Drive не нужно — только доступ к таблице.
//  Ограничение Google Sheets: 50 000 символов в ячейке, поэтому
//  интерфейс сжимает картинку до ~160 px перед отправкой.
//
//  КАК ПОДКЛЮЧИТЬ:
//  1. В Apps Script добавьте этот файл (он уже называется z_avatars.gs)
//  2. В Code.gs добавьте в объект F:         avatar: 'Аватар',
//  3. В Code.gs добавьте в HEAD_CLIENTS_EXTRA: 'Аватар',
//  4. В Code.gs добавьте в rowToClient_():    avatar: str_(val_(t, row, 'avatar')),
//  5. В Code.gs добавьте в TO_CELL:           avatar: function(v) { return str_(v); },
//  6. Запустите setupSheets() один раз из редактора Apps Script
//     (добавит колонку «Аватар» в лист «Клиенты»)
// ═══════════════════════════════════════════════════════════════

/** Потолок на ячейку: у Sheets это 50 000 символов, берём с запасом. */
var AVATAR_MAX_CHARS = 45000;

/**
 * action=upload_avatar
 *
 * Body: { client_id, image }
 *   image — строка "data:image/jpeg;base64,..."
 *
 * Пишет строку в колонку «Аватар» листа «Клиенты», возвращает { url }
 * (тот же data-URL — интерфейс показывает его напрямую в <img src>).
 */
POST_ACTIONS['upload_avatar'] = function (req) {
  var clientId = str_(req.client_id);
  if (!clientId) throw new Error('Не передан client_id');

  var dataUrl = String(req.image || '');
  if (!dataUrl) throw new Error('Не передано изображение');
  if (!/^data:image\/[A-Za-z0-9+.\-]+;base64,/.test(dataUrl)) {
    throw new Error('Неверный формат изображения (ожидается data:image/…;base64,…)');
  }
  if (dataUrl.length > AVATAR_MAX_CHARS) {
    throw new Error('Фото слишком большое для ячейки: ' + Math.round(dataUrl.length / 1024) +
      ' КБ, максимум ' + Math.round(AVATAR_MAX_CHARS / 1024) + ' КБ');
  }

  var t = clientsTable_();
  var row = findSheetRow_(t, 'id', clientId);
  if (!row) throw new Error('Клиент «' + clientId + '» не найден в листе');

  var col = colIndex_(t, 'avatar');
  if (col < 0) throw new Error('Нет колонки «Аватар» — добавьте её в F / HEAD_CLIENTS_EXTRA и запустите setupSheets()');

  // setValue со строкой, начинающейся не с «=», Sheets формулой не считает
  t.sheet.getRange(row, col + 1).setValue(dataUrl);
  SpreadsheetApp.flush();

  return { url: dataUrl };
};

/**
 * action=delete_avatar — очищает ячейку «Аватар».
 * Body: { client_id }
 */
POST_ACTIONS['delete_avatar'] = function (req) {
  var clientId = str_(req.client_id);
  if (!clientId) throw new Error('Не передан client_id');

  var t = clientsTable_();
  var row = findSheetRow_(t, 'id', clientId);
  if (!row) throw new Error('Клиент «' + clientId + '» не найден в листе');

  var col = colIndex_(t, 'avatar');
  if (col < 0) throw new Error('Нет колонки «Аватар»');

  t.sheet.getRange(row, col + 1).clearContent();
  SpreadsheetApp.flush();

  return { ok: true };
};
