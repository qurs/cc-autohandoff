---
name: handoff
description: Просмотр и повтор handoff в Codex или Claude Code, ручной запуск writer и восстановление доставки в Codex desktop.
---

Hooks `cc-autohandoff` готовят handoff отдельной **gpt-6-luna** в Codex или
**claude-haiku-5-5** в Claude Code. Не пиши резюме вместо writer, не заменяй его модель,
не вызывай compact или fork. После порога не продолжай исходную задачу.

Уведомление hook содержит каталог и команду retry. Для сбоя writer выполни эту команду
один раз. Просмотр: `python <plugin-root>/scripts/handoff.py status --session-dir <directory>`.
Ручная генерация: `python <plugin-root>/scripts/handoff.py --platform codex generate --transcript <absolute-jsonl-path> --workspace <absolute-workspace>`;
для Claude Code укажи `--platform claude`, на macOS/Linux при необходимости `python3`.
Корень плагина расположен на два уровня выше каталога этого навыка. Пути с пробелами
заключай в кавычки. Status и retry восстанавливают платформу из метаданных сессии.

После готовности дай абсолютные кликабельные ссылки на `HANDOFF.md` и
`START-NEXT-SESSION.txt`. В Claude Code и режиме `manual` пользователь сам открывает
новый чат и вставляет стартовый текст. В Codex desktop при включённом `codex-desktop`
разрешены только запрошенные hook операции доставки через `list_projects`,
`create_thread`, `navigate_to_codex_page`, с точными аргументами hook. Не используй shell,
App Server, fork или отдельную рабочую CLI-сессию вместо инструментов приложения.
После подтверждения создания выведи карточку `::created-thread{threadId="..."}`;
для очереди используй `clientThreadId`, не передавая его инструменту открытия.

При сбое доставки сообщи ошибку и сохрани ссылки. Явный повтор:
`python <plugin-root>/scripts/handoff.py retry-continuation --session-dir <directory>`.
Если создание могло выполниться, сначала проверь список чатов; только после проверки
отсутствия продолжения допустим `--confirm-no-chat`. Не повторяй создание с известным
`threadId` или `clientThreadId`. После retry пользователь отправляет в старый чат
`CODEX_HANDOFF_DELIVER:<имя каталога handoff>`. Команда сама не создаёт чат.

При сбое writer сообщи ошибку и команду retry. Не выдавай исходную переписку за готовый
handoff. После доставки остановись.
