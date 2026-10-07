// Половина для браузера: входящие в настройках — состояние, папки, параметры разбора, удаление документа (FR-41, FR-52, FR-54, FR-84).
// Раньше здесь же были очередь утверждения и карантин; теперь они только на вкладке (client-tab, client-batches), а подписи английские.
import assert from 'node:assert/strict'
import test from 'node:test'

import { translator } from './kit-archive.mjs'
import { find, html, load, mini as React, recorder, reply, text } from './kit.mjs'

const CYRILLIC = /[Ѐ-ӿ]/
const STATUS = { inbox: '/mnt/c/Users/x/flyarchive-inbox', returned: '/mnt/c/Users/x/returned', home: '/home/x/flyarchive', period: 30, llm: true, cloud: true,
  threshold: 20, max_gb: 2, max_files: 5000, max_ratio: 100, depth: 3, timer: true, waiting: 2, queue: 1, quarantine: 0, pending: 0, attention: 1,
  problems: 0, progress: null, last_batch: { batch: '20260101-120000', time: '2026-10-04T11:18:40Z', accept: 11, review: 2 } }
const noop = () => {}

async function loadWithT() {
  const loaded = await load()
  return { ...loaded, t: translator(loaded.exports.parts.EN) }
}
const button = (tree, label) => find(tree, (n) => n.type === 'button' && text(n) === label)

// ── обращения к серверной половине ──────────────────────────────
test('входящие: состояние, настройка, смена папки и запуск разбора', async () => {
  const { exports } = await load()
  const { calls, doFetch } = recorder((route, init) => reply(200, init?.method === 'POST' && route.endsWith('run') ? { started: true } : STATUS))
  const client = exports.parts.createClient(doFetch)
  assert.deepEqual(await client.inbox(), STATUS)
  await client.setInbox({ period: 5, llm: false })
  await client.setInboxPath('/data/new-inbox')
  assert.deepEqual(await client.runInbox(), { started: true })
  assert.deepEqual(calls.map((c) => [c[0], c[1]?.method ?? 'GET', c[1]?.body ? JSON.parse(c[1].body) : null]), [
    ['api/flyarchive.inbox', 'GET', null],
    ['api/flyarchive.inbox', 'POST', { period: 5, llm: false }],
    ['api/flyarchive.inbox', 'POST', { path: '/data/new-inbox', confirm: true }], // смена папки: только path и confirm, и confirm всегда true
    ['api/flyarchive.inbox.run', 'POST', {}]])
})

test('удаление документа: путь и подтверждение идут телом запроса; очередь и карантин из раздела ушли', async () => {
  const { exports } = await load()
  const { calls, doFetch } = recorder(() => reply(200, { path: 'x', rows: 1, moved_to: 'y' }))
  const client = exports.parts.createClient(doFetch)
  await client.deleteDoc('export-a\\Inbox\\письмо.eml')
  assert.deepEqual(calls.map((c) => [c[0], JSON.parse(c[1].body)]), [['api/flyarchive.doc.delete', { path: 'export-a\\Inbox\\письмо.eml', confirm: true }]])
  assert.ok(calls.every((c) => !c[0].startsWith('/') && !c[0].includes('?')))
  // списки очереди и карантина читает общее хранилище вкладки; решения идут через него же (client-store)
  assert.equal(typeof client.queue, 'function')
  assert.equal(exports.parts.ReviewTable, undefined)
  assert.equal(exports.parts.InboxPanel, undefined)
})

// ── Intake ──────────────────────────────────────────────────────
test('Intake: период, порог, пределы, проверка моделью и запасная модель — подписи английские, значения из состояния', async () => {
  const { exports, t } = await loadWithT()
  const out = html(React.createElement(exports.parts.IntakePanel, { status: STATUS, onSave: noop, busy: false, t }))
  for (const word of ['Run every', 'Score threshold', 'Check with the model', 'Cloud fallback model', 'Archive size, GB', 'Files', 'Compression, to 1', 'Nesting depth', 'Save', 'leaves this machine']) {
    assert.ok(out.includes(word), word)
  }
  assert.ok(!CYRILLIC.test(out), out)
  assert.ok(!out.includes('No timer'))
  assert.deepEqual(t.missing, [])
})

