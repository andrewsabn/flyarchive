'use strict'
/*
 * Проверка до запуска набора: есть ли всё нужное (Playwright, Chromium, dsh, bwrap, python3 с lancedb, pyarrow и PyMuPDF). Печатает причину пропуска
 * и возвращает код 3; если всё есть — печатает путь к командной строке Playwright Test и возвращает 0. Ничего не скачивает.
 */
const { missing } = require('./stand.cjs')

const reason = missing()
if (reason) {
  process.stdout.write(`${reason}\n`)
  process.exit(3)
}
try {
  process.stdout.write(`${require.resolve('@playwright/test/cli')}\n`)
} catch {
  process.stdout.write('the @playwright/test package is not installed (set NODE_PATH to the global node_modules)\n')
  process.exit(3)
}
