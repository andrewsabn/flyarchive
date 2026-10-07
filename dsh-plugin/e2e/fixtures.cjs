'use strict'
/*
 * Общие приспособления тестов: fixture stand (клиент стенда с проверкой «это стенд») и app (браузер, вошедший в DSH стенда, со всем нужным
 * для вкладки Archive и раздела настроек). Каждый тест получает свой контекст браузера и свой стенд-клиент; перед первым действием проверяется,
 * что адрес — стендовый порт, а архив лежит во временном каталоге стенда: иначе тест падает, ничего не нажав.
 */
const { test: base, expect } = require('@playwright/test')
const kit = require('./kit.cjs')

const TAB_ENTRY = '[data-sidebar-right-guide-entry="flyarchive-archive"] button'

/** Окно настроек и вкладка Archive: короткие имена для того, что тесты делают постоянно. */
class App {
  constructor(page, context, stand, outside) {
    this.page = page
    this.context = context
    this.stand = stand
    this.outside = outside // обращения страницы не к адресу DSH
  }

  get tab() {
    return this.page.locator('.ba-tab')
  }

  get tabTitle() {
    return this.page.locator('.ba-tab-title')
  }

  get dialog() {
    return this.page.getByRole('dialog')
  }

  /** Раздел Archive в окне настроек. */
  get settings() {
    return this.dialog.locator('.ba-section')
  }

  /** Правая панель с вкладкой Archive: открывает панель и запись путеводителя, если вкладка ещё не открыта. */
  async openTab() {
    const { page } = this
    if (await this.dialog.isVisible()) await this.closeSettings()
    if (await this.tab.isVisible()) return this.tab
    const sidebar = page.getByRole('button', { name: 'Open right sidebar' })
    if (await sidebar.count() && await sidebar.first().isVisible()) await sidebar.first().click()
    const entry = page.locator(TAB_ENTRY)
    const title = page.locator('.ba-tab-title')
    await expect(entry.or(title).or(this.tab).first()).toBeVisible()
    if (await entry.count() && await entry.first().isVisible()) await entry.first().click()
    else if (!(await this.tab.isVisible())) await title.first().click()
    await expect(this.tab).toBeVisible()
    return this.tab
  }

  /** Страница перечитывается и вкладка открывается заново: так она видит то, что тест подготовил командой, не дожидаясь опроса раз в 30 с. */
  async freshTab() {
    await this.reload()
    return this.openTab()
  }

  /** Раздел Archive в настройках: окно открыто, данные (токены, журнал) загружены. */
  async openSettings({ loaded = true } = {}) {
    const { page } = this
    if (!(await this.dialog.isVisible())) await page.getByRole('button', { name: 'Settings', exact: true }).click()
    await expect(this.dialog).toBeVisible()
    await this.dialog.getByRole('button', { name: 'Archive', exact: true }).click()
    await expect(this.settings.getByRole('heading', { name: 'Archive', exact: true })).toBeVisible()
    if (loaded) await this.settingsLoaded()
    return this.settings
  }

  async settingsLoaded() {
    for (const section of ['tokens', 'journal', 'folders']) {
      await expect(this.settings.locator(`[data-section="${section}"]`)).not.toContainText('Loading…')
    }
  }

  async closeSettings() {
    if (await this.dialog.isVisible()) await this.dialog.getByRole('button', { name: 'Close', exact: true }).click()
    await expect(this.dialog).toBeHidden()
  }

  /** Перезагрузка страницы целиком: окна начала работы снимаются, вкладка и окно настроек открываются заново — по вызовам теста. */
  async reload() {
    await this.page.reload({ waitUntil: 'domcontentloaded' })
    await kit.dismissOnboarding(this.page)
  }

  // ── токены ───────────────────────────────────────────────────
  get tokenPart() {
    return this.settings.locator('[data-section="tokens"]')
  }

  /** Выпуск токена кнопкой в настройках. Возвращает значение, показанное на экране; оно же запоминается для проверки на утечку. */
  async issueToken(name, level = 'read', days = '') {
    const form = this.tokenPart.locator('form')
    await form.locator('input[name="name"]').fill(name)
    await form.locator('select[name="level"]').selectOption(level)
    await form.locator('input[name="days"]').fill(String(days))
    await form.getByRole('button', { name: 'Issue', exact: true }).click()
    const box = this.issuedBox
    await expect(box).toBeVisible()
    const value = (await box.locator('.ba-token').innerText()).trim()
    this.stand.remember(value)
    return value
  }

  get issuedBox() {
    return this.tokenPart.locator('[role="status"]')
  }

  tokenRow(name) {
    return this.tokenPart.locator('tbody tr').filter({ has: this.page.locator('td:first-child', { hasText: new RegExp('^' + kit.ESC(name) + '$') }) })
  }

  /** Отзыв: «Revoke», затем подтверждение «Yes, close access». */
  async revokeToken(name) {
    const row = this.tokenRow(name)
    await row.getByRole('button', { name: 'Revoke', exact: true }).click()
    await row.getByRole('button', { name: 'Yes, close access', exact: true }).click()
  }

