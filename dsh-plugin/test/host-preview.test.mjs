// Серверная половина: просмотр содержимого — POST /api/flyarchive.preview и POST /api/flyarchive.preview.page (FR-83).
// Команду flyarchive подставляет запись вызовов: проверяется, что именно ушло в команду и что до неё не дошло.
// Текстовый запуск (описание) и двоичный (страница) — разные, у каждого своя запись.
import assert from 'node:assert/strict'
import test from 'node:test'

import * as host from '../lib/index.js'
import { createApi, routes, runCliBinary } from '../lib/index.js'

const CMD = '/opt/flyarchive/tools/flyarchive'
const SHOW = host.PREVIEW_PATH
const PAGE = host.PREVIEW_PAGE_PATH
const PATH = 'очередь/20260101-120000/отчёт.pdf'
const DESCRIPTION = { kind: 'pages', meta: { name: 'отчёт.pdf', type: 'pdf', size: 5, sha256: 'ab'.repeat(32), batch: '20260101-120000', origin: { archive: null, inner: null } }, pages: 3, shown: 3 }
// подпись PNG и несколько байтов, которые текстовая перекодировка испортила бы
const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.from([0, 0, 0, 13, 0xff, 0xfe, 0x80, 0x00, 0xc3, 0x28])])
const ok = (data) => ({ code: 0, stdout: JSON.stringify(data), stderr: '' })
const picture = (bytes = PNG) => ({ code: 0, stdout: bytes, stderr: '' })
const fail = (stderr, code = 1) => ({ code, stdout: '', stderr })
const failBinary = (stderr, code = 1) => ({ code, stdout: Buffer.alloc(0), stderr })

function cli({ show = () => ok(DESCRIPTION), page = () => picture() } = {}) {
  const calls = [] // каждый вызов: каким запуском, с какими аргументами и сроком
  const run = async (command, args, timeout) => {
    calls.push({ kind: 'text', command, args, timeout })
    return show(args)
  }
  const runBinary = async (command, args, timeout) => {
    calls.push({ kind: 'binary', command, args, timeout })
    return page(args)
  }
  const table = Object.fromEntries(routes(createApi({ command: CMD, run, runBinary })).map((r) => [r.path, r]))
  const send = (routePath, body, method = 'POST') => table[routePath].fetch(new Request(`http://127.0.0.1:3080${routePath}`, {
    method, ...(method === 'POST' && body !== undefined ? { body: typeof body === 'string' ? body : JSON.stringify(body) } : {}),
  }))
  const json = async (routePath, body) => {
    const response = await send(routePath, body)
    return { status: response.status, data: await response.json(), headers: response.headers }
  }
  return { calls, table, send, json }
}

const sample = (over = {}) => ({ area: 'queue', path: PATH, member: [], page: 2, ...over })

// ── адреса ──────────────────────────────────────────────────────
test('адреса просмотра объявлены, принимают только POST и читают тело целиком, как прочие', () => {
  assert.equal(SHOW, '/api/flyarchive.preview')
  assert.equal(PAGE, '/api/flyarchive.preview.page')
  const { table } = cli()
  for (const path of [SHOW, PAGE]) {
    assert.deepEqual(table[path].methods, ['POST'], path)
    assert.equal(table[path].requestBody, 'buffered', path)
    assert.match(path, /^\/api\/flyarchive\.[a-z.]+$/)
  }
})

test('GET по адресам просмотра — 405, команда не вызвана', async () => {
  const { calls, send } = cli()
  assert.equal((await send(SHOW, undefined, 'GET')).status, 405)
  assert.equal((await send(PAGE, undefined, 'GET')).status, 405)
  assert.deepEqual(calls, [])
})

// ── описание ────────────────────────────────────────────────────
test('описание: аргументы — по договору, путь после «--» отдельным аргументом, ответ команды отдаётся как есть', async () => {
  const { calls, json } = cli()
  const r = await json(SHOW, { area: 'queue', path: PATH, member: [] })
  assert.equal(r.status, 200)
  assert.deepEqual(r.data, DESCRIPTION)
  assert.deepEqual(calls.map((c) => [c.kind, c.args]), [['text', ['preview', 'show', '--area', 'queue', '--json', '--', PATH]]])
  assert.equal(calls[0].command, CMD)
})

