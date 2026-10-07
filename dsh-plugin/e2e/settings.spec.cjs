'use strict'
/*
 * FR-91: настройки приёмки из интерфейса. Смена входящей папки (существующая — принимается, несуществующая — отказ с сообщением), период таймера
 * и проверка моделью. Сохранённое видно после перезагрузки страницы и в команде inbox status --json. Каждый тест возвращает настройку, как была.
 * FR-94: описание изображений локальной моделью включается и выключается там же, вместе с пределами за проход.
 */
const fs = require('node:fs')
const path = require('node:path')
const { test, expect, kit } = require('./fixtures.cjs')

const noticeOf = (app) => app.settings.locator(':scope > [role="status"]')
const folderBox = (app) => app.settings.locator('[data-section="folders"]')
const intakeBox = (app) => app.settings.locator('[data-section="intake"]')

test.describe('настройки: входящая папка', () => {
  test('смена входящей папки на существующую принимается: адрес виден после перезагрузки страницы и в inbox status --json', async ({ app, stand }) => {
    const original = stand.status().inbox
    const folder = stand.scratch('inbox-' + kit.nonce())
    stand.later(() => stand.cli(['inbox', 'set', '--path', original]))
    await app.openSettings()
    const box = folderBox(app)
    await expect(box.locator('input[name="inbox"]')).toHaveValue(original)

    await box.locator('input[name="inbox"]').fill(folder)
    await box.getByRole('button', { name: 'Change', exact: true }).click()
    // вопрос с предупреждением: пока не подтверждён, ничего не меняется
    await expect(box).toContainText('The archive will take in everything that lies in the new folder')
    expect(stand.status().inbox).toBe(original)
    await box.getByRole('button', { name: 'Yes, change the inbox folder', exact: true }).click()
    await expect(noticeOf(app)).toContainText(`Inbox folder changed to ${folder}.`)
    await expect(box.getByRole('alert')).toHaveCount(0)

    expect(stand.status().inbox, 'the command sees the new folder').toBe(folder)
    await app.reload()
    await app.openSettings()
    await expect(folderBox(app).locator('input[name="inbox"]')).toHaveValue(folder)
    // смена записана в журнал обращений: старый и новый адрес
    const record = stand.journal(50).filter((entry) => entry.tool === 'inbox set').pop()
    expect(record && record.params.old === original && record.params.new === folder, 'the folder change is journaled with both paths').toBe(true)

    // папка действует: файл, положенный в новую папку, разбирается, а прежняя папка перестала быть входящей
    const tag = kit.nonce()
    const [name] = stand.drop('good', tag, 1, folder)
    await stand.runInbox()
    expect(fs.existsSync(path.join(folder, name)), 'the file was taken from the new inbox folder').toBe(false)
    expect(stand.status().last_batch.accept).toBeGreaterThanOrEqual(1)

    // и обратно тем же способом: настройка возвращена
    const again = folderBox(app)
    await again.locator('input[name="inbox"]').fill(original)
    await again.getByRole('button', { name: 'Change', exact: true }).click()
    await again.getByRole('button', { name: 'Yes, change the inbox folder', exact: true }).click()
    await expect(noticeOf(app)).toContainText(`Inbox folder changed to ${original}.`)
    expect(stand.status().inbox).toBe(original)
  })

  test('смена входящей папки на несуществующую отклоняется с сообщением: настройка прежняя, каталог не создан', async ({ app, stand }) => {
    const original = stand.status().inbox
    const missing = path.join(stand.scratch('absent'), 'no-such-' + kit.nonce())
    expect(fs.existsSync(missing)).toBe(false)
    await app.openSettings()
    const box = folderBox(app)
    await box.locator('input[name="inbox"]').fill(missing)
    await box.getByRole('button', { name: 'Change', exact: true }).click()
    await box.getByRole('button', { name: 'Yes, change the inbox folder', exact: true }).click()

    await expect(box.getByRole('alert')).toContainText(`The inbox folder does not exist: ${missing}`)
    expect(stand.status().inbox, 'the setting is unchanged').toBe(original)
    expect(fs.existsSync(missing), 'the plugin creates no folders').toBe(false)
    await expect(noticeOf(app)).toHaveCount(0)
    // после перезагрузки страница показывает прежнюю папку
    await app.reload()
    await app.openSettings()
    await expect(folderBox(app).locator('input[name="inbox"]')).toHaveValue(original)
    // отказ записан в журнал обращений
    const refused = stand.journal(50).filter((entry) => entry.tool === 'inbox set' && entry.status === 1)
    expect(refused.some((entry) => entry.params.new === missing), 'the refusal is journaled').toBe(true)
  })
})

