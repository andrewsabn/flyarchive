#!/usr/bin/env bash
# Сквозные проверки из интерфейса на одноразовом стенде (FR-91): настоящий DSH, настоящий Chromium, плагин из рабочего каталога.
#
#   dsh-plugin/e2e/run.sh                 весь набор
#   dsh-plugin/e2e/run.sh -g "токен"      только тесты, в названии которых есть слова (любые ключи Playwright Test допустимы)
#
# Нужны (ничего не скачивается и не ставится): Linux, Node 20+, Playwright и @playwright/test (глобально, NODE_PATH ищется сам:
# npm root -g), Chromium Playwright, команда dsh, bwrap (способный поднять песочницу), python3 с lancedb, pyarrow и PyMuPDF. Чего-то нет — понятный пропуск с причиной и код 0;
# иначе код возврата — итог тестов (и 1, если в каталоге отчёта нашёлся токен или ссылка входа).
# Переключатели: BA_E2E_TOOLS=worktree|head|<каталог tools> (откуда брать код сервера; по умолчанию рабочий каталог репозитория),
# BA_E2E_PLUGIN=<каталог плагина>, BA_E2E_OUT=<каталог результатов и отчёта>, BA_E2E_KEEP=1 (не удалять каталог стенда: для отладки).
# Живой архив, ~/.dsh, службы и модель набор не трогает: всё идёт во временном каталоге на свободных портах петли.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=${BA_E2E_OUT:-$HERE/.out}
export BA_E2E_OUT="$OUT"

if [ -z "${NODE_PATH:-}" ]; then
  NODE_PATH=$(npm root -g 2>/dev/null || true)
  export NODE_PATH
fi
if ! command -v node >/dev/null 2>&1; then
  echo "e2e suite skipped: node is not installed"
  exit 0
fi

CLI=$(node "$HERE/preflight.cjs")
case $? in
  0) ;;
  3) echo "e2e suite skipped: $CLI"; exit 0 ;;
  *) echo "e2e preflight failed: $CLI"; exit 1 ;;
esac

rm -rf "$OUT/results" "$OUT/report" "$OUT/results.json"
mkdir -p "$OUT"
node "$CLI" test --config "$HERE/playwright.config.cjs" "$@"
code=$?

# после прогона: в каталоге результатов и отчёта не должно быть токенов клиентов и ссылок входа (значения здесь не печатаются)
node "$HERE/leakscan.cjs" "$OUT" || code=1
exit $code
