'use strict'
/*
 * Настройка набора сквозных проверок (FR-91). Запуск — run.sh. Следы выключены намеренно: трасса и видео записали бы страницу, а на странице
 * в момент выпуска стоит значение токена; снимки экрана тест делает сам и закрывает токен маской. Итог — список в терминале и отчёт в каталоге
 * $BA_E2E_OUT/report (по умолчанию .out/ рядом с этим файлом); run.sh после прогона ищет в нём токены и ссылки входа.
 */
const path = require('node:path')
const { defineConfig } = require('@playwright/test')

const OUT = path.resolve(process.env.BA_E2E_OUT || path.join(__dirname, '.out'))

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: '*.spec.cjs',
  outputDir: path.join(OUT, 'results'),
  globalSetup: require.resolve('./global-setup.cjs'),
  workers: 1, // стенд один, тесты идут по очереди; порядок не важен: у каждого свой токен и свои файлы
  fullyParallel: false,
  retries: 0,
  timeout: 120000,
  expect: { timeout: 15000 },
  reporter: [['list'], ['html', { outputFolder: path.join(OUT, 'report'), open: 'never' }], ['json', { outputFile: path.join(OUT, 'results.json') }]],
  use: {
    headless: true,
    locale: 'en-US',
    viewport: { width: 1560, height: 1000 },
    actionTimeout: 20000,
    navigationTimeout: 30000,
    trace: 'off',
    video: 'off',
    screenshot: 'off',
  },
})
