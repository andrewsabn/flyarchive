// Серверная половина раздела «Архив» в DSH: FR-50. Запуск: node --test dsh-plugin/test
import assert from 'node:assert/strict'
import test from 'node:test'

import { JOURNAL_PATH, REVOKE_PATH, TOKENS_PATH, apply, createApi, defaultCommand, inject, name, routes } from '../lib/index.js'

const CMD = '/opt/flyarchive/tools/flyarchive'
const ok = (data) => ({ code: 0, stdout: JSON.stringify(data), stderr: '' })
// так отказывает настоящая команда с --json: последняя строка stderr — объект-сообщение
const refuse = (text, code = 'refused') => ({ code: 1, stdout: '', stderr: `${JSON.stringify({ error: { code, args: {}, text } })}\n` })

/** Подставная команда flyarchive: запоминает вызовы и отвечает по сценарию. */
function cli(script = () => ok([])) {
  const calls = []
  const run = async (command, args) => {
    calls.push([command, ...args])
    return script(args)
  }
  const table = Object.fromEntries(routes(createApi({ command: CMD, run })).map((r) => [r.path, r]))
  const call = async (routePath, method, { query = '', body, raw } = {}) => {
    const init = { method }
    if (raw !== undefined) init.body = raw
    else if (body !== undefined) init.body = JSON.stringify(body)
    const response = await table[routePath].fetch(new Request(`http://127.0.0.1:3080${routePath}${query}`, init))
    return { status: response.status, data: await response.json(), headers: response.headers }
  }
  return { calls, call, table }
}

test('плагин называет себя и просит у DSH только соединение', () => {
  assert.equal(name, 'flyarchive-admin')
  assert.deepEqual(inject, ['connection'])
})

test('все адреса под /api — за входом DSH', () => {
  const { table } = cli()
  assert.equal(Object.keys(table).length, 12)
  for (const route of Object.values(table)) {
    assert.match(route.path, /^\/api\/flyarchive\.[a-z.]+$/)
    assert.equal(route.requestBody, 'buffered')
  }
  assert.deepEqual(table[TOKENS_PATH].methods, ['GET', 'POST'])
  assert.deepEqual(table[REVOKE_PATH].methods, ['POST'])
  assert.deepEqual(table[JOURNAL_PATH].methods, ['GET'])
})

test('список токенов читается командой flyarchive', async () => {
  const rows = [{ name: 'ноутбук', level: 'read', state: 'действует' }]
  const { calls, call } = cli(() => ok(rows))
  const r = await call(TOKENS_PATH, 'GET')
  assert.equal(r.status, 200)
  assert.deepEqual(r.data, { tokens: rows })
  assert.deepEqual(calls, [[CMD, 'token', 'list', '--json']])
  assert.equal(r.headers.get('cache-control'), 'no-store')
})

test('выпуск токена: значение отдаётся один раз и не кэшируется', async () => {
  const issued = { name: 'ноутбук', level: 'full', token: 'ba_' + 'x'.repeat(43), expires: '2026-11-03T10:00:00Z' }
  const { calls, call } = cli(() => ok(issued))
  const r = await call(TOKENS_PATH, 'POST', { body: { name: 'ноутбук', level: 'full', days: 30 } })
  assert.equal(r.status, 200)
  assert.deepEqual(r.data, issued)
  assert.deepEqual(calls, [[CMD, 'token', 'add', 'ноутбук', '--level', 'full', '--json', '--days', '30']])
  assert.equal(r.headers.get('cache-control'), 'no-store')
})

test('выпуск без срока и уровня: чтение, бессрочно', async () => {
  const { calls, call } = cli(() => ok({ name: 'tablet.work_1', level: 'read', token: 'ba_y', expires: null }))
  const r = await call(TOKENS_PATH, 'POST', { body: { name: 'tablet.work_1' } })
  assert.equal(r.status, 200)
  assert.deepEqual(calls, [[CMD, 'token', 'add', 'tablet.work_1', '--level', 'read', '--json']])
  await call(TOKENS_PATH, 'POST', { body: { name: 'b', days: null } })
  assert.deepEqual(calls[1], [CMD, 'token', 'add', 'b', '--level', 'read', '--json'])
})