test('описание: каждое вложение — своим «--member», по порядку; без member — как пустой список', async () => {
  const { calls, json } = cli()
  await json(SHOW, { area: 'corpus', path: 'входящие/20260101-120000/a.eml', member: [3] })
  await json(SHOW, { area: 'quarantine', path: 'карантин/20260101-120000/b.eml', member: [3, 0] })
  await json(SHOW, { area: 'queue', path: PATH })
  assert.deepEqual(calls.map((c) => c.args), [
    ['preview', 'show', '--member', '3', '--area', 'corpus', '--json', '--', 'входящие/20260101-120000/a.eml'],
    ['preview', 'show', '--member', '3', '--member', '0', '--area', 'quarantine', '--json', '--', 'карантин/20260101-120000/b.eml'],
    ['preview', 'show', '--area', 'queue', '--json', '--', PATH]])
})

test('описание: страница в теле не нужна и в команду не уходит, но негодная — отказ', async () => {
  const { calls, json } = cli()
  assert.equal((await json(SHOW, { area: 'queue', path: PATH, member: [], page: 7 })).status, 200)
  assert.ok(!calls[0].args.includes('7'))
  for (const page of [0, 501, 1.5, '3', null]) assert.equal((await json(SHOW, { area: 'queue', path: PATH, member: [], page })).status, 400, String(page))
  assert.equal(calls.length, 1)
})

test('описание ждёт команду 300 секунд: хватает на преобразование большого файла', async () => {
  const { calls, json } = cli()
  await json(SHOW, { area: 'queue', path: PATH, member: [] })
  assert.equal(host.PREVIEW_TIMEOUT_MS, 300000)
  assert.equal(calls[0].timeout, 300000)
})

test('путь с пробелами, кавычками и знаками оболочки остаётся одним аргументом', async () => {
  const { calls, send } = cli()
  const odd = 'очередь/20260101-120000/a b; rm -rf x $(id) `id` "q" \'s\'.txt'
  await send(SHOW, { area: 'queue', path: odd, member: [] })
  await send(PAGE, { area: 'queue', path: odd, member: [], page: 1 })
  assert.deepEqual(calls.map((c) => c.args[c.args.indexOf('--') + 1]), [odd, odd]) // сразу после разделителя, целиком
  for (const c of calls) assert.equal(c.args.filter((a) => a.includes('rm -rf')).length, 1) // нигде больше обрывков пути нет
})

// ── страница ────────────────────────────────────────────────────
test('страница: двоичный запуск с аргументами по договору, номер последним, без --json', async () => {
  const { calls, send } = cli()
  const response = await send(PAGE, { area: 'quarantine', path: 'карантин/20260101-120000/a.eml', member: [1, 4], page: 3 })
  assert.equal(response.status, 200)
  assert.deepEqual(calls.map((c) => [c.kind, c.args]), [['binary', ['preview', 'page', '--member', '1', '--member', '4', '--area', 'quarantine', '--', 'карантин/20260101-120000/a.eml', '3']]])
  assert.equal(calls[0].command, CMD)
})

test('страница уходит наружу как PNG с nosniff и sandbox; байты те же, что вернула команда', async () => {
  const { send } = cli()
  const response = await send(PAGE, sample())
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('content-type'), 'image/png')
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff')
  assert.equal(response.headers.get('content-security-policy'), 'sandbox')
  assert.equal(response.headers.get('cache-control'), 'no-store')
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), PNG)
})

test('страница ждёт команду 300 секунд', async () => {
  const { calls, send } = cli()
  await send(PAGE, sample())
  assert.equal(calls[0].timeout, 300000)
  assert.equal(host.PREVIEW_TIMEOUT_MS, 300000)
})

