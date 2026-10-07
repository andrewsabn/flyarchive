'use strict'
/*
 * FR-91: токены из интерфейса. Выпуск и отзыв, уровни, срок, утечка значения. Результат сверяется снаружи: запросом по MCP, командой и
 * файлами стенда, а не только текстом страницы. У каждого теста свой токен.
 */
const fs = require('node:fs')
const path = require('node:path')
const { test, expect, kit } = require('./fixtures.cjs')

// инструменты, которые создают файлы или документы: токену read они недоступны
const CREATING = ['make_landscape', 'make_diagram', 'make_chart', 'make_document', 'submit_document']

const bodyText = (page) => page.evaluate(() => document.body.innerText)
const times = (text, needle) => text.split(needle).length - 1

test.describe('токены: выпуск и отзыв', () => {
  test('выпуск и отзыв токена read: значение показано один раз, по MCP только читающие инструменты, после отзыва 401', async ({ app, stand }) => {
    const { page } = app
    const name = kit.clientName('reader')
    let token
    await app.openSettings()

    await test.step('выпуск токена уровня read', async () => {
      token = await app.issueToken(name, 'read', '30')
      const shown = await bodyText(page)
      expect(times(shown, token), 'the token value is shown on the screen exactly once').toBe(1)
      await expect(app.issuedBox).toContainText('it is not shown a second time')
      await expect(app.tokenRow(name)).toHaveCount(1)
      expect(await app.tokenState(name)).toBe('active')
    })

    await test.step('tools/list по MCP с этим токеном: 200 и только читающие инструменты', async () => {
      const init = await stand.mcp(token, 'initialize', { protocolVersion: '2025-06-18', clientInfo: { name: name, version: '1' } })
      expect(init.status).toBe(200)
      const { status, names } = await stand.toolNames(token)
      expect(status).toBe(200)
      expect(names).toContain('search_archive')
      expect(names.filter((tool) => CREATING.includes(tool))).toEqual([])
    })

    await test.step('закрытие сообщения: значение с экрана уходит', async () => {
      await app.issuedBox.getByRole('button', { name: 'Dismiss', exact: true }).click()
      await expect(app.issuedBox).toHaveCount(0)
      expect(times(await bodyText(page), token), 'the token value is gone from the screen').toBe(0)
    })

    await test.step('отзыв в интерфейсе требует подтверждения; пока не подтверждён, токен действует', async () => {
      const row = app.tokenRow(name)
      await row.getByRole('button', { name: 'Revoke', exact: true }).click()
      await expect(row.getByRole('button', { name: 'Yes, close access', exact: true })).toBeVisible()
      expect((await stand.toolNames(token)).status).toBe(200)
      await row.getByRole('button', { name: 'Cancel', exact: true }).click()
      expect(await app.tokenState(name)).toBe('active')
      expect((await stand.toolNames(token)).status).toBe(200)
      await app.revokeToken(name)
      await expect.poll(() => app.tokenState(name)).toBe('revoked')
    })

    await test.step('следующий же запрос по MCP: 401; команда видит токен отозванным', async () => {
      expect((await stand.toolNames(token)).status).toBe(401)
      expect(stand.tokenRow(name).revoked).toBeTruthy()
      expect(stand.tokenRow(name).state).toBe('отозван')
    })

    await test.step('журнал обращений в интерфейсе: попытка с токеном и отказ после отзыва', async () => {
      await app.refreshSettings()
      const rows = await app.journalRows()
      // свежие записи сверху: последняя — отказ без токена-хозяина (401), раньше — обращение клиента с его именем
      expect(rows[0].join(' ')).toContain('401')
      const own = rows.filter((cells) => cells[1] === name)
      expect(own.map((cells) => cells[3]), 'the client call (initialize) is journaled under its name').toContain('mcp:initialize')
      expect(own.some((cells) => cells[4].startsWith('200'))).toBe(true)
    })
  })

  test('в журнале видны обе попытки с именем клиента: и обращение с токеном, и отказ после отзыва', async ({ app, stand }) => {
    const name = kit.clientName('journal')
    const token = stand.issue(name, 'read', 30)
    expect((await stand.mcp(token, 'initialize', { clientInfo: { name, version: '1' } })).status).toBe(200)
    expect((await stand.toolNames(token)).status).toBe(200)
    stand.cliJson(['token', 'revoke', name, '--json'])
    expect((await stand.toolNames(token)).status).toBe(401)
    await app.openSettings()
    const rows = await app.journalOf(name)
    expect(rows.length, 'both attempts are listed under the client name').toBeGreaterThanOrEqual(2)
    expect(rows.some((cells) => cells[4].startsWith('401')), 'the refused attempt is listed under the client name').toBe(true)
    expect(rows.some((cells) => cells[4].startsWith('200')), 'the accepted attempt is listed under the client name').toBe(true)
  })
})

