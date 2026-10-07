'use strict'
/*
 * FR-91: приёмка из интерфейса. «Run now» и ход разбора; решения по файлам (Accept, Quarantine, Delete со вторым нажатием, Return);
 * решения пачкой и счётчик в заголовке вкладки. Результат сверяется снаружи: поиском по MCP, командой и файлами стенда.
 * Файлы у каждого теста свои (метка в имени и в тексте); токен для поиска — свой, уровня read.
 */
const fs = require('node:fs')
const path = require('node:path')
const { test, expect, kit } = require('./fixtures.cjs')

const row = (app, tag) => app.tab.locator('li.ba-item').filter({ hasText: tag })
const titleCount = async (app) => {
  const text = await app.tabTitle.first().innerText()
  const found = /\((\d+)\)/.exec(text)
  return found ? Number(found[1]) : 0
}
const searchFor = (tag) => `Служебная записка ${tag} проверке нагрузочных сценариев`
const noteQuery = (tag) => `Сверка остатков по корреспондентским счетам ${tag}`

/** Файл с указанием для модели → очередь (по команде, без браузера): подготовка к решению в интерфейсе. */
async function queued(stand, tag, count = 1) {
  const names = stand.drop('tricky', tag, count)
  stand.later(() => stand.sweep(tag))
  await stand.runInbox()
  return names
}

test.describe('приёмка: «Run now» и ход разбора', () => {
  test('«Run now»: виден ход разбора; хороший файл принят и найден поиском по MCP; файл с указанием для модели ждёт решения; программа в карантине', async ({ app, stand }) => {
    const tag = kit.nonce()
    const token = stand.issue(kit.clientName('intake'), 'read', 1)
    stand.later(() => stand.sweep(tag))
    stand.drop('good', tag)
    stand.drop('tricky', tag)
    stand.drop('program', tag)
    stand.setEmbedDelay(4000) // индексация хорошего файла идёт 4 с: ход разбора успевает попасть в опрос страницы (раз в 2 с)
    stand.later(() => stand.setEmbedDelay(0))

    await app.openTab()
    await expect(app.tab).toContainText('Waiting in the inbox folder:')
    const runNow = app.tab.getByRole('button', { name: 'Run now', exact: true })
    await expect(runNow).toBeEnabled()
    await runNow.click()

    // ход разбора: полоса, этап и счётчик файлов
    const progress = app.tab.getByRole('progressbar', { name: 'Intake progress' })
    await expect(progress).toBeVisible({ timeout: 30000 })
    await expect(app.tab).toContainText(/Unpacking and rule checks|Model check|Filing and de-duplication|Indexing|Cleaning up sources/)
    await expect(app.tab.getByText(/\d+ of \d+|\d+ done/).first()).toBeVisible()
    await expect(runNow).toBeDisabled() // пока идёт разбор, второй запуск невозможен

    // конец разбора: ход ушёл, решения на экране
    await expect(progress).toBeHidden({ timeout: 60000 })
    stand.setEmbedDelay(0)
    const memo = row(app, `memo-${tag}`)
    const tool = row(app, `tool-${tag}`)
    await expect(memo).toHaveCount(1, { timeout: 30000 })
    await expect(tool).toHaveCount(1)
    await expect(memo).toContainText('review')
    await expect(tool).toContainText('quarantine')
    await expect(row(app, `note-${tag}`), 'the good file is not asked about').toHaveCount(0)
    await expect(app.tab).toContainText(`Last batch ${stand.status().last_batch.batch}`)

    // снаружи: хороший файл найден поиском по MCP, остальные в индекс не попали
    const found = await stand.waitSearch(token, noteQuery(tag), (hits) => hits.some((hit) => hit.path.includes(`note-${tag}`)))
    expect(found.some((hit) => hit.path.includes(`note-${tag}.md`)), 'the good file is found by search over MCP').toBe(true)
    expect(found.some((hit) => hit.path.includes(`memo-${tag}`) || hit.path.includes(`tool-${tag}`)), 'files waiting for a decision are not in search').toBe(false)
    // и командой: указание ждёт решения, программа в карантине
    expect(stand.queueItems().some((item) => item.name === `memo-${tag}.txt`)).toBe(true)
    expect(stand.quarantineItems().some((item) => item.name === `tool-${tag}.exe`)).toBe(true)
    expect(stand.queueItems().some((item) => item.name.includes('note-'))).toBe(false)
  })
})

