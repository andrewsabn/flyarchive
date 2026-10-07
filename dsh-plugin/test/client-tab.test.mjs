// Вкладка «Archive» в правой панели DSH: регистрация, язык, строка состояния, ход, решения (FR-80, FR-81).
// Плагин ставится на подставной контекст DSH, вкладка рисуется малым рисовальщиком с состоянием, архив и время подставные.
import assert from 'node:assert/strict'
import test from 'node:test'

import { fakeArchive, fakeCtx, item, progressOf, slotNamed } from './kit-archive.mjs'
import { createClock, fakePage, find, flush, load, reply, stateful, text } from './kit.mjs'

const INBOX = 'api/flyarchive.inbox'
const QUEUE = 'api/flyarchive.queue'
const QUARANTINE = 'api/flyarchive.quarantine'
const RUN = 'api/flyarchive.inbox.run'
const CYRILLIC = /[Ѐ-ӿ]/

/** Плагин на подставном DSH, тело вкладки смонтировано после первого опроса. */
async function boot({ without = [], mountBody = true, hidden = false, chunksFail = false, ...initial } = {}) {
  const { React, mount } = stateful()
  const loaded = await load(React, { chunksFail })
  const server = fakeArchive(initial)
  const clock = createClock()
  const page = fakePage(hidden)
  const ctx = fakeCtx({ without })
  const errors = [] // что плагин написал в console.error, пока словарь не пришёл
  const savedError = console.error
  if (chunksFail) console.error = (...args) => errors.push(args)
  try {
    loaded.exports.parts.install(ctx, { fetch: server.doFetch, timers: clock, page })
    await clock.advance(0)
    await loaded.idle()
    await flush()
  } finally {
    console.error = savedError
  }
  const h = React.createElement
  const bodyOf = () => slotNamed(ctx, 'sidebar.right.pane.tab')[0].component
  const app = mountBody ? mount(h(bodyOf(), {})) : null
  if (app) await app.settle()
  return { ...loaded, React, mount, h, server, clock, page, ctx, app, bodyOf, errors }
}

const rowOf = (app, path) => app.find((n) => n.type === 'li' && n.props['data-path'] === path)
const inRow = (app, path, label) => find(rowOf(app, path), (n) => n.type === 'button' && text(n) === label)
const boxOf = (app, path) => find(rowOf(app, path), (n) => n.type === 'input' && n.props.type === 'checkbox')
const buttonLabels = (node) => {
  const out = []
  const walk = (n) => {
    if (n === null || typeof n !== 'object') return
    if (Array.isArray(n)) return n.forEach(walk)
    if (n.type === 'button') out.push(text(n))
    walk(n.props.children)
  }
  walk(node)
  return out
}
const enabled = (button) => button !== null && !button.props.disabled
const has = (app, label) => app.button(label) !== null

// ── регистрация ─────────────────────────────────────────────────
test('плагин регистрирует одиночный тип вкладки с keepMounted, заголовком и записью в путеводителе', async () => {
  const { ctx } = await boot({ mountBody: false })
  assert.equal(ctx.live.tabs.length, 1)
  const [definition] = ctx.live.tabs
  assert.equal(definition.id, 'flyarchive-dsh-plugin')
  assert.ok(typeof definition.kind === 'string' && definition.kind.length > 0)
  assert.equal(definition.multiple, false)
  assert.equal(definition.keepMounted, true)
  assert.equal(definition.title(), 'Archive')
  assert.equal(definition.guide.length, 1)
  const [entry] = definition.guide
  assert.ok(typeof entry.id === 'string' && Number.isFinite(entry.order))
  assert.equal(entry.title(), 'Archive')
  assert.match(entry.description(), /^[\x20-\x7E]+$/) // английская строка из словаря
  assert.equal(typeof entry.icon, 'function')
})

test('тело, заголовок и запись путеводителя встают в места правой панели под id типа, с тем же словарём', async () => {
  const { ctx } = await boot({ mountBody: false })
  const [definition] = ctx.live.tabs
  for (const name of ['sidebar.right.pane.tab', 'sidebar.right.pane.tab.title', 'sidebar.right.tab.guide.entry']) {
    const entries = slotNamed(ctx, name)
    assert.equal(entries.length, 1, name)
    assert.equal(entries[0].options.key, definition.id, name)
    assert.equal(entries[0].options.locale, 'flyarchive', name)
    assert.equal(typeof entries[0].component, 'function', name)
  }
  assert.deepEqual(ctx.live.slots.map((s) => s.options.name).sort(), [
    'settings.section', 'sidebar.right.pane.tab', 'sidebar.right.pane.tab.title', 'sidebar.right.tab.guide.entry'])
})

test('раздел настроек регистрируется под тем же id, а название у него английское', async () => {
  const { ctx } = await boot({ mountBody: false })
  const [settings] = slotNamed(ctx, 'settings.section')
  assert.equal(settings.options.id, 'flyarchive')
  assert.equal(settings.options.label(), 'Archive')
})

test('словарь flyarchive: наборы ключей zh и en совпадают, zh повторяет en, всё по-английски', async () => {
  const { ctx } = await boot({ mountBody: false })
  const { zh, en } = ctx.dictionaries.flyarchive
  assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort())
  assert.deepEqual(zh, en)
  assert.ok(Object.keys(en).length > 20)
  for (const [key, value] of Object.entries(en)) assert.ok(typeof value === 'string' && value !== '' && !CYRILLIC.test(value), key)
})

test('без служб правой панели плагин не падает: вкладки нет, раздел настроек остаётся, а опрос идёт — он нужен разделу', async () => {
  for (const missing of ['locale', 'sidebarRight', 'sidebarRightTabs']) {
    const { exports } = await load()
    const ctx = fakeCtx({ without: [missing] })
    const server = fakeArchive()
    const clock = createClock()
    exports.parts.install(ctx, { fetch: server.doFetch, timers: clock, page: fakePage() })
    await clock.advance(120000)
    assert.deepEqual(ctx.live.slots.map((s) => s.options.name), ['settings.section'], missing)
    assert.equal(ctx.live.tabs.length, 0, missing)
    // состояние раздела настроек — то же хранилище, и его опрашивает один плагин: раз в 30 секунд, один опрос на тик
    assert.equal(server.count(INBOX), 5, `${missing}: опрос идёт и без вкладки`)
    ctx.dispose()
    await clock.advance(120000)
    assert.equal(server.count(INBOX), 5, `${missing}: снятие плагина останавливает опрос`)
  }
})

