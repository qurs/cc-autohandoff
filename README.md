# cc-autohandoff

Handoff для **Codex** и **Claude Code** без compact.

При достижении **120 000 токенов текущего контекста** плагин останавливает новую работу
на ближайшей границе hooks. Отдельная младшая модель готовит передачу:

| Среда | Модель |
| --- | --- |
| Codex | `gpt-6-luna` |
| Claude Code | `claude-haiku-5-5` |

В чате появляются ссылки на `HANDOFF.md` и `START-NEXT-SESSION.txt`.
В **Codex desktop** по умолчанию создаётся один новый чат в том же локальном проекте:
он получает точный текст второго файла, затем приложение открывает его, а старый чат
останавливается. Для этого основной модели нужны инструменты приложения
`list_projects`, `create_thread` и `navigate_to_codex_page`.
В **Claude Code** открой новый чат вручную и вставь текст из второго файла.
При ошибке доступны диагностика и повтор; подмена модели запрещена.

## Режим продолжения

В `handoff-settings.json` настройка `continuation.codex` принимает `codex-desktop`
или `manual`; `continuation.claude` поддерживает только `manual`.
Без секции `continuation` обе среды используют ручную передачу. Настройка desktop
разрешает плагину создать чат после готовности handoff. Отдельный процесс hook
не вызывает приложение: он передаёт основной модели точные инструкции и разрешает
только соответствующие вызовы приложения с проверенными аргументами.

Если инструментов нет или проект не найден по точному абсолютному пути, автопереход
завершается с диагностикой, а файлы остаются доступны для ручного продолжения.
После потери ответа создания плагин не пытается создать чат заново.

Состояние и явный повтор доставки без повторного writer:

```powershell
& '<каталог установленного плагина>/bin/cc-autohandoff.exe' status --session-dir '<абсолютный каталог handoff>'
& '<каталог установленного плагина>/bin/cc-autohandoff.exe' retry-continuation --session-dir '<абсолютный каталог handoff>'
```

Если результат создания неизвестен, сначала проверь список чатов. Только убедившись,
что продолжение не создано, добавь `--confirm-no-chat`. После успешного retry отправь
в **старый** чат строку `CODEX_HANDOFF_DELIVER:<имя каталога handoff>`: hook выдаст
инструкции для новой попытки. Retry сам не создаёт чат. Если чат создан, но открыть его
не удалось, используй карточку чата; повтор доставки разрешает лишь открытие этого же чата.

## Удалённая установка Windows x64

Python устанавливать не нужно: пакет содержит `bin/cc-autohandoff.exe` и его библиотеки.
Нужен установленный и авторизованный CLI соответствующей среды с доступом к фиксированной
модели writer. Для Claude Code нужна версия **2.1.295+** с `onFailure: block`, exec-form hooks
и блокировкой `PreCompact`. Другие ОС и Windows ARM64 готовым пакетом пока не поддерживаются.

Marketplace находится в GitHub: `qurs/cc-autohandoff`. Он ссылается на готовый пакет
в ветке `windows-x64` того же репозитория. Менеджер плагинов сам получает пакет через Git;
скачивать архив, клонировать исходники и собирать плагин вручную не нужно.

**Codex** — выполни в терминале:

```powershell
codex plugin marketplace add qurs/cc-autohandoff
codex plugin add cc-autohandoff@cc-autohandoff
```

Вместо `qurs/cc-autohandoff` можно указать `https://github.com/qurs/cc-autohandoff.git`.
После установки перезапусти Codex desktop. Проверь и доверь текущее определение hooks,
затем открой новый чат.
Само включение плагина не означает доверия к hooks.
[Официальная инструкция Codex](https://developers.openai.com/plugins/build/plugins).

**Claude Code** — выполни в терминале:

```powershell
claude plugin marketplace add qurs/cc-autohandoff
claude plugin install cc-autohandoff@cc-autohandoff
```

[Официальная инструкция Claude Code](https://code.claude.com/docs/en/plugin-marketplaces).
Внутри Claude Code те же команды доступны как `/plugin marketplace add qurs/cc-autohandoff`
и `/plugin install cc-autohandoff@cc-autohandoff`.
Не включай одновременно старый `codex-handoff` и этот плагин.

Команда `retry` из уведомления восстанавливает writer после ошибки и использует встроенный
исполняемый файл. `status` и `retry` определяют платформу по `session.json`.
Настройки меняются в `handoff-settings.json` установленного экземпляра; изменение локального
источника требует обновления установленной копии через менеджер плагинов.

## Разработка

Основан на пользовательском `codex-handoff` **0.1.1**.
Настройки: [handoff-settings.json](handoff-settings.json).
Исходные hooks требуют Python **3.11+**, runtime использует только стандартную библиотеку.
Для разработки исходный плагин Claude Code запускается через `claude --plugin-dir .`.
В исходной версии вместо `bin/cc-autohandoff.exe` используй `python scripts/handoff.py`.

Сборка на Windows x64 из корня репозитория:

```powershell
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests -v
python scripts/build_plugin.py
python scripts/check_package.py dist/plugin
```

Сборщик создаёт новый каталог `dist/plugin` и не перезаписывает существующий.
Для повторной сборки укажи новый `--output dist/plugin-next`.
Для локальной проверки разработчик может добавить `dist/plugin` как marketplace;
пользователи устанавливают опубликованный пакет командами выше.
В пакет попадают файлы из явного списка, bundled runtime, лицензии Python/PyInstaller
и SHA-256 manifest. Переписки, handoff и логи исключены из Git и состава пакета.

`check_package.py` проверяет состав и checksums, затем копирует пакет в путь с кириллицей
и пробелами и запускает обе конфигурации hooks без Python в `PATH`. Проверяет порог,
ошибку отсутствующего writer, запрет compact, повторные hooks, status/retry и защиту от
рекурсии. Это синтетическая проверка исполняемого пакета без обращения к моделям.
Для проверки только файлов доступен `--static-only`.

Workflow `.github/workflows/package.yml` проверяет и собирает пакет. После push в `master`
или ручного запуска из `master` он публикует проверенный artifact в ветку `windows-x64`.
Pull request только проверяется и собирается. Публикация идёт обычным push без force:
в ветке пакета находятся только разрешённые файлы сборки, runtime, лицензии и checksums.
Каталоги marketplace в `master` используют Git URL и `ref: windows-x64`; в готовом пакете
оба каталога ссылаются на его корень. `dist/` в ветку исходников не коммитится.

Адаптер Claude Code покрыт локальными тестами; живой запуск Haiku ещё не проверен.
Автопереход desktop покрыт тестами с подменёнными ответами приложения; полный живой
цикл создания и открытия чата пока не проверен.
Детали и ограничения: [архитектура](docs/architecture.md).