test.describe('приёмка: решения по файлам', () => {
  test('Accept переносит файл из очереди в архив и в поиск', async ({ app, stand }) => {
    const tag = kit.nonce()
    const token = stand.issue(kit.clientName('accept'), 'read', 1)
    await queued(stand, tag)
    await app.freshTab()
    const memo = row(app, `memo-${tag}`)
    await expect(memo).toHaveCount(1)
    expect((await stand.search(token, searchFor(tag))).some((hit) => hit.path.includes(`memo-${tag}`)), 'before the decision the file is not in search').toBe(false)

    await memo.getByRole('button', { name: 'Accept', exact: true }).click()
    await expect(memo).toHaveCount(0, { timeout: 60000 })
    expect(stand.queueItems().some((item) => item.name === `memo-${tag}.txt`), 'the file left the queue').toBe(false)
    const hits = await stand.waitSearch(token, searchFor(tag), (found) => found.some((hit) => hit.path.includes(`memo-${tag}`)))
    const hit = hits.find((entry) => entry.path.includes(`memo-${tag}`))
    expect(hit, 'the accepted file is found by search over MCP').toBeTruthy()
    expect(fs.existsSync(path.join(stand.archive, 'corpus', ...hit.path.split('/'))), 'the file lies in the archive corpus').toBe(true)
  })

  test('Quarantine переносит файл из очереди в карантин, в поиск он не попадает', async ({ app, stand }) => {
    const tag = kit.nonce()
    const token = stand.issue(kit.clientName('quar'), 'read', 1)
    await queued(stand, tag)
    await app.freshTab()
    const memo = row(app, `memo-${tag}`)
    await expect(memo).toContainText('review')
    await memo.getByRole('button', { name: 'Quarantine', exact: true }).click()
    // строка осталась в списке «Needs decision», но уже карантинная: у неё Return и Delete
    await expect(memo.getByRole('button', { name: 'Return', exact: true })).toBeVisible({ timeout: 30000 })
    await expect(memo).toContainText('quarantine')
    await expect(memo.getByRole('button', { name: 'Accept', exact: true })).toHaveCount(0)
    expect(stand.queueItems().some((item) => item.name === `memo-${tag}.txt`), 'not in the queue any more').toBe(false)
    const item = stand.quarantineItems().find((entry) => entry.name === `memo-${tag}.txt`)
    expect(item, 'the file is in quarantine').toBeTruthy()
    expect(fs.existsSync(path.join(stand.archive, ...item.path.split('/')))).toBe(true)
    expect((await stand.search(token, searchFor(tag))).some((hit) => hit.path.includes(`memo-${tag}`)), 'a quarantined file is not in search').toBe(false)
  })

  test('Delete из карантина требует второго нажатия: после первого файл на месте, после второго стёрт', async ({ app, stand }) => {
    const tag = kit.nonce()
    stand.drop('program', tag)
    stand.later(() => stand.sweep(tag))
    await stand.runInbox()
    const item = stand.quarantineItems().find((entry) => entry.name === `tool-${tag}.exe`)
    expect(item, 'the program is in quarantine').toBeTruthy()
    const onDisk = path.join(stand.archive, ...item.path.split('/'))
    await app.freshTab()
    const tool = row(app, `tool-${tag}`)
    await expect(tool).toHaveCount(1)

    await tool.getByRole('button', { name: 'Delete', exact: true }).click() // первое нажатие: только вопрос
    await expect(tool).toContainText('Delete permanently?')
    expect(fs.existsSync(onDisk), 'after the first click the file is still there').toBe(true)
    expect(stand.quarantineItems().some((entry) => entry.name === `tool-${tag}.exe`)).toBe(true)
    await tool.getByRole('button', { name: 'Cancel', exact: true }).click() // отказ от стирания ничего не стирает
    await expect(tool.getByRole('button', { name: 'Delete', exact: true })).toBeVisible()
    expect(fs.existsSync(onDisk)).toBe(true)

    await tool.getByRole('button', { name: 'Delete', exact: true }).click()
    await tool.getByRole('button', { name: 'Yes, delete forever', exact: true }).click() // второе нажатие
    await expect(tool).toHaveCount(0, { timeout: 30000 })
    expect(fs.existsSync(onDisk), 'after the second click the file is erased').toBe(false)
    expect(stand.quarantineItems().some((entry) => entry.name === `tool-${tag}.exe`)).toBe(false)
  })

  test('Return из карантина кладёт файл в папку возврата', async ({ app, stand }) => {
    const tag = kit.nonce()
    stand.drop('program', tag)
    stand.later(() => stand.sweep(tag))
    await stand.runInbox()
    const returned = stand.status().returned
    await app.freshTab()
    const tool = row(app, `tool-${tag}`)
    await expect(tool).toHaveCount(1)
    await tool.getByRole('button', { name: 'Return', exact: true }).click()
    await expect(tool).toHaveCount(0, { timeout: 30000 })

    expect(stand.quarantineItems().some((entry) => entry.name === `tool-${tag}.exe`), 'not in quarantine any more').toBe(false)
    const landed = fs.existsSync(returned) ? fs.readdirSync(returned, { recursive: true }).filter((entry) => entry.endsWith(`tool-${tag}.exe`)) : []
    expect(landed.length, 'the file lies in the returns folder').toBe(1)
  })
})