test('apply с обычным контекстом (только slots) по-прежнему ставит раздел настроек', async () => {
  const { exports } = await load()
  const registered = []
  exports.apply({
    slots: {
      inject: (slot, register) => register(),
      register: (options) => { registered.push(options); return () => {} },
    },
  })
  assert.deepEqual(registered.map((o) => o.id), ['flyarchive'])
})

test('служб четыре: slots, locale, sidebarRight, sidebarRightTabs', async () => {
  const { exports } = await load()
  assert.deepEqual([...exports.inject].sort(), ['locale', 'sidebarRight', 'sidebarRightTabs', 'slots'])
})

test('словарь сообщений просится через require.async; синхронно — React и два модуля основы DSH для сообщения, больше ничего', async () => {
  const { asyncRequested, requested } = await boot({ mountBody: false })
  assert.deepEqual(asyncRequested, ['./client.messages.js'])
  assert.deepEqual([...new Set(requested)].sort(), ['@deepseek-ai/dsh-client-ui-primitives', 'react', 'react-dom/client'])
})

test('снятие плагина снимает типы, места, словарь и опрос', async () => {
  const { ctx, server, clock } = await boot({ mountBody: false })
  const before = server.calls.length
  ctx.dispose()
  assert.equal(ctx.live.tabs.length, 0)
  assert.equal(ctx.live.dictionaries.length, 0)
  assert.deepEqual(ctx.live.slots.map((s) => s.options.name), ['settings.section'])
  await clock.advance(5 * 60 * 1000)
  assert.equal(server.calls.length, before)
})

test('хранилище одно: два тела вкладки (два сеанса) не удваивают опрос', async () => {
  const { h, mount, bodyOf, server, clock } = await boot({ mountBody: false })
  const first = mount(h(bodyOf(), {}))
  const second = mount(h(bodyOf(), {}))
  await first.settle()
  await second.settle()
  assert.equal(server.count(INBOX), 1)
  await clock.advance(30000)
  assert.equal(server.count(INBOX), 2)
  assert.equal(first.text(), second.text())
})

test('заголовок вкладки — «Archive» из словаря', async () => {
  const { h, mount, ctx } = await boot({ mountBody: false })
  const app = mount(h(slotNamed(ctx, 'sidebar.right.pane.tab.title')[0].component, {}))
  assert.equal(app.text().trim(), 'Archive')
})

test('запись путеводителя: название и описание из словаря, нажатие открывает вкладку на месте путеводителя', async () => {
  const { h, mount, ctx } = await boot({ mountBody: false })
  const [definition] = ctx.live.tabs
  const opened = []
  const useTabInfo = () => ({ tab: { actions: { openTab: (...args) => opened.push(args) } } })
  const guide = slotNamed(ctx, 'sidebar.right.tab.guide.entry')[0].component
  const app = mount(h(guide, { entryId: 'open', kind: definition.kind, title: 'Archive', description: 'What it does', useTabInfo }))
  assert.ok(app.text().includes('Archive') && app.text().includes('What it does'))
  await app.click(app.find((n) => n.type === 'button'))
  assert.deepEqual(opened, [[definition.kind, { replaceTab: true }]])
})

test('подпись из t, который подал DSH, главнее общей; без него берётся связанный словарь', async () => {
  const { h, mount, bodyOf } = await boot({ mountBody: false })
  const marked = mount(h(bodyOf(), { t: (key) => `«${key}»` }))
  await marked.settle()
  assert.ok(marked.button('«run»'))
  const plain = mount(h(bodyOf(), {}))
  await plain.settle()
  assert.ok(plain.button('Run now'))
})

// ── строка состояния ────────────────────────────────────────────
test('до первого ответа вкладка говорит, что грузится', async () => {
  const { app } = await boot({ hidden: true })
  assert.match(app.text(), /Loading/)
  assert.equal(enabled(app.button('Run now')), false) // состояние неизвестно — запускать рано
})

test('строка состояния: сколько ждёт, период таймера, итог последней пачки, число замечаний', async () => {
  const { app } = await boot({ status: { waiting: 3, period: 10, problems: 2, last_batch: { batch: '20260101-120000', accept: 11, review: 2, duplicate: 3, failed: 0 } } })
  const t = app.text()
  assert.ok(t.includes('Waiting in the inbox folder: 3'), t)
  assert.ok(t.includes('Runs every 10 min'), t)
  assert.ok(t.includes('Last batch 20260101-120000: accepted 11, needs review 2, duplicates 3'), t)
  assert.ok(!t.includes('failed 0'), t) // нулевые решения не пересказываются
  assert.ok(t.includes('Problems in the last batch: 2'), t)
})

test('строка состояния: нет таймера, нет пачек, нет замечаний', async () => {
  const { app } = await boot({ status: { timer: false, last_batch: null, problems: 0 } })
  const t = app.text()
  assert.ok(t.includes('No timer'), t)
  assert.ok(t.includes('No batches yet'), t)
  assert.ok(t.includes('Problems in the last batch: 0'), t)
})

test('сервер без поля problems (старая версия): строки о замечаниях нет, вкладка работает', async () => {
  const on = { ['GET ' + INBOX]: async (_, s) => { const view = s.view(); delete view.problems; return reply(200, view) } }
  const { app } = await boot({ on })
  assert.ok(app.text().includes('Waiting in the inbox folder'))
  assert.ok(!app.text().includes('Problems in the last batch'))
})

test('сервер без поля progress (старая версия): разбор не идёт, «Run now» доступна, опрос не частый', async () => {
  const on = { ['GET ' + INBOX]: async (_, s) => { const view = s.view(); delete view.progress; return reply(200, view) } }
  const { app, clock } = await boot({ on })
  assert.ok(enabled(app.button('Run now')))
  assert.equal(app.find((n) => n.props?.role === 'progressbar'), null)
  assert.deepEqual(clock.waits(), [30000])
})

