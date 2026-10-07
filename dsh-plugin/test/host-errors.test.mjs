// Серверная половина: ошибки команды наружу — 409 только с объектом-сообщением, всё прочее — 502 с коротким английским текстом (FR-84).
// Сырой текст stderr (в том числе разбор аргументов argparse) наружу не уходит; отказы самого плагина — тоже по-английски.
import assert from 'node:assert/strict'
import test from 'node:test'

import * as host from '../lib/index.js'
import { createApi, routes } from '../lib/index.js'

const CMD = '/opt/flyarchive/tools/flyarchive'
const CYRILLIC = /[Ѐ-ӿ]/
const ok = (data) => ({ code: 0, stdout: typeof data === 'string' ? data : JSON.stringify(data), stderr: '' })
const fail = (stderr, code = 1) => ({ code, stdout: '', stderr })
const failBinary = (stderr, code = 1) => ({ code, stdout: Buffer.alloc(0), stderr })
const asLine = (error) => `${JSON.stringify({ error })}\n`
const ERROR = { code: 'archive_busy', args: { seconds: 5 }, text: 'The archive is busy' }
const PATH = 'очередь/20260101-120000/a.txt'
const ARGPARSE = 'usage: flyarchive queue accept [-h] [--defer-index] [--json] [--] paths [paths ...]\nflyarchive queue accept: error: unrecognized arguments: --defer-index\n'

/** Один запрос к одному адресу плагина; команда отвечает по сценарию, и текстовая, и двоичная. */
function cli(script = () => ok({}), binary = () => failBinary('')) {
  const table = Object.fromEntries(routes(createApi({
    command: CMD,
    run: async (_command, args) => script(args),
    runBinary: async (_command, args) => binary(args),
  })).map((r) => [r.path, r]))
  const send = async (routePath, method = 'GET', { query = '', body } = {}) => {
    const response = await table[routePath].fetch(new Request(`http://127.0.0.1:3080${routePath}${query}`, {
      method, ...(body === undefined ? {} : { body: typeof body === 'string' ? body : JSON.stringify(body) }),
    }))
    const text = await response.text()
    let data = null
    try { data = JSON.parse(text) } catch { /* картинка */ }
    return { status: response.status, data, text, headers: response.headers }
  }
  return { send }
}

// запросы, которые доходят до команды: по одному на каждый адрес, где команда вообще вызывается
const REQUESTS = [
  ['токены', host.TOKENS_PATH, 'GET', {}],
  ['выпуск токена', host.TOKENS_PATH, 'POST', { body: { name: 'a' } }],
  ['отзыв токена', host.REVOKE_PATH, 'POST', { body: { name: 'a' } }],
  ['журнал', host.JOURNAL_PATH, 'GET', {}],
  ['состояние входящих', host.INBOX_PATH, 'GET', {}],
  ['настройка входящих', host.INBOX_PATH, 'POST', { body: { period: 5 } }],
  ['смена папки', host.INBOX_PATH, 'POST', { body: { path: '/a', confirm: true } }],
  ['разобрать сейчас', host.INBOX_RUN_PATH, 'POST', { body: {} }],
  ['очередь', host.QUEUE_PATH, 'GET', {}],
  ['решение по очереди', host.QUEUE_PATH, 'POST', { body: { paths: [PATH], action: 'accept' } }],
  ['карантин', host.QUARANTINE_PATH, 'GET', {}],
  ['решение по карантину', host.QUARANTINE_PATH, 'POST', { body: { path: 'карантин/a/b', action: 'delete' } }],
  ['удаление документа', host.DELETE_PATH, 'POST', { body: { path: 'входящие/a/b.txt', confirm: true } }],
  ['история пачек', host.BATCHES_PATH, 'GET', {}],
  ['пачка', host.BATCH_PATH, 'GET', { query: '?id=20260101-120000' }],
  ['описание просмотра', host.PREVIEW_PATH, 'POST', { body: { area: 'queue', path: PATH } }],
  ['страница просмотра', host.PREVIEW_PAGE_PATH, 'POST', { body: { area: 'queue', path: PATH, page: 1 } }],
]

// ── 502: команда не дала объекта-сообщения ──────────────────────
for (const [title, route, method, request] of REQUESTS) {
  test(`${title}: разбор аргументов (код 2) — 502, короткий английский текст, сырого текста нет`, async () => {
    const { send } = cli(() => fail(ARGPARSE, 2), () => failBinary(ARGPARSE, 2))
    const r = await send(route, method, request)
    assert.equal(r.status, 502)
    assert.equal(typeof r.data.error, 'string')
    assert.match(r.data.error, /^[\x20-\x7e]+$/) // английский текст без кириллицы и без управляющих знаков
    assert.ok(r.data.error.length <= 200, r.data.error)
    for (const piece of ['usage', 'unrecognized', '--defer-index', 'flyarchive queue accept', 'paths', 'error:']) assert.ok(!r.text.includes(piece), piece)
    assert.notEqual(r.headers.get('content-type'), 'image/png')
  })
}