test.describe('приёмка: решения пачкой и счётчик', () => {
  test('«Accept selected» принимает все отмеченные файлы, счётчик в заголовке вкладки уменьшается', async ({ app, stand }) => {
    const tag = kit.nonce()
    const token = stand.issue(kit.clientName('bulk'), 'read', 1)
    const names = await queued(stand, tag, 3)
    expect(names.length).toBe(3)
    await app.freshTab()
    await expect(app.tab.locator('li.ba-item').filter({ hasText: tag })).toHaveCount(3)
    const start = await titleCount(app)
    expect(start, 'the tab title counts files that wait').toBeGreaterThanOrEqual(3)
    await expect(app.tabTitle.first()).toContainText(`Archive (${start})`)

    for (const name of names) await app.tab.getByRole('checkbox', { name: `Select ${name}` }).check()
    await app.tab.getByRole('button', { name: 'Accept selected', exact: true }).click()
    await expect(app.tab).toContainText('Accept 3 files into the archive?')
    expect(stand.queueItems().filter((item) => item.name.includes(tag)).length, 'nothing moves before the confirmation').toBe(3)
    await app.tab.getByRole('button', { name: 'Yes, accept 3', exact: true }).click()

    await expect(app.tab.locator('li.ba-item').filter({ hasText: tag })).toHaveCount(0, { timeout: 60000 })
    await expect.poll(() => titleCount(app), { timeout: 30000 }).toBe(start - 3)
    expect(stand.queueItems().filter((item) => item.name.includes(tag)).length).toBe(0)
    // принятое перенесено в архив сразу, а в поиске появляется после индексации в фоне (её запускает служба разбора)
    for (let i = 1; i <= 3; i += 1) {
      const hits = await stand.waitSearch(token, `Служебная записка ${tag}${i} проверке нагрузочных сценариев`, (found) => found.some((hit) => hit.path.includes(`memo-${tag}-${i}`)), 90000)
      expect(hits.some((hit) => hit.path.includes(`memo-${tag}-${i}`)), `memo ${i} is found by search over MCP`).toBe(true)
    }
  })
})
