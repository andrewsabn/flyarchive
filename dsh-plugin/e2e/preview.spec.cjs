'use strict'
/*
 * FR-91: просмотр содержимого из интерфейса. Письмо с вложениями открывается; вложение (текст, PDF, архив) открывается и закрывается;
 * программа показана только сведениями. Страница не обращается ни к одному адресу, кроме адреса DSH: перехват запросов браузера
 * (app.outside собирает всё чужое и отклоняет его) плюс список запросов теста.
 */
const fs = require('node:fs')
const path = require('node:path')
const { test, expect, kit } = require('./fixtures.cjs')

/** Все запросы страницы за время теста: адреса и методы. */
function watch(page) {
  const seen = []
  page.on('request', (request) => seen.push({ url: request.url(), method: request.method() }))
  return seen
}

const onlyDsh = (seen, stand) => seen.filter((entry) => !/^(blob|data|about):/.test(entry.url) && new URL(entry.url).origin !== new URL(stand.dshUrl).origin)

test.describe('просмотр', () => {
  test('письмо с вложениями открывается; вложения открываются и закрываются; страница не обращается никуда, кроме адреса DSH', async ({ app, stand }) => {
    const tag = kit.nonce()
    const seen = watch(app.page)
    stand.drop('mail', tag)
    await stand.runInbox()
    const batch = stand.status().last_batch.batch
    await app.freshTab()

    // пачка → строка файла → просмотр письма
    const head = app.tab.locator(`li.ba-batch[data-batch="${batch}"] button.ba-batch-head`)
    await expect(head).toBeVisible()
    await head.click()
    const fileRow = app.tab.locator(`tr[data-file="letter-${tag}.eml"]`)
    await expect(fileRow).toBeVisible()
    await fileRow.getByRole('button', { name: `letter-${tag}.eml`, exact: true }).click()
    const info = app.tab.locator(`tr[data-file-info="letter-${tag}.eml"]`)
    const view = info.locator('[data-preview]')
    await expect(view).toContainText('From:', { timeout: 60000 })
    await expect(view).toContainText('Игорь Лаптев <igor.laptev@example.com>')
    await expect(view).toContainText('To:')
    await expect(view).toContainText('Анна Ветрова <anna.vetrova@example.com>')
    await expect(view).toContainText('Марина Соколова <marina.sokolova@example.com>')
    await expect(view).toContainText('10.03.2026 09:12 +03:00')
    await expect(view).toContainText(`Subject: Перенос регламентных работ ${tag}`)
    await expect(view).toContainText(`Метка письма: ${tag}`)
    const attachments = view.locator('tr[data-attachment]')
    await expect(attachments).toHaveCount(3)
    await expect(attachments.nth(0)).toContainText(`plan-${tag}.txt`)
    await expect(attachments.nth(1)).toContainText(`schedule-${tag}.pdf`)
    await expect(attachments.nth(2)).toContainText(`pack-${tag}.zip`)
    const back = view.getByRole('button', { name: 'Back to message', exact: true })
    await expect(back).toHaveCount(0)

    // вложение-текст: открывается и закрывается
    await attachments.nth(0).getByRole('button', { name: `plan-${tag}.txt` }).click()
    await expect(view.locator('.ba-preview-text')).toContainText('Остановить приём операций', { timeout: 60000 })
    await expect(view).toContainText(`Attachment of: letter-${tag}.eml`)
    await expect(back).toBeVisible()
    await back.click()
    await expect(back).toHaveCount(0)
    await expect(view.locator('tr[data-attachment]')).toHaveCount(3)

    // вложение-PDF: страница рисуется картинкой, которую страница получила от плагина DSH
    await attachments.nth(1).getByRole('button', { name: `schedule-${tag}.pdf` }).click()
    await expect(view).toContainText('Page 1 of 1', { timeout: 90000 })
    const picture = view.locator('img.ba-preview-img')
    await expect(picture).toBeVisible({ timeout: 90000 })
    await expect.poll(() => picture.evaluate((img) => img.naturalWidth), { timeout: 30000 }).toBeGreaterThan(0)
    expect(await picture.evaluate((img) => img.src.startsWith('blob:')), 'the page image is a local object of the page').toBe(true)
    await back.click()
    await expect(back).toHaveCount(0)

    // вложение-архив: показан составом
    await attachments.nth(2).getByRole('button', { name: `pack-${tag}.zip` }).click()
    const listing = view.locator('table').first()
    await expect(listing).toContainText(`docs/first-${tag}.txt`, { timeout: 60000 })
    await expect(listing).toContainText(`docs/second-${tag}.txt`)
    await expect(listing).toContainText('13 B')
    await expect(view).toContainText('Name:') // сведения об архиве: имя, тип, размер, sha256
    await back.click()
    await expect(back).toHaveCount(0)

    // перехват запросов: ни одного обращения не к адресу DSH; страницы шли через адреса плагина
    expect(app.outside, 'addresses other than DSH that the page tried to reach').toEqual([])
    expect(onlyDsh(seen, stand).map((entry) => new URL(entry.url).origin), 'requests to other origins').toEqual([])
    const previewCalls = seen.filter((entry) => entry.url.includes('api/flyarchive.preview'))
    expect(previewCalls.some((entry) => entry.url.includes('api/flyarchive.preview.page')), 'the PDF page came through the plugin address').toBe(true)
    expect(previewCalls.every((entry) => entry.method === 'POST'), 'preview requests are POST: the path is not in the address').toBe(true)
  })

  test('программа в карантине показана только сведениями; файл из архива называет архив и путь внутри него', async ({ app, stand }) => {
    const tag = kit.nonce()
    const seen = watch(app.page)
    stand.drop('program', tag)
    stand.drop('archive_program', tag)
    stand.later(() => stand.sweep(tag))
    await stand.runInbox()
    await app.freshTab()

    const program = app.tab.locator('li.ba-item').filter({ hasText: `tool-${tag}.exe` })
    await expect(program).toHaveCount(1)
    await program.locator('.ba-expander').click()
    const view = program.locator('[data-preview]')
    await expect(view).toContainText('A program or a script: its contents are not shown', { timeout: 60000 })
    await expect(view).toContainText(`tool-${tag}.exe`)
    await expect(view).toContainText('Type:')
    const onDisk = stand.quarantineItems().find((entry) => entry.name === `tool-${tag}.exe`)
    await expect(view).toContainText(`Size: ${fs.statSync(path.join(stand.archive, ...onDisk.path.split('/'))).size} B`)
    await expect(view).toContainText('SHA-256:')
    // только сведения: ни картинки, ни текста файла, ни таблицы
    await expect(view.locator('img, .ba-preview-text, table')).toHaveCount(0)

    const inner = app.tab.locator('li.ba-item').filter({ hasText: `setup-${tag}.exe` })
    await expect(inner).toHaveCount(1)
    await inner.locator('.ba-expander').click()
    const innerView = inner.locator('[data-preview]')
    await expect(innerView).toContainText('A program or a script: its contents are not shown', { timeout: 60000 })
    await expect(innerView).toContainText(`Archive: setup-${tag}.zip`)
    await expect(innerView).toContainText(`Path inside the archive: setup-${tag}.exe`)
    await expect(innerView.locator('img, .ba-preview-text')).toHaveCount(0)

    expect(app.outside, 'addresses other than DSH that the page tried to reach').toEqual([])
    expect(onlyDsh(seen, stand).map((entry) => new URL(entry.url).origin), 'requests to other origins').toEqual([])
  })
})