test.describe('токены: уровни и срок', () => {
  test('у токена full в списке инструментов есть submit_document, у read — нет, и вызов создающего инструмента с read отклонён', async ({ app, stand }) => {
    const reader = kit.clientName('lvl-read')
    const writer = kit.clientName('lvl-full')
    await app.openSettings()
    const readToken = await app.issueToken(reader, 'read', '30')
    await app.issuedBox.getByRole('button', { name: 'Dismiss', exact: true }).click()
    const fullToken = await app.issueToken(writer, 'full', '30')
    await expect(app.tokenRow(reader).locator('td').nth(1)).toHaveText('read')
    await expect(app.tokenRow(writer).locator('td').nth(1)).toHaveText('full')

    const read = await stand.toolNames(readToken)
    const full = await stand.toolNames(fullToken)
    expect(read.status).toBe(200)
    expect(full.status).toBe(200)
    expect(full.names).toContain('submit_document')
    expect(read.names).not.toContain('submit_document')
    expect(full.names.length).toBeGreaterThan(read.names.length)
    for (const tool of read.names) expect(full.names).toContain(tool)

    // вызов создающего инструмента с токеном read: отказ, ничего не создано
    const tag = kit.nonce()
    const inboxFiles = () => fs.readdirSync(stand.inbox, { recursive: true })
    stand.later(() => { // submit_document кладёт файл в папку mcp-<клиент>-…: убирается, чтобы не попасть в чужой разбор
      for (const entry of fs.readdirSync(stand.inbox)) if (entry.startsWith(`mcp-${writer}-`)) fs.rmSync(path.join(stand.inbox, entry), { recursive: true, force: true })
    })
    const before = inboxFiles().length
    const refused = await stand.mcp(readToken, 'tools/call', {
      name: 'submit_document', arguments: { name: `refused-${tag}.txt`, content_base64: Buffer.from('должно быть отклонено').toString('base64') },
    })
    expect(refused.status).toBe(200)
    expect(refused.body.error, 'JSON-RPC error: the tool needs level full').toBeTruthy()
    expect(refused.body.result).toBeUndefined()
    expect(inboxFiles().length, 'nothing was created in the inbox').toBe(before)
    const refusedRecord = stand.journal(50, reader).filter((record) => record.tool === 'mcp:submit_document')
    expect(refusedRecord.length).toBe(1)
    expect(refusedRecord[0].status).toBe(403)

    // и с токеном full тот же вызов принят: файл лёг во входящую папку (проверка, что отказ выше — от уровня, а не от поломки)
    const accepted = await stand.mcp(fullToken, 'tools/call', {
      name: 'submit_document', arguments: { name: `accepted-${tag}.txt`, content_base64: Buffer.from('Принято токеном full ' + tag).toString('base64') },
    })
    expect(accepted.status).toBe(200)
    expect(accepted.body.error).toBeUndefined()
    expect(accepted.body.result.isError, 'submit_document with full is not an error').toBeFalsy()
    expect(inboxFiles().some((entry) => entry.endsWith(`accepted-${tag}.txt`)), 'the submitted file lies in the inbox').toBe(true)
  })

  test('токен с истёкшим сроком получает 401, а интерфейс показывает expired', async ({ app, stand }) => {
    const name = kit.clientName('expiring')
    await app.openSettings()
    const token = await app.issueToken(name, 'read', '1')
    expect(await app.tokenState(name)).toBe('active')
    await expect(app.tokenRow(name).locator('td').nth(3)).not.toHaveText('no expiry') // срок задан
    expect((await stand.toolNames(token)).status).toBe(200)

    stand.expire(name) // срок сдвинут в прошлое в хранилище токенов стенда (archive/secrets/tokens.json)
    expect((await stand.toolNames(token)).status).toBe(401)
    await app.refreshSettings()
    await expect.poll(() => app.tokenState(name)).toBe('expired')
    await expect(app.tokenRow(name).getByRole('button', { name: 'Revoke' })).toHaveCount(0) // просроченный токен отзывать не нужно
  })
})

