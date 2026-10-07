// Серверная половина раздела «Архив»: входящие, очередь, карантин, удаление (FR-41, FR-52, FR-53, FR-54).
import assert from 'node:assert/strict'
import test from 'node:test'

import { DELETE_PATH, INBOX_PATH, INBOX_RUN_PATH, LONG_TIMEOUT_MS, QUARANTINE_PATH, QUEUE_PATH, createApi, routes, runCli } from '../lib/index.js'

const CMD = '/opt/flyarchive/tools/flyarchive'
const ok = (data) => ({ code: 0, stdout: typeof data === 'string' ? data : JSON.stringify(data), stderr: '' })
// так отказывает настоящая команда с --json: последняя строка stderr — объект-сообщение
const refuse = (text, code = 'refused') => ({ code: 1, stdout: '', stderr: `${JSON.stringify({ error: { code, args: {}, text } })}\n` })
const STATUS = { inbox: '/mnt/c/Users/x/flyarchive-inbox', period: 30, llm: true, cloud: true, threshold: 20, max_gb: 2, max_files: 5000,
  max_ratio: 100, depth: 3, timer: true, waiting: 2, queue: 1, quarantine: 0, pending: 0, last_batch: null }

function cli(script = () => ok([])) {
  const calls = []
  const run = async (command, args) => {
    calls.push(args)
    return script(args)
  }
  const table = Object.fromEntries(routes(createApi({ command: CMD, run })).map((r) => [r.path, r]))
  const call = async (routePath, method, { body, raw } = {}) => {
    const init = { method }
    if (raw !== undefined) init.body = raw
    else if (body !== undefined) init.body = JSON.stringify(body)
    const response = await table[routePath].fetch(new Request(`http://127.0.0.1:3080${routePath}`, init))
    return { status: response.status, data: await response.json() }
  }
  return { calls, call, table }
}

test('новые адреса тоже под /api и объявляют свои методы', () => {
  const { table } = cli()
  for (const [path, methods] of [[INBOX_PATH, ['GET', 'POST']], [INBOX_RUN_PATH, ['POST']], [QUEUE_PATH, ['GET', 'POST']],
    [QUARANTINE_PATH, ['GET', 'POST']], [DELETE_PATH, ['POST']]]) {
    assert.match(path, /^\/api\/flyarchive\.[a-z.]+$/)
    assert.deepEqual(table[path].methods, methods)
    assert.equal(table[path].requestBody, 'buffered')
  }
})

// ── входящие ────────────────────────────────────────────────────
test('состояние входящих', async () => {
  const { calls, call } = cli(() => ok(STATUS))
  const r = await call(INBOX_PATH, 'GET')
  assert.equal(r.status, 200)
  assert.deepEqual(r.data, STATUS)
  assert.deepEqual(calls, [['inbox', 'status', '--json']])
})

test('настройка: период, модель, порог и пределы уходят ключами команды, в ответ — новое состояние', async () => {
  const { calls, call } = cli((args) => (args[1] === 'status' ? ok({ ...STATUS, period: 5 }) : ok('Входящая папка: …')))
  const r = await call(INBOX_PATH, 'POST', { body: { period: 5, llm: false, cloud: true, threshold: 30, max_gb: 0.5, max_files: 100, max_ratio: 50, depth: 2 } })
  assert.equal(r.status, 200)
  assert.equal(r.data.period, 5)
  assert.deepEqual(calls, [
    ['inbox', 'set', '--period', '5', '--llm', 'off', '--cloud', 'on', '--threshold', '30', '--max-gb', '0.5', '--max-files', '100',
      '--max-ratio', '50', '--depth', '2'],
    ['inbox', 'status', '--json']])
})

test('настройка: меняется только названное', async () => {
  const { calls, call } = cli((args) => (args[1] === 'status' ? ok(STATUS) : ok('…')))
  await call(INBOX_PATH, 'POST', { body: { period: 10 } })
  assert.deepEqual(calls[0], ['inbox', 'set', '--period', '10'])
})