test.describe('настройки: период таймера и проверка моделью', () => {
  test('период таймера и проверка моделью сохраняются: после перезагрузки страницы и в inbox status --json', async ({ app, stand }) => {
    const before = stand.status()
    stand.later(() => stand.cli(['inbox', 'set', '--period', String(before.period), '--llm', before.llm ? 'on' : 'off', '--cloud', before.cloud ? 'on' : 'off']))
    const target = before.period === 5 ? 10 : 5
    const timerCalls = () => fs.readFileSync(path.join(stand.dir, 'systemctl.log'), 'utf8').split('\n').filter((line) => line.includes('flyarchive-inbox.timer')).length
    const calls = timerCalls()

    await app.openSettings()
    let box = intakeBox(app)
    await box.locator('select[name="period"]').selectOption(String(target))
    await box.locator('input[name="llm"]').check()
    await box.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(noticeOf(app)).toContainText('Intake settings saved.')

    const saved = stand.status()
    expect(saved.period).toBe(target)
    expect(saved.llm, 'the model check is on in the command').toBe(true)
    expect(timerCalls(), 'the timer was re-installed through systemctl').toBeGreaterThan(calls)
    await app.reload()
    await app.openSettings()
    box = intakeBox(app)
    await expect(box.locator('select[name="period"]')).toHaveValue(String(target))
    await expect(box.locator('input[name="llm"]')).toBeChecked()

    // выключено обратно из интерфейса: и страница, и команда видят выключенное
    await box.locator('select[name="period"]').selectOption(String(before.period))
    await box.locator('input[name="llm"]').uncheck()
    await box.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(noticeOf(app)).toContainText('Intake settings saved.')
    await app.reload()
    await app.openSettings()
    box = intakeBox(app)
    await expect(box.locator('select[name="period"]')).toHaveValue(String(before.period))
    await expect(box.locator('input[name="llm"]')).not.toBeChecked()
    const after = stand.status()
    expect(after.period).toBe(before.period)
    expect(after.llm).toBe(false)
  })
})

test.describe('настройки: описание изображений локальной моделью (FR-94)', () => {
  test('описание изображений включается и выключается в интерфейсе: пределы видны в inbox status --json и после перезагрузки страницы', async ({ app, stand }) => {
    const before = stand.cliJson(['inbox', 'status', '--json'])
    stand.later(() => stand.cli(['inbox', 'set', '--vision', before.vision ? 'on' : 'off', '--vision-pages', String(before.vision_pages),
      '--vision-minutes', String(before.vision_minutes)]))
    // исходное состояние — известное: выключено, пределы по умолчанию (стенд мог начать с любого)
    stand.cliJson(['inbox', 'set', '--vision', 'off', '--vision-pages', '60', '--vision-minutes', '10', '--json'])

    await app.openSettings()
    let box = intakeBox(app)
    const check = box.getByLabel('Describe images with the local model', { exact: true })
    const pages = box.getByLabel('Pages per run', { exact: true })
    const minutes = box.getByLabel('Minutes per run', { exact: true })
    await expect(check).not.toBeChecked()
    await expect(pages).toBeDisabled()
    await expect(minutes).toBeDisabled()
    await expect(box).not.toContainText('Images never leave this machine')

    // включить, задать 5 страниц и 2 минуты, сохранить общей кнопкой
    await check.check()
    await expect(box).toContainText('Images never leave this machine. Needs a local model with vision.')
    await expect(pages).toBeEnabled()
    await expect(minutes).toBeEnabled()
    await pages.fill('5')
    await minutes.fill('2')
    await box.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(noticeOf(app)).toContainText('Intake settings saved.')
    const on = stand.cliJson(['inbox', 'status', '--json'])
    expect(on.vision, 'the command sees image description turned on').toBe(true)
    expect(on.vision_pages).toBe(5)
    expect(on.vision_minutes).toBe(2)

    // после перезагрузки страницы то же самое видно в интерфейсе
    await app.reload()
    await app.openSettings()
    box = intakeBox(app)
    await expect(box.getByLabel('Describe images with the local model', { exact: true })).toBeChecked()
    await expect(box.getByLabel('Pages per run', { exact: true })).toHaveValue('5')
    await expect(box.getByLabel('Minutes per run', { exact: true })).toHaveValue('2')
    await expect(box).toContainText('Images never leave this machine')
    // в стенде ничего не ждёт описания: строки «Awaiting description» в Status нет
    if (on.vision_pending === 0) await expect(app.settings.locator('[data-section="status"]')).not.toContainText('Awaiting description')

    // выключить и сохранить: команда видит выключенное, числа остаются сохранёнными, но недоступны
    await box.getByLabel('Describe images with the local model', { exact: true }).uncheck()
    await expect(box.getByLabel('Pages per run', { exact: true })).toBeDisabled()
    await expect(box).not.toContainText('Images never leave this machine')
    await box.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(noticeOf(app)).toContainText('Intake settings saved.')
    const off = stand.cliJson(['inbox', 'status', '--json'])
    expect(off.vision, 'the command sees image description turned off').toBe(false)
    expect(off.vision_pages).toBe(5)
    expect(off.vision_minutes).toBe(2)
    await app.reload()
    await app.openSettings()
    box = intakeBox(app)
    await expect(box.getByLabel('Describe images with the local model', { exact: true })).not.toBeChecked()
    await expect(box.getByLabel('Pages per run', { exact: true })).toBeDisabled()
    await expect(box.getByLabel('Pages per run', { exact: true })).toHaveValue('5')
  })
})
