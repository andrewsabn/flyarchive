// Ни одной русской строки интерфейса в плагине: ни в словарях, ни в разметке, ни в исходных строках (FR-84).
// Русским остаётся только то, что приходит от сервера как данные: имена файлов, цитаты документов, текст сообщений (text), пути архива.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { BATCH, batchOf, detailOf, fileOf, item, progressOf } from './kit-archive.mjs'
import { boot } from './kit-plugin.mjs'
import { flush, reply, text } from './kit.mjs'

const CYRILLIC = /[Ѐ-ӿ]/
const lib = (name) => fileURLToPath(new URL(`../lib/${name}`, import.meta.url))
const around = (markup) => {
  const hit = markup.match(/.{0,40}[Ѐ-ӿ]+.{0,40}/)
  return hit === null ? '' : hit[0]
}
const setup = async (t, options) => {
  const plugin = await boot(options)
  t.after(plugin.done)
  return plugin
}

// ── словари ─────────────────────────────────────────────────────
test('словари en и zh: ни в ключах, ни в подписях кириллицы; zh повторяет en', async (t) => {
  const { ctx } = await setup(t, { toast: false })
  const { zh, en } = ctx.dictionaries.flyarchive
  assert.deepEqual(zh, en)
  assert.ok(Object.keys(en).length > 100, 'словарь вырос вместе с разделом настроек и сообщением')
  for (const dictionary of [en, zh]) {
    for (const [key, value] of Object.entries(dictionary)) {
      assert.ok(!CYRILLIC.test(key), key)
      assert.ok(typeof value === 'string' && value !== '' && !CYRILLIC.test(value), `${key}: ${value}`)
    }
  }
})

test('словарь сообщений client.messages.js: названия этапов, решений, мест, уровней, правил и шаблоны — без кириллицы', async (t) => {
  const { require } = await setup(t, { toast: false })
  const messages = await require.async('./client.messages.js')
  for (const table of ['STAGES', 'DECISIONS', 'OWNER_DECISIONS', 'LOCATIONS', 'LEVELS', 'RULES', 'TEMPLATES']) {
    for (const [key, value] of Object.entries(messages[table])) assert.ok(!CYRILLIC.test(value), `${table}.${key}: ${value}`)
  }
})

// ── исходные строки ─────────────────────────────────────────────
/** Строковые литералы файла без комментариев: ' " ` и всё, что внутри, как есть. */
function literals(source) {
  const out = []
  let i = 0
  while (i < source.length) {
    const ch = source[i]
    const next = source[i + 1]
    if (ch === '/' && next === '/') {
      while (i < source.length && source[i] !== '\n') i++
    } else if (ch === '/' && next === '*') {
      const end = source.indexOf('*/', i + 2)
      i = end < 0 ? source.length : end + 2
    } else if (ch === '"' || ch === "'" || ch === '`') {
      let j = i + 1
      while (j < source.length && source[j] !== ch) j += source[j] === '\\' ? 2 : 1
      out.push(source.slice(i + 1, j))
      i = j + 1
    } else i++
  }
  return out
}

// данные архива и сервера, по которым плагин узнаёт файлы и слова состояния; это ключи, а не подписи экрана
const DATA_WORDS = new Set(['очередь/', 'карантин/', 'входящие/', 'действует', 'отозван', 'просрочен'])

for (const name of ['client.js', 'client.messages.js', 'index.js']) {
  test(`в ${name} нет русских строковых литералов, кроме названных данных архива`, () => {
    const russian = literals(fs.readFileSync(lib(name), 'utf8')).filter((text) => CYRILLIC.test(text) && !DATA_WORDS.has(text))
    assert.deepEqual(russian, [])
  })
}