test('адрес входящей папки показан, «Copy path» кладёт его в буфер', async () => {
  const written = []
  const saved = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  Object.defineProperty(globalThis, 'navigator', { configurable: true, writable: true, value: { clipboard: { writeText: async (v) => { written.push(v) } } } })
  try {
    const { app } = await boot({ status: { inbox: '/home/x/inbox' } })
    assert.ok(app.text().includes('/home/x/inbox'))
    await app.click(app.button('Copy path'))
    assert.deepEqual(written, ['/home/x/inbox'])
    assert.ok(app.button('Copied'))
  } finally {
    if (saved) Object.defineProperty(globalThis, 'navigator', saved)
    else delete globalThis.navigator
  }
})

test('буфер недоступен или отказал — сказано, как скопировать руками, вкладка работает', async () => {
  const saved = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  Object.defineProperty(globalThis, 'navigator', { configurable: true, writable: true, value: { clipboard: { writeText: async () => { throw new Error('denied') } } } })
  try {
    const { app } = await boot({ status: { inbox: '/home/x/inbox' } })
    await app.click(app.button('Copy path'))
    assert.match(app.text(), /Could not copy/)
    assert.ok(app.button('Copy path') && !app.button('Copied'))
    Object.defineProperty(globalThis, 'navigator', { configurable: true, writable: true, value: {} })
    await app.click(app.button('Copy path'))
    assert.match(app.text(), /Could not copy/)
  } finally {
    if (saved) Object.defineProperty(globalThis, 'navigator', saved)
    else delete globalThis.navigator
  }
})

// ── «Run now» и ход ─────────────────────────────────────────────
test('«Run now» запускает разбор одним запросом и тут же берёт свежее состояние', async () => {
  const { app, server } = await boot()
  assert.ok(enabled(app.button('Run now')))
  await app.click(app.button('Run now'))
  assert.equal(server.posts(RUN).length, 1)
  assert.equal(server.count(INBOX), 2)
})

test('пока разбор идёт, «Run now» неактивна и показан ход; когда кончился — ход уходит, кнопка оживает', async () => {
  const { app, server, clock } = await boot({ progress: progressOf() })
  assert.equal(enabled(app.button('Run now')), false)
  assert.equal(await app.click(app.button('Run now')), false)
  assert.equal(server.posts(RUN).length, 0)
  assert.ok(app.find((n) => n.props?.role === 'progressbar'))
  server.progress = null
  await clock.advance(2000)
  await app.settle()
  assert.equal(enabled(app.button('Run now')), true)
  assert.equal(app.find((n) => n.props?.role === 'progressbar'), null)
})

test('пока запрос на запуск не вернулся, кнопка занята', async () => {
  const { app, server } = await boot()
  let release
  server.on['POST ' + RUN] = () => new Promise((resolve) => { release = () => resolve(reply(200, { started: true })) })
  const pressed = app.click(app.button('Run now'))
  await flush()
  assert.equal(enabled(app.button('Run now')), false)
  release()
  await pressed
  await app.settle()
  assert.equal(enabled(app.button('Run now')), true)
})

test('ход: этап по-английски, done из total, полоса, последний готовый файл', async () => {
  const { app } = await boot({ progress: progressOf({ stage: 'model', done: 12, total: 40, current: 'report.txt' }) })
  const t = app.text()
  assert.ok(t.includes('Model check'), t)
  assert.ok(t.includes('12 of 40'), t)
  assert.ok(t.includes('Last file: report.txt'), t)
  const bar = app.find((n) => n.props?.role === 'progressbar')
  assert.equal(bar.props['aria-valuenow'], 12)
  assert.equal(bar.props['aria-valuemax'], 40)
  assert.equal(bar.props['aria-valuemin'], 0)
})

test('ход со всеми пятью этапами называет их по словарю', async () => {
  const { app, server, clock } = await boot({ progress: progressOf({ stage: 'intake' }) })
  const seen = []
  for (const stage of ['intake', 'model', 'settle', 'index', 'sources']) {
    server.progress = progressOf({ stage })
    await clock.advance(2000)
    await app.settle()
    seen.push(stage)
    const label = { intake: 'Unpacking and rule checks', model: 'Model check', settle: 'Filing and de-duplication', index: 'Indexing', sources: 'Cleaning up sources' }[stage]
    assert.ok(app.text().includes(label), `${stage}: ${app.text()}`)
  }
  assert.equal(seen.length, 5)
})

test('ход, когда total неизвестен: только done, без «of», полоса без значения', async () => {
  const { app } = await boot({ progress: progressOf({ done: 12, total: null }) })
  const t = app.text()
  assert.ok(t.includes('12 done'), t)
  assert.ok(!t.includes(' of '), t)
  assert.ok(!/null|NaN|undefined/.test(t), t)
  const bar = app.find((n) => n.props?.role === 'progressbar')
  assert.equal(bar.props['aria-valuenow'], undefined)
})

test('ход: total не число — как неизвестный; total меньше done — не показывает «12 из 10»', async () => {
  const odd = await boot({ progress: progressOf({ done: 5, total: 'много' }) })
  assert.ok(odd.app.text().includes('5 done'))
  const grown = await boot({ progress: progressOf({ done: 12, total: 10 }) })
  assert.ok(grown.app.text().includes('12 of 12'), grown.app.text())
  const bar = grown.app.find((n) => n.props?.role === 'progressbar')
  assert.equal(bar.props['aria-valuenow'], 12)
  assert.equal(bar.props['aria-valuemax'], 12)
})

test('ход: нет последнего файла — строки о нём нет; ход обновляется по опросу', async () => {
  const { app, server, clock } = await boot({ progress: progressOf({ done: 1, total: 4, current: null }) })
  assert.ok(!app.text().includes('Last file'))
  server.progress = progressOf({ done: 3, total: 4, current: 'c.txt' })
  await clock.advance(2000)
  await app.settle()
  assert.ok(app.text().includes('3 of 4') && app.text().includes('Last file: c.txt'))
})

// ── «Needs decision» ────────────────────────────────────────────
const TWO_AND_ONE = () => ({
  queue: [item('queue', 'a.txt'), item('queue', 'b.txt', { score: 10, findings: [{ rule: 'garbled', level: 'MEDIUM', where: 'x', quote: 'y' }] })],
  quarantine: [item('quarantine', 'c.exe', { score: 50, findings: [{ rule: 'executable', level: 'CRITICAL', where: 'x', quote: 'y' }] })],
})

