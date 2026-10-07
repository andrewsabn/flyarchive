// Серверная половина: история пачек — GET /api/flyarchive.batches и GET /api/flyarchive.batch (FR-82).
// Команду flyarchive подставляет запись вызовов: проверяется, что именно ушло в команду и что до неё не дошло.
import assert from 'node:assert/strict'
import test from 'node:test'

import * as host from '../lib/index.js'
import { createApi, routes } from '../lib/index.js'

const CMD = '/opt/flyarchive/tools/flyarchive'
const ok = (data) => ({ code: 0, stdout: JSON.stringify(data), stderr: '' })
const fail = (stderr, code = 1) => ({ code, stdout: '', stderr })
const LIST = { batches: [{ id: '20260101-120000', time: '2026-10-04T18:11:56Z', seconds: 65, counts: { accept: 12 }, problems: 0, files: 12 }], more: false }
const DETAIL = { id: '20260101-120000', time: '2026-10-04T18:11:56Z', seconds: 65, counts: { accept: 1 }, problems: [], files: [{ name: 'a.txt', location: 'corpus' }] }

function cli(script = (args) => ok(args[1] === 'batches' ? LIST : DETAIL)) {
  const calls = []
  const timeouts = []
  const run = async (command, args, timeout) => {
    calls.push(args)
    timeouts.push(timeout)
    return script(args)
  }
  const table = Object.fromEntries(routes(createApi({ command: CMD, run })).map((r) => [r.path, r]))
  const get = async (routePath, query = '', method = 'GET') => {
    const response = await table[routePath].fetch(new Request(`http://127.0.0.1:3080${routePath}${query}`, { method }))
    return { status: response.status, data: await response.json() }
  }
  return { calls, timeouts, table, get }
}

const BATCHES = host.BATCHES_PATH
const BATCH = host.BATCH_PATH
const ID = '20260101-120000'

// ── адреса ──────────────────────────────────────────────────────
test('адреса истории объявлены и принимают только GET', async () => {
  assert.equal(BATCHES, '/api/flyarchive.batches')
  assert.equal(BATCH, '/api/flyarchive.batch')
  const table = Object.fromEntries(routes(createApi({ command: CMD, run: async () => ok(LIST) })).map((r) => [r.path, r]))
  assert.deepEqual(table[BATCHES].methods, ['GET'])
  assert.deepEqual(table[BATCH].methods, ['GET'])
})

test('POST по адресам истории — 405, команда не вызвана', async () => {
  const { calls, get } = cli()
  assert.equal((await get(BATCHES, '', 'POST')).status, 405)
  assert.equal((await get(BATCH, `?id=${ID}`, 'POST')).status, 405)
  assert.deepEqual(calls, [])
})

// ── список пачек ────────────────────────────────────────────────
test('без параметров: по умолчанию 30, ответ команды отдаётся как есть', async () => {
  const { calls, get } = cli()
  const r = await get(BATCHES)
  assert.equal(r.status, 200)
  assert.deepEqual(r.data, LIST)
  assert.deepEqual(calls, [['inbox', 'batches', '--json', '--limit', '30']])
})

test('limit и before уходят отдельными аргументами, а не склеенной строкой', async () => {
  const { calls, get } = cli()
  await get(BATCHES, `?limit=50&before=${ID}`)
  assert.deepEqual(calls, [['inbox', 'batches', '--json', '--limit', '50', '--before', ID]])
  assert.ok(calls[0].every((arg) => !arg.includes('=') && !arg.includes(' ')), 'ни один аргумент не склеен из ключа и значения')
})

test('before без limit: limit остаётся по умолчанию, before идёт после него', async () => {
  const { calls, get } = cli()
  await get(BATCHES, `?before=${ID}-2`)
  assert.deepEqual(calls, [['inbox', 'batches', '--json', '--limit', '30', '--before', `${ID}-2`]])
})

