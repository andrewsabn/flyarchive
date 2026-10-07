// Серверная половина: смена входящей папки из настроек — POST /api/flyarchive.inbox с ключами path и confirm (FR-84).
// Команду flyarchive подставляет запись вызовов: проверяется, что именно ушло в команду и что до неё не дошло.
import assert from 'node:assert/strict'
import test from 'node:test'

import { INBOX_PATH, createApi, routes } from '../lib/index.js'

const CMD = '/opt/flyarchive/tools/flyarchive'
const ok = (data) => ({ code: 0, stdout: typeof data === 'string' ? data : JSON.stringify(data), stderr: '' })
const fail = (stderr, code = 1) => ({ code, stdout: '', stderr })
const STATUS = { inbox: '/mnt/c/Users/x/new-inbox', returned: '/mnt/c/Users/x/returned', home: '/home/x/flyarchive', period: 30, attention: 0 }
const GOOD = '/mnt/c/Users/x/new-inbox'
const CYRILLIC = /[Ѐ-ӿ]/

function cli(script = (args) => (args[1] === 'status' ? ok(STATUS) : ok({ inbox: GOOD }))) {
  const calls = []
  const run = async (command, args) => {
    calls.push(args)
    return script(args)
  }
  const table = Object.fromEntries(routes(createApi({ command: CMD, run })).map((r) => [r.path, r]))
  const post = async (body) => {
    const response = await table[INBOX_PATH].fetch(new Request(`http://127.0.0.1:3080${INBOX_PATH}`, {
      method: 'POST', body: typeof body === 'string' ? body : JSON.stringify(body),
    }))
    return { status: response.status, data: await response.json() }
  }
  return { calls, post }
}

// ── смена папки ─────────────────────────────────────────────────
test('смена папки: команда inbox set --path P --must-exist --json, потом свежее состояние, в ответ оно же', async () => {
  const { calls, post } = cli()
  const r = await post({ path: GOOD, confirm: true })
  assert.equal(r.status, 200)
  assert.deepEqual(r.data, STATUS)
  assert.deepEqual(calls, [['inbox', 'set', '--path', GOOD, '--must-exist', '--json'], ['inbox', 'status', '--json']])
})

test('путь с пробелами, скобками и кириллицей уходит одним аргументом, без оболочки и без «--» перед ним', async () => {
  const path = '/mnt/c/Users/x/Мои документы (входящие) $HOME; `x`'
  const { calls, post } = cli()
  assert.equal((await post({ path, confirm: true })).status, 200)
  assert.deepEqual(calls[0], ['inbox', 'set', '--path', path, '--must-exist', '--json'])
})

test('ключ --must-exist есть всегда: из плагина каталоги не создаются', async () => {
  const { calls, post } = cli()
  await post({ path: '/a', confirm: true })
  await post({ path: '/b/c', confirm: true })
  assert.deepEqual(calls.filter((args) => args[1] === 'set').map((args) => args.includes('--must-exist')), [true, true])
})

test('предел длины: 1000 знаков проходят, 1001 — нет', async () => {
  const { calls, post } = cli()
  assert.equal((await post({ path: '/' + 'a'.repeat(999), confirm: true })).status, 200)
  const before = calls.length
  assert.equal((await post({ path: '/' + 'a'.repeat(1000), confirm: true })).status, 400)
  assert.equal(calls.length, before)
})

// ── без подтверждения — отказ до вызова команды ─────────────────
for (const [title, body] of [
  ['нет confirm', { path: GOOD }],
  ['confirm: false', { path: GOOD, confirm: false }],
  ['confirm строкой', { path: GOOD, confirm: 'true' }],
  ['confirm числом', { path: GOOD, confirm: 1 }],
  ['confirm: null', { path: GOOD, confirm: null }],
  ['confirm: «да»', { path: GOOD, confirm: 'да' }],
]) {
  test(`смена папки без подтверждения отклонена до вызова команды: ${title}`, async () => {
    const { calls, post } = cli()
    const r = await post(body)
    assert.equal(r.status, 400)
    assert.equal(typeof r.data.error, 'string')
    assert.ok(!CYRILLIC.test(r.data.error), r.data.error)
    assert.deepEqual(calls, [])
  })
}

