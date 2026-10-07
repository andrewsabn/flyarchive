// Половина раздела «Archive» для браузера: загрузка, обращения к серверной половине, токены и журнал (FR-50, FR-84).
// Подписи экрана английские, из словаря; русские здесь только данные сервера (слова состояния токена, имена, параметры журнала).
// Запуск: node --test dsh-plugin/test/*.test.mjs
import assert from 'node:assert/strict'
import test from 'node:test'

import { translator } from './kit-archive.mjs'
import { html, load, mini, recorder, reply } from './kit.mjs'

const CYRILLIC = /[Ѐ-ӿ]/
const TOKENS = [
  { name: 'dsh-local', level: 'local', created: '2026-10-03T21:04:00Z', expires: null, last_used: '2026-10-04T08:53:32Z', revoked: null, state: 'действует' },
  { name: 'laptop', level: 'read', created: '2026-10-04T09:00:00Z', expires: '2027-01-02T09:00:00Z', last_used: null, revoked: null, state: 'действует' },
  { name: 'old', level: 'full', created: '2026-09-01T09:00:00Z', expires: null, last_used: null, revoked: '2026-09-02T10:00:00Z', state: 'отозван' },
  { name: 'guest', level: 'read', created: '2026-09-01T09:00:00Z', expires: '2026-09-02T09:00:00Z', last_used: null, revoked: null, state: 'просрочен' },
]
const RECORDS = [
  { ts: '2026-10-04T09:13:59Z', server: 'search', client: 'laptop', tool: '/api/search', params: { q: 'capital', k: '3' }, status: 200, outcome: 'ok', ms: 540, via: 'tailnet', ip: '203.0.113.14' },
  { ts: '2026-10-04T09:14:00Z', server: 'mcp', client: '-', tool: 'mcp', params: {}, status: 401, outcome: 'denied: token needed', ms: 1, via: null, ip: '127.0.0.1' },
]

/** Модуль плагина и t из его же словаря: подписи в разметке — те, что увидит человек. */
async function loadWithT() {
  const loaded = await load()
  return { ...loaded, t: translator(loaded.exports.parts.EN) }
}

// ── загрузка и подключение ───────────────────────────────────────
test('модуль называет себя именем пакета; синхронно просит только React и то, что есть в основе DSH', async () => {
  const { definition, exports, requested } = await load()
  assert.equal(definition.id, 'flyarchive-dsh-plugin')
  // примитивы и react-dom/client нужны сообщению об окончании пачки; обоих нет в package.json plugin'а: они в основе платформы DSH
  assert.deepEqual([...new Set(requested)].sort(), ['@deepseek-ai/dsh-client-ui-primitives', 'react', 'react-dom/client'])
  // раздел настроек просит slots; вкладка правой панели добавила службы панели и языка
  assert.deepEqual(exports.inject, ['slots', 'locale', 'sidebarRight', 'sidebarRightTabs'])
  assert.equal(typeof exports.apply, 'function')
})

test('раздел встаёт в настройки DSH под именем «Archive», даже когда у DSH есть только slots', async () => {
  const { exports } = await load()
  const registered = []
  const ctx = {
    slots: {
      inject(slot, register) {
        assert.equal(slot, 'settings.section')
        return register()
      },
      register(options, component) {
        registered.push([options, component])
        return () => {}
      },
    },
  }
  exports.apply(ctx)
  assert.equal(registered.length, 1)
  const [options, component] = registered[0]
  assert.equal(options.name, 'settings.section')
  assert.equal(options.id, 'flyarchive')
  assert.equal(typeof options.label === 'function' ? options.label() : options.label, 'Archive')
  assert.ok(options.order > 20) // после штатных разделов
  assert.equal(typeof component, 'function')
})

// ── обращения к серверной половине ──────────────────────────────
test('адреса относительные: раздел работает и когда DSH открыт не из корня', async () => {
  const { exports } = await load()
  const { calls, doFetch } = recorder((route) => reply(200, route.includes('journal') ? { records: [] } : { tokens: [] }))
  const client = exports.parts.createClient(doFetch)
  await client.tokens()
  await client.journal('', 100)
  await client.journal('laptop', 50)
  await client.journal('-', 10)
  assert.deepEqual(calls.map((c) => c[0]), [
    'api/flyarchive.tokens', 'api/flyarchive.journal?n=100', 'api/flyarchive.journal?n=50&client=laptop', 'api/flyarchive.journal?n=10&client=-'])
  assert.ok(calls.every((c) => !c[0].startsWith('/') && !c[0].startsWith('http')))
})