test('«Needs decision (N)»: очередь и карантин одним списком, строка: имя, откуда, баллы, главное правило, время', async () => {
  const data = TWO_AND_ONE()
  const { app } = await boot(data)
  assert.ok(app.text().includes('Needs decision (3)'), app.text())
  const rows = app.all((n) => n.type === 'li')
  assert.equal(rows.length, 3)
  assert.deepEqual(rows.map((r) => r.props['data-path']), [...data.queue, ...data.quarantine].map((i) => i.path)) // очередь, потом карантин
  const [a, b, c] = rows.map(text)
  assert.ok(a.includes('a.txt') && a.includes('review') && a.includes('Score 55') && a.includes('Prompt injection') && a.includes('High'), a)
  assert.ok(a.includes('04.10.2026 11:18'), a)
  assert.ok(b.includes('b.txt') && b.includes('Score 10') && b.includes('Garbled text') && b.includes('Medium'), b)
  assert.ok(c.includes('c.exe') && c.includes('quarantine') && c.includes('Score 50') && c.includes('Executable file') && c.includes('Critical'), c)
  assert.ok(!c.includes('review'), c)
})

test('главное правило — самая тяжёлая находка, при равенстве — первая; без находок и с чужим правилом строка не ломается', async () => {
  const f = (rule, level) => ({ rule, level, where: '', quote: '' })
  const data = {
    queue: [
      item('queue', 'heavy.txt', { findings: [f('garbled', 'HIGH'), f('executable', 'CRITICAL'), f('secret', 'LOW')] }),
      item('queue', 'tie.txt', { findings: [f('garbled', 'HIGH'), f('secret', 'HIGH')] }),
      item('queue', 'none.txt', { findings: [], score: null }),
      item('queue', 'future.txt', { findings: [f('rule_from_the_future', 'SEVERE')] }),
      item('queue', 'mixed.txt', { findings: [f('rule_from_the_future', 'SEVERE'), f('secret', 'LOW')] }), // известный уровень тяжелее неизвестного
    ],
  }
  const { app } = await boot(data)
  const rows = Object.fromEntries(data.queue.map((i) => [i.name, text(rowOf(app, i.path))]))
  assert.ok(rows['heavy.txt'].includes('Executable file') && !rows['heavy.txt'].includes('Garbled'), rows['heavy.txt'])
  assert.ok(rows['mixed.txt'].includes('Secret or credential') && !rows['mixed.txt'].includes('rule_from_the_future'), rows['mixed.txt'])
  assert.ok(rows['tie.txt'].includes('Garbled text') && !rows['tie.txt'].includes('Secret'), rows['tie.txt'])
  assert.match(rows['none.txt'], /No findings/)
  assert.ok(!/null|undefined|NaN/.test(rows['none.txt']), rows['none.txt'])
  assert.ok(rows['future.txt'].includes('rule_from_the_future') && rows['future.txt'].includes('SEVERE'), rows['future.txt'])
})

test('нечего решать — так и сказано, «Select all» неактивна', async () => {
  const { app } = await boot()
  assert.ok(app.text().includes('Needs decision (0)'))
  assert.match(app.text(), /Nothing is waiting for a decision/)
  assert.equal(enabled(app.button('Select all')), false)
})

test('имя файла и находка вставляются как текст, а не как разметка', async () => {
  const evil = item('queue', '<b>x</b>.txt', { findings: [{ rule: '<img src=x onerror=1>', level: 'HIGH', where: '', quote: '<script>alert(1)</script>' }] })
  const { app } = await boot({ queue: [evil] })
  assert.ok(app.html().includes('&lt;b&gt;x&lt;/b&gt;.txt'))
  assert.ok(!app.html().includes('<b>x</b>') && !app.html().includes('<img') && !app.html().includes('<script'))
})

test('кнопки у строки: у очереди Accept и Quarantine, у карантина Return и Delete', async () => {
  const data = TWO_AND_ONE()
  const { app } = await boot(data)
  assert.deepEqual(buttonLabels(rowOf(app, data.queue[0].path)), ['Accept', 'Quarantine'])
  assert.deepEqual(buttonLabels(rowOf(app, data.quarantine[0].path)), ['Return', 'Delete'])
})

// ── выбор и действия над отмеченными ────────────────────────────
test('«Select all» отмечает все показанные строки', async () => {
  const data = TWO_AND_ONE()
  const { app } = await boot(data)
  assert.ok(app.all((n) => n.type === 'input' && n.props.type === 'checkbox').every((c) => !c.props.checked))
  await app.click(app.button('Select all'))
  const boxes = app.all((n) => n.type === 'input' && n.props.type === 'checkbox')
  assert.equal(boxes.length, 3)
  assert.ok(boxes.every((c) => c.props.checked === true))
})

test('галочка отмечает и снимает одну строку, «Clear selection» снимает все', async () => {
  const data = TWO_AND_ONE()
  const { app } = await boot(data)
  const [a, b] = data.queue
  await app.check(boxOf(app, a.path), true)
  assert.equal(boxOf(app, a.path).props.checked, true)
  assert.equal(boxOf(app, b.path).props.checked, false)
  assert.ok(enabled(app.button('Clear selection')))
  await app.check(boxOf(app, a.path), false)
  assert.equal(boxOf(app, a.path).props.checked, false)
  await app.click(app.button('Select all'))
  await app.click(app.button('Clear selection'))
  assert.ok(app.all((n) => n.type === 'input' && n.props.type === 'checkbox').every((c) => !c.props.checked))
  assert.equal(enabled(app.button('Clear selection')), false)
})

test('какие кнопки действуют при каком выборе: очередь, карантин, смесь, ничего', async () => {
  const data = TWO_AND_ONE()
  const { app } = await boot(data)
  const [qa] = data.queue
  const [kc] = data.quarantine
  const state = () => ['Accept selected', 'Quarantine selected', 'Return selected'].map((label) => enabled(app.button(label)))
  assert.deepEqual(state(), [false, false, false]) // ничего не отмечено
  await app.check(boxOf(app, qa.path), true)
  assert.deepEqual(state(), [true, true, false]) // только очередь
  await app.check(boxOf(app, qa.path), false)
  await app.check(boxOf(app, kc.path), true)
  assert.deepEqual(state(), [false, false, true]) // только карантин
  await app.check(boxOf(app, qa.path), true)
  assert.deepEqual(state(), [true, true, true]) // и то и другое
  await app.click(app.button('Clear selection'))
  await app.click(app.button('Select all'))
  assert.deepEqual(state(), [true, true, true])
})

