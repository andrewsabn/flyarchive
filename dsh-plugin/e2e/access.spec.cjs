'use strict'
/*
 * FR-91: без входа. Адреса плагина без сеанса DSH отвечают отказом; выпуск, отзыв и решения недоступны — и ничего не меняют.
 * Сеанс DSH — подписанная cookie; чужие Origin и Host DSH отклоняет сам. Кроме ответов проверяется, что в архиве ничего не изменилось.
 */
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')
const { test, expect, kit } = require('./fixtures.cjs')

/** Запрос без fetch: можно задать любой Host. */
function raw(url, { method = 'GET', headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const target = new URL(url)
    const request = http.request({ host: target.hostname, port: target.port, path: target.pathname + target.search, method, headers }, (response) => {
      const chunks = []
      response.on('data', (chunk) => chunks.push(chunk))
      response.on('end', () => resolve({ status: response.statusCode, text: Buffer.concat(chunks).toString('utf8') }))
    })
    request.on('error', reject)
    if (body !== undefined) request.write(body)
    request.end()
  })
}

const ROUTES = [
  ['GET', 'api/flyarchive.tokens'], ['POST', 'api/flyarchive.tokens'], ['POST', 'api/flyarchive.tokens.revoke'], ['GET', 'api/flyarchive.journal'],
  ['GET', 'api/flyarchive.inbox'], ['POST', 'api/flyarchive.inbox'], ['POST', 'api/flyarchive.inbox.run'], ['GET', 'api/flyarchive.queue'],
  ['POST', 'api/flyarchive.queue'], ['GET', 'api/flyarchive.quarantine'], ['POST', 'api/flyarchive.quarantine'], ['POST', 'api/flyarchive.doc.delete'],
  ['GET', 'api/flyarchive.batches'], ['GET', 'api/flyarchive.batch?id=20260101-000000'], ['POST', 'api/flyarchive.preview'], ['POST', 'api/flyarchive.preview.page'],
]

const systemctlCalls = (stand) => fs.readFileSync(path.join(stand.dir, 'systemctl.log'), 'utf8').split('\n').filter(Boolean).length

