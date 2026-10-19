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
выдать ссылки, затем завершает ход. Уже запущенные инструменты и фоновые процессы
не прерываются. Порог проверяется на границах hooks; точная остановка на токене 120 000
внутри генерации не гарантируется.

В Codex `PreCompact` возвращает `continue: false`. Claude Code требует
`decision: "block"` и игнорирует `continue`/`systemMessage` этого события.
Для Claude Code нужна версия с поддержкой блокировки `PreCompact` и exec-form hooks
(`command` + `args`). На старых версиях контракт не гарантируется.

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