test.describe('просмотр: архив в карантине', () => {
  test('архив, который приёмка не приняла целиком, лежит в карантине и показан составом: имена и размеры, без распаковки', async ({ app, stand }) => {
    const tag = kit.nonce()
    const seen = watch(app.page)
    stand.drop('archive_many', tag) // файлов больше предела приёмки: архив уходит в карантин целиком
    stand.later(() => stand.sweep(tag))
    await stand.runInbox()
    const item = stand.quarantineItems().find((entry) => entry.name === `many-${tag}.zip`)
    expect(item, 'the archive is in quarantine as a whole').toBeTruthy()
    await app.freshTab()

    const archive = app.tab.locator('li.ba-item').filter({ hasText: `many-${tag}.zip` })
    await expect(archive).toHaveCount(1)
    await archive.locator('.ba-expander').click()
    const view = archive.locator('[data-preview]')
    const listing = view.locator('table')
    await expect(listing).toContainText('data/part-0000.txt', { timeout: 60000 })
    await expect(listing.locator('tbody tr')).toHaveCount(500) // состав: первые 500 строк
    await expect(listing.locator('tbody tr').first()).toContainText('14 B')
    await expect(view).toContainText('The list is truncated: only the first 500 entries are shown.')
    await expect(view).toContainText(`many-${tag}.zip`)
    await expect(view.locator('img, .ba-preview-text')).toHaveCount(0)
    // состав показан без распаковки: во входящей и в корпусе ничего из архива не появилось
    expect(stand.queueItems().some((entry) => entry.name.includes('part-')), 'nothing was extracted into the queue').toBe(false)
    const unpacked = fs.readdirSync(path.join(stand.archive, 'corpus'), { recursive: true }).filter((entry) => entry.includes(`many-${tag}`))
    expect(unpacked, 'nothing from the archive reached the corpus').toEqual([])

    expect(app.outside, 'addresses other than DSH that the page tried to reach').toEqual([])
    expect(onlyDsh(seen, stand).map((entry) => new URL(entry.url).origin), 'requests to other origins').toEqual([])
  })
})

test.describe('просмотр: настоящий docker не трогается', () => {
  test('стенд подменяет docker: за прогон просмотров ни один контейнер не запущен, остановлен или удалён', async ({ stand }) => {
    // просмотр писем, PDF, текста, архивов и программ контейнера не требует; команда preview при старте только спрашивает у docker список своих
    // осиротевших контейнеров (ps), и этот вопрос уходит в подставной docker стенда. Любое другое обращение — тест красный.
    stand.cliJson(['preview', 'clean', '--json'])
    const log = path.join(stand.dir, 'docker.log')
    const calls = fs.existsSync(log) ? fs.readFileSync(log, 'utf8').split('\n').filter(Boolean) : []
    const risky = calls.filter((call) => /^(run|pull|create|start|restart|kill|stop|rm|exec|build|load|import|cp|commit|push)\b/.test(call))
    expect(risky, 'docker calls that would start, change or remove something').toEqual([])
  })
})
