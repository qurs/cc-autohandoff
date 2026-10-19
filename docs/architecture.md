# Устройство и ограничения

Начальная версия перенесена из установленного пользовательского плагина
`codex-handoff` 0.1.1. Общая машина состояний сохранена:
`monitoring → generating → ready | failed`. Retry явно переводит `failed` в `generating`.
Блокировка файла предотвращает одновременный запуск нескольких writers одной сессии.

## Две среды

Один Python CLI обслуживает обе среды; `--platform codex|claude` выбирает формат transcript,
модель и CLI writer. По умолчанию используется Codex. При `status` и `retry` среда
восстанавливается из `session.json`.

Codex читает последнее `last_token_usage` из `event_msg/token_count` либо
`token_usage_record.payload.usage`. Накопленные `total_token_usage` не используются.
Claude Code читает usage последнего основного сообщения `assistant`: input, output,
cache creation и cache read. Они складываются внутри одного ответа, не между ответами.
Неполная строка JSONL перечитывается следующим hook. Без usage порог не считается
подтверждённым; пользователь видит предупреждение.

Hooks: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `PreCompact`.
После порога `PreToolUse` отклоняет новые инструменты, `UserPromptSubmit` блокирует новую
работу, `PostToolUse` передаёт инструкции модели. `Stop` может один раз попросить модель
выдать ссылки, затем завершает ход. В desktop-режиме дополнительно разрешены ограниченные
операции доставки, описанные ниже. Уже запущенные инструменты и фоновые процессы
не прерываются. Порог проверяется на границах hooks; точная остановка на токене 120 000
внутри генерации не гарантируется.

В Codex `PreCompact` возвращает `continue: false`. Claude Code требует
`decision: "block"` и игнорирует `continue`/`systemMessage` этого события.
Для Claude Code нужна версия с поддержкой блокировки `PreCompact` и exec-form hooks
(`command` + `args`). На старых версиях контракт не гарантируется.

## Доставка в Codex desktop

`continuation.codex = codex-desktop` разрешает основной модели после `ready` вызвать
`mcp__codex_app__list_projects` с `{}`, затем `mcp__codex_app__create_thread` и
`mcp__codex_app__navigate_to_codex_page`. Сам Python hook не имеет доступа к этим инструментам.
При `manual`, в Claude Code и у старого состояния без режима продолжения автосоздания нет.
Выбор проекта требует единственного совпадения абсолютного пути с рабочей папкой,
`projectKind = local` и `hostId = local`. Используется локальная среда, без worktree.
Prompt равен сохранённому `START-NEXT-SESSION.txt`; модель и reasoning нового чата
не переопределяются. Writer по-прежнему не имеет инструментов и только готовит handoff.

Отдельное состояние `continuation` проходит этапы
`pending → discovering → project_ready → creating → created → opening → opened`.
До разрешения вызова `PreToolUse` под блокировкой сохраняет его имя и `tool_use_id`.
`PostToolUse` принимает только соответствующий ответ. Список проектов читается из
MCP `structuredContent` либо единственного JSON-текстового блока `content`.
Ответы сохраняются в `desktop-discovering.json`, `desktop-creating.json`,
`desktop-opening.json` внутри каталога handoff; эти файлы не попадают в Git.
Остальные инструменты после порога заблокированы. Это ограничение действует в пределах
путей tools, поддерживаемых hooks; hooks не являются полной границей изоляции.

Повтор `PreToolUse` не разрешает уже зарезервированный вызов. Если ответ потерян,
`Stop` или `SessionStart` фиксирует `failed`, `uncertain` либо `open_failed`.
Автоматического повтора нет. Ответ с `clientThreadId` переводит доставку в `queued`:
выдаётся карточка очереди, идентификатор не передаётся инструменту открытия.
Подтверждённый `threadId` сохраняется и выводится карточкой даже при ошибке открытия.
Отключение режима запрещает новые вызовы, сохраняя уже полученные подтверждения.

`Stop` может запросить каждый из трёх этапов доставки только один раз и затем один раз
попросить финальные ссылки и карточку. Маркер `CODEX_HANDOFF_DELIVER:<session-key>`
пропускает такой служебный prompt через `UserPromptSubmit`, но не снимает блокировку tools.
Это предотвращает бесконечную цепочку `Stop`, если инструменты недоступны или модель
не выполнила запрос. Артефакты и диагностика остаются доступны пользователю.

`retry-continuation --session-dir ...` сбрасывает только неудавшуюся доставку, не writer.
Для `uncertain` требуется `--confirm-no-chat` после ручной проверки списка чатов;
плагин не может доказать отсутствие чата при потере ответа. Для `open_failed` повтор
разрешает только открыть сохранённый чат. При `created`, `opened`, `queued` или ещё
зарезервированном вызове повтор отклоняется. После retry нужен служебный prompt
в старом чате; команда не вызывает приложение самостоятельно.

## Writer и артефакты

Writer запускается через локальный авторизованный CLI, отдельный API-ключ плагин не требует.
Codex использует ephemeral read-only `codex exec`; Claude Code — print mode без сохранения
сессии, инструментов и MCP. Hooks writer отключены; дополнительная переменная окружения
защищает от рекурсии. Модели фиксированы, fallback запрещён.

Снимок сохраняет пользовательские и текстовые ответы агента, вызовы инструментов и их
результаты. Длинные технические выводы обрезаются с явным учётом пропусков.
Изображения представлены отметками; system/developer и скрытые рассуждения не экспортируются.
Если пользовательский текст превышает бюджет, генерация завершается ошибкой вместо
молчаливой потери текста. Ответ writer проверяется по схеме до публикации handoff.

В `.cc-autohandoff/<session-key>/` сохраняются `SOURCE.jsonl`, `source-manifest.json`,
`session.json`, диагностика writer, `HANDOFF.md` и `START-NEXT-SESSION.txt`.
Это локальные данные сессии, исключённые из Git. Ошибка оставляет сессию заблокированной;
уведомление содержит точную команду `retry`. Путь CLI можно задать через
`CODEX_HANDOFF_CODEX` или `CC_AUTOHANDOFF_CLAUDE`.

## Проверка перед выпуском

Unit-тесты используют синтетические transcript и подменённый writer, без сетевых запросов.
Они не подтверждают доступ к моделям, реальную загрузку plugins и доставку hooks в UI.
Живой read-only `list_projects` подтвердил формат списка проектов. Создание и открытие
чата в полном цикле плагина пока проверены только с подменёнными ответами.
Перед выпуском нужно проверить обе среды с живыми CLI: достижение порога, выдачу ссылок,
ручной и автоматический compact, отказ модели, retry, пути с пробелами и Windows/POSIX.
Claude Code на машине начальной разработки отсутствовал; его интеграция пока экспериментальная.

## Официальные контракты

- [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- [Упаковка и локальные marketplace Codex](https://developers.openai.com/plugins/build/plugins)
- [Claude Code hooks](https://code.claude.com/docs/en/hooks)
- [Claude Code CLI](https://code.claude.com/docs/en/cli-reference)
- [Модели Claude Code](https://code.claude.com/docs/en/model-config)
- [Поля текущего контекста Claude Code](https://code.claude.com/docs/en/statusline)
- [Формат plugins Claude Code](https://code.claude.com/docs/en/plugins-reference)