test('выпуск и отзыв идут телом запроса, а не адресом', async () => {
  const { exports } = await load()
  const { calls, doFetch } = recorder(() => reply(200, { name: 'laptop', token: 'ba_new' }))
  const client = exports.parts.createClient(doFetch)
  assert.deepEqual(await client.issue('laptop', 'full', 30), { name: 'laptop', token: 'ba_new' })
  await client.issue('b', 'read', null)
  await client.revoke('laptop')
  assert.deepEqual(calls.map((c) => [c[0], c[1].method, JSON.parse(c[1].body)]), [
    ['api/flyarchive.tokens', 'POST', { name: 'laptop', level: 'full', days: 30 }],
    ['api/flyarchive.tokens', 'POST', { name: 'b', level: 'read', days: null }],
    ['api/flyarchive.tokens.revoke', 'POST', { name: 'laptop' }]])
  assert.ok(calls.every((c) => c[1].headers['content-type'] === 'application/json'))
})

test('отказ сервера показывается его словами: строкой и объектом-сообщением', async () => {
  const { exports } = await load()
  const byString = exports.parts.createClient(async () => reply(409, { error: 'the name is taken' }))
  await assert.rejects(byString.issue('laptop', 'read', null), { message: 'the name is taken', kind: 'refused' })
  const byObject = exports.parts.createClient(async () => reply(409, { error: { code: 'x', args: {}, text: 'The name is taken' } }))
  await assert.rejects(byObject.issue('laptop', 'read', null), (e) => e.message === 'The name is taken' && e.refusal.code === 'x')
})

test('истёкший вход, обрыв связи и прочий сбой объясняются по-английски', async () => {
  const { exports } = await load()
  const expired = exports.parts.createClient(async () => ({ ok: false, status: 401, json: async () => { throw new Error('not JSON') } }))
  await assert.rejects(expired.tokens(), (e) => /sign-in has expired/.test(e.message) && !CYRILLIC.test(e.message) && e.kind === 'auth')
  const offline = exports.parts.createClient(async () => { throw new TypeError('Failed to fetch') })
  await assert.rejects(offline.tokens(), (e) => /No connection to DSH/.test(e.message) && !CYRILLIC.test(e.message) && e.kind === 'offline')
  const broken = exports.parts.createClient(async () => reply(500, null))
  await assert.rejects(broken.tokens(), (e) => /500/.test(e.message) && !CYRILLIC.test(e.message) && e.kind === 'failed')
})

test('список клиентов для отбора журнала: из токенов и из самих записей', async () => {
  const { exports } = await load()
  assert.deepEqual(exports.parts.clientsOf(RECORDS, TOKENS), ['-', 'dsh-local', 'guest', 'laptop', 'old'])
  assert.deepEqual(exports.parts.clientsOf(null, null), [])
})

test('время показывается в поясе браузера, пустое — словом', async () => {
  const { exports } = await load()
  assert.match(exports.parts.formatTime('2026-10-04T09:13:59Z'), /04\.10\.2026.*09:13/)
  assert.equal(exports.parts.formatTime(null, 'never'), 'never')
  assert.equal(exports.parts.formatTime('garbage', '—'), '—')
  assert.equal(exports.parts.formatTime('2026-10-04T09:13:59Z', '—', true), '04.10.2026')
})

// ── отрисовка ───────────────────────────────────────────────────
test('таблица токенов: имена, уровни и состояния по-английски, слова состояния сервера переведены', async () => {
  const { exports, t } = await loadWithT()
  const out = html(mini.createElement(exports.parts.TokensTable, { tokens: TOKENS, onRevoke: () => {}, busy: false, t }))
  for (const word of ['dsh-local', 'laptop', 'old', 'guest', 'service', 'read', 'full', 'active', 'revoked', 'expired', 'no expiry', 'never used']) {
    assert.ok(out.includes(word), word)
  }
  assert.ok(!CYRILLIC.test(out), out)
  assert.ok(!out.includes('hash'))
  // выпуск и срок — датой без часов, иначе таблица не помещается в окно настроек; последний вызов — с часами
  assert.ok(out.includes('<td>04.10.2026</td><td>02.01.2027</td>') && out.includes('04.10.2026 08:53'))
  assert.deepEqual(t.missing, [])
})

test('незнакомое слово состояния или уровня остаётся как есть, а не пустотой', async () => {
  const { exports, t } = await loadWithT()
  const odd = [{ ...TOKENS[1], name: 'x', level: 'admin', state: 'приостановлен' }]
  const out = html(mini.createElement(exports.parts.TokensTable, { tokens: odd, onRevoke: () => {}, busy: false, t }))
  assert.ok(out.includes('admin') && out.includes('приостановлен'))
  assert.ok(!out.includes('Revoke')) // отозвать можно только действующий
})