const BAD_ISSUE = [
  ['пустое имя', { name: '' }],
  ['имя с пробелом', { name: 'мой ноутбук' }],
  ['имя с путём', { name: '../x' }],
  ['имя, похожее на ключ команды', { name: '--json' }],
  ['имя с дефиса', { name: '-x' }],
  ['слишком длинное имя', { name: 'я'.repeat(65) }],
  ['имя не строка', { name: 7 }],
  ['нет имени', {}],
  ['уровень local выпускать нельзя', { name: 'a', level: 'local' }],
  ['неизвестный уровень', { name: 'a', level: 'admin' }],
  ['срок ноль', { name: 'a', days: 0 }],
  ['срок отрицательный', { name: 'a', days: -3 }],
  ['срок дробный', { name: 'a', days: 1.5 }],
  ['срок строкой', { name: 'a', days: '7' }],
  ['срок больше десяти лет', { name: 'a', days: 3651 }],
  ['тело — список', ['a']],
  ['тело — строка', 'a'],
]
for (const [title, body] of BAD_ISSUE) {
  test(`выпуск отклонён до вызова команды: ${title}`, async () => {
    const { calls, call } = cli()
    const r = await call(TOKENS_PATH, 'POST', { body })
    assert.equal(r.status, 400)
    assert.equal(typeof r.data.error, 'string')
    assert.ok(!/[Ѐ-ӿ]/.test(r.data.error), r.data.error) // отказ показывается на экране: по-английски
    assert.deepEqual(calls, [])
  })
}

test('тело не JSON — 400, команда не вызвана', async () => {
  const { calls, call } = cli()
  const r = await call(TOKENS_PATH, 'POST', { raw: '{name:' })
  assert.equal(r.status, 400)
  assert.deepEqual(calls, [])
})

test('отказ команды доходит до человека объектом-сообщением', async () => {
  const text = 'The name is taken by an active token: revoke it or pick another one'
  const { call } = cli(() => refuse(text, 'token_name_taken'))
  const r = await call(TOKENS_PATH, 'POST', { body: { name: 'ноутбук' } })
  assert.equal(r.status, 409)
  assert.deepEqual(r.data.error, { code: 'token_name_taken', args: {}, text })
})

test('отзыв токена', async () => {
  const { calls, call } = cli(() => ok({ name: 'ноутбук', revoked: true }))
  const r = await call(REVOKE_PATH, 'POST', { body: { name: 'ноутбук' } })
  assert.equal(r.status, 200)
  assert.deepEqual(r.data, { name: 'ноутбук', revoked: true })
  assert.deepEqual(calls, [[CMD, 'token', 'revoke', 'ноутбук', '--json']])
})

test('служебный токен самого DSH отсюда не отзывается', async () => {
  const { calls, call } = cli()
  const r = await call(REVOKE_PATH, 'POST', { body: { name: 'dsh-local' } })
  assert.equal(r.status, 400)
  assert.match(r.data.error, /service token/)
  assert.ok(!/[Ѐ-ӿ]/.test(r.data.error), r.data.error)
  assert.deepEqual(calls, [])
})

test('отзыв: негодное имя и неизвестный токен', async () => {
  const { calls, call } = cli(() => refuse('There is no active token with that name'))
  assert.equal((await call(REVOKE_PATH, 'POST', { body: { name: '--json' } })).status, 400)
  assert.equal((await call(REVOKE_PATH, 'POST', { body: {} })).status, 400)
  assert.deepEqual(calls, [])
  const r = await call(REVOKE_PATH, 'POST', { body: { name: 'нет' } })
  assert.equal(r.status, 409)
  assert.match(r.data.error.text, /no active token/)
})

test('журнал: число записей и отбор по клиенту', async () => {
  const recs = [{ ts: '2026-10-04T09:00:00Z', client: 'ноутбук', tool: '/api/search', status: 200 }]
  const { calls, call } = cli(() => ok(recs))
  const r = await call(JOURNAL_PATH, 'GET', { query: '?n=50&client=' + encodeURIComponent('ноутбук') })
  assert.equal(r.status, 200)
  assert.deepEqual(r.data, { records: recs })
  assert.deepEqual(calls, [[CMD, 'journal', '--json', '-n', '50', '--client', 'ноутбук']])
  assert.equal(r.headers.get('cache-control'), 'no-store')
})