  async tokenState(name) {
    return (await this.tokenRow(name).locator('td').nth(5).innerText()).trim()
  }

  /** Журнал обращений: строки таблицы как массивы ячеек. */
  async journalRows() {
    const rows = this.settings.locator('[data-section="journal"] tbody tr')
    const count = await rows.count()
    const out = []
    for (let i = 0; i < count; i += 1) out.push((await rows.nth(i).locator('td').allInnerTexts()).map((cell) => cell.trim()))
    return out
  }

  /** Журнал одного клиента (выбор в списке «Client»): ждёт ответ на отбор и возвращает строки таблицы. */
  async journalOf(name) {
    const select = this.settings.locator('[data-section="journal"]').locator('select[name="client"]')
    const answered = this.page.waitForResponse((response) => response.url().includes('api/flyarchive.journal') && response.url().includes('client='))
    await select.selectOption(name)
    await answered
    await expect(select).toHaveValue(name)
    await this.page.waitForTimeout(150) // перерисовка таблицы после ответа
    return this.journalRows()
  }

  /** «Refresh» в настройках: ждёт ответ журнала и конца работы кнопки, чтобы таблицы показывали свежее, а не прежнее. */
  async refreshSettings() {
    const button = this.settings.getByRole('button', { name: 'Refresh', exact: true })
    const answered = this.page.waitForResponse((response) => response.url().includes('api/flyarchive.journal'))
    await button.click()
    await answered
    await expect(button).toBeEnabled()
    await this.settingsLoaded()
  }
}

const test = base.extend({
  // на случай, если сообщение о провале Playwright вместе с описанием элемента повторит значение токена или ссылку входа: оно затирается до отчёта
  scrubErrors: [async ({}, use, testInfo) => { // eslint-disable-line no-empty-pattern
    await use()
    for (const error of testInfo.errors) {
      for (const key of ['message', 'stack', 'snippet', 'value']) if (typeof error[key] === 'string') error[key] = kit.scrub(error[key])
    }
  }, { auto: true }],

  stand: async ({}, use, testInfo) => { // eslint-disable-line no-empty-pattern
    testInfo.skip(Boolean(process.env.BA_E2E_SKIP), process.env.BA_E2E_SKIP || '')
    const stand = kit.connect()
    stand.assertStandOnly() // до любого действия: адрес и каталоги стендовые
    const later = []
    stand.later = (fn) => later.push(fn)
    await use(stand)
    await stand.idle().catch(() => {}) // фоновый проход, начатый кнопкой, не должен переходить к соседнему тесту
    for (const fn of later.reverse()) {
      try {
        await fn()
      } catch {
        // уборка не должна скрывать итог теста
      }
    }
  },

  app: async ({ browser, stand }, use) => {
    const context = await browser.newContext({ storageState: stand.statePath() })
    const dshOrigin = new URL(stand.dshUrl).origin
    const outside = []
    // страница не должна обращаться никуда, кроме адреса DSH: чужое отклоняется и записывается (data: и blob: — свои)
    await context.route('**/*', (route) => {
      const url = route.request().url()
      if (/^(data|blob|about):/.test(url) || new URL(url).origin === dshOrigin) return route.continue()
      outside.push(new URL(url).origin)
      return route.abort()
    })
    const page = await context.newPage()
    page.on('websocket', (socket) => {
      const url = new URL(socket.url())
      if (url.host !== new URL(stand.dshUrl).host) outside.push(url.origin)
    })
    await page.goto(stand.dshUrl + '/', { waitUntil: 'domcontentloaded' })
    await kit.dismissOnboarding(page)
    // первое обращение страницы к плагину — только чтение: плагин DSH должен смотреть на архив стенда
    const seen = await page.evaluate(async () => (await fetch('api/flyarchive.inbox')).json())
    if (seen.home !== stand.archive || !String(seen.inbox).startsWith(stand.dir)) {
      await context.close()
      throw new Error('the DSH page works on another archive than the stand')
    }
    await use(new App(page, context, stand, outside))
    // Playwright при закрытии контекста после сбоя снимает с живой страницы дерево доступности и кладёт его в error-context.md:
    // значение токена, оставшееся на экране, попало бы в отчёт. Поэтому перед закрытием оно затирается в самой странице.
    await page.evaluate(() => {
      const source = '(?<![A-Za-z0-9_])ba_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])'
      const found = new RegExp(source)
      const all = new RegExp(source, 'g')
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
      for (let node = walker.nextNode(); node; node = walker.nextNode()) if (found.test(node.nodeValue)) node.nodeValue = node.nodeValue.replace(all, 'ba_***')
      for (const input of document.querySelectorAll('input, textarea')) if (found.test(input.value)) input.value = input.value.replace(all, 'ba_***')
    }).catch(() => {})
    await context.close()
  },
})

module.exports = { test, expect, kit, App }