test('Intake: в настройке пять периодов, выбран текущий, порог и пределы в полях', async () => {
  const { exports, t } = await loadWithT()
  const out = html(React.createElement(exports.parts.IntakePanel, { status: STATUS, onSave: noop, busy: false, t }))
  assert.match(out, /<select name="period" value="30">/)
  assert.deepEqual([...out.matchAll(/<option value="(\d+)">/g)].map((m) => Number(m[1])), [1, 5, 10, 30, 60])
  assert.ok(out.includes('<option value="5">5 min</option>'))
  for (const [name, value] of [['threshold', 20], ['max_gb', 2], ['max_files', 5000], ['max_ratio', 100], ['depth', 3]]) {
    assert.match(out, new RegExp(`<input name="${name}" value="${value}"`), name)
  }
  assert.match(out, /<input type="checkbox" name="llm" checked>/)
  assert.match(out, /<input type="checkbox" name="cloud" checked>/)
})

test('Intake: без проверки моделью запасная модель недоступна и предупреждения нет', async () => {
  const { exports, t } = await loadWithT()
  const out = html(React.createElement(exports.parts.IntakePanel, { status: { ...STATUS, llm: false }, onSave: noop, busy: false, t }))
  assert.match(out, /<input type="checkbox" name="cloud" checked disabled>/)
  assert.ok(!out.includes('leaves this machine'))
})

test('Intake: о непоставленном таймере сказано', async () => {
  const { exports, t } = await loadWithT()
  const out = html(React.createElement(exports.parts.IntakePanel, { status: { ...STATUS, timer: false }, onSave: noop, busy: false, t }))
  assert.ok(out.includes('No timer: runs only on request'))
})

test('Intake: сохранять нечего, пока ничего не изменено; во время запроса кнопка выключена; до ответа — «Loading…»', async () => {
  const { exports, t } = await loadWithT()
  const idle = html(React.createElement(exports.parts.IntakePanel, { status: STATUS, onSave: noop, busy: false, t }))
  assert.match(idle, /<button type="button" disabled>Save<\/button>/)
  assert.ok(!idle.includes('Run now')) // разбор запускается на вкладке
  assert.ok(html(React.createElement(exports.parts.IntakePanel, { status: null, onSave: noop, busy: false, t })).includes('Loading'))
})

// ── Folders ─────────────────────────────────────────────────────
test('Folders: поле с текущим адресом, «Change» (пока не изменено — выключена), папка возврата и каталог архива с «Copy path»', async () => {
  const { exports, t } = await loadWithT()
  const out = html(React.createElement(exports.parts.FoldersPanel, { status: STATUS, onChange: noop, busy: false, t }))
  assert.match(out, /<input name="inbox" value="\/mnt\/c\/Users\/x\/flyarchive-inbox"/)
  assert.match(out, /<button type="button" disabled>Change<\/button>/)
  for (const word of ['Inbox folder', '/mnt/c/Users/x/returned', '/home/x/flyarchive', 'Returns folder', 'Archive directory']) assert.ok(out.includes(word), word)
  assert.equal(out.split('>Copy path<').length - 1, 2)
  assert.ok(!CYRILLIC.test(out), out)
  assert.deepEqual(t.missing, [])
})

test('Folders: «Change» до ответа сервера и без изменений ничего не зовёт', async () => {
  const { exports, t } = await loadWithT()
  let called = 0
  const tree = React.createElement(exports.parts.FoldersPanel, { status: STATUS, onChange: () => { called++ }, busy: false, t })
  const change = button(tree, 'Change')
  assert.equal(change.props.disabled, true)
  change.props.onClick?.()
  assert.equal(called, 0)
  assert.ok(html(React.createElement(exports.parts.FoldersPanel, { status: null, onChange: noop, busy: false, t })).includes('Loading'))
})

// ── Delete document ─────────────────────────────────────────────
test('Delete document: поле пути и кнопка, без пути кнопка выключена, первое нажатие не удаляет', async () => {
  const { exports, t } = await loadWithT()
  let deleted = 0
  const tree = React.createElement(exports.parts.DeleteDoc, { onDelete: () => { deleted++ }, busy: false, t })
  const out = html(tree)
  assert.match(out, /<input name="path" value=""/)
  assert.match(out, /<button type="button" disabled>Remove from archive<\/button>/)
  assert.ok(!out.includes('Yes, remove'))
  assert.ok(out.includes('Document path, as shown in search results') && !CYRILLIC.test(out), out)
  button(tree, 'Remove from archive').props.onClick?.()
  assert.equal(deleted, 0)
})
