# cc-autohandoff
Экономия токенов с помощью автоматического переноса сессии (handoff)

При достижении **150 000 токенов текущего контекста** плагин останавливает работу на ближайшей точке,
и отдельная младшая модель готовит сжатую сводку и инструкцию для продолжения работы в свежей сессии.
В desktop-приложениях Codex и Claude плагин затем создаёт одну новую сессию в том же проекте
с этой инструкцией. В CLI и IDE новая сессия открывается вручную по ссылке на `START-NEXT-SESSION.txt`.

| Среда | Модель для сводки |
| --- | --- |
| Codex | `GPT 6 Luna` |
| Claude Code | `Claude Haiku 5.5` |

Сводка и инструкция для нового чата пишутся на английском. Переписка не копируется
в файлы плагина: новый чат получает только `HANDOFF.md` как источник прежнего контекста
и явный запрет читать старую историю. Рабочие файлы проекта доступны для продолжения задачи.
Начальный контекст также включает инструкции Codex и определения инструментов;
поэтому даже короткий handoff не означает старт с нуля токенов.

## Установка

Добавь в `.gitignore` проекта:
```
.cc-autohandoff/
.codex-handoff/
```

**Codex** — выполни в терминале:

```powershell
codex plugin marketplace add qurs/cc-autohandoff
codex plugin add cc-autohandoff@cc-autohandoff
```

После установки перезапусти Codex. Проверь и доверь текущее определение хуков,
затем открой новый чат.

**Claude Code** — выполни в терминале:

```powershell
claude plugin marketplace add qurs/cc-autohandoff
claude plugin install cc-autohandoff@cc-autohandoff
```

Внутри Claude Code те же команды доступны как `/plugin marketplace add qurs/cc-autohandoff`
и `/plugin install cc-autohandoff@cc-autohandoff`.

## Claude Code: авторизация writer

Сводку пишет отдельный процесс `claude --print`. Вход, выполненный в приложении Claude,
этому процессу не передаётся, поэтому один раз выполни в терминале:

```powershell
claude auth login
```

Если в PATH нет `claude`, плагин использует CLI, встроенный в приложение. Другой путь
можно задать переменной `CC_AUTOHANDOFF_CLAUDE`. При старте сессии плагин проверяет
`claude auth status` и предупреждает, если writer не авторизован.

## Если handoff не удался

Compact и новая работа остаются заблокированы, а уведомление показывает ошибку.
Устрани причину (например, выполни `claude auth login`) и отправь в этот же чат сообщение
`CC_AUTOHANDOFF_RETRY`: writer запустится повторно. Тот же повтор доступен командой
`retry` из уведомления.

## Настройки продолжения

В `handoff-settings.json` ключ `continuation` выбирает способ продолжения:
`codex-desktop` или `manual` для Codex, `claude-desktop` или `manual` для Claude Code.
Вне desktop-приложения Claude режим `claude-desktop` автоматически работает как `manual`.
Если ответ приложения о создании сессии потерян, автоматического повтора нет: проверь список
сессий и только затем выполни `retry-continuation ... --confirm-no-chat` из уведомления.