test.describe('без входа', () => {
  test('без сеанса DSH все адреса плагина отвечают отказом, а выпуск, отзыв, решения и удаление ничего не меняют', async ({ stand }) => {
    const tag = kit.nonce()
    const victim = kit.clientName('victim')
    const victimToken = stand.issue(victim, 'read', 1)
    stand.drop('tricky', tag)
    stand.drop('program', tag)
    stand.drop('good', tag)
    stand.later(() => stand.sweep(tag))
    await stand.runInbox()
    const memo = stand.queueItems().find((item) => item.name === `memo-${tag}.txt`)
    const tool = stand.quarantineItems().find((item) => item.name === `tool-${tag}.exe`)
    const docs = await stand.search(victimToken, `Сверка остатков по корреспондентским счетам ${tag}`)
    const doc = docs.find((hit) => hit.path.includes(`note-${tag}.md`))
    expect(memo && tool && doc, 'preparation: a queued file, a quarantined program and a searchable document').toBeTruthy()
    const before = { tokens: JSON.stringify(stand.tokens()), status: JSON.stringify(stand.status()), calls: systemctlCalls(stand) }

    // каждый адрес без cookie: 401
    for (const [method, route] of ROUTES) {
      const answer = await raw(`${stand.dshUrl}/${route}`, { method, headers: { 'content-type': 'application/json' }, body: method === 'POST' ? '{}' : undefined })
      expect(answer.status, `${method} ${route} without a DSH session`).toBe(401)
    }
    // действия, которые что-то меняли бы: с настоящими данными тела — и всё равно отказ
    const attempts = [
      ['api/flyarchive.tokens', { name: kit.clientName('intruder'), level: 'full' }],
      ['api/flyarchive.tokens.revoke', { name: victim }],
      ['api/flyarchive.queue', { path: memo.path, action: 'accept' }],
      ['api/flyarchive.queue', { paths: [memo.path], action: 'quarantine' }],
      ['api/flyarchive.quarantine', { path: tool.path, action: 'delete' }],
      ['api/flyarchive.quarantine', { paths: [tool.path], action: 'return' }],
      ['api/flyarchive.doc.delete', { path: doc.path, confirm: true }],
      ['api/flyarchive.inbox', { period: 1, llm: true }],
      ['api/flyarchive.inbox.run', {}],
    ]
    for (const [route, body] of attempts) {
      const answer = await raw(`${stand.dshUrl}/${route}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
      expect(answer.status, `POST ${route} ${JSON.stringify(Object.keys(body))} without a session`).toBe(401)
    }

    // и ничего не изменилось: токены, настройка, очередь, карантин, поиск, службы
    expect(JSON.stringify(stand.tokens()), 'the token store is unchanged').toBe(before.tokens)
    expect((await stand.toolNames(victimToken)).status, 'the victim token still works').toBe(200)
    expect(JSON.stringify(stand.status()), 'the inbox state is unchanged').toBe(before.status)
    expect(systemctlCalls(stand), 'no intake run or timer change was started').toBe(before.calls)
    expect(stand.queueItems().some((item) => item.path === memo.path), 'the queued file is still waiting').toBe(true)
    expect(stand.quarantineItems().some((item) => item.path === tool.path), 'the program is still in quarantine').toBe(true)
    const again = await stand.search(victimToken, `Сверка остатков по корреспондентским счетам ${tag}`)
    expect(again.some((hit) => hit.path === doc.path), 'the document is still searchable').toBe(true)
    expect(fs.existsSync(path.join(stand.archive, 'corpus', ...doc.path.split('/')))).toBe(true)
  })

  test('с чужим Origin или Host запрос выпуска токена отклоняется даже с настоящим сеансом; со своим адресом тот же запрос проходит', async ({ stand }) => {
    const state = JSON.parse(fs.readFileSync(stand.statePath(), 'utf8'))
    const cookie = state.cookies.map((entry) => `${entry.name}=${entry.value}`).join('; ')
    expect(cookie.length, 'the stand session cookie exists').toBeGreaterThan(0)
    const issue = (headers, name) => raw(`${stand.dshUrl}/api/flyarchive.tokens`, {
      method: 'POST', headers: { 'content-type': 'application/json', cookie, ...headers }, body: JSON.stringify({ name, level: 'read', days: 1 }),
    })
    const own = new URL(stand.dshUrl).host
    const alien = kit.clientName('alien')
    const badOrigin = await issue({ origin: 'http://evil.example', host: own }, alien)
    expect(badOrigin.status, 'a foreign Origin is refused').toBe(403)
    const badHost = await issue({ origin: stand.dshUrl, host: 'evil.example' }, alien)
    expect(badHost.status, 'a foreign Host is refused').toBeGreaterThanOrEqual(400)
    expect(stand.tokens().some((row) => row.name === alien), 'no token was issued by the refused requests').toBe(false)

    // контроль: с настоящими Host и Origin тот же запрос проходит — иначе отказы выше ничего не доказывают
    const control = kit.clientName('control')
    const good = await issue({ origin: stand.dshUrl, host: own }, control)
    expect(good.status).toBe(200)
    stand.remember(JSON.parse(good.text).token)
    expect(stand.tokens().some((row) => row.name === control), 'the control request issued a token').toBe(true)
    stand.later(() => stand.cli(['token', 'revoke', control, '--json']))
  })

  test('страница без входа не показывает архив; а потеря сеанса на открытой странице: кнопки не действуют, экран просит войти заново', async ({ app, stand, browser }) => {
    // 1. новая страница без сеанса: DSH просит войти, плагина на экране нет
    const bare = await browser.newContext()
    const anonymous = await bare.newPage()
    await anonymous.goto(stand.dshUrl + '/', { waitUntil: 'domcontentloaded' })
    const text = await anonymous.evaluate(() => document.body.innerText)
    expect(text).toMatch(/authentication required/i)
    expect(await anonymous.locator('.ba-section, .ba-tab').count(), 'no archive screen without a session').toBe(0)
    const fromPage = await anonymous.evaluate(async () => (await fetch('api/flyarchive.tokens', { method: 'POST', body: '{}' })).status)
    expect(fromPage, 'the page itself gets a refusal too').toBe(401)
    await bare.close()

    // 2. сеанс потерян на открытой странице: вкладка и настройки показывают, что вход истёк, и ничего не делают
    await app.openTab()
    const calls = systemctlCalls(stand)
    const tokensBefore = JSON.stringify(stand.tokens())
    await app.context.clearCookies()
    await app.tab.getByRole('button', { name: 'Run now', exact: true }).click()
    await expect(app.tab.getByRole('alert')).toContainText('Your DSH sign-in has expired', { timeout: 30000 })
    expect(systemctlCalls(stand), 'Run now without a session started nothing').toBe(calls)

    await app.openSettings({ loaded: false }) // без сеанса таблицы не загрузятся
    const name = kit.clientName('nosession')
    await app.tokenPart.locator('form input[name="name"]').fill(name)
    await app.tokenPart.locator('form').getByRole('button', { name: 'Issue', exact: true }).click()
    await expect(app.settings.getByRole('alert').first()).toContainText('Your DSH sign-in has expired', { timeout: 30000 })
    await expect(app.issuedBox, 'no token is shown').toHaveCount(0)
    expect(JSON.stringify(stand.tokens()), 'no token was issued').toBe(tokensBefore)
  })
})