test('limit: границы 1 и 200 проходят, значение уходит нормальной десятичной записью', async () => {
  const { calls, get } = cli()
  for (const limit of ['1', '9', '10', '99', '100', '199', '200']) assert.equal((await get(BATCHES, `?limit=${limit}`)).status, 200, limit)
  assert.deepEqual(calls.map((args) => args[4]), ['1', '9', '10', '99', '100', '199', '200'])
})

// значения записаны как есть (после разбора адреса); в адрес они уходят закодированными
const BAD_LIMITS = ['0', '201', '1000', '99999999999', '-1', '1.5', '1e2', '0x10', '+5', '', ' 5', '5 ', 'abc', '30abc', '٣٠', '5\n', '5;ls', '5 --before x', 'NaN', 'Infinity']
for (const limit of BAD_LIMITS) {
  test(`limit=${JSON.stringify(limit)} — отказ 400 до вызова команды`, async () => {
    const { calls, get } = cli()
    const r = await get(BATCHES, `?limit=${encodeURIComponent(limit)}`)
    assert.equal(r.status, 400)
    assert.equal(typeof r.data.error, 'string')
    assert.deepEqual(calls, [])
  })
}

const GOOD_IDS = ['20260101-120000', '20260101-120000-2', '20260101-120000-9', '20260101-120000-10', '20260101-120000-123', '19991231-235959']
const BAD_IDS = [
  '', '--json', '-x', '--limit', 'x', '20260101-120000-1', '20260101-120000-0', '20260101-120000-', '20260101-120000-02',
  '20260101-120000-2-3', '2026010-120000', '20260101-12000', '202601011-120000', '20260101-1200000', '20260101_120000', '20260101120000',
  '20260101-120000 --json', ' 20260101-120000', '20260101-120000 ', '20260101-120000\n', '20260101-120000\u0000', '../20260101-120000',
  '20260101-120000/..', '20260101-120000.jsonl', '２０２６０１０１-120000', 'ГГГГММДД-ЧЧММСС', '20260101-120000-x',
]
for (const before of BAD_IDS) {
  test(`before=${JSON.stringify(before)} — отказ 400 до вызова команды`, async () => {
    const { calls, get } = cli()
    const r = await get(BATCHES, `?before=${encodeURIComponent(before)}`)
    assert.equal(r.status, 400)
    assert.equal(typeof r.data.error, 'string')
    assert.deepEqual(calls, [])
  })
}

test('before вида ГГГГММДД-ЧЧММСС с необязательным -N (N от 2) проходит и уходит как есть', async () => {
  const { calls, get } = cli()
  for (const id of GOOD_IDS) assert.equal((await get(BATCHES, `?before=${id}`)).status, 200, id)
  assert.deepEqual(calls.map((args) => args[6]), GOOD_IDS)
})

test('значения, закодированные в адресе, разбираются до проверки: %35 — это 5, а перевод строки в before отказ', async () => {
  const { calls, get } = cli()
  assert.equal((await get(BATCHES, '?limit=%35')).status, 200)
  assert.deepEqual(calls, [['inbox', 'batches', '--json', '--limit', '5']])
  assert.equal((await get(BATCHES, `?before=${ID}%0Aabc`)).status, 400)
  assert.equal(calls.length, 1)
})

test('повторный параметр — отказ: какое из значений имелось в виду, неизвестно', async () => {
  const { calls, get } = cli()
  assert.equal((await get(BATCHES, '?limit=5&limit=6')).status, 400)
  assert.equal((await get(BATCHES, `?before=${ID}&before=20260101-120001`)).status, 400)
  assert.deepEqual(calls, [])
})

test('плохое значение отклоняет запрос, даже когда второе в порядке', async () => {
  const { calls, get } = cli()
  assert.equal((await get(BATCHES, `?limit=5&before=--json`)).status, 400)
  assert.equal((await get(BATCHES, `?limit=500&before=${ID}`)).status, 400)
  assert.deepEqual(calls, [])
})

