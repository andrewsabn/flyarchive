'use strict'
/*
 * FR-91: удаление документа из настроек. Путь — как в выдаче поиска; подтверждение вторым нажатием; документ исчезает из поиска по MCP,
 * файл остаётся в папке удалённого (не стирается).
 */
const fs = require('node:fs')
const path = require('node:path')
const { test, expect, kit } = require('./fixtures.cjs')

const noticeOf = (app) => app.settings.locator(':scope > [role="status"]')
const query = (tag) => `Сверка остатков по корреспондентским счетам ${tag}`

test.describe('удаление документа', () => {
  test('удаление документа из настроек: он исчезает из поиска по MCP, а файл лежит в папке удалённого', async ({ app, stand }) => {
    const tag = kit.nonce()
    const other = kit.nonce()
    const token = stand.issue(kit.clientName('delete'), 'read', 1)
    stand.drop('good', tag)
    stand.drop('good', other) // соседний документ: его удаление не касается
    await stand.runInbox()
    const hits = await stand.waitSearch(token, query(tag), (found) => found.some((hit) => hit.path.includes(`note-${tag}`)))
    const doc = hits.find((hit) => hit.path.includes(`note-${tag}.md`))
    expect(doc, 'the document is found by search before the removal').toBeTruthy()
    const neighbour = (await stand.waitSearch(token, query(other), (found) => found.some((hit) => hit.path.includes(`note-${other}`)))).find((hit) => hit.path.includes(`note-${other}.md`))
    expect(neighbour).toBeTruthy()
    const inCorpus = path.join(stand.archive, 'corpus', ...doc.path.split('/'))
    expect(fs.existsSync(inCorpus)).toBe(true)

    await app.openSettings()
    const box = app.settings.locator('[data-section="delete"]')
    await box.locator('input[name="path"]').fill(doc.path)
    await box.getByRole('button', { name: 'Remove from archive', exact: true }).click()
    // первое нажатие — только вопрос: документ на месте
    await expect(box.getByRole('button', { name: 'Yes, remove from archive and index', exact: true })).toBeVisible()
    expect(fs.existsSync(inCorpus), 'before the confirmation the file is in the corpus').toBe(true)
    expect((await stand.search(token, query(tag))).some((hit) => hit.path === doc.path), 'before the confirmation the document is searchable').toBe(true)
    await box.getByRole('button', { name: 'Yes, remove from archive and index', exact: true }).click()

    const notice = noticeOf(app)
    await expect(notice).toContainText('Document removed from the archive', { timeout: 60000 })
    const moved = /file moved to (\S+?)\.?$/.exec((await notice.innerText()).trim())
    expect(moved, 'the message names where the file went').toBeTruthy()
    const trashed = path.join(stand.archive, ...moved[1].split('/'))
    expect(fs.existsSync(trashed), 'the file lies in the deleted folder').toBe(true)
    expect(moved[1].startsWith('удалённое/'), 'it is the deleted folder of the archive').toBe(true)
    expect(fs.existsSync(inCorpus), 'the file left the corpus').toBe(false)
    expect(fs.readFileSync(trashed, 'utf8')).toContain(tag) // не стёрт: содержимое на месте

    // снаружи: из поиска по MCP исчез, соседний на месте; запись в журнале обращений
    const after = await stand.search(token, query(tag))
    expect(after.some((hit) => hit.path === doc.path), 'the removed document is not found by search over MCP').toBe(false)
    expect((await stand.search(token, query(other))).some((hit) => hit.path === neighbour.path), 'the neighbour document is still found').toBe(true)
    expect(stand.journal(50).some((entry) => entry.tool === 'doc delete' && entry.status === 0), 'the removal is journaled').toBe(true)
  })

  test('удаление несуществующего документа отклоняется с сообщением, ничего не меняется', async ({ app, stand }) => {
    const before = stand.status()
    await app.openSettings()
    const box = app.settings.locator('[data-section="delete"]')
    await box.locator('input[name="path"]').fill(`входящие/00000000-000000/no-such-${kit.nonce()}.md`)
    await box.getByRole('button', { name: 'Remove from archive', exact: true }).click()
    await box.getByRole('button', { name: 'Yes, remove from archive and index', exact: true }).click()
    await expect(app.settings.getByRole('alert').first()).toBeVisible({ timeout: 30000 })
    await expect(noticeOf(app)).toHaveCount(0)
    expect(stand.status().attention).toBe(before.attention)
  })
})
