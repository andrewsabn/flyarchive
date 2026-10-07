// Серверная половина: решения пачкой и ошибка команды объектом-сообщением (FR-81, FR-80).
import assert from 'node:assert/strict'
import test from 'node:test'

import { LONG_TIMEOUT_MS, QUARANTINE_PATH, QUEUE_PATH, TOKENS_PATH, createApi, routes } from '../lib/index.js'

const CMD = '/opt/flyarchive/tools/flyarchive'
const ok = (data) => ({ code: 0, stdout: JSON.stringify(data), stderr: '' })
const fail = (stderr, code = 1) => ({ code, stdout: '', stderr })

function cli(script = () => ok({ results: [] })) {
  const calls = []
  const timeouts = []
  const run = async (command, args, timeout) => {
    calls.push(args)
    timeouts.push(timeout)
    return script(args)
  }
  const table = Object.fromEntries(routes(createApi({ command: CMD, run })).map((r) => [r.path, r]))
  const call = async (routePath, method, body) => {
    const init = { method }
    if (body !== undefined) init.body = JSON.stringify(body)
    const response = await table[routePath].fetch(new Request(`http://127.0.0.1:3080${routePath}`, init))
    return { status: response.status, data: await response.json() }
  }
  return { calls, timeouts, call }
}

const P = (n) => Array.from({ length: n }, (_, i) => `очередь/20260101-120000/файл-${i}.txt`)

// ── решения пачкой ──────────────────────────────────────────────
test('принятие списком: все пути после «--», ключ --defer-index, ответ команды отдаётся как есть', async () => {
  const answer = { results: [{ path: P(2)[0], ok: true, indexed: false }, { path: P(2)[1], ok: false, error: { code: 'x', args: {}, text: 'нет' } }], extra: 1 }
  const { calls, call } = cli(() => ok(answer))
  const r = await call(QUEUE_PATH, 'POST', { paths: P(2), action: 'accept' })
  assert.equal(r.status, 200)
  assert.deepEqual(r.data, answer)
  assert.deepEqual(calls, [['queue', 'accept', '--defer-index', '--json', '--', ...P(2)]])
})

test('«в карантин» и «вернуть» списком: свои команды, без --defer-index', async () => {
  const { calls, call } = cli()
  await call(QUEUE_PATH, 'POST', { paths: P(3), action: 'quarantine' })
  await call(QUARANTINE_PATH, 'POST', { paths: ['карантин/20260101-120000/setup.exe', 'карантин/20260101-120000/b.exe'], action: 'return' })
  assert.deepEqual(calls, [
    ['queue', 'quarantine', '--json', '--', ...P(3)],
    ['quarantine', 'return', '--json', '--', 'карантин/20260101-120000/setup.exe', 'карантин/20260101-120000/b.exe']])
  assert.ok(calls.every((args) => !args.includes('--defer-index') || args[1] === 'accept'))
})

test('ровно 200 путей проходят; один путь списком — тоже список', async () => {
  const { calls, call } = cli()
  assert.equal((await call(QUEUE_PATH, 'POST', { paths: P(200), action: 'accept' })).status, 200)
  assert.equal((await call(QUEUE_PATH, 'POST', { paths: P(1), action: 'accept' })).status, 200)
  assert.equal(calls[0].length, 5 + 200)
  assert.deepEqual(calls[1], ['queue', 'accept', '--defer-index', '--json', '--', P(1)[0]])
})

test('список ждёт команду долго для всех трёх действий: хватает на хэши и перенос двухсот файлов', async () => {
  const { timeouts, call } = cli()
  await call(QUEUE_PATH, 'POST', { paths: P(2), action: 'accept' })
  await call(QUEUE_PATH, 'POST', { paths: P(2), action: 'quarantine' })
  await call(QUARANTINE_PATH, 'POST', { paths: ['карантин/a/b'], action: 'return' })
  assert.deepEqual(timeouts, [LONG_TIMEOUT_MS, LONG_TIMEOUT_MS, LONG_TIMEOUT_MS])
})

test('прежний вид запроса с одним path продолжает работать, в том числе стирание', async () => {
  const { calls, call } = cli(() => ok({ path: 'x' }))
  await call(QUEUE_PATH, 'POST', { path: P(1)[0], action: 'accept' })
  await call(QUEUE_PATH, 'POST', { path: P(1)[0], action: 'quarantine' })
  await call(QUARANTINE_PATH, 'POST', { path: 'карантин/a/b', action: 'return' })
  await call(QUARANTINE_PATH, 'POST', { path: 'карантин/a/b', action: 'delete' })
  assert.deepEqual(calls, [
    ['queue', 'accept', '--json', '--', P(1)[0]],
    ['queue', 'quarantine', '--json', '--', P(1)[0]],
    ['quarantine', 'return', '--json', '--', 'карантин/a/b'],
    ['quarantine', 'delete', '--json', '--', 'карантин/a/b']])
})

