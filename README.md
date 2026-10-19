# cc-autohandoff

Handoff для **Codex** и **Claude Code** без compact.

При достижении **120 000 токенов текущего контекста** плагин останавливает новую работу
на ближайшей границе hooks. Отдельная младшая модель готовит передачу:

| Среда | Модель |
| --- | --- |
| Codex | `gpt-6-luna` |
| Claude Code | `claude-haiku-5-5` |

В чате появляются ссылки на `HANDOFF.md` и `START-NEXT-SESSION.txt`.
Открой новый чат в том же проекте и вставь текст из второго файла.
При ошибке доступны диагностика и повтор; подмена модели запрещена.

## Установка

Нужны Python **3.11+** и авторизованный CLI соответствующей среды.
Для Claude Code команда `python` должна быть доступна в `PATH`.

**Claude Code** — из корня репозитория:

```sh
claude --plugin-dir .
```

**Codex desktop** — добавь локальный marketplace:

```sh
codex plugin marketplace add .
```

Перезапусти приложение, установи `cc-autohandoff` из этого marketplace и доверь его hooks.
Не включай одновременно старый `codex-handoff` и этот плагин.

## Разработка

Основан на пользовательском `codex-handoff` **0.1.1**.
Настройки: [handoff-settings.json](handoff-settings.json).
Проверки: `python -m unittest discover -s tests -v`.

Адаптер Claude Code покрыт локальными тестами; живой запуск Haiku ещё не проверен.
Детали и ограничения: [архитектура](docs/architecture.md).