test('отозвать можно только действующий токен клиента, служебный — нельзя', async () => {
  const { exports, t } = await loadWithT()
  const out = html(mini.createElement(exports.parts.TokensTable, { tokens: TOKENS, onRevoke: () => {}, busy: false, t }))
  assert.equal(out.split('Revoke').length - 1, 1)
  const row = out.split('<tr').find((r) => r.includes('Revoke'))
  assert.ok(row.includes('laptop'))
})

test('пока идёт запрос, кнопки выключены', async () => {
  const { exports, t } = await loadWithT()
  const table = html(mini.createElement(exports.parts.TokensTable, { tokens: TOKENS, onRevoke: () => {}, busy: true, t }))
  const form = html(mini.createElement(exports.parts.IssueForm, { onIssue: () => {}, busy: true, t }))
  assert.match(table, /<button[^>]*disabled/)
  assert.match(form, /<button[^>]*disabled/)
})

test('пустые и ещё не загруженные списки объясняются словами', async () => {
  const { exports, t } = await loadWithT()
  const { TokensTable, JournalTable } = exports.parts
  const make = (component, props) => html(mini.createElement(component, { onRevoke: () => {}, busy: false, t, ...props }))
  assert.ok(make(TokensTable, { tokens: null }).includes('Loading'))
  assert.ok(make(TokensTable, { tokens: [] }).includes('No tokens.'))
  assert.ok(make(JournalTable, { records: null }).includes('Loading'))
  assert.ok(make(JournalTable, { records: [] }).includes('No records.'))
})

test('выпущенный токен показан с предупреждением, что второго раза не будет', async () => {
  const { exports, t } = await loadWithT()
  const token = 'ba_' + 'q'.repeat(43)
  const out = html(mini.createElement(exports.parts.IssuedToken, { issued: { name: 'laptop', level: 'read', token, expires: null }, onDismiss: () => {}, t }))
  assert.ok(out.includes(token) && out.includes('laptop'))
  assert.match(out, /not shown a second time/)
  assert.ok(out.includes('Copy') && out.includes('Dismiss'))
  assert.ok(!CYRILLIC.test(out), out)
})

test('форма выпуска: имя, уровень, срок', async () => {
  const { exports, t } = await loadWithT()
  const out = html(mini.createElement(exports.parts.IssueForm, { onIssue: () => {}, busy: false, t }))
  assert.match(out, /<input[^>]*name="name"/)
  assert.match(out, /<select[^>]*name="level"/)
  assert.match(out, /<input[^>]*name="days"/)
  assert.ok(out.includes('value="read"') && out.includes('value="full"') && !out.includes('value="local"'))
  assert.ok(out.includes('Client name') && out.includes('Issue') && !CYRILLIC.test(out), out)
})

test('журнал: клиент, инструмент, исход, параметры, откуда; подписи по-английски', async () => {
  const { exports, t } = await loadWithT()
  const out = html(mini.createElement(exports.parts.JournalTable, { records: RECORDS, t }))
  for (const word of ['laptop', '/api/search', 'q=capital', 'tailnet', '203.0.113.14', '401', 'denied: token needed', 'Time', 'Client', 'Service', 'Tool', 'Outcome', 'From', 'Parameters']) {
    assert.ok(out.includes(word), word)
  }
  assert.ok(out.indexOf('09:14') < out.indexOf('09:13')) // свежие записи сверху
  assert.ok(!CYRILLIC.test(out), out)
})

test('текст из журнала вставляется как текст, а не как разметка', async () => {
  const { exports, t } = await loadWithT()
  const evil = [{ ...RECORDS[0], params: { q: '<img src=x onerror=alert(1)>' }, client: '<b>x</b>' }]
  const out = html(mini.createElement(exports.parts.JournalTable, { records: evil, t }))
  assert.ok(!out.includes('<img') && !out.includes('<b>x</b>'))
  assert.ok(out.includes('&lt;img'))
})

test('раздел целиком отрисовывается до загрузки данных: заголовок и все шесть частей по порядку', async () => {
  const { exports, t } = await loadWithT()
  const client = exports.parts.createClient(async () => reply(200, { tokens: [], records: [] }))
  const store = exports.parts.createStore({ client, timers: { setTimeout() {}, clearTimeout() {} }, page: { isVisible: () => false, subscribe: () => () => {} } })
  const out = html(mini.createElement(exports.parts.ArchiveSection, { client, store, t }))
  assert.ok(out.includes('<h2>Archive</h2>'))
  const at = ['Status', 'Folders', 'Intake', 'Tokens', 'Delete document', 'Access journal'].map((w) => out.indexOf(`<h3>${w}</h3>`))
  assert.ok(at.every((i) => i >= 0), JSON.stringify(at))
  assert.deepEqual([...at].sort((a, b) => a - b), at)
  assert.ok(out.includes('Refresh') && !CYRILLIC.test(out), out)
})