const BAD_LISTS = [
  ['пустой список', QUEUE_PATH, { paths: [], action: 'accept' }],
  ['201 путь', QUEUE_PATH, { paths: P(201), action: 'accept' }],
  ['тысяча путей', QUARANTINE_PATH, { paths: P(1000), action: 'return' }],
  ['повторяющийся путь', QUEUE_PATH, { paths: [P(3)[0], P(3)[1], P(3)[0]], action: 'accept' }],
  ['повтор на последнем месте', QUEUE_PATH, { paths: [...P(199), P(199)[0]], action: 'quarantine' }],
  ['paths — строка', QUEUE_PATH, { paths: P(1)[0], action: 'accept' }],
  ['paths — объект', QUEUE_PATH, { paths: { 0: 'a' }, action: 'accept' }],
  ['paths — null', QUEUE_PATH, { paths: null, action: 'accept' }],
  ['путь — число', QUEUE_PATH, { paths: [P(1)[0], 7], action: 'accept' }],
  ['путь — null', QUEUE_PATH, { paths: [null], action: 'accept' }],
  ['путь — список', QUEUE_PATH, { paths: [['очередь/a/b']], action: 'accept' }],
  ['путь — пустая строка', QUEUE_PATH, { paths: [''], action: 'accept' }],
  ['путь похож на ключ команды', QUEUE_PATH, { paths: [P(1)[0], '--json'], action: 'accept' }],
  ['путь с дефиса', QUARANTINE_PATH, { paths: ['-x'], action: 'return' }],
  ['путь с переводом строки', QUEUE_PATH, { paths: ['очередь/a/b\nc'], action: 'accept' }],
  ['путь с нулевым знаком', QUEUE_PATH, { paths: ['очередь/a/b\u0000c'], action: 'accept' }],
  ['слишком длинный путь', QUEUE_PATH, { paths: ['очередь/' + 'я'.repeat(1200)], action: 'accept' }],
  ['плохой путь на последнем месте', QUEUE_PATH, { paths: [...P(199), '--yes'], action: 'accept' }],
  ['и path, и paths', QUEUE_PATH, { path: P(1)[0], paths: P(1), action: 'accept' }],
  ['path и paths: null', QUEUE_PATH, { path: P(1)[0], paths: null, action: 'accept' }],
  ['нет ни path, ни paths', QUEUE_PATH, { action: 'accept' }],
  ['стирание списком', QUARANTINE_PATH, { paths: ['карантин/a/b', 'карантин/a/c'], action: 'delete' }],
  ['стирание списком из одного', QUARANTINE_PATH, { paths: ['карантин/a/b'], action: 'delete' }],
  ['действие карантина у очереди', QUEUE_PATH, { paths: P(1), action: 'return' }],
  ['действие очереди у карантина', QUARANTINE_PATH, { paths: ['карантин/a/b'], action: 'accept' }],
  ['нет действия', QUEUE_PATH, { paths: P(1) }],
  ['чужое действие', QUEUE_PATH, { paths: P(1), action: 'rm' }],
  ['действие не строка', QUEUE_PATH, { paths: P(1), action: ['accept'] }],
]
for (const [title, route, body] of BAD_LISTS) {
  test(`список отклонён до вызова команды: ${title}`, async () => {
    const { calls, call } = cli()
    const r = await call(route, 'POST', body)
    assert.equal(r.status, 400)
    assert.equal(typeof r.data.error, 'string')
    assert.deepEqual(calls, [])
  })
}

test('сбой одного пути в ответе команды не превращается в сбой запроса: ответ уходит как есть', async () => {
  const answer = { results: [{ path: P(1)[0], ok: false, error: { code: 'sha_mismatch', args: { name: 'a' }, text: 'Файл изменился' } }] }
  const { call } = cli(() => ok(answer))
  const r = await call(QUEUE_PATH, 'POST', { paths: P(1), action: 'accept' })
  assert.equal(r.status, 200)
  assert.deepEqual(r.data, answer)
})