const BAD_SETTINGS = [
  ['период не из списка', { period: 7 }], ['период строкой', { period: '5' }], ['модель не да/нет', { llm: 'on' }],
  ['порог отрицательный', { threshold: -1 }], ['порог больше ста', { threshold: 101 }], ['порог дробный', { threshold: 1.5 }],
  ['объём ноль', { max_gb: 0 }], ['файлов ноль', { max_files: 0 }], ['вложенность ноль', { depth: 0 }],
  ['смена папки без подтверждения (с ним — host-folder)', { path: '/tmp/x' }], ['неизвестное поле', { secret: 1 }], ['пустая настройка', {}], ['не объект', [1]],
]
for (const [title, body] of BAD_SETTINGS) {
  test(`настройка отклонена до вызова команды: ${title}`, async () => {
    const { calls, call } = cli()
    const r = await call(INBOX_PATH, 'POST', { body })
    assert.equal(r.status, 400)
    assert.deepEqual(calls, [])
  })
}

test('разобрать сейчас: разбор запускается службой и не держит запрос', async () => {
  const { calls, call } = cli(() => ok({ started: true }))
  const r = await call(INBOX_RUN_PATH, 'POST', { body: {} })
  assert.equal(r.status, 200)
  assert.deepEqual(r.data, { started: true })
  assert.deepEqual(calls, [['inbox', 'kick', '--json']])
})

// ── очередь и карантин ──────────────────────────────────────────
test('очередь и карантин читаются командой', async () => {
  const items = [{ path: 'очередь/20261004-120000/отчёт.txt', score: 55, findings: [] }]
  const { calls, call } = cli(() => ok(items))
  assert.deepEqual((await call(QUEUE_PATH, 'GET')).data, { items })
  assert.deepEqual((await call(QUARANTINE_PATH, 'GET')).data, { items })
  assert.deepEqual(calls, [['queue', 'list', '--json'], ['quarantine', 'list', '--json']])
})

const DECISIONS = [
  [QUEUE_PATH, 'accept', 'очередь/20261004-120000/отчёт.txt', ['queue', 'accept', '--json', '--', 'очередь/20261004-120000/отчёт.txt']],
  [QUEUE_PATH, 'quarantine', 'очередь/20261004-120000/отчёт.txt', ['queue', 'quarantine', '--json', '--', 'очередь/20261004-120000/отчёт.txt']],
  [QUARANTINE_PATH, 'delete', 'карантин/20261004-120000/setup.exe', ['quarantine', 'delete', '--json', '--', 'карантин/20261004-120000/setup.exe']],
  [QUARANTINE_PATH, 'return', 'карантин/20261004-120000/setup.exe', ['quarantine', 'return', '--json', '--', 'карантин/20261004-120000/setup.exe']],
]
for (const [route, action, path, args] of DECISIONS) {
  test(`решение «${action}»: путь уходит после разделителя, как данные`, async () => {
    const { calls, call } = cli(() => ok({ path }))
    const r = await call(route, 'POST', { body: { path, action } })
    assert.equal(r.status, 200)
    assert.deepEqual(calls, [args])
  })
}

const BAD_DECISIONS = [
  ['чужое действие', QUEUE_PATH, { path: 'очередь/a/b', action: 'delete' }],
  ['действие очереди в карантине', QUARANTINE_PATH, { path: 'карантин/a/b', action: 'accept' }],
  ['нет действия', QUEUE_PATH, { path: 'очередь/a/b' }],
  ['нет пути', QUEUE_PATH, { action: 'accept' }],
  ['путь не строка', QUEUE_PATH, { path: 7, action: 'accept' }],
  ['путь похож на ключ команды', QUEUE_PATH, { path: '--json', action: 'accept' }],
  ['путь с переводом строки', QUEUE_PATH, { path: 'очередь/a/b\nc', action: 'accept' }],
  ['слишком длинный путь', QUEUE_PATH, { path: 'очередь/' + 'я'.repeat(1200), action: 'accept' }],
  ['пустой путь', QUARANTINE_PATH, { path: '', action: 'delete' }],
]
for (const [title, route, body] of BAD_DECISIONS) {
  test(`решение отклонено до вызова команды: ${title}`, async () => {
    const { calls, call } = cli()
    assert.equal((await call(route, 'POST', { body })).status, 400)
    assert.deepEqual(calls, [])
  })
}

