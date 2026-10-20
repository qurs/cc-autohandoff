# cc-autohandoff
Экономия токенов с помощью автоматического переноса сессии (handoff)

При достижении **120 000 токенов текущего контекста** плагин останавливает работу на ближайшей точке,
и отдельная младшая модель готовит сжатую сводку, инструкции для продолжения работы в свежей сессии,
и далее плагин автоматически создает новую чистую сессию с указанными инструкциями.

| Среда | Модель для сводки |
| --- | --- |
| Codex | `GPT 6 Luna` |
| Claude Code | `Claude Haiku 5.5` |

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
