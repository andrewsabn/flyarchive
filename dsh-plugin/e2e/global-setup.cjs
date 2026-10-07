'use strict'
/*
 * Перед набором: поднимает стенд (stand.cjs) и один раз проходит начало работы в DSH — уведомление о версии и выбор рабочей папки, —
 * сохраняя состояние входа в закрытый файл стенда. Остановка стенда — функция, которую Playwright вызовет в конце, в том числе после
 * упавших тестов; на Ctrl+C и на убитый прогон стенд убирает себя сам (он следит за каналом родителя).
 * Нет Playwright, Chromium, dsh, bwrap или нужных модулей Python — набор пропускается с названной причиной (переменная BA_E2E_SKIP).
 */
const { chromium } = require('@playwright/test')
const kit = require('./kit.cjs')
const { start, missing } = require('./stand.cjs')

module.exports = async () => {
  const reason = missing()
  if (reason) {
    process.env.BA_E2E_SKIP = reason
    process.stdout.write(`e2e suite skipped: ${reason}\n`)
    return undefined
  }
  const stand = await start()
  process.env.BA_E2E_STAND = stand.dir
  const standClient = kit.connect()
  standClient.assertStandOnly()
  const browser = await chromium.launch({ headless: true })
  try {
    const context = await browser.newContext({ locale: 'en-US', viewport: { width: 1560, height: 1000 } })
    const page = await context.newPage()
    await kit.signIn(page, standClient)
    await kit.dismissOnboarding(page)
    // рабочая папка нужна, чтобы появился сеанс, а с ним — правая панель с вкладкой Archive; выбирается один раз и хранится в DSH_HOME стенда
    const choose = page.getByRole('button', { name: 'Choose workspace', exact: true })
    if (await choose.count() && await choose.first().isVisible()) {
      await choose.first().click()
      await page.getByRole('dialog').getByRole('button', { name: 'Open', exact: true }).click()
      await page.locator('[aria-label^="Workspace actions for"]').first().waitFor({ state: 'attached', timeout: 30000 })
    }
    await context.storageState({ path: standClient.statePath() })
    await context.close()
  } catch (error) {
    await browser.close()
    await stand.stop()
    throw new Error('the DSH start screens could not be passed: ' + kit.scrub(error.message).split('\n')[0])
  }
  await browser.close()
  return async () => {
    await stand.stop()
  }
}
