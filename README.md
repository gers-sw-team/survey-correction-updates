# Обновления Survey Correction

Публичный репозиторий: <https://github.com/gers-sw-team/survey-correction-updates>.
Каталог Advanced Installer Updater: <https://gers-sw-team.github.io/survey-correction-updates/updates.json>.

Исходники приложения находятся в основном проекте. Здесь хранятся генератор каталога, тесты, workflow Pages и русские описания выпусков. Локальная копия поддерживается в `installer/ai-updates/` основного проекта.

## Публикация версии

1. Соберите x64-пакет через `installer/Build-Installer.ps1` в основном проекте.
2. Создайте draft release с числовым тегом, например `1.16.2`, равным ProductVersion и версии JSON. Префикс `v` не используется.
3. Прикрепите ровно один EXE/MSI и сгенерированный вместе с ним `advinst_update.json`.
4. Скопируйте содержимое `release-notes/<версия>.ru.md` в описание GitHub Release и опубликуйте стабильный выпуск.
5. Дождитесь успешных build и deploy в **Build and publish updater feed**. Каталог обновляется по событию релиза, раз в час и вручную.

С 1.16.2 используется x64-установщик. Его JSON должен содержать `RegistryKey: HKLM\Software\GERS Technology\Survey Correction\Version`, `Version`, `Flags: Sys64`, `ServerFileName` и `SHA256`. Имя EXE должно точно совпадать с ServerFileName. При пересборке используйте новый JSON из той же сборки.

Исторические выпуски до 1.16.2 используют прежний установщик и могут не содержать JSON. Они сохраняются в каталоге без изменений. Начиная с 1.16.2 отсутствие JSON останавливает генерацию. Несколько установщиков в одном релизе, дубликаты JSON, незавершённые загрузки и нечисловые теги также отклоняются; предыдущий успешный каталог при ошибке не заменяется.

## Русские описания

Описание 1.16.2: [release-notes/1.16.2.ru.md](release-notes/1.16.2.ru.md). Текст нужно вставить в описание соответствующего GitHub Release; генератор получает его оттуда. Отдельный текстовый файл в Assets не заменяет описание.

Заголовки разделов должны совпадать буквально: `Features`, `Enhancements`, `Bug Fixes`. Пункты пишутся по-русски. Кириллица передаётся через ASCII JSON с Unicode-экранированием, переносы нормализуются в CRLF. Таблицы в описании не используются.

`updates.json` создаётся автоматически в Pages. Отдельно загружать его в Assets не нужно.

## Настройка GitHub Pages

В **Settings → Pages** источник — **GitHub Actions**. Переменная **Settings → Secrets and variables → Actions → Variables → UPDATES_PAGES_ENABLED** должна иметь значение `true`. В окружении `github-pages` разрешите ветку `main` и теги релизов `*.*.*`, если включены ограничения.

Для ручной публикации каталога запустите **Actions → Build and publish updater feed → Run workflow**, ветка `main`, параметр `publish` включён. Проверка после деплоя допускает задержку CDN до 12,5 минуты и фиксирует успех только после совпадения SHA-256 со свежим артефактом.

## Проверки

```powershell
python -m unittest discover -s tests -v
python scripts/build_updates.py --repository gers-sw-team/survey-correction-updates --output-dir _site
```

Стенд `tools/test_updater_lab.ps1` использует отдельную копию Updater и локальный сервер. Он проверяет русский текст через `/justcheck`, не устанавливая программу.

Формат: [Advanced Installer GitHub Updater](https://www.advancedinstaller.com/user-guide/qa-github-updater-integration.html).