test('последняя строка stderr не JSON с error — 502, сырая строка не уходит ни в каком виде', async () => {
  const cases = [
    'ошибка: имя занято\n',
    'замечание: раз\nошибка: два\n',
    '{"warning": "x"}\n',
    '{"error": "строкой"}\n',
    '{"error": {"code": "x"}}\n', // нет text
    '{"error": {"text": 5}}\n',
    '{"error": null}\n',
    '[1,2]\n',
    '{"error": {"text": "обрыв"\n',
    'Traceback (most recent call last):\n  File "tools/flyarchive", line 9\nKeyError: secret-name\n',
    `${JSON.stringify({ error: ERROR })}\nошибка: потом ещё\n`, // JSON не в последней строке
  ]
  for (const stderr of cases) {
    const { send } = cli(() => fail(stderr))
    const r = await send(host.QUEUE_PATH)
    assert.equal(r.status, 502, stderr)
    assert.match(r.data.error, /^[\x20-\x7e]+$/, stderr)
    for (const piece of ['имя занято', 'два', 'warning', 'строкой', 'обрыв', 'Traceback', 'secret-name', 'потом ещё', 'archive_busy']) {
      assert.ok(!r.text.includes(piece), `${stderr} → ${piece}`)
    }
  }
})

test('код возврата 2 — 502, даже если в последней строке stderr объект-сообщение', async () => {
  const { send } = cli(() => fail(asLine(ERROR), 2))
  const r = await send(host.QUEUE_PATH)
  assert.equal(r.status, 502)
  assert.equal(typeof r.data.error, 'string')
  assert.ok(!r.text.includes('archive_busy') && !r.text.includes('The archive is busy'))
})

test('пустой stderr и другой код: 502, в тексте код возврата', async () => {
  const { send } = cli(() => fail('', 3))
  const r = await send(host.QUEUE_PATH)
  assert.equal(r.status, 502)
  assert.match(r.data.error, /exit code 3/)
  const { send: sendTwo } = cli(() => fail('', 2))
  assert.match((await sendTwo(host.QUEUE_PATH)).data.error, /^[\x20-\x7e]+$/)
})

test('тексты 502 не зависят от того, что написал stderr: одно и то же для любого сырого текста', async () => {
  const texts = new Set()
  for (const stderr of ['', 'x', 'ошибка: y\n', ARGPARSE]) {
    const { send } = cli(() => fail(stderr, 1))
    texts.add((await send(host.QUEUE_PATH)).data.error)
  }
  assert.equal(texts.size, 1, [...texts].join(' | '))
})

test('значение токена из stderr не доходит до ответа: сырой текст не пересказывается', async () => {
  const token = 'ba_' + 'z'.repeat(43)
  const { send } = cli(() => fail(`ошибка: сбой рядом с ${token}\n`))
  const r = await send(host.TOKENS_PATH, 'POST', { body: { name: 'a' } })
  assert.equal(r.status, 502)
  assert.ok(!r.text.includes('zzzz'))
})

// ── 409: только объект-сообщение ────────────────────────────────
test('объект-сообщение в последней строке stderr при коде 1 и при коде 3 — 409 с этим объектом', async () => {
  for (const code of [1, 3]) {
    const { send } = cli(() => fail(`замечание: что-то по дороге\n${asLine(ERROR)}`, code))
    const r = await send(host.QUEUE_PATH)
    assert.equal(r.status, 409, `код ${code}`)
    assert.deepEqual(r.data, { error: ERROR })
  }
})

test('отказ страницы: объект при коде 1 — 409 и не картинка; не объект — 502 и не картинка', async () => {
  const body = { area: 'queue', path: PATH, page: 1 }
  const refused = cli(() => ok({}), () => failBinary(asLine(ERROR)))
  const a = await refused.send(host.PREVIEW_PAGE_PATH, 'POST', { body })
  assert.equal(a.status, 409)
  assert.deepEqual(a.data, { error: ERROR })
  const raw = cli(() => ok({}), () => failBinary('ошибка: нет такой страницы\n'))
  const b = await raw.send(host.PREVIEW_PAGE_PATH, 'POST', { body })
  assert.equal(b.status, 502)
  assert.ok(!b.text.includes('нет такой страницы'))
  assert.notEqual(b.headers.get('content-type'), 'image/png')
})