test('пока идёт запрос, кнопки для отмеченных и у строк неактивны', async () => {
  const data = TWO_AND_ONE()
  const { app, server } = await boot(data)
  let release
  server.on['POST ' + QUEUE] = () => new Promise((resolve) => { release = () => resolve(reply(200, { results: [] })) })
  await app.click(app.button('Select all'))
  await app.click(app.button('Accept selected'))
  const pending = app.click(app.button('Yes, accept 2'))
  await flush()
  for (const label of ['Accept selected', 'Quarantine selected', 'Return selected', 'Select all', 'Run now'])
    assert.equal(enabled(app.button(label)), false, label)
  assert.equal(enabled(inRow(app, data.queue[0].path, 'Accept')), false)
  assert.equal(enabled(inRow(app, data.quarantine[0].path, 'Return')), false)
  release()
  await pending
  await app.settle()
})

test('«Accept selected» просит подтверждение с числом файлов и без «да» ничего не посылает', async () => {
  const data = TWO_AND_ONE()
  const { app, server } = await boot(data)
  await app.click(app.button('Select all'))
  await app.click(app.button('Accept selected'))
  assert.ok(app.text().includes('Accept 2 files into the archive?'), app.text()) // только строки очереди: карантин не в счёт
  assert.equal(server.posts(QUEUE).length, 0)
  await app.click(app.button('Cancel'))
  assert.ok(!app.text().includes('Accept 2 files'))
  assert.equal(server.posts(QUEUE).length, 0)
})

test('подтверждение: один файл — «1 file», дальше «files»; число следует за выбором', async () => {
  const data = TWO_AND_ONE()
  const { app } = await boot(data)
  const [a, b] = data.queue
  await app.check(boxOf(app, a.path), true)
  await app.click(app.button('Quarantine selected'))
  assert.ok(app.text().includes('Move 1 file to quarantine?'), app.text())
  assert.ok(app.button('Yes, quarantine 1'))
  await app.check(boxOf(app, b.path), true) // выбор изменился, пока вопрос на экране
  assert.ok(app.text().includes('Move 2 files to quarantine?'), app.text())
  assert.ok(app.button('Yes, quarantine 2'))
  await app.check(boxOf(app, a.path), false)
  await app.check(boxOf(app, b.path), false) // отмечать больше нечего — вопрос уходит
  assert.ok(!app.text().includes('to quarantine?'))
  assert.equal(app.button('Yes, quarantine 0'), null)
})

test('«Accept selected» после «да»: один запрос с отмеченными путями очереди, ключ действия accept', async () => {
  const data = TWO_AND_ONE()
  const { app, server } = await boot(data)
  await app.click(app.button('Select all'))
  await app.click(app.button('Accept selected'))
  await app.click(app.button('Yes, accept 2'))
  assert.equal(server.posts(QUEUE).length, 1)
  assert.deepEqual(server.posts(QUEUE)[0].body, { paths: data.queue.map((i) => i.path), action: 'accept' })
  assert.equal(server.posts(QUARANTINE).length, 0) // строка карантина не тронута
  assert.equal(app.all((n) => n.type === 'li').length, 1) // список перечитан
  assert.ok(app.text().includes('Needs decision (1)'))
  assert.equal(app.text().includes('Accept 2 files'), false) // вопрос закрыт
})

test('«Quarantine selected» действует на отмеченные строки очереди, «Return selected» — на карантин', async () => {
  const data = TWO_AND_ONE()
  const { app, server } = await boot(data)
  const [qa] = data.queue
  const [kc] = data.quarantine
  await app.check(boxOf(app, qa.path), true)
  await app.check(boxOf(app, kc.path), true)
  await app.click(app.button('Quarantine selected'))
  await app.click(app.button('Yes, quarantine 1'))
  assert.deepEqual(server.posts(QUEUE).map((c) => c.body), [{ paths: [qa.path], action: 'quarantine' }])
  await app.check(boxOf(app, kc.path), true)
  await app.click(app.button('Return selected'))
  assert.ok(app.text().includes('Return 1 file to the returns folder?'), app.text())
  await app.click(app.button('Yes, return 1'))
  assert.deepEqual(server.posts(QUARANTINE).map((c) => c.body), [{ paths: [kc.path], action: 'return' }])
})

test('вопрос об одном действии заменяет вопрос о другом', async () => {
  const data = TWO_AND_ONE()
  const { app } = await boot(data)
  await app.click(app.button('Select all'))
  await app.click(app.button('Accept selected'))
  await app.click(app.button('Return selected'))
  assert.ok(app.text().includes('Return 1 file') && !app.text().includes('Accept 2 files'))
})

test('сбой одного файла при действии над отмеченными виден у его строки, остальные приняты', async () => {
  const data = { queue: [item('queue', 'a.txt'), item('queue', 'b.txt'), item('queue', 'c.txt')] }
  const { app, server } = await boot(data)
  server.rejects[data.queue[1].path] = { code: null, args: {}, text: 'Файл изменился после проверки' }
  await app.click(app.button('Select all'))
  await app.click(app.button('Accept selected'))
  await app.click(app.button('Yes, accept 3'))
  const rows = app.all((n) => n.type === 'li')
  assert.equal(rows.length, 1)
  assert.equal(rows[0].props['data-path'], data.queue[1].path)
  assert.ok(text(rows[0]).includes('Файл изменился после проверки')) // неизвестный код — исходный text
  assert.equal(boxOf(app, data.queue[1].path).props.checked, true) // остаётся отмеченным: можно повторить
  assert.equal(app.find((n) => n.props?.role === 'alert'), null) // над списком ошибки нет
})

test('известный код отказа у строки показывается по английскому словарю', async () => {
  const data = { queue: [item('queue', 'a.txt')] }
  const { app, server } = await boot(data)
  server.rejects[data.queue[0].path] = { code: 'llm_finding', args: { model: 'm1', page: 1, why: 'odd request' }, text: 'модель m1: странная просьба' }
  await app.click(inRow(app, data.queue[0].path, 'Accept'))
  const row = text(rowOf(app, data.queue[0].path))
  assert.ok(row.includes('The model m1 reported: odd request'), row)
  assert.ok(!CYRILLIC.test(row), row)
})