// ── негодный путь — отказ до вызова команды ─────────────────────
const BAD_PATHS = [
  ['нет пути', undefined],
  ['пустая строка', ''],
  ['не строка: число', 7],
  ['не строка: список', ['/a']],
  ['не строка: объект', { x: 1 }],
  ['не строка: null', null],
  ['относительный путь', 'inbox/new'],
  ['относительный путь с точками', '../inbox'],
  ['путь с пробела', ' /inbox'],
  ['диск Windows', 'C:\\inbox'],
  ['адрес сети', 'https://example.test/inbox'],
  ['похож на ключ команды', '--json'],
  ['похож на ключ команды, один дефис', '-x'],
  ['ключ с равно', '--path=/etc'],
  ['перевод строки', '/inbox\n--json'],
  ['нулевой знак', '/inbox\u0000x'],
  ['табуляция', '/inbox\tx'],
  ['знак 0x1f', '/inbox\u001fx'],
  ['знак DEL', '/inbox\u007fx'],
  ['управляющий знак C1', '/inbox\u0085x'],
  ['длиннее 1000 знаков', '/' + 'я'.repeat(1000)],
]
for (const [title, path] of BAD_PATHS) {
  test(`смена папки: негодный путь отклонён до вызова команды: ${title}`, async () => {
    const { calls, post } = cli()
    const body = { confirm: true }
    if (path !== undefined) body.path = path
    const r = await post(body)
    assert.equal(r.status, 400)
    assert.equal(typeof r.data.error, 'string')
    assert.ok(!CYRILLIC.test(r.data.error), r.data.error)
    assert.deepEqual(calls, [])
  })
}

// ── смена папки — отдельный запрос ──────────────────────────────
test('смена папки не смешивается с настройкой разбора: любой лишний ключ — 400, команда не вызвана', async () => {
  const { calls, post } = cli()
  for (const extra of [{ period: 5 }, { llm: false }, { threshold: 10 }, { extra: 1 }, { command: 'x' }]) {
    assert.equal((await post({ path: GOOD, confirm: true, ...extra })).status, 400, JSON.stringify(extra))
  }
  assert.deepEqual(calls, [])
})

test('confirm без path — 400, а не тихая «настройка»', async () => {
  const { calls, post } = cli()
  assert.equal((await post({ confirm: true })).status, 400)
  assert.equal((await post({ confirm: true, period: 5 })).status, 400)
  assert.deepEqual(calls, [])
})

test('обычная настройка по-прежнему идёт без path и без confirm', async () => {
  const { calls, post } = cli((args) => (args[1] === 'status' ? ok(STATUS) : ok('…')))
  assert.equal((await post({ period: 5 })).status, 200)
  assert.deepEqual(calls[0], ['inbox', 'set', '--period', '5'])
  assert.ok(!calls[0].includes('--path') && !calls[0].includes('--must-exist'))
})

// ── что отвечает команда ────────────────────────────────────────
test('отказ команды (папки нет, папка внутри архива) — 409 с объектом-сообщением, он показывается у поля', async () => {
  const error = { code: 'inbox_missing', args: { path: GOOD }, text: 'The folder does not exist' }
  const { calls, post } = cli(() => fail(`${JSON.stringify({ error })}\n`))
  const r = await post({ path: GOOD, confirm: true })
  assert.equal(r.status, 409)
  assert.deepEqual(r.data, { error })
  assert.equal(calls.length, 1) // состояние после отказа не читается
})

test('команда без --must-exist (разбор аргументов, код 2) — 502 с коротким английским текстом, сырой текст не уходит', async () => {
  const raw = 'usage: flyarchive inbox set [-h] [--path PATH]\nflyarchive inbox set: error: unrecognized arguments: --must-exist\n'
  const { post } = cli(() => fail(raw, 2))
  const r = await post({ path: GOOD, confirm: true })
  assert.equal(r.status, 502)
  assert.equal(typeof r.data.error, 'string')
  assert.ok(r.data.error.length <= 200, r.data.error)
  assert.ok(!CYRILLIC.test(r.data.error))
  for (const piece of ['usage', 'unrecognized', '--must-exist', 'flyarchive inbox set', 'PATH']) assert.ok(!JSON.stringify(r.data).includes(piece), piece)
})