test('страница: без номера или без области отказ, номер 1 и 500 проходят и уходят десятичной записью', async () => {
  const { calls, json, send } = cli()
  assert.equal((await json(PAGE, { area: 'queue', path: PATH, member: [] })).status, 400) // номера нет
  assert.deepEqual(calls, [])
  for (const page of [1, 9, 10, 99, 100, 500]) assert.equal((await send(PAGE, sample({ page }))).status, 200, String(page))
  assert.deepEqual(calls.map((c) => c.args.at(-1)), ['1', '9', '10', '99', '100', '500'])
})

// ── что отсекается до команды ───────────────────────────────────
const BAD_AREAS = [undefined, null, '', 'Queue', 'QUEUE', 'очередь', 'queue ', ' queue', 'queue\n', 'inbox', 'returned', 'deleted', '../queue', ['queue'], 7, {}, true]
const BAD_PATHS = [undefined, null, '', 7, ['a'], {}, true, '-x', '--json', '-', 'a'.repeat(1001), 'очередь/x\n', 'очередь/x\u0000', 'очередь/\u001fx', 'a\tb', 'a\r']
const BAD_MEMBERS = ['0', 0, {}, null, true, [-1], [1.5], ['1'], [null], [[0]], [true], [0, 1, 2], [0, 0, 0, 0], [2 ** 53], [1e21], [NaN], [Infinity], [-0.5], [{}]]
const BAD_PAGES = [0, 501, 1000, -1, 1.5, '3', '', null, [], [1], {}, true, 1e21, 2 ** 53, NaN]

for (const [what, values, make] of [
  ['area', BAD_AREAS, (v) => sample({ area: v })],
  ['path', BAD_PATHS, (v) => sample({ path: v })],
  ['member', BAD_MEMBERS, (v) => sample({ member: v })],
]) {
  for (const value of values) {
    for (const routePath of [SHOW, PAGE]) {
      test(`${what}=${JSON.stringify(value) ?? 'нет'} — отказ 400 до вызова команды (${routePath.split('.').slice(1).join('.')})`, async () => {
        const { calls, json } = cli()
        const r = await json(routePath, make(value))
        assert.equal(r.status, 400)
        assert.equal(typeof r.data.error, 'string')
        assert.deepEqual(calls, [])
      })
    }
  }
}

for (const page of BAD_PAGES) {
  test(`page=${JSON.stringify(page)} — отказ 400 до вызова команды`, async () => {
    const { calls, json } = cli()
    const r = await json(PAGE, sample({ page }))
    assert.equal(r.status, 400)
    assert.equal(typeof r.data.error, 'string')
    assert.deepEqual(calls, [])
  })
}

test('границы проходят: три области, пути до 1000 знаков, member из одного и двух целых от 0', async () => {
  const { calls, send } = cli()
  for (const area of ['queue', 'quarantine', 'corpus']) assert.equal((await send(PAGE, sample({ area }))).status, 200, area)
  assert.equal((await send(PAGE, sample({ path: 'a'.repeat(1000) }))).status, 200)
  for (const member of [[], [0], [7], [0, 0], [3, 0], [2 ** 53 - 1]]) assert.equal((await send(PAGE, sample({ member }))).status, 200, JSON.stringify(member))
  assert.equal(calls.length, 3 + 1 + 6)
  assert.deepEqual(calls.at(-1).args.slice(2, 4), ['--member', String(2 ** 53 - 1)])
})

test('тело не объект или не JSON — отказ 400 до вызова команды', async () => {
  const { calls, json } = cli()
  for (const body of ['не JSON', '[]', '"queue"', 'null', '7', '{', '']) {
    for (const routePath of [SHOW, PAGE]) assert.equal((await json(routePath, body)).status, 400, `${routePath}: ${body}`)
  }
  assert.deepEqual(calls, [])
})

test('лишние ключи тела в команду не попадают', async () => {
  const { calls, send } = cli()
  await send(PAGE, { ...sample(), extra: '--evil', command: 'x', args: ['y'] })
  assert.deepEqual(calls[0].args, ['preview', 'page', '--area', 'queue', '--', PATH, '2'])
})