// ── все отказы самого плагина — по-английски ────────────────────
const BAD = [
  ['выпуск: пустое имя', host.TOKENS_PATH, 'POST', { body: { name: '' } }],
  ['выпуск: уровень', host.TOKENS_PATH, 'POST', { body: { name: 'a', level: 'admin' } }],
  ['выпуск: срок', host.TOKENS_PATH, 'POST', { body: { name: 'a', days: 0 } }],
  ['выпуск: не объект', host.TOKENS_PATH, 'POST', { body: ['a'] }],
  ['выпуск: тело не JSON', host.TOKENS_PATH, 'POST', { body: '{name:' }],
  ['отзыв: служебный токен', host.REVOKE_PATH, 'POST', { body: { name: 'dsh-local' } }],
  ['отзыв: не объект', host.REVOKE_PATH, 'POST', { body: 'a' }],
  ['журнал: n', host.JOURNAL_PATH, 'GET', { query: '?n=abc' }],
  ['журнал: клиент', host.JOURNAL_PATH, 'GET', { query: '?client=--json' }],
  ['настройка: пустая', host.INBOX_PATH, 'POST', { body: {} }],
  ['настройка: чужой ключ', host.INBOX_PATH, 'POST', { body: { secret: 1 } }],
  ['настройка: значение', host.INBOX_PATH, 'POST', { body: { period: 7 } }],
  ['настройка: не объект', host.INBOX_PATH, 'POST', { body: [1] }],
  ['смена папки: без подтверждения', host.INBOX_PATH, 'POST', { body: { path: '/a' } }],
  ['смена папки: негодный путь', host.INBOX_PATH, 'POST', { body: { path: 'a', confirm: true } }],
  ['решение: действие', host.QUEUE_PATH, 'POST', { body: { path: PATH, action: 'delete' } }],
  ['решение: путь', host.QUEUE_PATH, 'POST', { body: { path: '--json', action: 'accept' } }],
  ['решение: список', host.QUEUE_PATH, 'POST', { body: { paths: [], action: 'accept' } }],
  ['решение: повторы', host.QUEUE_PATH, 'POST', { body: { paths: [PATH, PATH], action: 'accept' } }],
  ['решение: и path, и paths', host.QUEUE_PATH, 'POST', { body: { path: PATH, paths: [PATH], action: 'accept' } }],
  ['решение: стирание списком', host.QUARANTINE_PATH, 'POST', { body: { paths: ['карантин/a/b'], action: 'delete' } }],
  ['удаление: без подтверждения', host.DELETE_PATH, 'POST', { body: { path: 'входящие/a/b.txt' } }],
  ['пачки: limit', host.BATCHES_PATH, 'GET', { query: '?limit=0' }],
  ['пачки: повтор параметра', host.BATCHES_PATH, 'GET', { query: '?limit=1&limit=2' }],
  ['пачки: before', host.BATCHES_PATH, 'GET', { query: '?before=x' }],
  ['пачка: нет id', host.BATCH_PATH, 'GET', {}],
  ['просмотр: область', host.PREVIEW_PATH, 'POST', { body: { area: 'x', path: PATH } }],
  ['просмотр: member', host.PREVIEW_PATH, 'POST', { body: { area: 'queue', path: PATH, member: [1, 2, 3] } }],
  ['страница: номер', host.PREVIEW_PAGE_PATH, 'POST', { body: { area: 'queue', path: PATH, page: 0 } }],
  ['чужой метод', host.REVOKE_PATH, 'GET', {}],
]
for (const [title, route, method, request] of BAD) {
  test(`отказ плагина по-английски, команда не вызвана: ${title}`, async () => {
    const calls = []
    const { send } = cli((args) => { calls.push(args); return ok({}) }, (args) => { calls.push(args); return failBinary('') })
    const r = await send(route, method, request)
    assert.ok([400, 405].includes(r.status), String(r.status))
    assert.equal(typeof r.data.error, 'string')
    assert.ok(!CYRILLIC.test(r.data.error), r.data.error)
    assert.match(r.data.error, /^[\x20-\x7e]+$/, r.data.error)
    assert.deepEqual(calls, [])
  })
}

test('команда не запустилась, ответила не JSON, вернула не PNG — 502 по-английски', async () => {
  const dead = () => { throw Object.assign(new Error('spawn ENOENT'), { code: 'ENOENT' }) }
  const table = Object.fromEntries(routes(createApi({ command: CMD, run: dead, runBinary: dead })).map((r) => [r.path, r]))
  const get = (route, init) => table[route].fetch(new Request(`http://127.0.0.1:3080${route}`, init))
  const started = await get(host.TOKENS_PATH)
  assert.equal(started.status, 502)
  const notJson = routes(createApi({ command: CMD, run: async () => ok('plain words'), runBinary: async () => ({ code: 0, stdout: Buffer.from('x'), stderr: '' }) }))
  const find = (path) => notJson.find((r) => r.path === path)
  const json = await find(host.TOKENS_PATH).fetch(new Request(`http://127.0.0.1:3080${host.TOKENS_PATH}`))
  const png = await find(host.PREVIEW_PAGE_PATH).fetch(new Request(`http://127.0.0.1:3080${host.PREVIEW_PAGE_PATH}`, { method: 'POST', body: JSON.stringify({ area: 'queue', path: PATH, page: 1 }) }))
  for (const response of [started, json, png]) {
    assert.equal(response.status, 502)
    const { error } = await response.json()
    assert.ok(!CYRILLIC.test(error), error)
  }
})

test('неожиданный сбой внутри плагина — 502 по-английски, без подробностей', async () => {
  const route = routes({ listTokens: () => { throw new Error('внутренний секрет') } }).find((r) => r.path === host.TOKENS_PATH)
  const response = await route.fetch(new Request(`http://127.0.0.1:3080${host.TOKENS_PATH}`))
  assert.equal(response.status, 502)
  const text = await response.text()
  assert.ok(!CYRILLIC.test(text) && !text.includes('секрет'), text)
})