test('отказ команды по решению доходит объектом-сообщением', async () => {
  const text = 'The file changed after the check: its sha256 no longer matches the receipt'
  const { call } = cli(() => refuse(text, 'sha_mismatch'))
  const r = await call(QUEUE_PATH, 'POST', { body: { path: 'очередь/a/b', action: 'accept' } })
  assert.equal(r.status, 409)
  assert.deepEqual(r.data.error, { code: 'sha_mismatch', args: {}, text })
})

// ── удаление документа ──────────────────────────────────────────
test('удаление документа: только с подтверждением', async () => {
  const { calls, call } = cli(() => ok({ path: 'входящие/a/b.txt', rows: 2, moved_to: 'удалённое/2026-10-04/входящие/a/b.txt' }))
  for (const body of [{ path: 'входящие/a/b.txt' }, { path: 'входящие/a/b.txt', confirm: false }, { path: 'входящие/a/b.txt', confirm: 'да' }]) {
    assert.equal((await call(DELETE_PATH, 'POST', { body })).status, 400)
  }
  assert.deepEqual(calls, [])
  const r = await call(DELETE_PATH, 'POST', { body: { path: 'входящие/a/b.txt', confirm: true } })
  assert.equal(r.status, 200)
  assert.equal(r.data.rows, 2)
  assert.deepEqual(calls, [['doc', 'delete', '--yes', '--json', '--', 'входящие/a/b.txt']])
})

test('удаление: путь из выдачи поиска с обратными слэшами проходит как есть', async () => {
  const path = 'export-a\\Inbox\\письмо.eml'
  const { calls, call } = cli(() => ok({ path, rows: 1, moved_to: 'x' }))
  assert.equal((await call(DELETE_PATH, 'POST', { body: { path, confirm: true } })).status, 200)
  assert.equal(calls[0].at(-1), path)
})

for (const path of ['--yes', '', 7, 'a\u0000b']) {
  test(`удаление: негодный путь ${JSON.stringify(path)} — 400`, async () => {
    const { calls, call } = cli()
    assert.equal((await call(DELETE_PATH, 'POST', { body: { path, confirm: true } })).status, 400)
    assert.deepEqual(calls, [])
  })
}

test('принятие и удаление ждут индексацию дольше обычных команд', async () => {
  // векторы считаются на процессоре: большой документ индексируется минуты, а не секунды
  const seen = []
  const run = async (command, args, timeout) => {
    seen.push([args[0], args[1], timeout])
    return ok({ path: 'x', indexed: true, rows: 1, items: [] })
  }
  const table = Object.fromEntries(routes(createApi({ command: CMD, run })).map((r) => [r.path, r]))
  const post = (route, body) => table[route].fetch(new Request(`http://127.0.0.1:3080${route}`, { method: 'POST', body: JSON.stringify(body) }))
  await post(QUEUE_PATH, { path: 'очередь/b/a.txt', action: 'accept' })
  await post(QUEUE_PATH, { path: 'очередь/b/a.txt', action: 'quarantine' })
  await post(QUARANTINE_PATH, { path: 'карантин/b/a.txt', action: 'return' })
  await post(DELETE_PATH, { path: 'входящие/b/a.txt', confirm: true })
  assert.deepEqual(seen, [['queue', 'accept', LONG_TIMEOUT_MS], ['queue', 'quarantine', undefined], ['quarantine', 'return', undefined],
    ['doc', 'delete', LONG_TIMEOUT_MS]])
  assert.ok(LONG_TIMEOUT_MS >= 10 * 60 * 1000)
})

test('команда обрывается по своему сроку, а не по общему', async () => {
  const started = Date.now()
  await assert.rejects(runCli(process.execPath, ['-e', 'setTimeout(() => {}, 30000)'], 300))
  assert.ok(Date.now() - started < 10000)
  const quick = await runCli(process.execPath, ['-e', 'console.log("ok")'], 10000)
  assert.deepEqual([quick.code, quick.stdout.trim()], [0, 'ok'])
})