test('журнал: по умолчанию сто записей, не больше пятисот', async () => {
  const { calls, call } = cli()
  await call(JOURNAL_PATH, 'GET')
  await call(JOURNAL_PATH, 'GET', { query: '?n=100000' })
  await call(JOURNAL_PATH, 'GET', { query: '?n=1' })
  assert.deepEqual(calls.map((c) => c.slice(1)), [
    ['journal', '--json', '-n', '100'], ['journal', '--json', '-n', '500'], ['journal', '--json', '-n', '1']])
})

for (const query of ['?n=abc', '?n=0', '?n=-5', '?n=1.5', '?client=--json', '?client=' + encodeURIComponent('a b'), '?client=']) {
  test(`журнал: негодный запрос ${query} — 400`, async () => {
    const { calls, call } = cli()
    assert.equal((await call(JOURNAL_PATH, 'GET', { query })).status, 400)
    assert.deepEqual(calls, [])
  })
}

test('в журнале можно искать клиента «-» и клиента-ссылку', async () => {
  const { calls, call } = cli()
  assert.equal((await call(JOURNAL_PATH, 'GET', { query: '?client=-' })).status, 200)
  assert.equal((await call(JOURNAL_PATH, 'GET', { query: '?client=' + encodeURIComponent('ссылка') })).status, 200)
  assert.deepEqual(calls.map((c) => c.at(-1)), ['-', 'ссылка'])
})

test('команда не запустилась — 502 с понятной причиной', async () => {
  const table = Object.fromEntries(routes(createApi({
    command: CMD,
    run: async () => {
      throw Object.assign(new Error('spawn ENOENT'), { code: 'ENOENT' })
    },
  })).map((r) => [r.path, r]))
  const response = await table[TOKENS_PATH].fetch(new Request('http://127.0.0.1:3080' + TOKENS_PATH))
  assert.equal(response.status, 502)
  assert.match((await response.json()).error, /flyarchive/)
})

test('команда ответила не JSON — 502', async () => {
  const { call } = cli(() => ({ code: 0, stdout: 'Токенов нет.', stderr: '' }))
  assert.equal((await call(TOKENS_PATH, 'GET')).status, 502)
})

test('чужой метод — 405', async () => {
  const { calls, call } = cli()
  assert.equal((await call(REVOKE_PATH, 'GET')).status, 405)
  assert.equal((await call(JOURNAL_PATH, 'POST', { body: {} })).status, 405)
  assert.deepEqual(calls, [])
})

test('значение токена не попадает в текст ошибки: ни в объекте-сообщении, ни в сыром тексте', async () => {
  const token = 'ba_' + 'z'.repeat(43)
  const object = cli(() => refuse(`failure near ${token}`))
  const a = await object.call(TOKENS_PATH, 'POST', { body: { name: 'a' } })
  assert.equal(a.status, 409)
  assert.ok(!JSON.stringify(a.data).includes('zzzz'))
  const raw = cli(() => ({ code: 1, stdout: '', stderr: `ошибка: сбой рядом с ${token}\n` }))
  const b = await raw.call(TOKENS_PATH, 'POST', { body: { name: 'a' } })
  assert.equal(b.status, 502) // сырая строка — не сообщение: наружу не уходит вовсе
  assert.ok(!JSON.stringify(b.data).includes('zzzz'))
})

test('путь к команде: настройка, переменная окружения, имя из пути поиска команд', () => {
  assert.equal(defaultCommand({ command: '/x/flyarchive' }, {}), '/x/flyarchive')
  assert.equal(defaultCommand({}, { FLYARCHIVE_CLI: '/y/flyarchive' }), '/y/flyarchive')
  assert.equal(defaultCommand({}, {}), 'flyarchive') // запасной путь внутри каталога архива убран: кода там нет (host-command.test.mjs)
})

test('apply регистрирует адреса в соединении DSH и снимает их вместе с плагином', () => {
  const registered = []
  const disposed = []
  const effects = []
  const ctx = {
    connection: {
      fetch: {
        register(route) {
          registered.push(route.path)
          return async () => {
            disposed.push(route.path)
          }
        },
      },
    },
    effect(make) {
      effects.push(make())
    },
  }
  apply(ctx, { command: CMD })
  assert.equal(new Set(registered).size, 12)
  for (const path of [JOURNAL_PATH, TOKENS_PATH, REVOKE_PATH]) assert.ok(registered.includes(path))
  assert.equal(effects.length, 12)
  for (const dispose of effects) dispose()
  assert.equal(disposed.length, 12)
})