// ── кнопки у строки ─────────────────────────────────────────────
test('Accept у строки очереди действует сразу, одним запросом на один путь', async () => {
  const data = TWO_AND_ONE()
  const { app, server } = await boot(data)
  await app.click(inRow(app, data.queue[1].path, 'Accept'))
  assert.deepEqual(server.posts(QUEUE).map((c) => c.body), [{ paths: [data.queue[1].path], action: 'accept' }])
  assert.equal(rowOf(app, data.queue[1].path), null)
})

test('Quarantine у строки очереди и Return у строки карантина действуют сразу', async () => {
  const data = TWO_AND_ONE()
  const { app, server } = await boot(data)
  await app.click(inRow(app, data.queue[0].path, 'Quarantine'))
  await app.click(inRow(app, data.quarantine[0].path, 'Return'))
  assert.deepEqual(server.posts(QUEUE).map((c) => c.body), [{ paths: [data.queue[0].path], action: 'quarantine' }])
  assert.deepEqual(server.posts(QUARANTINE).map((c) => c.body), [{ paths: [data.quarantine[0].path], action: 'return' }])
})

test('отказ по строке виден у этой строки и не пропадает от опроса', async () => {
  const data = TWO_AND_ONE()
  const { app, server, clock } = await boot(data)
  server.rejects[data.queue[0].path] = { code: null, args: {}, text: 'Файл занят' }
  await app.click(inRow(app, data.queue[0].path, 'Accept'))
  assert.ok(text(rowOf(app, data.queue[0].path)).includes('Файл занят'))
  assert.ok(!text(rowOf(app, data.queue[1].path)).includes('Файл занят'))
  await clock.advance(30000)
  await app.settle()
  assert.ok(text(rowOf(app, data.queue[0].path)).includes('Файл занят'))
})

test('Delete есть только у строки карантина, действует только после подтверждения и стирает один файл', async () => {
  const data = TWO_AND_ONE()
  const { app, server } = await boot(data)
  const [kc] = data.quarantine
  assert.equal(inRow(app, data.queue[0].path, 'Delete'), null)
  await app.click(app.button('Select all'))
  assert.equal(app.buttons().filter((b) => /delete/i.test(text(b))).length, 1) // одна кнопка на всю вкладку: у строки, массовой нет
  await app.click(inRow(app, kc.path, 'Delete'))
  assert.ok(app.text().includes('Delete permanently?'), app.text())
  assert.equal(server.posts(QUARANTINE).length, 0)
  await app.click(app.button('Cancel'))
  assert.ok(!app.text().includes('Delete permanently?'))
  assert.equal(server.posts(QUARANTINE).length, 0)
  await app.click(inRow(app, kc.path, 'Delete'))
  await app.click(app.button('Yes, delete forever'))
  assert.deepEqual(server.posts(QUARANTINE).map((c) => c.body), [{ path: kc.path, action: 'delete' }])
  assert.equal(rowOf(app, kc.path), null)
})

test('вопрос о стирании касается одной строки: другой Delete его перебивает', async () => {
  const data = { quarantine: [item('quarantine', 'a.exe'), item('quarantine', 'b.exe')] }
  const { app } = await boot(data)
  await app.click(inRow(app, data.quarantine[0].path, 'Delete'))
  await app.click(inRow(app, data.quarantine[1].path, 'Delete'))
  assert.equal(app.all((n) => n.type === 'button' && text(n) === 'Yes, delete forever').length, 1)
  assert.ok(find(rowOf(app, data.quarantine[1].path), (n) => n.type === 'button' && text(n) === 'Yes, delete forever'))
})

test('отказ стирания показан у строки; файл остаётся', async () => {
  const data = { quarantine: [item('quarantine', 'a.exe')] }
  const { app, server } = await boot(data)
  server.on['POST ' + QUARANTINE] = async () => reply(409, { error: { code: null, args: {}, text: 'Файла уже нет в карантине' } })
  await app.click(inRow(app, data.quarantine[0].path, 'Delete'))
  await app.click(app.button('Yes, delete forever'))
  assert.ok(text(rowOf(app, data.quarantine[0].path)).includes('Файла уже нет в карантине'))
})

test('выбор не держит строки, которых уже нет: число в вопросе считается по тем, что на экране', async () => {
  const data = { queue: [item('queue', 'a.txt'), item('queue', 'b.txt')] }
  const { app, server, clock } = await boot(data)
  await app.click(app.button('Select all'))
  server.queue.splice(0, 1) // файл ушёл другим путём
  await clock.advance(30000)
  await app.settle()
  await app.click(app.button('Accept selected'))
  assert.ok(app.text().includes('Accept 1 file into the archive?'), app.text())
  await app.click(app.button('Yes, accept 1'))
  assert.deepEqual(server.posts(QUEUE).map((c) => c.body.paths), [[data.queue[1].path]])
})

// ── ошибки ──────────────────────────────────────────────────────
test('отказ команды (409) при опросе показан текстом сообщения; неизвестный код — исходным text; вкладка продолжает работать', async () => {
  const data = TWO_AND_ONE()
  const { app, server, clock } = await boot(data)
  server.on['GET ' + INBOX] = async () => reply(409, { error: { code: 'no_such_code', args: {}, text: 'Каталог архива недоступен' } })
  await clock.advance(30000)
  await app.settle()
  const alert = app.find((n) => n.props?.role === 'alert')
  assert.equal(text(alert), 'Каталог архива недоступен')
  assert.equal(app.all((n) => n.type === 'li').length, 3) // прежние данные на экране
  assert.ok(app.button('Select all'))
  delete server.on['GET ' + INBOX]
  await clock.advance(30000)
  await app.settle()
  assert.equal(app.find((n) => n.props?.role === 'alert'), null)
})