// ── что приходит от команды ─────────────────────────────────────
test('отказ описания объектом-сообщением идёт наружу как 409 с этим объектом', async () => {
  const error = { code: 'preview_outside', args: { area: 'queue' }, text: 'Файл не из этой области' }
  const { json } = cli({ show: () => fail(`замечание: что-то\n${JSON.stringify({ error })}\n`) })
  const r = await json(SHOW, sample())
  assert.equal(r.status, 409)
  assert.deepEqual(r.data, { error })
})

test('отказ страницы: код возврата 1 и объект в последней строке stderr — 409 с этим объектом, не картинка', async () => {
  const error = { code: 'preview_page_range', args: { page: 9, pages: 3 }, text: 'Нет такой страницы' }
  const { send } = cli({ page: () => failBinary(`${JSON.stringify({ error })}\n`) })
  const response = await send(PAGE, sample())
  assert.equal(response.status, 409)
  assert.deepEqual(await response.json(), { error })
  assert.notEqual(response.headers.get('content-type'), 'image/png')
})

test('отказ страницы строкой (не объект-сообщение) — 502 с коротким английским текстом, строка наружу не уходит', async () => {
  const { send } = cli({ page: () => failBinary('ошибка: нет такой страницы\n') })
  const response = await send(PAGE, sample())
  assert.equal(response.status, 502)
  const { error } = await response.json()
  assert.equal(typeof error, 'string')
  assert.ok(!error.includes('страниц') && !/[Ѐ-ӿ]/.test(error), error)
  assert.notEqual(response.headers.get('content-type'), 'image/png')
})

test('команда ответила успехом, но не JSON, — 502 у описания; не запустилась — 502 у обоих адресов', async () => {
  const bad = cli({ show: () => ({ code: 0, stdout: 'просто текст', stderr: '' }) })
  assert.equal((await bad.json(SHOW, sample())).status, 502)
  const dead = () => { throw Object.assign(new Error('нет файла'), { code: 'ENOENT' }) }
  const table = Object.fromEntries(routes(createApi({ command: CMD, run: dead, runBinary: dead })).map((r) => [r.path, r]))
  for (const routePath of [SHOW, PAGE]) {
    const response = await table[routePath].fetch(new Request(`http://127.0.0.1:3080${routePath}`, { method: 'POST', body: JSON.stringify(sample()) }))
    assert.equal(response.status, 502, routePath)
  }
})

test('значение токена не просачивается через отказ ни описания, ни страницы', async () => {
  const token = 'ba_' + 'q'.repeat(43)
  const stderr = `${JSON.stringify({ error: { code: 'x', args: { who: token }, text: `рядом ${token}` } })}\n`
  const { json, send } = cli({ show: () => fail(stderr), page: () => failBinary(stderr) })
  assert.ok(!JSON.stringify((await json(SHOW, sample())).data).includes('qqqq'))
  assert.ok(!(await (await send(PAGE, sample())).text()).includes('qqqq'))
})

// ── не PNG наружу не уходит ─────────────────────────────────────
const NOT_PNG = {
  'GIF': Buffer.from('GIF89a\u0001\u0000\u0001\u0000', 'latin1'),
  'разметка SVG со сценарием': Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'),
  'страница HTML': Buffer.from('<!doctype html><script>alert(1)</script>'),
  'JPEG': Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 16, 0x4a, 0x46, 0x49, 0x46]),
  'PDF': Buffer.from('%PDF-1.7\n'),
  'пусто': Buffer.alloc(0),
  'подпись без последнего байта': PNG.subarray(0, 7),
  'подпись с испорченным первым байтом': Buffer.concat([Buffer.from([0x88]), PNG.subarray(1)]),
  'подпись не с начала': Buffer.concat([Buffer.from([0x20]), PNG]),
  'подпись с подменой перевода строки': Buffer.concat([PNG.subarray(0, 4), Buffer.from([0x0a, 0x0d, 0x1a, 0x0a]), PNG.subarray(8)]),
}
for (const [what, bytes] of Object.entries(NOT_PNG)) {
  test(`ответ страницы «${what}» — отказ 502, содержимое наружу не уходит`, async () => {
    const { send } = cli({ page: () => picture(bytes) })
    const response = await send(PAGE, sample())
    assert.equal(response.status, 502)
    assert.match(response.headers.get('content-type'), /^application\/json/)
    assert.notEqual(response.headers.get('content-type'), 'image/png')
    const body = await response.text()
    assert.equal(typeof JSON.parse(body).error, 'string')
    for (const piece of ['GIF89a', '<svg', '<script', '%PDF', 'onload']) assert.ok(!body.includes(piece), piece)
  })
}

