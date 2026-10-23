# Архитектура cc-autohandoff

Плагин работает только в Claude Code (CLI, IDE и desktop-приложение Claude).
Поддержка Codex и отдельного writer-процесса через Claude CLI удалена в 0.5.0:
writer — субагент плагина внутри текущей сессии, поэтому Claude CLI на компьютере не нужен.

## Компоненты

| Файл | Назначение |
| --- | --- |
| `scripts/handoff.py` | обработчик hooks, выгрузка источника, состояние, проверка ответа writer, CLI |
| `agents/handoff-writer.md` | субагент `cc-autohandoff:handoff-writer`: `model: claude-haiku-5-5`, `tools: Read` |
| `hooks/hooks.json` | 9 событий Claude Code, все вызывают `handoff.py hook` |
| `handoff-settings.json` | порог, модель, режим продолжения, тайм-аут, бюджет источника |
| `skills/handoff/SKILL.md` | инструкции основной модели по запуску writer, retry и доставке |
| `.claude-plugin/` | manifest плагина и каталог marketplace |

Python 3.11+, только стандартная библиотека. Готовый пакет Windows x64 запускает
тот же код как `bin/cc-autohandoff.exe`.

## Порог

Текущий контекст берётся из последнего `usage` основной цепочки transcript:
`input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens`.
Это размер текущего окна, а не накопленный расход сессии. Строки субагентов (`isSidechain`)
не учитываются. Передача начинается при значении `>= threshold_tokens` (150 000)
на ближайшем hook: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`
или `Stop`. Выполняющийся инструмент не прерывается, фоновые процессы не завершаются.
`PreCompact` всегда отвечает `{"decision": "block"}`. Ручной compact (`trigger: manual`) сам запускает
передачу, даже если порог не достигнут. Автоматический compact (`trigger: auto`) хост запускает раньше порога
(около 115–125 тыс.): пока значение `< threshold_tokens`, он только блокируется без handoff.

## Фазы

Состояние сессии хранится в `CLAUDE_PLUGIN_DATA/states/<ключ>.json`; если хост не передал
каталог данных плагина — в `<проект>/.cc-autohandoff/.plugin-data/`. Ключ — 20 hex-символов
хэша идентификатора сессии.

```
monitoring → writing → writer_running → ready
                 ↘            ↘
                  failed ← ← ← ┘   (retry → writing)