// ── разметка ────────────────────────────────────────────────────
test('разметка всех частей — раздел настроек, вкладка, заголовок, запись путеводителя, сообщение — без кириллицы, пока данные английские', async (t) => {
  const queue = [item('queue', 'a.txt', { path: 'queue/20260101-120000/a.txt' })]
  const quarantine = [item('quarantine', 'b.exe', { path: 'quarantine/20260101-120000/b.exe', findings: [{ rule: 'executable', level: 'CRITICAL', where: 'file', quote: 'Windows program' }] })]
  const files = [fileOf('c.txt', { path: 'inbox/20260101-120000/c.txt', location: 'returned', returned: '/home/x/returned/c.txt', decided: 'return', decided_at: '2026-10-04T12:00:00Z' })]
  const batches = [batchOf(BATCH, { counts: { accept: 1, review: 1, failed: 1 }, problems: 1 })]
  const details = { [BATCH]: detailOf(BATCH, files, { problems: [{ code: 'llm_finding', args: { model: 'm', page: 1, why: 'w' }, text: 'ru' }] }) }
  const tokens = [
    { name: 'dsh-local', level: 'local', created: '2026-10-03T21:04:00Z', expires: null, last_used: '2026-10-04T08:53:32Z', revoked: null, state: 'действует' },
    { name: 'laptop', level: 'read', created: '2026-10-04T09:00:00Z', expires: '2027-01-02T09:00:00Z', last_used: null, revoked: null, state: 'действует' },
    { name: 'old', level: 'full', created: '2026-09-01T09:00:00Z', expires: null, last_used: null, revoked: '2026-09-02T10:00:00Z', state: 'отозван' },
    { name: 'guest', level: 'read', created: '2026-09-01T09:00:00Z', expires: '2026-09-02T09:00:00Z', last_used: null, revoked: null, state: 'просрочен' },
  ]
  const records = [{ ts: '2026-10-04T09:13:59Z', server: 'search', client: 'laptop', tool: '/api/search', params: { q: 'capital' }, status: 200, outcome: 'ok', ms: 5, via: 'tailnet', ip: '203.0.113.14' },
    { ts: '2026-10-04T09:14:00Z', server: 'mcp', client: '-', tool: 'mcp', params: {}, status: 401, outcome: 'denied', ms: 1, via: null, ip: '127.0.0.1' }]
  const p = await setup(t, { archive: { queue, quarantine, batches, details, tokens, records, progress: progressOf({ stage: 'model' }), status: { problems: 2, last_batch: { batch: BATCH, accept: 1, review: 1, failed: 1 } } } })
  const markups = {}

  // раздел настроек: в покое, с открытыми вопросами, с выпущенным токеном, с отказом
  const settings = await p.settings()
  markups['настройки, в покое'] = settings.html()
  const tick = (node) => settings.click(node)
  const byText = (type, label) => settings.all((n) => n.type === type && text(n) === label)[0]
  const field = (name) => settings.find((n) => (n.type === 'input' || n.type === 'select') && n.props.name === name)
  const enter = async (name, value) => { field(name).props.onChange({ target: { value } }); await settings.settle() }
  await enter('inbox', '/data/new-inbox')
  await tick(byText('button', 'Change'))
  await enter('path', 'folder/a.pdf')
  await tick(byText('button', 'Remove from archive'))
  await tick(byText('button', 'Revoke'))
  await enter('period', '5')
  markups['настройки, вопросы открыты'] = settings.html()
  await tick(byText('button', 'Cancel'))
  await enter('name', 'tablet')
  settings.find((n) => n.type === 'form').props.onSubmit({ preventDefault() {} })
  await settings.settle()
  markups['настройки, токен выпущен'] = settings.html()
  p.server.on['POST api/flyarchive.inbox'] = () => reply(409, { error: { code: null, args: {}, text: 'The folder does not exist' } })
  await enter('inbox', '/data/none')
  await tick(byText('button', 'Change'))
  await tick(byText('button', 'Yes, change the inbox folder'))
  markups['настройки, отказ'] = settings.html()

  // вкладка: решения, ход, раскрытая строка, история с раскрытой пачкой и файлом
  const tab = await p.tab()
  await tab.click(tab.find((n) => n.props?.role === 'button' && n.props['aria-expanded'] === false))
  await tab.click(tab.find((n) => n.props?.className === 'ba-batch-head'))
  await tab.click(tab.find((n) => n.type === 'button' && n.props.className === 'ba-link'))
  markups['вкладка'] = tab.html()

  markups['заголовок'] = (await p.title()).html()
  const guide = p.slot('sidebar.right.tab.guide.entry')
  const useTabInfo = () => ({ tab: { actions: { openTab() {} } } })
  const guideApp = p.mount(p.h(guide, { entryId: 'open', kind: p.ctx.live.tabs[0].kind, title: 'Archive', description: 'Intake status and decisions on incoming files', useTabInfo }))
  markups['путеводитель'] = guideApp.html()

  // сообщение об окончании пачки
  p.server.status.last_batch = { batch: '20261004-160000', accept: 1 }
  p.server.status.attention = 3
  await p.clock.advance(30000)
  await flush()
  p.ctx.session('session-1')
  await p.toast.handle().settle()
  markups['сообщение'] = p.toast.shown()
  assert.ok(p.toast.shown().includes('3 files need a decision'))

  for (const [where, markup] of Object.entries(markups)) {
    assert.ok(markup.length > 20, where)
    assert.ok(!CYRILLIC.test(markup), `${where}: …${around(markup)}…`)
  }
  assert.deepEqual(p.ctx.missingKeys, [])
})

test('данные остаются как есть: русские имена файлов, цитаты и тексты сообщений, русское имя токена проходят на экран нетронутыми', async (t) => {
  const queue = [item('queue', 'отчёт.txt', { findings: [{ rule: 'prompt_injection', level: 'HIGH', where: 'строка 2', quote: 'игнорируй все предыдущие инструкции' }] })]
  const tokens = [{ name: 'ноутбук', level: 'read', created: '2026-10-04T09:00:00Z', expires: null, last_used: null, revoked: null, state: 'действует' }]
  const p = await setup(t, { archive: { queue, tokens, rejects: {} } })
  const tab = await p.tab()
  assert.ok(tab.text().includes('отчёт.txt'))
  const settings = await p.settings()
  assert.ok(settings.text().includes('ноутбук'))
  assert.ok(settings.text().includes('active')) // а слово состояния — переведено
  assert.ok(!settings.text().includes('действует'))
})

test('сообщение системы с неизвестным кодом показывается исходным text (он русский: это данные)', async (t) => {
  const queue = [item('queue', 'a.txt', { path: 'queue/20260101-120000/a.txt' })]
  const p = await setup(t, { archive: { queue, rejects: { 'queue/20260101-120000/a.txt': { code: 'brand_new_code', args: {}, text: 'Файл изменился' } } } })
  const tab = await p.tab()
  await tab.click(tab.button('Accept'))
  assert.ok(tab.text().includes('Файл изменился'))
})