test('ответ страницы строкой вместо двоичных данных — отказ 502, даже если строка похожа на PNG', async () => {
  const { send } = cli({ page: () => ({ code: 0, stdout: PNG.toString('latin1'), stderr: '' }) })
  assert.equal((await send(PAGE, sample())).status, 502)
})

test('ответ, который начинается как PNG, проходит: проверяется начало, а не вся картинка', async () => {
  const { send } = cli({ page: () => picture(PNG.subarray(0, 8)) })
  const response = await send(PAGE, sample())
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('content-type'), 'image/png')
})

test('заголовки картинки ставятся на каждый ответ-картинку, а на ошибки образ не ставится', async () => {
  const good = cli()
  const response = await good.send(PAGE, sample())
  for (const [name, value] of [['x-content-type-options', 'nosniff'], ['content-security-policy', 'sandbox'], ['content-type', 'image/png']]) {
    assert.equal(response.headers.get(name), value, name)
  }
  for (const bad of [cli({ page: () => picture(Buffer.from('x')) }), cli({ page: () => failBinary('ошибка: x') })]) {
    assert.notEqual((await bad.send(PAGE, sample())).headers.get('content-type'), 'image/png')
  }
})

// ── двоичное чтение команды ─────────────────────────────────────
const MIB = 1024 * 1024

test('runCliBinary: байты stdout доходят без перекодировки в текст', async () => {
  const bytes = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0xff, 0xfe, 0x00, 0xc3, 0x28, 0x80]
  const r = await runCliBinary(process.execPath, ['-e', `process.stdout.write(Buffer.from(${JSON.stringify(bytes)}))`], 20000)
  assert.equal(r.code, 0)
  assert.ok(Buffer.isBuffer(r.stdout))
  assert.deepEqual([...r.stdout], bytes)
})

test('runCliBinary: ненулевой код — не исключение, stderr остаётся строкой', async () => {
  const r = await runCliBinary(process.execPath, ['-e', 'process.stderr.write("ошибка: нет\\n"); process.exit(3)'], 20000)
  assert.equal(r.code, 3)
  assert.equal(r.stderr, 'ошибка: нет\n')
  assert.equal(r.stdout.length, 0)
})

test('runCliBinary: предел 32 МБ — 20 МБ проходят (текстовый запуск остановился бы на 16), больше 32 — отказ', async () => {
  const write = (n) => ['-e', `process.stdout.write(Buffer.alloc(${n}, 7))`]
  const big = await runCliBinary(process.execPath, write(20 * MIB), 60000)
  assert.equal(big.stdout.length, 20 * MIB)
  await assert.rejects(runCliBinary(process.execPath, write(32 * MIB + 1), 60000))
})

test('runCliBinary: срок — по истечении процесс останавливается и запуск отказывает', async () => {
  await assert.rejects(runCliBinary(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], 300))
})

test('runCliBinary не пускает команду через оболочку: знаки в аргументах остаются данными', async () => {
  const r = await runCliBinary(process.execPath, ['-e', 'process.stdout.write(process.argv[1])', '$(echo x); `y` | z'], 20000)
  assert.equal(r.stdout.toString('utf8'), '$(echo x); `y` | z')
})