```

- **monitoring** — подсчёт токенов, никаких вмешательств.
- **writing** — источник выгружен. `PreToolUse` отклоняет любые инструменты, кроме
  точного вызова `Agent` с `subagent_type: cc-autohandoff:handoff-writer`, `description`
  и `prompt` из уведомления (разрешены только дополнительные `model: haiku`, `effort`,
  `run_in_background`); причина отказа содержит точный JSON вызова. `Stop` один раз
  блокирует ход с тем же напоминанием, при повторе переводит handoff в `failed`
  и останавливает чат.
- **writer_running** — `SubagentStart` запомнил `agent_id` writer. Основной модели
  запрещены инструменты, `Stop` разрешён (`{}`), чтобы фоновый writer мог доработать.
  Если writer молчит дольше `writer_timeout_seconds`, следующий hook переводит
  handoff в `failed`.
- **ready** — плагин записал `HANDOFF.md` и `START-NEXT-SESSION.txt`, удалил источник
  и выдаёт ссылки (и при необходимости выполняет доставку в desktop).
- **failed** — уведомление с ошибкой, путём к `writer-errors.log` и командами повтора.
  Инструменты и compact остаются заблокированы.

## Источник для writer

Плагин читает transcript основной цепочки и пишет текстовые части
`.cc-autohandoff/<ключ>/source/part-NNN.txt` (до `part_chars` символов и 1500 строк в части,
строки переносятся по 1000 символов, потому что Read обрезает длинные строки).
Если переписка больше `max_source_chars`, сначала сокращаются и отбрасываются старые
выводы инструментов (каждый ограничен `tool_output_chars`), затем старые ответы ассистента;
сообщения пользователя сохраняются приоритетно. Сведения о сокращении попадают в prompt
writer. Части — временный рабочий материал, а не копия переписки: они удаляются
сразу после финализации или сбоя.

## Writer-субагент

Основная модель вызывает инструмент `Agent` ровно с аргументами из уведомления.
Модель субагента зафиксирована frontmatter агента (`claude-haiku-5-5`); основная модель
не пишет резюме сама, а подмена модели запрещена: вызов с `model`, отличным от `haiku`,
отклоняется.

Для вызовов инструментов субагентов hooks получают `agent_id` и `agent_type`.
Writer может вызывать только `Read` и только для файлов своих частей источника;
всё остальное отклоняется. Чужие субагенты hook не трогает. Рекурсии нет: writer
не имеет инструмента `Agent`, а hooks его вызовов не запускают новую передачу.

Ответ writer — один JSON-объект ровно с полями `goal`, `constraints`, `decisions`,
`workspace`, `changes`, `validation`, `pending`, `next_step`, `uncertainties`. Ответ берётся из
`SubagentStop.last_assistant_message`, при его отсутствии — из `agent_transcript_path`;
для синхронного вызова также из результата `PostToolUse` инструмента `Agent`.
Невалидный ответ первый раз возвращается writer через `SubagentStop` с
`decision: block` и описанием ошибки; повторная ошибка переводит handoff в `failed`.
Если `Agent` запущен асинхронно (`async_launched`), плагин ждёт `SubagentStop`.

Плагин сам собирает `HANDOFF.md` из полей ответа и `START-NEXT-SESSION.txt` на английском.
Стартовый текст указывает читать только `HANDOFF.md` и запрещает читать прежний чат,
transcript и логи writer. `writer-errors.log` содержит только тип ошибки и служебные поля,
без текста переписки.

## Доставка

Режим задаётся `continuation`:

- `manual` — ссылки на оба файла; пользователь открывает новый чат сам.
- `claude-desktop` — действует, только если `CLAUDE_CODE_ENTRYPOINT == "claude-desktop"`,
  иначе работает как `manual`. Hook разрешает `ToolSearch` с
  `select:mcp__ccd_session__start_session` и один вызов
  `mcp__ccd_session__start_session` с точными аргументами: `initiation: user_asked`,
  `context: fresh`, `use_worktree: false`, `prompt` — текст `START-NEXT-SESSION.txt`.

Создание резервируется до вызова (`desktop-creating.json`, стадия `creating`).
Результат: `created` с идентификатором `local_…`, `failed` при явной ошибке или `uncertain`,
если ответ потерян. Автоматического повтора нет. `retry-continuation` возвращает стадию
`pending` только для `failed` или, после ручной проверки списка сессий, для `uncertain`
с флагом `--confirm-no-chat`; для `created` и `creating` повтор запрещён.

`Stop` в фазе `ready` блокирует ход маркером `CC_AUTOHANDOFF_DELIVER:<ключ>` не более
одного раза на запрос создания и одного раза на итоговый ответ; ответ считается доставленным,
когда содержит оба пути и ссылку на новую сессию (в desktop). Затем старый чат
останавливается (`continue: false`), и плагин его не возобновляет.

## Повтор

- Сообщение `CC_AUTOHANDOFF_RETRY` в том же чате (`UserPromptSubmit`) из фазы `failed`
  заново выгружает источник и возвращает фазу `writing`.
- `handoff.py retry --session-dir <каталог>` делает то же из терминала; затем нужно любое
  сообщение в чате. Пока writer работает, команда отказывает с кодом 2.
- `handoff.py status --session-dir <каталог>` печатает состояние.
- `handoff.py retry-continuation --session-dir <каталог> [--confirm-no-chat]` — повтор доставки.

При исключении внутри hook обработчик блокирует `PreToolUse`, `UserPromptSubmit`
и `PreCompact`, чтобы сбой не открыл путь к продолжению работы или compact.

## Файлы handoff

`<проект>/.cc-autohandoff/<ключ>/`:

- `session.json` — путь к состоянию и модель;
- `source/` — временные части источника;
- `HANDOFF.md`, `START-NEXT-SESSION.txt`;
- `writer-errors.log`;
- `desktop-creating.json` — резерв операции создания сессии.

Каталог `.cc-autohandoff/` нужно добавить в `.gitignore` проекта.

## Стартовый расход нового чата

Новый чат загружает системные инструкции, определения инструментов, навыки и подключённые
plugins, поэтому даже короткий handoff не означает нулевой стартовый контекст.
Плагин не измеряет эту часть отдельно.

## Автономный пакет Windows x64

`scripts/build_plugin.py` собирает runtime через PyInstaller (onedir) в `bin/`.
`bin/cc-autohandoff.exe` определяет корень пакета по своему расположению и находит
настройки и агента после копирования в cache приложения. stdin/stdout используют UTF-8.

Исходный `hooks/hooks.json` вызывает `python`. Сборщик заменяет команду на
`${CLAUDE_PLUGIN_ROOT}/bin/cc-autohandoff.exe` с `args: ["hook"]`, без shell, и добавляет
`onFailure: block` для `PreToolUse`, `UserPromptSubmit` и `PreCompact`.

Каталог marketplace в `master` использует источник `source: url`,
URL `https://github.com/qurs/cc-autohandoff.git` и `ref: windows-x64`. Эта ветка содержит
готовый пакет; пользователю не нужны Python, архивы или сборка.

Workflow `package.yml` запускает unit-тесты, сборку и проверку пакета на Windows,
затем на Ubuntu повторно проверяет checksums и публикует содержимое пакета в `windows-x64`
без force-push. Публикация разрешена после push в `master` или ручного запуска из `master`.
Изменения пакета требуют новой версии в manifest, чтобы Claude Code обновил копию.

Сборка копирует только разрешённые файлы, runtime и лицензии; SHA-256 manifest включает
все файлы, кроме самого manifest. Сгенерированный `.gitattributes` отключает преобразование
строк Git. `scripts/check_package.py` запускает пакет из каталога с пробелами и кириллицей
без Python в `PATH` на синтетических hooks; модели при этом не вызываются.

## Проверка перед выпуском

Unit-тесты используют синтетические transcript и события hooks, без запросов к моделям.
Они не подтверждают живую работу в Claude Desktop. Перед выпуском нужно вручную проверить:
принятие `model: claude-haiku-5-5` агентом плагина, наличие `agent_id`/`agent_type`
в hooks субагента, асинхронный writer, блокировку compact, retry и создание сессии через
`mcp__ccd_session__start_session`.

## Официальные контракты

- [Claude Code hooks](https://code.claude.com/docs/en/hooks)
- [Субагенты Claude Code](https://code.claude.com/docs/en/sub-agents)
- [Модели Claude Code](https://code.claude.com/docs/en/model-config)
- [Поля текущего контекста Claude Code](https://code.claude.com/docs/en/statusline)
- [Формат plugins Claude Code](https://code.claude.com/docs/en/plugins-reference)
