# Проверка пакета Windows x64

Отчёт о версии 0.3.1 (20 октября 2026 года) устарел: он описывал Codex и writer через
внешний CLI, которые удалены в 0.5.0.

Для 0.5.0 автоматически проверяются:

- `python -m unittest discover -s tests -v` — синтетические hooks и transcript;
- `python scripts/check_package.py dist/plugin-publish` — структура, SHA-256 и запуск
  hooks из пакета в каталоге с пробелами и кириллицей без Python в `PATH`.

Эти проверки не вызывают модель. Живая проверка в Claude Desktop ещё не выполнялась;
перед выпуском нужно подтвердить:

1. агент плагина запускается на `claude-haiku-5-5`;
2. hooks получают `agent_id` и `agent_type` для вызовов writer;
3. порог 150 000 токенов и ручной compact приводят к запуску writer;
4. фоновый writer завершается и handoff становится готовым;
5. создаётся одна новая сессия через `mcp__ccd_session__start_session`;
6. повтор через `CC_AUTOHANDOFF_RETRY` после искусственного сбоя.