// ── ошибка команды: последняя строка stderr — JSON с error ──────
const ERROR = { code: 'archive_busy', args: { seconds: 5 }, text: 'Архив занят' }
const asLine = (error) => JSON.stringify({ error })

test('ошибка команды объектом: последняя строка stderr с error → 409 с этим объектом', async () => {
  const { call } = cli(() => fail(`замечание: что-то по дороге\n${asLine(ERROR)}\n`))
  const r = await call(QUEUE_PATH, 'POST', { paths: P(1), action: 'accept' })
  assert.equal(r.status, 409)
  assert.deepEqual(r.data, { error: ERROR })
})

test('ошибка объектом доходит и по адресам чтения и токенов', async () => {
  const { call } = cli(() => fail(asLine(ERROR)))
  assert.deepEqual((await call(QUEUE_PATH, 'GET')).data, { error: ERROR })
  const r = await call(TOKENS_PATH, 'POST', { name: 'a' })
  assert.equal(r.status, 409)
  assert.deepEqual(r.data, { error: ERROR })
})

test('объект ошибки приводится к виду {code, args, text}: лишнее отбрасывается, недостающее заполняется', async () => {
  const { call } = cli((args) => fail(args[0] === 'queue' ? JSON.stringify({ error: { text: 'Только текст', extra: 'x' }, more: 1 })
    : JSON.stringify({ error: { code: 7, args: ['a'], text: 'Код и параметры негодные' } })))
  assert.deepEqual((await call(QUEUE_PATH, 'GET')).data, { error: { code: null, args: {}, text: 'Только текст' } })
  assert.deepEqual((await call(QUARANTINE_PATH, 'GET')).data, { error: { code: null, args: {}, text: 'Код и параметры негодные' } })
})

// прежнее поведение (409 и сырая строка stderr) закончилось: см. host-errors.test.mjs — всё, что не объект-сообщение, это 502
test('если последняя строка — не JSON с error, это 502 с одним и тем же коротким текстом, а не 409 со строкой', async () => {
  const cases = ['ошибка: имя занято\n', 'замечание: раз\nошибка: два\n', '{"warning": "x"}\n', '{"error": "строкой"}\n',
    '{"error": {"code": "x"}}\n', '{"error": {"text": 5}}\n', '{"error": null}\n', '[1,2]\n', '{"error": {"text": "обрыв"\n']
  for (const stderr of cases) {
    const { call } = cli(() => fail(stderr))
    const r = await call(QUEUE_PATH, 'GET')
    assert.equal(r.status, 502, stderr)
    assert.equal(typeof r.data.error, 'string', stderr)
    assert.ok(!/[Ѐ-ӿ]/.test(JSON.stringify(r.data)), stderr)
  }
})

test('ошибка с JSON не в последней строке не считается объектом', async () => {
  const { call } = cli(() => fail(`${asLine(ERROR)}\nошибка: потом ещё\n`))
  const r = await call(QUEUE_PATH, 'GET')
  assert.equal(r.status, 502)
  assert.ok(!JSON.stringify(r.data).includes('archive_busy'))
})

test('пустой stderr — 502 с кодом возврата в тексте', async () => {
  const { call } = cli(() => fail('', 3))
  const r = await call(QUEUE_PATH, 'GET')
  assert.equal(r.status, 502)
  assert.match(r.data.error, /exit code 3/)
})

test('значение токена не попадает ни в текст, ни в параметры объекта-ошибки', async () => {
  const token = 'ba_' + 'z'.repeat(43)
  const { call } = cli(() => fail(asLine({ code: 'x', args: { who: token }, text: `сбой рядом с ${token}` })))
  const r = await call(QUEUE_PATH, 'GET')
  assert.equal(r.status, 409)
  assert.ok(!JSON.stringify(r.data).includes('zzzz'), JSON.stringify(r.data))
  assert.match(r.data.error.text, /ba_\*\*\*/)
})

test('код 2 — это разбор аргументов: 502, даже если последняя строка stderr похожа на объект-сообщение; при коде 1 и 3 объект читается', async () => {
  const two = await cli(() => fail(asLine(ERROR), 2)).call(QUEUE_PATH, 'GET')
  assert.equal(two.status, 502)
  assert.equal(typeof two.data.error, 'string')
  for (const code of [1, 3]) {
    const r = await cli(() => fail(asLine(ERROR), code)).call(QUEUE_PATH, 'GET')
    assert.deepEqual([r.status, r.data], [409, { error: ERROR }], `код ${code}`)
  }
})