test('сбой связи показан отдельным текстом, не текстом отказа; когда связь вернулась — исчезает', async () => {
  const { app, server, clock } = await boot(TWO_AND_ONE())
  server.offline = true
  await clock.advance(30000)
  await app.settle()
  const alert = app.find((n) => n.props?.role === 'alert')
  assert.match(text(alert), /No connection to DSH/)
  assert.ok(!CYRILLIC.test(text(alert)))
  server.offline = false
  await clock.advance(30000)
  await app.settle()
  assert.equal(app.find((n) => n.props?.role === 'alert'), null)
})

test('вход в DSH истёк (401) и прочие сбои — свои английские тексты', async () => {
  const { app, server, clock } = await boot()
  server.on['GET ' + INBOX] = async () => ({ ok: false, status: 401, json: async () => { throw new Error('не JSON') } })
  await clock.advance(30000)
  await app.settle()
  assert.match(text(app.find((n) => n.props?.role === 'alert')), /sign-in has expired/)
  server.on['GET ' + INBOX] = async () => reply(502, null)
  await clock.advance(30000)
  await app.settle()
  assert.match(text(app.find((n) => n.props?.role === 'alert')), /request failed \(code 502\)/)
})

test('обрыв связи при действии над отмеченными показан над списком, строки целы', async () => {
  const data = TWO_AND_ONE()
  const { app, server } = await boot(data)
  await app.click(app.button('Select all'))
  server.offline = true
  await app.click(app.button('Accept selected'))
  await app.click(app.button('Yes, accept 2'))
  assert.match(text(app.find((n) => n.props?.role === 'alert')), /No connection to DSH/)
  assert.equal(app.all((n) => n.type === 'li').length, 3)
})

test('отказ запуска показан над списком', async () => {
  const { app, server } = await boot()
  server.on['POST ' + RUN] = async () => reply(409, { error: { code: null, args: {}, text: 'Разбор уже идёт' } })
  await app.click(app.button('Run now'))
  assert.equal(text(app.find((n) => n.props?.role === 'alert')), 'Разбор уже идёт')
})

// ── язык ────────────────────────────────────────────────────────
test('все подписи вкладки берутся из словаря: ключей, которых в словаре нет, не запрашивается', async () => {
  const data = TWO_AND_ONE()
  const { app, server, ctx, clock } = await boot({ ...data, progress: progressOf() })
  await app.click(app.button('Select all'))
  for (const label of ['Accept selected', 'Quarantine selected', 'Return selected']) {
    await app.click(app.button(label))
    await app.click(app.button('Cancel'))
  }
  await app.click(inRow(app, data.quarantine[0].path, 'Delete'))
  await app.click(app.button('Cancel'))
  server.progress = progressOf({ total: null, current: null })
  await clock.advance(2000)
  await app.settle()
  server.progress = null
  server.status.timer = false
  server.status.last_batch = null
  await clock.advance(2000)
  await app.settle()
  server.offline = true
  await clock.advance(30000)
  await app.settle()
  assert.deepEqual([...new Set(ctx.missingKeys)], [])
})

test('на экране нет русских слов, кроме данных из архива (на этом образце их нет)', async () => {
  const data = TWO_AND_ONE()
  const states = [
    await boot({ ...data }),
    await boot({ ...data, progress: progressOf() }),
    await boot({ ...data, status: { timer: false, last_batch: null, problems: 4 } }),
  ]
  for (const { app } of states) {
    await app.click(app.button('Select all'))
    assert.ok(!CYRILLIC.test(app.text()), app.text())
    await app.click(app.button('Accept selected'))
    assert.ok(!CYRILLIC.test(app.text()), app.text())
  }
})

// ── дополнения по порче кода ────────────────────────────────────
const fillOf = (app) => app.find((n) => n.props?.className === 'ba-bar-fill')

test('полоса хода: ширина — доля готового, при нуле всего — пустая; негодный done — как ноль', async () => {
  const half = await boot({ progress: progressOf({ done: 12, total: 40 }) })
  assert.equal(fillOf(half.app).props.style.width, '30%')
  const empty = await boot({ progress: progressOf({ done: 0, total: 0 }) })
  assert.equal(fillOf(empty.app).props.style.width, '0%')
  assert.ok(empty.app.text().includes('0 of 0'))
  const odd = await boot({ progress: progressOf({ done: null, total: 40 }) })
  assert.ok(odd.app.text().includes('0 of 40'), odd.app.text())
  assert.ok(!/null|NaN|undefined/.test(odd.app.text()))
  const busy = await boot({ progress: progressOf({ done: 3, total: null }) })
  assert.equal(fillOf(busy.app).props.style, undefined) // ширину без total не показываем: полоса «занято»
})

test('итог последней пачки идёт в порядке решений из словаря, а не в порядке ключей ответа; пустая пачка названа пустой', async () => {
  const shuffled = await boot({ status: { last_batch: { duplicate: 3, review: 2, batch: '20260101-120000', accept: 11, time: '2026-10-04T11:18:40Z' } } })
  assert.ok(shuffled.app.text().includes('Last batch 20260101-120000: accepted 11, needs review 2, duplicates 3'), shuffled.app.text())
  const empty = await boot({ status: { last_batch: { batch: '20261004-150000', accept: 0, review: 0, duplicate: 0 } } })
  assert.ok(empty.app.text().includes('Last batch 20261004-150000: no files'), empty.app.text())
})

test('пока вопрос на экране, а запрос другого действия идёт, «да» тоже занято', async () => {
  const data = TWO_AND_ONE()
  const { app, server } = await boot(data)
  await app.check(boxOf(app, data.queue[0].path), true)
  await app.check(boxOf(app, data.quarantine[0].path), true)
  await app.click(app.button('Accept selected'))
  let release
  server.on['POST ' + QUARANTINE] = () => new Promise((resolve) => { release = () => resolve(reply(200, { results: [] })) })
  await app.click(inRow(app, data.quarantine[0].path, 'Return')) // в это время вопрос о принятии ещё на экране
  assert.ok(app.text().includes('Accept 1 file'))
  assert.equal(enabled(app.button('Yes, accept 1')), false)
  release()
  await app.settle()
})

test('пока вопрос о стирании на экране, а другой запрос идёт, «да» занято', async () => {
  const data = { quarantine: [item('quarantine', 'a.exe'), item('quarantine', 'b.exe')] }
  const { app, server } = await boot(data)
  await app.click(inRow(app, data.quarantine[0].path, 'Delete'))
  let release
  server.on['POST ' + QUARANTINE] = () => new Promise((resolve) => { release = () => resolve(reply(200, { results: [] })) })
  await app.click(inRow(app, data.quarantine[1].path, 'Return'))
  assert.equal(enabled(app.button('Yes, delete forever')), false)
  release()
  await app.settle()
})