test.describe('токены: утечка значения', () => {
  test('значение токена не остаётся ни в тексте страницы, ни в разметке, ни в хранилищах браузера, ни после перезагрузки', async ({ app, stand }) => {
    const { page } = app
    const name = kit.clientName('leak')
    // всё, что можно спросить у страницы, сводится к булевым значениям: сообщение о провале не повторит искомое
    const probe = (needle) => page.evaluate(async (value) => {
      const has = (text) => String(text).includes(value)
      const stores = (area) => has(JSON.stringify(Object.entries(area)))
      let indexed = false
      try {
        for (const info of (await indexedDB.databases()) || []) {
          const db = await new Promise((resolve, reject) => {
            const request = indexedDB.open(info.name)
            request.onsuccess = () => resolve(request.result)
            request.onerror = () => reject(request.error)
          })
          for (const store of Array.from(db.objectStoreNames)) {
            const rows = await new Promise((resolve, reject) => {
              const request = db.transaction(store, 'readonly').objectStore(store).getAll()
              request.onsuccess = () => resolve(request.result)
              request.onerror = () => reject(request.error)
            })
            try {
              if (has(JSON.stringify(rows))) indexed = true
            } catch {
              // нерасшифровываемое значение
            }
          }
          db.close()
        }
      } catch {
        // IndexedDB недоступна
      }
      let cached = false
      try {
        for (const key of await caches.keys()) {
          for (const request of await (await caches.open(key)).keys()) if (has(request.url)) cached = true
        }
      } catch {
        // кэша нет
      }
      return {
        text: has(document.body.innerText), markup: has(document.documentElement.outerHTML), local: stores(localStorage),
        session: stores(sessionStorage), indexed, cookie: has(document.cookie), url: has(location.href), title: has(document.title),
        name: has(window.name), history: has(JSON.stringify(history.state)), cached,
      }
    }, needle)
    const anywhere = (found) => Object.entries(found).filter(([, hit]) => hit).map(([where]) => where)
    const sentBack = []
    page.on('request', (request) => {
      if (request.url().includes('api/flyarchive.tokens')) return
      sentBack.push(request) // запросы страницы к чему угодно кроме самого выпуска: значения в них быть не должно
    })

    await app.openSettings()
    const token = await app.issueToken(name, 'read', '7')
    // контроль: пока сообщение на экране, значение в тексте и в разметке есть — иначе проверка ниже ничего бы не доказывала
    const shown = await probe(token)
    expect(shown.text && shown.markup, 'while the message is open the value is on the page (control)').toBe(true)
    expect(anywhere({ ...shown, text: false, markup: false }), 'the value is kept nowhere but the message while it is open').toEqual([])

    await app.issuedBox.getByRole('button', { name: 'Dismiss', exact: true }).click()
    await expect(app.issuedBox).toHaveCount(0)
    expect(anywhere(await probe(token)), 'after the message is closed').toEqual([])

    await app.closeSettings()
    await app.openSettings() // раздел собран заново
    expect(anywhere(await probe(token)), 'after the settings section is rebuilt').toEqual([])
    await app.openTab()
    expect(anywhere(await probe(token)), 'with the Archive tab open').toEqual([])

    await app.reload()
    expect(anywhere(await probe(token)), 'after the page is reloaded').toEqual([])
    await app.openSettings()
    expect(anywhere(await probe(token)), 'after reload with the settings section open').toEqual([])
    await expect(app.tokenRow(name)).toHaveCount(1) // токен в списке есть, а значения нет

    // страница не отправляла значение никуда: ни в адресах, ни в телах запросов
    const leakedOut = sentBack.filter((request) => request.url().includes(token) || String(request.postData() || '').includes(token))
    expect(leakedOut.length, 'requests that carry the value').toBe(0)
    // и серверные ответы со списками его не содержат
    const lists = await page.evaluate(async () => {
      const texts = []
      for (const route of ['api/flyarchive.tokens', 'api/flyarchive.journal?n=500', 'api/flyarchive.inbox']) texts.push(await (await fetch(route)).text())
      return texts
    })
    expect(lists.some((text) => text.includes(token)), 'the plugin lists carry the value').toBe(false)
    // журналы стенда (DSH, службы, журнал обращений) значения тоже не хранят
    const logs = [...fs.readdirSync(path.join(stand.dir, 'logs')).map((file) => path.join(stand.dir, 'logs', file)), path.join(stand.archive, 'logs', 'access.jsonl')]
    const inLogs = logs.filter((file) => fs.existsSync(file) && fs.readFileSync(file, 'latin1').includes(token))
    expect(inLogs.length, 'stand logs that carry the value').toBe(0)
  })

  test('в каталоге отчёта и в снимках теста нет значения токена и ссылки входа: следы выключены, снимок закрыт маской', async ({ app, stand }, testInfo) => {
    const { scan } = require('./leakscan.cjs')
    const use = testInfo.project.use
    for (const key of ['trace', 'video', 'screenshot']) {
      const value = typeof use[key] === 'object' && use[key] !== null ? use[key].mode : use[key]
      expect(value === undefined || value === 'off', `the Playwright option ${key} is off: a recording would hold the token`).toBe(true)
    }
    const name = kit.clientName('trace')
    await app.openSettings()
    const token = await app.issueToken(name, 'read', '7')
    // снимок с токеном на экране: значение закрыто маской, то есть затёрто до записи
    const snap = testInfo.outputPath('issued-masked.png')
    await app.page.screenshot({ path: snap, mask: [app.issuedBox.locator('.ba-token')], maskColor: '#000' })
    await testInfo.attach('issued-masked', { path: snap, contentType: 'image/png' })
    expect(fs.statSync(snap).size).toBeGreaterThan(1000)

    // каталог результатов и отчёта на этот момент: ни значения токена, ни ссылки входа, ни токен-подобных строк
    const known = [token, ...stand.seen()]
    const state = JSON.parse(fs.readFileSync(stand.statePath(), 'utf8'))
    for (const cookie of state.cookies) known.push(cookie.value)
    known.push(stand.link().split('token=')[1])
    const out = path.dirname(testInfo.project.outputDir)
    const hits = scan(out, known)
    expect(hits.map((hit) => `${hit.kind} in ${hit.file}`), 'secrets in the report and result directories').toEqual([])
  })
})