// ── одна пачка ──────────────────────────────────────────────────
test('пачка: номер идёт отдельным аргументом, ответ команды отдаётся как есть', async () => {
  const { calls, get } = cli()
  const r = await get(BATCH, `?id=${ID}`)
  assert.equal(r.status, 200)
  assert.deepEqual(r.data, DETAIL)
  assert.deepEqual(calls, [['inbox', 'batch', ID, '--json']])
})

test('номер пачки с -N проходит', async () => {
  const { calls, get } = cli()
  for (const id of GOOD_IDS) assert.equal((await get(BATCH, `?id=${id}`)).status, 200, id)
  assert.deepEqual(calls.map((args) => args[2]), GOOD_IDS)
})

for (const id of BAD_IDS) {
  test(`id=${JSON.stringify(id)} — отказ 400 до вызова команды`, async () => {
    const { calls, get } = cli()
    const r = await get(BATCH, `?id=${encodeURIComponent(id)}`)
    assert.equal(r.status, 400)
    assert.equal(typeof r.data.error, 'string')
    assert.deepEqual(calls, [])
  })
}

test('пачка без id или с повторным id — отказ 400 до вызова команды', async () => {
  const { calls, get } = cli()
  assert.equal((await get(BATCH)).status, 400)
  assert.equal((await get(BATCH, '?limit=5')).status, 400)
  assert.equal((await get(BATCH, `?id=${ID}&id=20260101-120001`)).status, 400)
  assert.deepEqual(calls, [])
})

// ── что приходит от команды ─────────────────────────────────────
test('отказ команды объектом-сообщением идёт наружу как 409 с этим объектом — по обоим адресам', async () => {
  const error = { code: 'no_such_batch', args: { id: ID }, text: 'Нет такой пачки' }
  const { get } = cli(() => fail(`${JSON.stringify({ error })}\n`))
  for (const r of [await get(BATCHES), await get(BATCH, `?id=${ID}`)]) {
    assert.equal(r.status, 409)
    assert.deepEqual(r.data, { error })
  }
})

test('отказ команды строкой (не объект-сообщение) — 502 с коротким английским текстом, строка наружу не уходит', async () => {
  const { get } = cli(() => fail(`ошибка: нет такой пачки: ${ID}\n`))
  const r = await get(BATCH, `?id=${ID}`)
  assert.equal(r.status, 502)
  assert.equal(typeof r.data.error, 'string')
  assert.ok(!r.data.error.includes(ID) && !/[Ѐ-ӿ]/.test(r.data.error), r.data.error)
})

test('команда ответила не JSON — 502, а не падение', async () => {
  const { get } = cli(() => ({ code: 0, stdout: 'Пачек пока нет.\n', stderr: '' }))
  assert.equal((await get(BATCHES)).status, 502)
  assert.equal((await get(BATCH, `?id=${ID}`)).status, 502)
})

test('команда не запустилась — 502', async () => {
  const run = async () => { throw Object.assign(new Error('нет файла'), { code: 'ENOENT' }) }
  const table = Object.fromEntries(routes(createApi({ command: CMD, run })).map((r) => [r.path, r]))
  const response = await table[BATCHES].fetch(new Request(`http://127.0.0.1:3080${BATCHES}`))
  assert.equal(response.status, 502)
})

test('значение токена не просачивается через отказ команды и по адресам истории', async () => {
  const token = 'ba_' + 'q'.repeat(43)
  const { get } = cli(() => fail(`${JSON.stringify({ error: { code: 'x', args: { who: token }, text: `рядом ${token}` } })}\n`))
  const r = await get(BATCH, `?id=${ID}`)
  assert.equal(r.status, 409)
  assert.ok(!JSON.stringify(r.data).includes('qqqq'), JSON.stringify(r.data))
})

test('адреса истории только читают: ответы не кэшируются', async () => {
  const { table } = cli()
  const response = await table[BATCHES].fetch(new Request(`http://127.0.0.1:3080${BATCHES}`))
  assert.equal(response.headers.get('cache-control'), 'no-store')
})