test('после «да» вопрос закрыт и тогда, когда часть файлов осталась отмеченной из-за сбоя', async () => {
  const data = { queue: [item('queue', 'a.txt'), item('queue', 'b.txt')] }
  const { app, server } = await boot(data)
  server.rejects[data.queue[1].path] = { code: null, args: {}, text: 'Файл занят' }
  await app.click(app.button('Select all'))
  await app.click(app.button('Accept selected'))
  await app.click(app.button('Yes, accept 2'))
  assert.equal(boxOf(app, data.queue[1].path).props.checked, true) // сбойный остался отмеченным
  assert.ok(!app.text().includes('into the archive?')) // но вопроса на экране уже нет
  assert.equal(app.button('Yes, accept 1'), null)
})

test('отказ по файлу без пояснения: у строки стандартный английский текст', async () => {
  const data = { queue: [item('queue', 'a.txt')] }
  const { app, server } = await boot(data)
  server.on['POST ' + QUEUE] = async () => reply(200, { results: [{ path: data.queue[0].path, ok: false }] })
  await app.click(inRow(app, data.queue[0].path, 'Accept'))
  assert.ok(text(rowOf(app, data.queue[0].path)).includes('This file could not be processed.'))
})

test('списки не прочитались: вкладка не уверяет, что решать нечего, а показывает ошибку', async () => {
  const on = { ['GET ' + QUEUE]: async () => reply(500, null) }
  const { app } = await boot({ on })
  assert.ok(!app.text().includes('Nothing is waiting for a decision'), app.text())
  assert.match(app.text(), /Loading/)
  assert.match(text(app.find((n) => n.props?.role === 'alert')), /request failed \(code 500\)/)
})

test('словарь сообщений не пришёл: вкладка работает, сообщения — исходным text, названия — словами сервера, в консоль — одна запись', async () => {
  const data = TWO_AND_ONE()
  const { app, server, errors } = await boot({ ...data, chunksFail: true, progress: progressOf({ stage: 'model' }) })
  assert.equal(errors.length, 1)
  assert.match(String(errors[0][0]), /client\.messages\.js/)
  const t = app.text()
  assert.ok(t.includes('model') && !t.includes('Model check'), t) // этап сырым словом
  assert.ok(t.includes('Last batch 20260101-120000: accept 11, review 2'), t) // решения сырыми словами
  assert.ok(text(rowOf(app, data.queue[0].path)).includes('prompt_injection · HIGH'))
  server.progress = null
  server.rejects[data.queue[0].path] = { code: 'llm_finding', args: { model: 'm1', page: 1, why: 'x' }, text: 'модель m1: странная просьба' }
  await app.settle()
  await app.click(inRow(app, data.queue[0].path, 'Accept'))
  assert.ok(text(rowOf(app, data.queue[0].path)).includes('модель m1: странная просьба')) // известный код без словаря — text
})

test('заголовок вкладки берёт подпись из t, который подал DSH', async () => {
  const { h, mount, ctx } = await boot({ mountBody: false })
  const app = mount(h(slotNamed(ctx, 'sidebar.right.pane.tab.title')[0].component, { t: (key) => `«${key}»` }))
  assert.equal(app.text().trim(), '«title»')
})

test('apply с настоящими fetch и document: адреса относительные, скрытая страница молчит, снятие плагина снимает подписку', async () => {
  const { exports } = await load(stateful().React)
  const fetched = []
  const listeners = new Set()
  const doc = { hidden: true, addEventListener: (name, fn) => { assert.equal(name, 'visibilitychange'); listeners.add(fn) }, removeEventListener: (name, fn) => { listeners.delete(fn) } }
  const saved = { fetch: globalThis.fetch, document: Object.getOwnPropertyDescriptor(globalThis, 'document') }
  globalThis.fetch = async (route, init) => { fetched.push([route, init]); return reply(200, fakeArchive().view()) }
  Object.defineProperty(globalThis, 'document', { configurable: true, writable: true, value: doc })
  const ctx = fakeCtx()
  try {
    exports.apply(ctx)
    await flush()
    assert.deepEqual(fetched, []) // страница скрыта — опроса нет
    assert.equal(listeners.size, 1)
    doc.hidden = false
    listeners.forEach((fn) => fn())
    await flush()
    assert.ok(fetched.length >= 1)
    assert.equal(fetched[0][0], 'api/flyarchive.inbox') // без ведущего «/»: DSH может стоять не в корне сайта
  } finally {
    ctx.dispose() // снимает и таймер опроса, иначе процесс не завершится
    globalThis.fetch = saved.fetch
    if (saved.document) Object.defineProperty(globalThis, 'document', saved.document)
    else delete globalThis.document
  }
  assert.equal(listeners.size, 0)
})

test('ответ не того вида: над списком английский текст, а не текст исключения JavaScript', async () => {
  const on = { ['GET ' + INBOX]: async () => reply(200, null) }
  const { app } = await boot({ on })
  assert.equal(text(app.find((n) => n.props?.role === 'alert')), 'Something went wrong while reading the archive.')
})

test('списки не прочитались: в заголовке число по счётчикам из состояния', async () => {
  const on = { ['GET ' + QUEUE]: async () => reply(500, null) }
  const { app } = await boot({ on, status: { queue: 2, quarantine: 1 } })
  assert.ok(app.text().includes('Needs decision (3)'), app.text())
})

test('у заголовка вкладки и у карточки путеводителя есть значок', async () => {
  const { h, mount, ctx } = await boot({ mountBody: false })
  const title = mount(h(slotNamed(ctx, 'sidebar.right.pane.tab.title')[0].component, {}))
  assert.ok(title.find((n) => n.type === 'svg'))
  const useTabInfo = () => ({ tab: { actions: { openTab() {} } } })
  const guide = mount(h(slotNamed(ctx, 'sidebar.right.tab.guide.entry')[0].component, { kind: 'k', title: 'Archive', useTabInfo }))
  assert.ok(guide.find((n) => n.type === 'svg'))
})
