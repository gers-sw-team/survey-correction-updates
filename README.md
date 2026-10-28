# Обновления Survey Correction

Создаёт `updates.json` из GitHub Releases и публикует его через GitHub Pages. Этот файл позволяет updater правильно отображать русский текст.

## Первый запуск

1. В **Settings → Pages** выбрать источник **GitHub Actions**.
2. В **Settings → Secrets and variables → Actions → Variables** создать переменную `UPDATES_PAGES_ENABLED` со значением `true`.
3. Если для окружения `github-pages` установлены ограничения, разрешить публикацию из `main` и тегов релизов.
4. В **Actions** запустить **Build and publish updater feed**: ветка `main`, опция `publish` включена.

После настройки каталог обновляется автоматически при изменении релизов и раз в час.

## Использование

Описания релизов пишутся обычным русским текстом. Заголовки разделов остаются английскими: `Features`, `Enhancements`, `Bug Fixes`.

Адрес каталога после публикации:

```text
https://gers-sw-team.github.io/survey-correction-updates/updates.json
```

Указать этот адрес в настройках updater в Advanced Installer, сохранив включённой **GitHub integration**. Новый адрес доставляется пользователям следующим выпуском SC.
