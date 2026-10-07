// История пачек на вкладке «Archive»: обращения, общее хранилище и раздел «Batches» (FR-82).
// Плагин ставится на подставной контекст DSH, вкладка рисуется малым рисовальщиком с состоянием, архив, время и часовой пояс подставные.
import assert from 'node:assert/strict'
import test from 'node:test'

import { BATCH, batchIds, batchOf, detailOf, fakeArchive, fakeCtx, fileOf, item, messageOf, slotNamed } from './kit-archive.mjs'
import { createClock, fakePage, find, flush, load, recorder, reply, stateful, text } from './kit.mjs'

const INBOX = 'api/flyarchive.inbox'
const BATCHES = 'api/flyarchive.batches'
const ONE = 'api/flyarchive.batch'
const QUEUE = 'api/flyarchive.queue'
const CYRILLIC = /[Ѐ-ӿ]/
const IDS = batchIds(70) // от новой к старой
const TOP = IDS[0]

/** Хранилище на подставных архиве, таймерах и странице. */
async function setup(initial = {}, { hidden = false } = {}) {
  const { exports } = await load()
  const server = fakeArchive(initial)
  const clock = createClock()
  const page = fakePage(hidden)
  const client = exports.parts.createClient(server.doFetch)
  const store = exports.parts.createStore({ client, timers: clock, page })
  return { exports, server, clock, page, client, store }
}

/** Плагин на подставном DSH, тело вкладки смонтировано после первого опроса. */
async function boot({ chunksFail = false, ...initial } = {}) {
  const { React, mount } = stateful()
  const loaded = await load(React, { chunksFail })
  const server = fakeArchive(initial)
  const clock = createClock()
  const page = fakePage(false)
  const ctx = fakeCtx()
  const savedError = console.error
  if (chunksFail) console.error = () => {}
  try {
    loaded.exports.parts.install(ctx, { fetch: server.doFetch, timers: clock, page })
    await clock.advance(0)
    await loaded.idle()
    await flush()
  } finally {
    console.error = savedError
  }
  const h = React.createElement
  const app = mount(h(slotNamed(ctx, 'sidebar.right.pane.tab')[0].component, {}))
  await app.settle()
  return { ...loaded, React, mount, h, server, clock, page, ctx, app }
}

const deferred = () => {
  let resolve
  const promise = new Promise((r) => { resolve = r })
  return { promise, resolve }
}
const collect = (node, match, out = []) => {
  if (node === null || typeof node !== 'object') return out
  if (Array.isArray(node)) {
    node.forEach((n) => collect(n, match, out))
    return out
  }
  if (match(node)) out.push(node)
  collect(node.props.children, match, out)
  return out
}
const section = (app) => app.find((n) => n.props?.['data-section'] === 'batches')
const batchRow = (app, id) => app.find((n) => n.type === 'li' && n.props['data-batch'] === id)
const rowsOfList = (app) => app.all((n) => n.type === 'li' && n.props['data-batch'] !== undefined)
const headOf = (app, id) => find(batchRow(app, id), (n) => n.type === 'button' && n.props['aria-expanded'] !== undefined)
const open = (app, id) => app.click(headOf(app, id))
const inBatch = (app, id, label) => find(batchRow(app, id), (n) => n.type === 'button' && text(n) === label)
const fileRows = (app, id) => collect(batchRow(app, id), (n) => n.type === 'tr' && n.props['data-file'] !== undefined)
const cellsOf = (row) => row.props.children.map(text)
const infoOf = (app, id, name) => find(batchRow(app, id), (n) => n.type === 'tr' && n.props['data-file-info'] === name)
const nameButton = (app, id, name) => find(fileRows(app, id).find((r) => r.props['data-file'] === name), (n) => n.type === 'button')
const alerts = (node) => collect(node, (n) => n.props?.role === 'alert')
const detailsFor = (...ids) => Object.fromEntries(ids.map((id) => [id, detailOf(id, [fileOf('a.txt')])]))
const enabled = (button) => button !== null && !button.props.disabled
const batches = (n) => IDS.slice(0, n).map((id) => batchOf(id))

// ── обращения к серверной половине ──────────────────────────────
test('createClient: список пачек — GET с limit, а before добавляется, только когда он есть', async () => {
  const { exports } = await load()
  const { calls, doFetch } = recorder(() => reply(200, { batches: [], more: false }))
  const client = exports.parts.createClient(doFetch)
  await client.batches(30, null)
  await client.batches(30, '20260101-120000-2')
  assert.deepEqual(calls.map(([route]) => route), ['api/flyarchive.batches?limit=30', 'api/flyarchive.batches?limit=30&before=20260101-120000-2'])
  assert.ok(calls.every(([, init]) => init === undefined || (init.method ?? 'GET') === 'GET'))
})

test('createClient: одна пачка — GET с id; значение кодируется, а не клеится в адрес', async () => {
  const { exports } = await load()
  const { calls, doFetch } = recorder(() => reply(200, { files: [] }))
  const client = exports.parts.createClient(doFetch)
  await client.batch('20260101-120000')
  await client.batch('a&b=c d')
  assert.deepEqual(calls.map(([route]) => route), ['api/flyarchive.batch?id=20260101-120000', 'api/flyarchive.batch?id=a%26b%3Dc+d'])
})

test('createClient: отказ по адресам истории приходит ошибкой с сообщением, как и по прочим', async () => {
  const { exports } = await load()
  const refusal = { code: 'no_such_batch', args: {}, text: 'Нет такой пачки' }
  const client = exports.parts.createClient(async () => reply(409, { error: refusal }))
  await assert.rejects(client.batch('20260101-120000'), (e) => e.kind === 'refused' && e.refusal.text === 'Нет такой пачки')
  await assert.rejects(client.batches(30, null), (e) => e.kind === 'refused')
})

// ── хранилище: список ───────────────────────────────────────────
test('первый опрос читает первую страницу: 30 пачек, без before; дальше без перемен её не перечитывают', async () => {
  const { server, clock, store } = await setup({ batches: batches(70) })
  assert.equal(store.getSnapshot().history.items, null) // до опроса списка нет
  store.start()
  await flush()
  const { items, more, error } = store.getSnapshot().history
  assert.deepEqual(items.map((b) => b.id), IDS.slice(0, 30))
  assert.equal(more, true)
  assert.equal(error, null)
  assert.deepEqual(server.batchCalls().map((c) => [c.limit, c.before]), [['30', null]])
  await clock.advance(5 * 30000)
  assert.equal(server.batchCalls().length, 1)
})

test('«Load more»: before — последняя из уже показанных; страницы копятся, на третьей before уже вторая страница', async () => {
  const { server, store } = await setup({ batches: batches(70) })
  store.start()
  await flush()
  await store.loadMoreBatches()
  assert.deepEqual(server.batchCalls().map((c) => c.before), [null, IDS[29]])
  assert.deepEqual(store.getSnapshot().history.items.map((b) => b.id), IDS.slice(0, 60))
  assert.equal(store.getSnapshot().history.more, true)
  await store.loadMoreBatches()
  assert.deepEqual(server.batchCalls().map((c) => c.before), [null, IDS[29], IDS[59]])
  assert.deepEqual(store.getSnapshot().history.items.map((b) => b.id), IDS)
  assert.equal(store.getSnapshot().history.more, false)
  assert.ok(server.batchCalls().every((c) => c.limit === '30'))
})

test('«Load more» при more: false запроса не шлёт; два нажатия подряд дают один запрос', async () => {
  const { server, store } = await setup({ batches: batches(70) })
  store.start()
  await flush()
  store.loadMoreBatches()
  store.loadMoreBatches()
  await flush()
  assert.equal(server.batchCalls().length, 2)
  assert.equal(store.getSnapshot().history.items.length, 60)
  const small = await setup({ batches: batches(5) })
  small.store.start()
  await flush()
  assert.equal(small.store.getSnapshot().history.more, false)
  await small.store.loadMoreBatches()
  assert.equal(small.server.batchCalls().length, 1)
})

test('пачка, которая уже есть в списке, при «Load more» второй раз не добавляется', async () => {
  const { server, store } = await setup({ batches: batches(70) })
  server.on['GET ' + BATCHES] = async (_, s) => {
    const last = s.calls.at(-1).route
    return reply(200, last.includes('before=') ? { batches: [batchOf(IDS[29]), batchOf(IDS[30])], more: false } : { batches: batches(30), more: true })
  }
  store.start()
  await flush()
  await store.loadMoreBatches()
  assert.deepEqual(store.getSnapshot().history.items.map((b) => b.id), IDS.slice(0, 31))
})

test('сменился номер последней пачки: первая страница перечитывается, загруженные ниже страницы остаются', async () => {
  const { server, clock, store } = await setup({ batches: batches(70) })
  store.start()
  await flush()
  await store.loadMoreBatches()
  const fresh = '20261005-000100'
  server.addBatch(batchOf(fresh))
  await clock.advance(30000)
  const { items, more } = store.getSnapshot().history
  assert.deepEqual(items.map((b) => b.id), [fresh, ...IDS.slice(0, 60)])
  assert.equal(more, true)
  assert.deepEqual(server.batchCalls().at(-1), { limit: '30', before: null, route: 'api/flyarchive.batches?limit=30' })
  assert.equal(server.batchCalls().length, 3)
})

test('номер последней пачки не менялся — первая страница не перечитывается, сколько бы опросов ни прошло', async () => {
  const { server, clock, store } = await setup({ batches: batches(5) })
  store.start()
  await flush()
  await clock.advance(10 * 30000)
  assert.equal(server.batchCalls().length, 1)
  assert.equal(server.count(INBOX), 11)
})

test('всё уже загружено, пришла новая пачка: «ещё есть» остаётся «нет», а не берётся из свежей страницы', async () => {
  const { server, clock, store } = await setup({ batches: batches(70) })
  store.start()
  await flush()
  await store.loadMoreBatches()
  await store.loadMoreBatches()
  assert.equal(store.getSnapshot().history.more, false)
  server.addBatch(batchOf('20261005-000100'))
  await clock.advance(30000)
  const { items, more } = store.getSnapshot().history
  assert.deepEqual(items.map((b) => b.id), ['20261005-000100', ...IDS])
  assert.equal(more, false)
})

test('пачек прибыло больше страницы: загруженное ниже не склеивается с новым через дыру, остаётся одна свежая страница', async () => {
  const { server, clock, store } = await setup({ batches: batches(70) })
  store.start()
  await flush()
  await store.loadMoreBatches()
  for (let i = 1; i <= 35; i++) server.addBatch(batchOf(`20261005-00${String(i).padStart(2, '0')}00`))
  await clock.advance(30000)
  const { items, more } = store.getSnapshot().history
  assert.deepEqual(items.map((b) => b.id), server.batches.slice(0, 30).map((b) => b.id))
  assert.equal(more, true)
})

test('перечитывание первой страницы, когда пачек немного: список целиком заменяется свежим', async () => {
  const { server, clock, store } = await setup({ batches: batches(3) })
  store.start()
  await flush()
  server.addBatch(batchOf('20261005-000100'))
  await clock.advance(30000)
  assert.deepEqual(store.getSnapshot().history.items.map((b) => b.id), ['20261005-000100', ...IDS.slice(0, 3)])
  assert.equal(store.getSnapshot().history.more, false)
})

test('пока страница скрыта, историю не читают; показали — читают', async () => {
  const { server, clock, page, store } = await setup({ batches: batches(3) }, { hidden: true })
  store.start()
  await flush()
  await clock.advance(120000)
  assert.equal(server.batchCalls().length, 0)
  page.set(false)
  await flush()
  assert.equal(server.batchCalls().length, 1)
})

// ── хранилище: подробности ──────────────────────────────────────
test('раскрытие подгружает подробности один раз; закрыли и раскрыли снова — берутся из памяти', async () => {
  const { server, clock, store } = await setup({ batches: batches(3), details: detailsFor(IDS[0], IDS[1]) })
  store.start()
  await flush()
  await store.toggleBatch(IDS[0])
  assert.deepEqual(server.detailCalls(), [IDS[0]])
  assert.deepEqual(store.getSnapshot().history.open, [IDS[0]])
  assert.equal(store.getSnapshot().history.details[IDS[0]].data.id, IDS[0])
  await store.toggleBatch(IDS[0])
  assert.deepEqual(store.getSnapshot().history.open, [])
  await store.toggleBatch(IDS[0])
  await clock.advance(3 * 30000)
  assert.deepEqual(server.detailCalls(), [IDS[0]])
  await store.toggleBatch(IDS[1])
  assert.deepEqual(server.detailCalls(), [IDS[0], IDS[1]]) // другая пачка — свой запрос
  assert.deepEqual(store.getSnapshot().history.open, [IDS[0], IDS[1]])
})

test('быстрое второе раскрытие, пока первый запрос не вернулся, второго запроса не порождает', async () => {
  const gate = deferred()
  const { server, store } = await setup({ batches: batches(2), details: detailsFor(IDS[0]) })
  server.on['GET ' + ONE] = async (_, s) => { await gate.promise; return reply(200, s.details[IDS[0]]) }
  store.start()
  await flush()
  store.toggleBatch(IDS[0])
  store.toggleBatch(IDS[0]) // закрыли
  store.toggleBatch(IDS[0]) // раскрыли снова, ответа ещё нет
  await flush()
  assert.equal(server.detailCalls().length, 1)
  gate.resolve()
  await flush()
  assert.equal(store.getSnapshot().history.details[IDS[0]].loading, false)
  assert.ok(store.getSnapshot().history.details[IDS[0]].data)
})

test('счётчики очереди или карантина изменились: подробности раскрытых пачек перечитываются', async () => {
  const { server, clock, store } = await setup({ batches: batches(2), details: detailsFor(IDS[0], IDS[1]) })
  store.start()
  await flush()
  await store.toggleBatch(IDS[0])
  await clock.advance(3 * 30000)
  assert.equal(server.detailCalls().length, 1, 'пока счётчики прежние, подробности не перечитываются')
  server.queue.push(item('queue', 'x.txt'))
  await clock.advance(30000)
  assert.deepEqual(server.detailCalls(), [IDS[0], IDS[0]])
  server.quarantine.push(item('quarantine', 'y.exe'))
  await clock.advance(30000)
  assert.deepEqual(server.detailCalls(), [IDS[0], IDS[0], IDS[0]])
  await clock.advance(3 * 30000)
  assert.equal(server.detailCalls().length, 3)
})

test('счётчики изменились: закрытая пачка с подробностями в памяти не перечитывается сама, но при раскрытии спросит заново', async () => {
  const { server, clock, store } = await setup({ batches: batches(2), details: detailsFor(IDS[0], IDS[1]) })
  store.start()
  await flush()
  await store.toggleBatch(IDS[0])
  await store.toggleBatch(IDS[1])
  await store.toggleBatch(IDS[1]) // закрыта
  assert.deepEqual(server.detailCalls(), [IDS[0], IDS[1]])
  server.queue.push(item('queue', 'x.txt'))
  await clock.advance(30000)
  assert.deepEqual(server.detailCalls(), [IDS[0], IDS[1], IDS[0]]) // открытая перечитана, закрытая нет
  assert.deepEqual(Object.keys(store.getSnapshot().history.details), [IDS[0]]) // устаревшее в памяти не лежит
  await store.toggleBatch(IDS[1])
  assert.deepEqual(server.detailCalls(), [IDS[0], IDS[1], IDS[0], IDS[1]])
})

test('решение на вкладке перечитывает подробности открытых пачек, даже если счётчики остались прежними', async () => {
  const data = { queue: [item('queue', 'a.txt')], batches: batches(1), details: detailsFor(IDS[0]) }
  const { server, store } = await setup(data)
  server.on['POST ' + QUEUE] = async () => reply(200, { results: [{ path: data.queue[0].path, ok: true }] }) // файл «ушёл», а в очереди другой
  store.start()
  await flush()
  await store.toggleBatch(IDS[0])
  await store.decide('queue', 'accept', [data.queue[0].path])
  await flush()
  assert.deepEqual(server.detailCalls(), [IDS[0], IDS[0]])
})

test('ответ на устаревший запрос подробностей не затирает свежий', async () => {
  const gates = []
  const { server, clock, store } = await setup({ batches: batches(1), details: detailsFor(IDS[0]) })
  server.on['GET ' + ONE] = () => new Promise((resolve) => gates.push((files) => resolve(reply(200, detailOf(IDS[0], files)))))
  store.start()
  await flush()
  store.toggleBatch(IDS[0])
  await flush()
  server.queue.push(item('queue', 'x.txt'))
  await clock.advance(30000)
  assert.equal(gates.length, 2)
  gates[1]([fileOf('new.txt')]) // свежий ответ приходит первым
  await flush()
  gates[0]([fileOf('old.txt')]) // а устаревший — потом
  await flush()
  assert.deepEqual(store.getSnapshot().history.details[IDS[0]].data.files.map((f) => f.name), ['new.txt'])
})

test('устаревший сбой подробностей не затирает свежие данные', async () => {
  const gates = []
  const { server, clock, store } = await setup({ batches: batches(1), details: detailsFor(IDS[0]) })
  server.on['GET ' + ONE] = () => new Promise((resolve, reject) => gates.push({ resolve: (files) => resolve(reply(200, detailOf(IDS[0], files))), reject }))
  store.start()
  await flush()
  store.toggleBatch(IDS[0])
  await flush()
  server.queue.push(item('queue', 'x.txt'))
  await clock.advance(30000)
  assert.equal(gates.length, 2)
  gates[1].resolve([fileOf('new.txt')])
  await flush()
  gates[0].reject(new TypeError('Failed to fetch')) // первый запрос оборвался уже после свежего ответа
  await flush()
  const entry = store.getSnapshot().history.details[IDS[0]]
  assert.deepEqual(entry.data.files.map((f) => f.name), ['new.txt'])
  assert.equal(entry.error, null)
})

// ── хранилище: ошибки истории не задевают остальное ─────────────
test('отказ списка пачек виден в истории, а над вкладкой ошибки нет; состояние и списки решений читаются', async () => {
  const { server, clock, store } = await setup({ queue: [item('queue', 'a.txt')], batches: batches(3) })
  const refusal = { code: 'no_such_code', args: {}, text: 'Квитанции недоступны' }
  server.on['GET ' + BATCHES] = async () => reply(409, { error: refusal })
  store.start()
  await flush()
  const snap = store.getSnapshot()
  assert.equal(snap.error, null)
  assert.equal(snap.status.waiting, 3)
  assert.equal(snap.queue.length, 1)
  assert.equal(snap.history.items, null)
  assert.equal(snap.history.error.kind, 'refused')
  assert.deepEqual(snap.history.error.refusal, refusal)
  assert.deepEqual(clock.waits(), [30000]) // опрос продолжается
})

test('сбой связи только на адресе истории: kind offline в истории, остальное работает', async () => {
  const { server, store } = await setup({ batches: batches(3) })
  server.on['GET ' + BATCHES] = async () => { throw new TypeError('Failed to fetch') }
  store.start()
  await flush()
  assert.equal(store.getSnapshot().history.error.kind, 'offline')
  assert.equal(store.getSnapshot().error, null)
  assert.notEqual(store.getSnapshot().status, null)
})

test('первую страницу, которая не прочиталась, перечитывают следующим опросом; удалось — ошибка уходит', async () => {
  const { server, clock, store } = await setup({ batches: batches(3) })
  server.on['GET ' + BATCHES] = async () => reply(500, null)
  store.start()
  await flush()
  assert.equal(store.getSnapshot().history.error.status, 500)
  assert.equal(server.batchCalls().length, 1)
  delete server.on['GET ' + BATCHES]
  await clock.advance(30000)
  assert.equal(server.batchCalls().length, 2)
  assert.equal(store.getSnapshot().history.error, null)
  assert.deepEqual(store.getSnapshot().history.items.map((b) => b.id), IDS.slice(0, 3))
})

test('перечитывание не удалось: прежний список остаётся с ошибкой, следующий опрос пробует снова', async () => {
  const { server, clock, store } = await setup({ batches: batches(3) })
  store.start()
  await flush()
  server.addBatch(batchOf('20261005-000100'))
  server.on['GET ' + BATCHES] = async () => reply(500, null)
  await clock.advance(30000)
  assert.deepEqual(store.getSnapshot().history.items.map((b) => b.id), IDS.slice(0, 3))
  assert.equal(store.getSnapshot().history.error.status, 500)
  delete server.on['GET ' + BATCHES]
  await clock.advance(30000)
  assert.deepEqual(store.getSnapshot().history.items.map((b) => b.id), ['20261005-000100', ...IDS.slice(0, 3)])
  assert.equal(store.getSnapshot().history.error, null)
})

test('«Load more» не удался: ошибка в истории, прежние пачки на месте, повтор возможен', async () => {
  const { server, store } = await setup({ batches: batches(70) })
  store.start()
  await flush()
  server.on['GET ' + BATCHES] = async () => reply(500, null)
  await store.loadMoreBatches()
  assert.equal(store.getSnapshot().history.items.length, 30)
  assert.equal(store.getSnapshot().history.error.status, 500)
  assert.equal(store.getSnapshot().history.loading, false)
  delete server.on['GET ' + BATCHES]
  await store.loadMoreBatches()
  assert.equal(store.getSnapshot().history.items.length, 60)
  assert.equal(store.getSnapshot().history.error, null)
})

test('отказ подробностей виден у своей пачки, список цел; следующий опрос и повторное раскрытие пробуют снова', async () => {
  const { server, clock, store } = await setup({ batches: batches(2), details: detailsFor(IDS[0], IDS[1]) })
  server.on['GET ' + ONE] = async () => reply(409, { error: { code: null, args: {}, text: 'Квитанция повреждена' } })
  store.start()
  await flush()
  await store.toggleBatch(IDS[0])
  const entry = store.getSnapshot().history.details[IDS[0]]
  assert.equal(entry.data, null)
  assert.equal(entry.error.refusal.text, 'Квитанция повреждена')
  assert.equal(store.getSnapshot().history.error, null)
  assert.equal(store.getSnapshot().error, null)
  delete server.on['GET ' + ONE]
  await clock.advance(30000) // счётчики прежние, но открытая пачка без данных — пробуем снова
  assert.ok(store.getSnapshot().history.details[IDS[0]].data)
  assert.equal(store.getSnapshot().history.details[IDS[0]].error, null)
  // закрыли и раскрыли: неудавшееся тоже спрашивается заново
  server.on['GET ' + ONE] = async () => reply(500, null)
  await store.toggleBatch(IDS[1])
  assert.equal(store.getSnapshot().history.details[IDS[1]].error.status, 500)
  await store.toggleBatch(IDS[1])
  delete server.on['GET ' + ONE]
  await store.toggleBatch(IDS[1])
  assert.ok(store.getSnapshot().history.details[IDS[1]].data)
})

test('сбой перечитывания подробностей оставляет прежние данные на экране вместе с ошибкой', async () => {
  const { server, clock, store } = await setup({ batches: batches(1), details: detailsFor(IDS[0]) })
  store.start()
  await flush()
  await store.toggleBatch(IDS[0])
  server.on['GET ' + ONE] = async () => reply(500, null)
  server.queue.push(item('queue', 'x.txt'))
  await clock.advance(30000)
  const entry = store.getSnapshot().history.details[IDS[0]]
  assert.deepEqual(entry.data.files.map((f) => f.name), ['a.txt'])
  assert.equal(entry.error.status, 500)
})

test('ответы не того вида — ошибка в истории, а не исключение', async () => {
  const { server, store } = await setup({ batches: batches(2), details: detailsFor(IDS[0]) })
  server.on['GET ' + BATCHES] = async () => reply(200, { batches: 'много', more: false })
  server.on['GET ' + ONE] = async () => reply(200, { id: IDS[0], files: 'нет' })
  store.start()
  await flush()
  assert.equal(store.getSnapshot().history.items, null)
  assert.ok(store.getSnapshot().history.error)
  assert.equal(store.getSnapshot().error, null)
  await store.toggleBatch(IDS[0])
  assert.equal(store.getSnapshot().history.details[IDS[0]].data, null)
  assert.ok(store.getSnapshot().history.details[IDS[0]].error)
})

test('снимок истории неизменяем: после обновления и раскрытия другой пачки прежний снимок остаётся прежним', async () => {
  const { server, clock, store } = await setup({ batches: batches(3), details: detailsFor(IDS[0], IDS[1]) })
  store.start()
  await flush()
  await store.toggleBatch(IDS[0])
  const before = store.getSnapshot()
  const frozen = JSON.stringify(before.history)
  server.addBatch(batchOf('20261005-000100'))
  server.queue.push(item('queue', 'x.txt'))
  await clock.advance(30000)
  await store.toggleBatch(IDS[1])
  await store.toggleBatch(IDS[0])
  assert.notEqual(store.getSnapshot(), before)
  assert.equal(JSON.stringify(before.history), frozen)
  assert.deepEqual(before.history.open, [IDS[0]])
})

test('первая страница читается долго: опросы за это время не плодят запросов', async () => {
  const gate = deferred()
  const { server, clock, store } = await setup({ batches: batches(3) })
  server.on['GET ' + BATCHES] = async (_, s) => { await gate.promise; return reply(200, { batches: s.batches, more: false }) }
  store.start()
  await flush()
  await clock.advance(5 * 30000)
  assert.equal(server.batchCalls().length, 1)
  assert.equal(store.getSnapshot().history.loading, true)
  gate.resolve()
  await flush()
  assert.equal(store.getSnapshot().history.items.length, 3)
  assert.equal(store.getSnapshot().history.loading, false)
  assert.equal(server.batchCalls().length, 1, 'и после ответа очередь из повторных заказов не разбирается')
})

// ── раздел на экране ────────────────────────────────────────────
test('раздел «Batches» стоит под «Needs decision»; пачек нет — «No batches yet», кнопки «Load more» нет', async () => {
  const { app } = await boot()
  const html = app.html()
  assert.ok(html.indexOf('Needs decision') > 0 && html.indexOf('Needs decision') < html.indexOf('data-section="batches"'))
  const box = section(app)
  assert.equal(text(find(box, (n) => n.type === 'h3')), 'Batches')
  assert.ok(text(box).includes('No batches yet'), text(box))
  assert.equal(find(box, (n) => n.type === 'button'), null)
  assert.equal(rowsOfList(app).length, 0)
})

test('до первого ответа в разделе «Loading…», а не «No batches yet»', async () => {
  const gate = deferred()
  const { React, mount } = stateful()
  const loaded = await load(React)
  const server = fakeArchive()
  server.on['GET ' + BATCHES] = async () => { await gate.promise; return reply(200, { batches: [], more: false }) }
  const clock = createClock()
  const ctx = fakeCtx()
  loaded.exports.parts.install(ctx, { fetch: server.doFetch, timers: clock, page: fakePage(false) })
  await clock.advance(0)
  const app = mount(React.createElement(slotNamed(ctx, 'sidebar.right.pane.tab')[0].component, {}))
  await app.settle()
  assert.ok(text(section(app)).includes('Loading'), text(section(app)))
  assert.ok(!text(section(app)).includes('No batches yet'))
  gate.resolve()
  await app.settle()
  assert.ok(text(section(app)).includes('No batches yet'))
})

test('список от новой к старой в порядке сервера; в строке: время, файлы, итог по решениям, замечания, длительность', async () => {
  const data = [
    batchOf(IDS[0], { time: '2026-10-04T18:11:56Z', files: 14, counts: { accept: 12, review: 2 }, problems: 3, seconds: 65 }),
    batchOf(IDS[1], { time: '2026-10-03T07:05:00Z', files: 1, counts: { duplicate: 1 }, problems: 1, seconds: 4 }),
    batchOf(IDS[2], { time: '2026-10-02T10:00:00Z', files: 0, counts: {}, problems: 0, seconds: 0 }),
  ]
  const { app } = await boot({ batches: data })
  assert.deepEqual(rowsOfList(app).map((r) => r.props['data-batch']), IDS.slice(0, 3))
  const [a, b, c] = rowsOfList(app).map(text)
  for (const part of ['04.10.2026 18:11', '14 files', 'accepted 12 · needs review 2', '3 problems', 'Took 1 min 5 s']) assert.ok(a.includes(part), `${part}: ${a}`)
  for (const part of ['03.10.2026 07:05', '1 file', 'duplicates 1', '1 problem', 'Took 4 s']) assert.ok(b.includes(part), `${part}: ${b}`)
  assert.ok(!b.includes('1 files') && !b.includes('1 problems'), b)
  for (const part of ['02.10.2026 10:00', '0 files', '0 problems', 'Took 0 s']) assert.ok(c.includes(part), `${part}: ${c}`)
})

test('итог по решениям: порядок из словаря, нулевые не показаны, незнакомое решение названо как есть и не прячется', async () => {
  const data = [
    batchOf(IDS[0], { counts: { review: 2, weird: 3, accept: 12, failed: 0, duplicate: 4 } }),
    batchOf(IDS[1], { counts: { accept: 1 } }),
  ]
  const { app } = await boot({ batches: data })
  const [a, b] = rowsOfList(app).map(text)
  assert.ok(a.includes('accepted 12 · needs review 2 · duplicates 4 · weird 3'), a)
  assert.ok(!a.includes('failed'), a)
  assert.ok(b.includes('accepted 1') && !b.includes(' · '), b)
})

test('время пачки показывается по местному времени браузера: Токио, Лос-Анджелес и обратно', async () => {
  const data = [batchOf(IDS[0], { time: '2026-10-04T18:11:56Z' })]
  try {
    process.env.TZ = 'Asia/Tokyo'
    const tokyo = await boot({ batches: data })
    assert.ok(text(rowsOfList(tokyo.app)[0]).includes('05.10.2026 03:11'), text(rowsOfList(tokyo.app)[0]))
    process.env.TZ = 'America/Los_Angeles'
    const la = await boot({ batches: data })
    assert.ok(text(rowsOfList(la.app)[0]).includes('04.10.2026 11:11'), text(rowsOfList(la.app)[0]))
  } finally {
    process.env.TZ = 'UTC'
  }
  const utc = await boot({ batches: data })
  assert.ok(text(rowsOfList(utc.app)[0]).includes('04.10.2026 18:11'))
})

test('длительность словами: секунды, минуты, часы; нет значения — нет и подписи', async () => {
  const seconds = [5, 59, 60, 65, 120, 3599, 3600, 3725, 7200, null, 'долго', -3]
  const { app } = await boot({ batches: seconds.map((s, i) => batchOf(IDS[i], { seconds: s })) })
  const rows = rowsOfList(app).map(text)
  const has = (i, s) => assert.ok(rows[i].includes(s), `${seconds[i]}: ${rows[i]}`)
  has(0, 'Took 5 s')
  has(1, 'Took 59 s')
  has(2, 'Took 1 min')
  assert.ok(!rows[2].includes('1 min 0 s'), rows[2])
  has(3, 'Took 1 min 5 s')
  has(4, 'Took 2 min')
  has(5, 'Took 59 min 59 s')
  has(6, 'Took 1 h')
  assert.ok(!rows[6].includes('1 h 0 min'), rows[6])
  has(7, 'Took 1 h 2 min')
  has(8, 'Took 2 h')
  for (const i of [9, 10, 11]) assert.ok(!rows[i].includes('Took') && !/null|NaN|undefined/.test(rows[i]), `${seconds[i]}: ${rows[i]}`)
})

test('сводка без времени, без счётчиков и без замечаний не рисует «null», «NaN» и «undefined»', async () => {
  const data = [
    { id: IDS[0], time: null, seconds: null, counts: {}, problems: 0, files: 0 },
    { id: IDS[1], time: 'вчера', seconds: null, counts: null, files: 'много' },
    { id: IDS[2] },
  ]
  const { app } = await boot({ batches: data })
  assert.equal(rowsOfList(app).length, 3)
  for (const row of rowsOfList(app)) assert.ok(!/null|NaN|undefined|Invalid/.test(text(row)), text(row))
})

test('«Load more» есть, пока more: true; нажатие добавляет следующие 30 после показанных, в конце кнопка пропадает', async () => {
  const { app, server } = await boot({ batches: batches(70) })
  assert.equal(rowsOfList(app).length, 30)
  assert.ok(app.button('Load more'))
  await app.click(app.button('Load more'))
  assert.deepEqual(rowsOfList(app).map((r) => r.props['data-batch']), IDS.slice(0, 60))
  assert.deepEqual(server.batchCalls().map((c) => c.before), [null, IDS[29]])
  await app.click(app.button('Load more'))
  assert.equal(rowsOfList(app).length, 70)
  assert.deepEqual(server.batchCalls().map((c) => c.before), [null, IDS[29], IDS[59]])
  assert.equal(app.button('Load more'), null)
})

test('пока следующая страница грузится, «Load more» неактивна', async () => {
  const gate = deferred()
  const { app, server } = await boot({ batches: batches(70) })
  server.on['GET ' + BATCHES] = async (_, s) => { await gate.promise; return reply(200, { batches: s.batches.slice(30, 60), more: true }) }
  const pressed = app.click(app.button('Load more'))
  await flush()
  assert.equal(enabled(app.button('Load more')), false)
  gate.resolve()
  await pressed
  await app.settle()
  assert.equal(enabled(app.button('Load more')), true)
  assert.equal(rowsOfList(app).length, 60)
})

test('пока пачка раскрывается, показано «Loading…»; потом — подробности; повторное раскрытие запроса не шлёт', async () => {
  const gate = deferred()
  const { app, server } = await boot({ batches: batches(3), details: detailsFor(IDS[0]) })
  server.on['GET ' + ONE] = async (_, s) => { await gate.promise; return reply(200, s.details[IDS[0]]) }
  assert.equal(headOf(app, IDS[0]).props['aria-expanded'], false)
  const pressed = open(app, IDS[0])
  await flush()
  assert.ok(text(batchRow(app, IDS[0])).includes('Loading'), text(batchRow(app, IDS[0])))
  assert.equal(headOf(app, IDS[0]).props['aria-expanded'], true)
  gate.resolve()
  await pressed
  await app.settle()
  assert.ok(!text(batchRow(app, IDS[0])).includes('Loading'))
  assert.equal(fileRows(app, IDS[0]).length, 1)
  await open(app, IDS[0]) // закрыли
  assert.equal(fileRows(app, IDS[0]).length, 0)
  assert.equal(headOf(app, IDS[0]).props['aria-expanded'], false)
  await open(app, IDS[0]) // раскрыли снова
  assert.equal(fileRows(app, IDS[0]).length, 1)
  assert.deepEqual(server.detailCalls(), [IDS[0]])
})

// образец пачки с разными решениями и местами
const MIXED = [
  fileOf('a.txt'),
  fileOf('b.exe', { decision: 'quarantine', score: 50, location: 'quarantine', decided: 'return', decided_at: '2026-10-04T19:30:00Z',
    findings: [{ rule: 'executable', level: 'CRITICAL', where: 'x', quote: 'MZ header' }, { rule: 'garbled', level: 'LOW', where: 'y', quote: 'z' }] }),
  fileOf('c.txt', { decision: 'review', score: 25, location: 'queue', findings: [{ rule: 'prompt_injection', level: 'HIGH', where: 'line 2', quote: 'ignore all previous instructions' }] }),
  fileOf('d.txt', { decision: 'accept', location: 'missing' }),
  fileOf('e.txt', { decision: 'duplicate', score: null, location: null, path: null }),
  fileOf('f.txt', { decision: 'failed', location: 'returned', returned: `${BATCH}/f.txt`, path: null }),
  fileOf('g.txt', { decision: 'accept', location: 'deleted', decided: 'delete' }),
  fileOf('h.txt', { decision: 'accept', location: 'corpus', decided: 'accept', decided_at: '2026-10-04T19:00:00Z' }),
  fileOf('i.txt', { decision: 'review', location: 'quarantine', decided: 'quarantine' }),
]
const mixedBoot = (extra = {}) => boot({
  batches: [batchOf(IDS[0]), batchOf(IDS[1])],
  details: { [IDS[0]]: detailOf(IDS[0], MIXED, extra), ...detailsFor(IDS[1]) },
})

test('таблица файлов: имя, решение приёмки, решение владельца, где файл сейчас, баллы, главное правило', async () => {
  const { app } = await mixedBoot()
  await open(app, IDS[0])
  const cols = collect(batchRow(app, IDS[0]), (n) => n.type === 'th').map(text)
  assert.deepEqual(cols, ['File', 'Decision', 'Owner decision', 'Location', 'Score', 'Main rule'])
  const rows = Object.fromEntries(fileRows(app, IDS[0]).map((r) => [r.props['data-file'], cellsOf(r)]))
  assert.deepEqual(Object.keys(rows), MIXED.map((f) => f.name)) // порядок сервера
  assert.deepEqual(rows['a.txt'], ['a.txt', 'accepted', '—', 'In the archive', '5', '—'])
  assert.deepEqual(rows['b.exe'], ['b.exe', 'quarantined', 'returned', 'In quarantine', '50', 'Executable file · Critical'])
  assert.deepEqual(rows['c.txt'], ['c.txt', 'needs review', '—', 'Awaiting review', '25', 'Prompt injection · High'])
  assert.deepEqual(rows['d.txt'], ['d.txt', 'accepted', '—', 'Missing', '5', '—'])
  assert.deepEqual(rows['e.txt'], ['e.txt', 'duplicates', '—', 'Not stored', '—', '—'])
  assert.deepEqual(rows['f.txt'], ['f.txt', 'failed', '—', 'In the returns folder', '5', '—'])
  assert.deepEqual(rows['g.txt'], ['g.txt', 'accepted', 'deleted', 'Deleted', '5', '—'])
  assert.deepEqual(rows['h.txt'], ['h.txt', 'accepted', 'accepted', 'In the archive', '5', '—'])
  assert.deepEqual(rows['i.txt'], ['i.txt', 'needs review', 'quarantined', 'In quarantine', '5', '—'])
})

test('решение владельца приводится к подписи из словаря для всех четырёх; место — для всех шести', async () => {
  const { require } = await load()
  const m = await require.async('./client.messages.js')
  assert.deepEqual(['accept', 'quarantine', 'return', 'delete'].map(m.ownerDecisionName), ['accepted', 'quarantined', 'returned', 'deleted'])
  assert.deepEqual(['corpus', 'queue', 'quarantine', 'returned', 'deleted', 'missing'].map(m.locationName), [
    'In the archive', 'Awaiting review', 'In quarantine', 'In the returns folder', 'Deleted', 'Missing'])
  assert.equal(m.locationName('elsewhere'), 'elsewhere')
  assert.equal(m.ownerDecisionName('maybe'), 'maybe')
  for (const table of [m.LOCATIONS, m.OWNER_DECISIONS]) {
    assert.ok(Object.keys(table).length >= 4)
    for (const value of Object.values(table)) assert.ok(typeof value === 'string' && value !== '' && !CYRILLIC.test(value), value)
  }
})

test('замечания прохода: известный код — по английскому словарю, неизвестный — исходным text; без замечаний блока нет', async () => {
  const problems = [
    { code: 'llm_finding', args: { model: 'm1', page: 1, why: 'odd request' }, text: 'модель m1: странная просьба' },
    { code: 'code_from_the_future', args: { a: 1 }, text: 'Не разложен: a.txt' },
    { code: null, args: {}, text: 'Старое замечание без кода' },
  ]
  const { app } = await mixedBoot({ problems })
  await open(app, IDS[0])
  const box = text(batchRow(app, IDS[0]))
  assert.ok(box.includes('The model m1 reported: odd request'), box)
  assert.ok(!box.includes('модель m1'), box)
  assert.ok(box.includes('Не разложен: a.txt'), box) // неизвестный код — текстом сервера
  assert.ok(box.includes('Старое замечание без кода'), box)
  assert.ok(box.includes('Problems in this run'), box)
  await open(app, IDS[1])
  assert.ok(!text(batchRow(app, IDS[1])).includes('Problems in this run'), text(batchRow(app, IDS[1]))) // у второй пачки замечаний нет
})

test('замечание с неизвестным кодом показывается текстом даже если у кода есть похожее имя из прототипа', async () => {
  const problems = [{ code: 'constructor', args: {}, text: 'Текст про constructor' }, { code: '__proto__', args: {}, text: 'Текст про proto' }]
  const { app } = await mixedBoot({ problems })
  await open(app, IDS[0])
  const box = text(batchRow(app, IDS[0]))
  assert.ok(box.includes('Текст про constructor') && box.includes('Текст про proto'), box)
})

test('отбор по решению: «All» и по кнопке на каждое встретившееся решение с числом файлов; показаны только выбранные', async () => {
  const { app } = await mixedBoot()
  await open(app, IDS[0])
  const filters = collect(batchRow(app, IDS[0]), (n) => n.type === 'button' && n.props['aria-pressed'] !== undefined)
  assert.deepEqual(filters.map(text), ['All (9)', 'accepted (4)', 'needs review (2)', 'quarantined (1)', 'duplicates (1)', 'failed (1)'])
  assert.equal(filters[0].props['aria-pressed'], true)
  assert.equal(fileRows(app, IDS[0]).length, 9)
  await app.click(inBatch(app, IDS[0], 'needs review (2)'))
  assert.deepEqual(fileRows(app, IDS[0]).map((r) => r.props['data-file']), ['c.txt', 'i.txt'])
  assert.equal(inBatch(app, IDS[0], 'needs review (2)').props['aria-pressed'], true)
  assert.equal(inBatch(app, IDS[0], 'All (9)').props['aria-pressed'], false)
  await app.click(inBatch(app, IDS[0], 'accepted (4)'))
  assert.deepEqual(fileRows(app, IDS[0]).map((r) => r.props['data-file']), ['a.txt', 'd.txt', 'g.txt', 'h.txt'])
  await app.click(inBatch(app, IDS[0], 'failed (1)'))
  assert.deepEqual(fileRows(app, IDS[0]).map((r) => r.props['data-file']), ['f.txt'])
  await app.click(inBatch(app, IDS[0], 'All (9)'))
  assert.equal(fileRows(app, IDS[0]).length, 9)
})

test('отбор: решений, которых в пачке не было, кнопок нет; решение вне словаря получает кнопку со своим словом', async () => {
  const files = [fileOf('a.txt'), fileOf('b.txt', { decision: 'weird' }), fileOf('c.txt', { decision: 'weird' })]
  const { app } = await boot({ batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], files) } })
  await open(app, IDS[0])
  const filters = collect(batchRow(app, IDS[0]), (n) => n.type === 'button' && n.props['aria-pressed'] !== undefined).map(text)
  assert.deepEqual(filters, ['All (3)', 'accepted (1)', 'weird (2)'])
  await app.click(inBatch(app, IDS[0], 'weird (2)'))
  assert.deepEqual(fileRows(app, IDS[0]).map((r) => r.props['data-file']), ['b.txt', 'c.txt'])
})

test('отбор живёт у своей пачки: у другой раскрытой пачки он не меняется', async () => {
  const { app } = await boot({
    batches: [batchOf(IDS[0]), batchOf(IDS[1])],
    details: { [IDS[0]]: detailOf(IDS[0], MIXED), [IDS[1]]: detailOf(IDS[1], MIXED.map((f) => ({ ...f }))) },
  })
  await open(app, IDS[0])
  await open(app, IDS[1])
  await app.click(inBatch(app, IDS[0], 'failed (1)'))
  assert.equal(fileRows(app, IDS[0]).length, 1)
  assert.equal(fileRows(app, IDS[1]).length, 9)
})

test('в пачке без файлов отбора нет, сказано, что файлов нет', async () => {
  const { app } = await boot({ batches: [batchOf(IDS[0], { files: 0, counts: {} })], details: { [IDS[0]]: detailOf(IDS[0], []) } })
  await open(app, IDS[0])
  assert.ok(text(batchRow(app, IDS[0])).includes('No files in this batch'), text(batchRow(app, IDS[0])))
  assert.equal(collect(batchRow(app, IDS[0]), (n) => n.props?.['aria-pressed'] !== undefined).length, 0)
})

// ── длинные списки файлов ───────────────────────────────────────
const many = (n, make = () => ({})) => Array.from({ length: n }, (_, i) => fileOf(`file-${String(i).padStart(4, '0')}.txt`, make(i)))

test('в таблице первые 200 файлов, ниже строка «… and N more» с кнопкой «Show more», которая добавляет по 200', async () => {
  const files = many(450)
  const { app } = await boot({ batches: [batchOf(IDS[0], { files: 450 })], details: { [IDS[0]]: detailOf(IDS[0], files) } })
  await open(app, IDS[0])
  assert.deepEqual(fileRows(app, IDS[0]).map((r) => r.props['data-file']), files.slice(0, 200).map((f) => f.name))
  assert.ok(text(batchRow(app, IDS[0])).includes('… and 250 more'), 'строка об остатке')
  await app.click(inBatch(app, IDS[0], 'Show more'))
  assert.deepEqual(fileRows(app, IDS[0]).map((r) => r.props['data-file']), files.slice(0, 400).map((f) => f.name))
  assert.ok(text(batchRow(app, IDS[0])).includes('… and 50 more'))
  await app.click(inBatch(app, IDS[0], 'Show more'))
  assert.equal(fileRows(app, IDS[0]).length, 450)
  assert.ok(!text(batchRow(app, IDS[0])).includes('more'), 'остатка нет — строки и кнопки тоже')
  assert.equal(inBatch(app, IDS[0], 'Show more'), null)
})

test('ровно 200 файлов — строки об остатке нет; 201 — «… and 1 more»', async () => {
  const exact = await boot({ batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], many(200)) } })
  await open(exact.app, IDS[0])
  assert.equal(fileRows(exact.app, IDS[0]).length, 200)
  assert.equal(inBatch(exact.app, IDS[0], 'Show more'), null)
  const over = await boot({ batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], many(201)) } })
  await open(over.app, IDS[0])
  assert.equal(fileRows(over.app, IDS[0]).length, 200)
  assert.ok(text(batchRow(over.app, IDS[0])).includes('… and 1 more'))
})

test('200 считаются среди выбранных отбором, а не среди всех файлов пачки; смена отбора начинает счёт заново', async () => {
  const files = many(450, (i) => (i % 3 === 0 ? { decision: 'review' } : {}))
  const reviewed = files.filter((f) => f.decision === 'review') // 150
  const accepted = files.filter((f) => f.decision === 'accept') // 300
  const { app } = await boot({ batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], files) } })
  await open(app, IDS[0])
  await app.click(inBatch(app, IDS[0], 'needs review (150)'))
  assert.deepEqual(fileRows(app, IDS[0]).map((r) => r.props['data-file']), reviewed.map((f) => f.name))
  assert.equal(inBatch(app, IDS[0], 'Show more'), null)
  await app.click(inBatch(app, IDS[0], 'accepted (300)'))
  assert.deepEqual(fileRows(app, IDS[0]).map((r) => r.props['data-file']), accepted.slice(0, 200).map((f) => f.name))
  assert.ok(text(batchRow(app, IDS[0])).includes('… and 100 more'))
  await app.click(inBatch(app, IDS[0], 'Show more'))
  assert.equal(fileRows(app, IDS[0]).length, 300)
  await app.click(inBatch(app, IDS[0], 'All (450)'))
  assert.equal(fileRows(app, IDS[0]).length, 200) // показано снова 200, а не 300
  assert.ok(text(batchRow(app, IDS[0])).includes('… and 250 more'))
})

// ── раскрытый файл ──────────────────────────────────────────────
const RICH = fileOf('r.txt', {
  decision: 'review', score: 55, location: 'queue', sha256: '0123456789abcdef'.repeat(4), size: 5 * 1024 * 1024 + 300 * 1024, checked_by: 'model-test',
  date: '2026-09-30', reason: 'старая причина', path: `очередь/${BATCH}/r.txt`, decided_at: null,
  findings: [
    { rule: 'prompt_injection', level: 'HIGH', where: 'строка 2', quote: 'ignore all previous instructions' },
    { rule: 'secret', level: 'MEDIUM', where: 'строка 9', quote: 'password: hunter2' },
  ],
})
const richBoot = (file = RICH) => boot({ batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], [file, fileOf('z.txt')]) } })

test('нажатие на имя раскрывает файл, второе — сворачивает; другие строки не затронуты', async () => {
  const { app } = await richBoot()
  await open(app, IDS[0])
  assert.equal(infoOf(app, IDS[0], 'r.txt'), null)
  await app.click(nameButton(app, IDS[0], 'r.txt'))
  assert.ok(infoOf(app, IDS[0], 'r.txt'))
  assert.equal(infoOf(app, IDS[0], 'z.txt'), null)
  assert.equal(nameButton(app, IDS[0], 'r.txt').props['aria-expanded'], true)
  await app.click(nameButton(app, IDS[0], 'r.txt'))
  assert.equal(infoOf(app, IDS[0], 'r.txt'), null)
})

test('раскрытый файл без сообщений в находках: уровень, правило и цитата как есть; причина, кто проверил, дата, размер, sha256, путь', async () => {
  const { app } = await richBoot()
  await open(app, IDS[0])
  await app.click(nameButton(app, IDS[0], 'r.txt'))
  const t = text(infoOf(app, IDS[0], 'r.txt'))
  for (const part of ['High · Prompt injection', 'ignore all previous instructions', 'Medium · Secret or credential', 'password: hunter2',
    'Reason: старая причина', 'Checked by: model-test', 'Document date: 30.09.2026', 'Size: 5.3 MB', 'SHA-256: ' + RICH.sha256, 'Path: очередь/' + BATCH + '/r.txt'])
    assert.ok(t.includes(part), `${part}: ${t}`)
  assert.ok(!t.includes('строка 2'), t) // прежнее «где» в сообщении не нужно: оно русское, а «msg/where_msg» ещё не пришли
})

test('находка с сообщением: пояснение по словарю; цитата — только когда сообщение говорит, что это цитата документа', async () => {
  const file = fileOf('m.txt', {
    findings: [
      { rule: 'llm_malicious', level: 'HIGH', where: 'стр. 1', quote: 'RUSSIAN-QUOTE-HIDDEN', msg: { code: 'llm_finding', args: { model: 'm1', page: 1, why: 'asks to ignore rules' }, text: 'модель m1: просит забыть правила' } },
      { rule: 'prompt_injection', level: 'HIGH', where: 'стр. 2', quote: 'ignore all previous', msg: { code: 'llm_finding', args: { model: 'm1', page: 2, why: 'quoted case', quoted: true }, text: 'ru' } },
      { rule: 'macros', level: 'LOW', where: 'x', quote: 'ОПИСАНИЕ-НА-РУССКОМ', msg: { code: 'code_from_the_future', args: {}, text: 'Новое правило: подробности' } },
    ],
  })
  const { app } = await richBoot(file)
  await open(app, IDS[0])
  await app.click(nameButton(app, IDS[0], 'm.txt'))
  const t = text(infoOf(app, IDS[0], 'm.txt'))
  assert.ok(t.includes('The model m1 reported: asks to ignore rules'), t)
  assert.ok(!t.includes('RUSSIAN-QUOTE-HIDDEN') && !t.includes('модель m1'), t)
  assert.ok(t.includes('ignore all previous'), t) // quoted: true — цитата документа показана
  assert.ok(t.includes('Новое правило: подробности'), t) // неизвестный код — исходным text
  assert.ok(!t.includes('ОПИСАНИЕ-НА-РУССКОМ'), t)
})

test('«где» и причина с сообщениями показываются по словарю, неизвестный код — текстом; без них — прежние quote и reason', async () => {
  const file = fileOf('w.txt', {
    reason: 'причина-как-была', reason_msg: { code: 'llm_finding', args: { model: 'm2', page: 3, why: 'because' }, text: 'ru' },
    findings: [{ rule: 'hidden_text', level: 'MEDIUM', where: 'строка 4', quote: 'q', where_msg: { code: 'line_of_text', args: { n: 4 }, text: 'строка 4 (текст)' } }],
  })
  const { app } = await richBoot(file)
  await open(app, IDS[0])
  await app.click(nameButton(app, IDS[0], 'w.txt'))
  const t = text(infoOf(app, IDS[0], 'w.txt'))
  assert.ok(t.includes('Reason: The model m2 reported: because'), t)
  assert.ok(!t.includes('причина-как-была'), t)
  assert.ok(t.includes('строка 4 (текст)'), t) // where_msg с неизвестным кодом — исходным text
  const old = await richBoot(fileOf('o.txt', { reason: 'причина-как-была', findings: [{ rule: 'hidden_text', level: 'MEDIUM', where: 'строка 4', quote: 'прежняя цитата' }] }))
  await open(old.app, IDS[0])
  await old.app.click(nameButton(old.app, IDS[0], 'o.txt'))
  const o = text(infoOf(old.app, IDS[0], 'o.txt'))
  assert.ok(o.includes('Reason: причина-как-была') && o.includes('прежняя цитата'), o)
})

test('сообщение с известным кодом, но без нужного параметра, показывается текстом сервера, а не дырой', async () => {
  const file = fileOf('p.txt', { findings: [{ rule: 'llm_other', level: 'LOW', where: '', quote: '', msg: { code: 'llm_finding', args: { model: 'm1' }, text: 'Модель m1 что-то нашла' } }] })
  const { app } = await richBoot(file)
  await open(app, IDS[0])
  await app.click(nameButton(app, IDS[0], 'p.txt'))
  assert.ok(text(infoOf(app, IDS[0], 'p.txt')).includes('Модель m1 что-то нашла'))
})

// ── выдержка, примечания, причина (FR-73в) ──────────────────────
/** Текст раскрытого файла пачки: файл показан на подставных данных, строка раскрыта. Путь — латиницей, чтобы кириллица на экране значила сообщение. */
async function infoText(given) {
  const file = { ...given, path: given.path.replace('входящие/', 'inbox/') }
  const { app } = await richBoot(file)
  await open(app, IDS[0])
  await app.click(nameButton(app, IDS[0], file.name))
  return text(infoOf(app, IDS[0], file.name))
}
const injection = (args, over = {}) => ({
  rule: 'prompt_injection', level: 'HIGH', where: 'строка 2', quote: 'отмена прежних указаний: ignore all previous instructions',
  msg: messageOf('finding.prompt_injection', { kind: 'cancel_instructions', quoted: true, ...args }, 'отмена прежних указаний'),
  where_msg: messageOf('where.line', { line: 2 }, 'строка 2'), ...over,
})

test('цитата документа: когда в сообщении есть выдержка (args.excerpt), в кавычках она, а не quote с русским ярлыком', async () => {
  const t = await infoText(fileOf('e.txt', { findings: [injection({ excerpt: 'ignore all previous instructions' })] }))
  assert.ok(t.includes('Attempt to cancel earlier instructions “ignore all previous instructions”'), t)
  assert.ok(!t.includes('отмена прежних указаний') && !CYRILLIC.test(t), t) // русского ярлыка quote на экране нет
  assert.ok(t.includes('Line 2'), t)
})

test('выдержки нет — в кавычках quote, как раньше; выдержка не строка — тоже quote', async () => {
  for (const args of [{}, { excerpt: null }, { excerpt: 7 }, { excerpt: { text: 'x' } }]) {
    const t = await infoText(fileOf('q.txt', { findings: [injection(args)] }))
    assert.ok(t.includes('Attempt to cancel earlier instructions “отмена прежних указаний: ignore all previous instructions”'), JSON.stringify(args) + ': ' + t)
  }
})

test('пустая выдержка: кавычек нет совсем, а русский quote вместо неё не показывается', async () => {
  const t = await infoText(fileOf('empty.txt', { findings: [injection({ excerpt: '' })] }))
  assert.ok(t.includes('Attempt to cancel earlier instructions'), t)
  assert.ok(!t.includes('“') && !t.includes('ignore all previous') && !t.includes('отмена'), t)
})

test('выдержка показывается только вместе с quoted: true; у описания ни quote, ни выдержки', async () => {
  const description = { rule: 'macros', level: 'LOW', where: 'весь файл', quote: 'в документе макросы', where_msg: messageOf('where.file', {}, 'весь файл') }
  const t = await infoText(fileOf('d.txt', { findings: [
    { ...description, msg: messageOf('finding.macros', { excerpt: 'SECRET-EXCERPT' }, 'в документе макросы') },
    { ...description, rule: 'executable', msg: messageOf('finding.macros', { quoted: false, excerpt: 'SECRET-EXCERPT' }, 'в документе макросы') },
  ] }))
  assert.ok(t.includes('The document contains macros') && t.includes('Whole file'), t)
  assert.ok(!t.includes('SECRET-EXCERPT') && !t.includes('“') && !t.includes('в документе макросы'), t)
})

test('выдержка при сообщении с неизвестным кодом: его text и выдержка в кавычках', async () => {
  const t = await infoText(fileOf('f.txt', { findings: [{ rule: 'hidden_text', level: 'LOW', where: '', quote: 'новая находка: цитата',
    msg: messageOf('finding.from_the_future', { quoted: true, excerpt: 'cited words' }, 'Новая находка') }] }))
  assert.ok(t.includes('Новая находка “cited words”'), t)
  assert.ok(!t.includes('цитата'), t)
})

test('находка модели: слова модели по-английски, страница названа один раз — в «где», цитата документа — выдержка', async () => {
  const llm = (page) => ({ rule: 'llm_malicious', level: 'HIGH', where: 'по оценке модели m1', quote: 'ЦИТАТА-С-РУССКИМ-ЯРЛЫКОМ',
    msg: messageOf('llm_finding', { model: 'm1', page, why: 'asks to ignore the rules', quoted: true, excerpt: 'ignore the rules' }, 'модель m1: просит забыть правила'),
    where_msg: page === null ? messageOf('where.llm', { model: 'm1' }, 'по оценке модели m1') : messageOf('where.llm_page', { model: 'm1', page }, 'по оценке модели m1, страница ' + page) })
  const t = await infoText(fileOf('l.txt', { findings: [llm(null), llm(3)] }))
  assert.ok(t.includes('The model m1 reported: asks to ignore the rules “ignore the rules”'), t) // без страницы и со страницей текст находки один и тот же
  assert.equal(t.split('The model m1 reported: asks to ignore the rules “ignore the rules”').length - 1, 2, t)
  assert.ok(t.includes('According to the model m1, page 3'), t)
  assert.equal(t.split('page 3').length - 1, 1, t) // страница названа один раз: в «где», а не ещё и в тексте находки
  assert.ok(!CYRILLIC.test(t), t)
})

test('причина «задержан до решения владельца» называет правило английским названием, а не кодом', async () => {
  const held = (rule) => fileOf('h.txt', { reason: 'задержан до решения владельца: находка ' + rule, reason_msg: messageOf('reason.held', { rule }, 'задержан до решения владельца: находка ' + rule) })
  const t = await infoText(held('prompt_injection'))
  assert.ok(t.includes("Reason: Held for the owner's decision. Finding: Prompt injection"), t)
  assert.ok(!t.includes('prompt_injection') && !CYRILLIC.test(t), t)
  const unknown = await infoText(held('rule_from_the_future'))
  assert.ok(unknown.includes("Finding: rule_from_the_future"), unknown) // незнакомое правило — как пришло
})

test('примечания к архиву: по сообщениям из notes_msg в порядке notes; неизвестный код — его text', async () => {
  const t = await infoText(fileOf('a.zip', { family: 'archive', notes: ['исходный архив не копировался в карантин', 'внутри был исполняемый файл: run.exe', 'примечание из будущего'],
    notes_msg: [messageOf('note.archive_not_copied', {}, 'исходный архив не копировался в карантин'),
      messageOf('note.executable_inside', { name: 'run.exe' }, 'внутри был исполняемый файл: run.exe'),
      messageOf('note.from_the_future', { x: 1 }, 'Примечание из будущего')] }))
  assert.ok(t.includes('Notes:'), t)
  const first = t.indexOf('The source archive was not copied to quarantine')
  const second = t.indexOf('An executable file was inside: run.exe')
  const third = t.indexOf('Примечание из будущего')
  assert.ok(first >= 0 && second > first && third > second, t)
  assert.ok(!t.includes('исходный архив не копировался') && !t.includes('внутри был исполняемый файл'), t) // русские notes при notes_msg не нужны
})

test('примечания старой квитанции без notes_msg — прежние notes как есть; нет ни тех, ни других — строки «Notes:» нет', async () => {
  const old = await infoText(fileOf('o.zip', { notes: ['первое примечание', 'второе примечание'] }))
  assert.ok(old.includes('Notes:') && old.indexOf('первое примечание') >= 0 && old.indexOf('второе примечание') > old.indexOf('первое примечание'), old)
  for (const over of [{}, { notes: [] }, { notes: [], notes_msg: [] }, { notes: null, notes_msg: null }, { notes_msg: 'не список' }]) {
    const none = await infoText(fileOf('n.zip', over))
    assert.ok(!none.includes('Notes:'), JSON.stringify(over) + ': ' + none)
  }
})

test('примечания: негодный элемент notes_msg заменяется прежним notes той же позиции, а не дырой', async () => {
  const t = await infoText(fileOf('m.zip', { notes: ['старое первое', 'старое второе'], notes_msg: [null, messageOf('note.executable_inside', { name: 'a.exe' }, 'Русский текст')] }))
  assert.ok(t.includes('старое первое') && t.includes('An executable file was inside: a.exe'), t)
  assert.ok(!t.includes('старое второе') && !t.includes('Русский текст'), t)
})

test('причина: reason_msg с известным кодом — по словарю, с неизвестным — его text, null или нет — прежний reason', async () => {
  const known = await infoText(fileOf('k.txt', { reason: 'пустой файл', reason_msg: messageOf('reason.empty_file', {}, 'пустой файл') }))
  assert.ok(known.includes('Reason: Empty file') && !known.includes('пустой файл'), known)
  const unknown = await infoText(fileOf('u.txt', { reason: 'прежняя причина', reason_msg: messageOf('reason.from_the_future', {}, 'Причина из будущего') }))
  assert.ok(unknown.includes('Reason: Причина из будущего') && !unknown.includes('прежняя причина'), unknown)
  const nothing = await infoText(fileOf('n.txt', { reason: 'прежняя причина', reason_msg: null }))
  assert.ok(nothing.includes('Reason: прежняя причина'), nothing)
  const empty = await infoText(fileOf('x.txt', { reason: '', reason_msg: null }))
  assert.ok(!empty.includes('Reason:'), empty)
})

test('архив, отказ распаковки и находки правил с настоящими кодами каталога: весь раскрытый файл английский', async () => {
  const file = fileOf('bad.zip', {
    path: 'inbox/' + BATCH + '/bad.zip', family: 'archive', decision: 'quarantine',
    reason: 'архив не принят целиком', reason_msg: messageOf('reason.archive_rejected', {}, 'архив не принят целиком'),
    notes: ['внутри был исполняемый файл: run.exe'], notes_msg: [messageOf('note.executable_inside', { name: 'run.exe' }, 'внутри был исполняемый файл: run.exe')],
    findings: [
      { rule: 'executable', level: 'CRITICAL', where: 'весь файл', quote: 'программа Windows',
        msg: messageOf('finding.executable', { kind: 'pe' }, 'программа Windows'), where_msg: messageOf('where.file', {}, 'весь файл') },
      { rule: 'archive', level: 'HIGH', where: 'весь архив', quote: 'архив сжат сильнее, чем 100 к 1 — похоже на архивную бомбу',
        msg: messageOf('unpack.bomb_ratio', { ratio: 100 }, 'архив сжат сильнее, чем 100 к 1 — похоже на архивную бомбу'), where_msg: messageOf('where.archive', {}, 'весь архив') },
      { rule: 'secret', level: 'HIGH', where: 'строка 9', quote: 'ключ AWS: AKIA***',
        msg: messageOf('finding.secret', { kind: 'aws_key', quoted: true, excerpt: 'AKIA***' }, 'ключ AWS'), where_msg: messageOf('where.line', { line: 9 }, 'строка 9') },
    ],
  })
  const t = await infoText(file)
  for (const part of ['Critical · Executable file', 'Windows program', 'Whole file', 'High · Archive problem', 'The archive is compressed more than 100 to 1: it looks like an archive bomb',
    'Whole archive', 'AWS key “AKIA***”', 'Line 9', 'Reason: The archive was rejected as a whole', 'Notes: An executable file was inside: run.exe'])
    assert.ok(t.includes(part), `${part}: ${t}`)
  assert.ok(!CYRILLIC.test(t), t)
})

test('файл без находок и без сведений: «No findings» и прочерки, а не «null» или «undefined»', async () => {
  const bare = { name: 'bare.txt', decision: 'skip', location: null, decided: null }
  const { app } = await boot({ batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], [bare]) } })
  await open(app, IDS[0])
  await app.click(nameButton(app, IDS[0], 'bare.txt'))
  const t = text(infoOf(app, IDS[0], 'bare.txt'))
  assert.ok(t.includes('No findings'), t)
  assert.ok(!/null|undefined|NaN/.test(t), t)
  for (const label of ['Checked by: —', 'Document date: —', 'Size: —', 'SHA-256: —', 'Path: —']) assert.ok(t.includes(label), `${label}: ${t}`)
  assert.deepEqual(cellsOf(fileRows(app, IDS[0])[0]), ['bare.txt', 'skipped', '—', 'Not stored', '—', '—'])
})

test('файл, вернувшийся в папку возврата, показывает, куда возвращён; решение владельца — когда принято', async () => {
  const file = fileOf('ret.txt', { decision: 'failed', path: null, returned: `${BATCH}/ret.txt`, location: 'returned', decided: 'return', decided_at: '2026-10-04T19:30:00Z' })
  const { app } = await richBoot(file)
  await open(app, IDS[0])
  await app.click(nameButton(app, IDS[0], 'ret.txt'))
  const t = text(infoOf(app, IDS[0], 'ret.txt'))
  assert.ok(t.includes(`Returned to: ${BATCH}/ret.txt`) && !t.includes('Path:'), t)
  assert.ok(t.includes('04.10.2026 19:30'), t)
})

test('размер файла английскими единицами, дата документа без сдвига по часовому поясу', async () => {
  const sizes = { 'b.txt': [512, '512 B'], 'k.txt': [2048, '2 KB'], 'm.txt': [3 * 1024 * 1024, '3.0 MB'], 'g.txt': [2 * 1024 ** 3, '2.0 GB'] }
  const files = Object.entries(sizes).map(([name, [size]]) => fileOf(name, { size, date: '2026-01-01', path: 'p/' + name }))
  try {
    process.env.TZ = 'America/Los_Angeles' // UTC-8: сдвиг по поясу превратил бы 1 января в 31 декабря
    const { app } = await boot({ batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], files) } })
    await open(app, IDS[0])
    for (const [name, [, label]] of Object.entries(sizes)) {
      await app.click(nameButton(app, IDS[0], name))
      const t = text(infoOf(app, IDS[0], name))
      assert.ok(t.includes(`Size: ${label}`), `${name}: ${t}`)
      assert.ok(t.includes('Document date: 01.01.2026'), t)
      assert.ok(!CYRILLIC.test(t), t)
    }
  } finally {
    process.env.TZ = 'UTC'
  }
})

test('имена файлов, сообщения и цитаты вставляются как текст, а не как разметка', async () => {
  const evil = fileOf('<b>x</b>.txt', {
    reason: '<img src=x onerror=1>', findings: [{ rule: '<i>r</i>', level: '<u>L</u>', where: '', quote: '<script>alert(1)</script>' }],
  })
  const { app } = await boot({
    batches: [batchOf(IDS[0])],
    details: { [IDS[0]]: detailOf(IDS[0], [evil], { problems: [{ code: null, args: {}, text: '<svg onload=1>' }] }) },
  })
  await open(app, IDS[0])
  await app.click(nameButton(app, IDS[0], '<b>x</b>.txt'))
  const html = app.html()
  assert.ok(html.includes('&lt;b&gt;x&lt;/b&gt;.txt') && html.includes('&lt;script&gt;') && html.includes('&lt;svg onload=1&gt;'))
  for (const raw of ['<b>x</b>', '<img', '<script', '<svg', '<i>r</i>', '<u>']) assert.ok(!html.includes(raw), raw)
})

test('кривые данные в ответах архива раздел не роняют: лишнее пропускается, понятное показывается', async () => {
  const files = [
    fileOf('ok.txt'), null, 7, 'строка',
    { name: 'nofind.txt', findings: 'x', decision: 'accept' },
    { decision: 'accept', location: 5, decided: 7, score: 'много', findings: [null, 3, { rule: 'secret' }] },
  ]
  const problems = ['Строкой', 5, null, { text: 'Объектом' }, { code: 'llm_finding' }]
  const { app } = await boot({ batches: [batchOf(IDS[0])], details: { [IDS[0]]: { id: IDS[0], files, problems } } })
  await open(app, IDS[0])
  const box = text(batchRow(app, IDS[0]))
  assert.ok(box.includes('Строкой') && box.includes('Объектом'), box)
  assert.deepEqual(fileRows(app, IDS[0]).map((r) => r.props['data-file']), ['ok.txt', 'nofind.txt', ''])
  await app.click(nameButton(app, IDS[0], 'nofind.txt'))
  await app.click(nameButton(app, IDS[0], ''))
  assert.ok(infoOf(app, IDS[0], 'nofind.txt') && infoOf(app, IDS[0], ''))
  assert.ok(!/undefined|NaN|\[object/.test(text(batchRow(app, IDS[0]))), text(batchRow(app, IDS[0])))
})

// ── обновление по ходу опроса ───────────────────────────────────
test('в состоянии сменился номер последней пачки — новая пачка появляется наверху списка', async () => {
  const { app, server, clock } = await boot({ batches: batches(3) })
  assert.equal(rowsOfList(app).length, 3)
  server.addBatch(batchOf('20261005-000100', { files: 7, counts: { accept: 7 } }))
  await clock.advance(30000)
  await app.settle()
  const rows = rowsOfList(app)
  assert.deepEqual(rows.map((r) => r.props['data-batch']), ['20261005-000100', ...IDS.slice(0, 3)])
  assert.ok(text(rows[0]).includes('accepted 7'))
  assert.equal(server.batchCalls().length, 2)
})

test('счётчики изменились: подробности раскрытой пачки обновляются на экране вместе с отбором и раскрытым файлом', async () => {
  const { app, server, clock } = await mixedBoot()
  await open(app, IDS[0])
  await app.click(inBatch(app, IDS[0], 'needs review (2)'))
  await app.click(nameButton(app, IDS[0], 'c.txt'))
  assert.deepEqual(cellsOf(fileRows(app, IDS[0])[0]).slice(2, 4), ['—', 'Awaiting review'])
  // владелец принял c.txt другим путём: меняются решение владельца и место, а очередь стала короче
  server.details[IDS[0]].files[2] = { ...MIXED[2], decided: 'accept', decided_at: '2026-10-04T20:00:00Z', location: 'corpus' }
  server.queue.push(item('queue', 'q.txt'))
  await clock.advance(30000)
  await app.settle()
  assert.deepEqual(cellsOf(fileRows(app, IDS[0])[0]).slice(2, 4), ['accepted', 'In the archive'])
  assert.equal(inBatch(app, IDS[0], 'needs review (2)').props['aria-pressed'], true) // отбор сохранён
  assert.ok(infoOf(app, IDS[0], 'c.txt'), 'раскрытый файл остался раскрытым')
  assert.ok(text(infoOf(app, IDS[0], 'c.txt')).includes('04.10.2026 20:00'))
})

test('решение владельца кнопкой в «Needs decision» тут же отражается в раскрытой пачке', async () => {
  const queued = item('queue', 'c.txt')
  const files = [fileOf('c.txt', { decision: 'review', location: 'queue', path: queued.path })]
  const { app, server } = await boot({ queue: [queued], batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], files) } })
  server.on['POST ' + QUEUE] = async (body, s) => {
    s.details[IDS[0]].files[0] = { ...s.details[IDS[0]].files[0], decided: 'accept', decided_at: '2026-10-04T20:00:00Z', location: 'corpus' }
    s.queue = []
    return reply(200, { results: body.paths.map((path) => ({ path, ok: true })) })
  }
  await open(app, IDS[0])
  assert.deepEqual(cellsOf(fileRows(app, IDS[0])[0]).slice(2, 4), ['—', 'Awaiting review'])
  await app.click(find(app.find((n) => n.type === 'li' && n.props['data-path'] === queued.path), (n) => n.type === 'button' && text(n) === 'Accept'))
  assert.deepEqual(cellsOf(fileRows(app, IDS[0])[0]).slice(2, 4), ['accepted', 'In the archive'])
})

// ── сбои не ломают вкладку ──────────────────────────────────────
test('отказ адреса истории показан в разделе «Batches» его сообщением; над вкладкой ошибки нет, остальное работает', async () => {
  const data = { queue: [item('queue', 'a.txt')], on: { ['GET ' + BATCHES]: async () => reply(409, { error: { code: 'x', args: {}, text: 'Квитанции повреждены' } }) } }
  const { app, server } = await boot(data)
  const inside = alerts(section(app))
  assert.equal(inside.length, 1)
  assert.equal(text(inside[0]), 'Квитанции повреждены') // неизвестный код — текст сообщения
  assert.equal(alerts(app.tree()).length, 1) // других ошибок на вкладке нет
  assert.ok(app.text().includes('Needs decision (1)'))
  assert.ok(app.text().includes('Waiting in the inbox folder: 3'))
  assert.equal(enabled(app.button('Run now')), true)
  await app.click(app.button('Select all'))
  await app.click(app.button('Accept selected'))
  await app.click(app.button('Yes, accept 1'))
  assert.equal(server.posts(QUEUE).length, 1) // решения по-прежнему работают
  assert.equal(alerts(section(app)).length, 1)
})

test('сбой связи на адресе истории — отдельный английский текст в разделе; список решений и состояние целы', async () => {
  const { app } = await boot({ queue: [item('queue', 'a.txt')], on: { ['GET ' + BATCHES]: async () => { throw new TypeError('Failed to fetch') } } })
  const inside = alerts(section(app))
  assert.equal(inside.length, 1)
  assert.match(text(inside[0]), /No connection to DSH/)
  assert.ok(!CYRILLIC.test(text(inside[0])))
  assert.equal(alerts(app.tree()).length, 1)
  assert.equal(app.all((n) => n.type === 'li' && n.props['data-path'] !== undefined).length, 1)
})

test('вход истёк (401) и прочие сбои истории — свои английские тексты в разделе', async () => {
  const { app, server, clock } = await boot({ batches: batches(2) })
  server.on['GET ' + BATCHES] = async () => ({ ok: false, status: 401, json: async () => { throw new Error('не JSON') } })
  server.addBatch(batchOf('20261005-000100'))
  await clock.advance(30000)
  await app.settle()
  assert.match(text(alerts(section(app))[0]), /sign-in has expired/)
  server.on['GET ' + BATCHES] = async () => reply(502, null)
  await clock.advance(30000)
  await app.settle()
  assert.match(text(alerts(section(app))[0]), /request failed \(code 502\)/)
  assert.equal(rowsOfList(app).length, 2) // прежний список на месте
  delete server.on['GET ' + BATCHES]
  await clock.advance(30000)
  await app.settle()
  assert.equal(alerts(section(app)).length, 0)
  assert.equal(rowsOfList(app).length, 3)
})

test('отказ подробностей одной пачки показан внутри неё; другие пачки раскрываются нормально', async () => {
  const { app } = await boot({ batches: batches(2), details: detailsFor(IDS[1]) }) // подробностей первой пачки у архива нет
  await open(app, IDS[0])
  const inside = alerts(batchRow(app, IDS[0]))
  assert.equal(inside.length, 1)
  assert.equal(text(inside[0]), 'Нет такой пачки')
  assert.equal(alerts(app.tree()).length, 1)
  await open(app, IDS[1])
  assert.equal(fileRows(app, IDS[1]).length, 1)
  assert.equal(alerts(batchRow(app, IDS[1])).length, 0)
})

test('сбой связи при раскрытии пачки: английский текст внутри пачки, вкладка работает', async () => {
  const { app, server } = await boot({ batches: batches(1), details: detailsFor(IDS[0]), queue: [item('queue', 'a.txt')] })
  server.on['GET ' + ONE] = async () => { throw new TypeError('Failed to fetch') }
  await open(app, IDS[0])
  assert.match(text(alerts(batchRow(app, IDS[0]))[0]), /No connection to DSH/)
  assert.ok(app.text().includes('Needs decision (1)'))
})

// ── язык ────────────────────────────────────────────────────────
test('подписи раздела берутся из словаря: ни одного ключа, которого в словаре нет', async () => {
  const files = [...MIXED, RICH, fileOf('m.txt', { findings: [{ rule: 'llm_other', level: 'LOW', where: '', quote: 'q', msg: { code: 'llm_finding', args: { model: 'm', page: 1, why: 'w', quoted: true }, text: 't' } }] })]
  const { app, ctx, server, clock } = await boot({
    batches: [batchOf(IDS[0], { seconds: 3725, problems: 2 }), batchOf(IDS[1], { files: 1, seconds: null }), ...batches(70).slice(2)],
    details: { [IDS[0]]: detailOf(IDS[0], [...files, ...many(250)], { problems: [{ code: null, args: {}, text: 'x' }] }), ...detailsFor(IDS[1]) },
  })
  await open(app, IDS[0])
  for (const name of ['r.txt', 'b.exe', 'e.txt', 'm.txt']) await app.click(nameButton(app, IDS[0], name))
  await app.click(inBatch(app, IDS[0], 'Show more'))
  await app.click(app.button('Load more'))
  await open(app, IDS[1])
  server.on['GET ' + BATCHES] = async () => reply(500, null)
  server.addBatch(batchOf('20261005-000100'))
  await clock.advance(30000)
  await app.settle()
  server.on['GET ' + BATCHES] = async () => { throw new TypeError('Failed to fetch') }
  server.addBatch(batchOf('20261005-000200'))
  await clock.advance(30000)
  await app.settle()
  delete server.on['GET ' + BATCHES]
  server.on['GET ' + ONE] = async () => reply(500, null)
  server.queue.push(item('queue', 'x.txt'))
  await clock.advance(30000)
  await app.settle()
  assert.deepEqual([...new Set(ctx.missingKeys)], [])
})

test('на экране раздела нет русских слов, кроме данных из архива (на этом образце их нет)', async () => {
  const english = (f) => ({ ...f, path: `queue/${BATCH}/${f.name}`, date_source: 'file' })
  const files = MIXED.map(english)
  const { app } = await boot({ batches: [batchOf(IDS[0], { problems: 1 }), batchOf(IDS[1], { seconds: null })], details: { [IDS[0]]: detailOf(IDS[0], files, { problems: [{ code: 'llm_finding', args: { model: 'm', page: 1, why: 'w' }, text: 'ru' }] }), ...detailsFor(IDS[1]) } })
  await open(app, IDS[0])
  for (const f of files) await app.click(nameButton(app, IDS[0], f.name))
  assert.ok(!CYRILLIC.test(text(section(app))), text(section(app)).match(/.{0,30}[Ѐ-ӿ]+.{0,30}/)?.[0])
})

test('словарь сообщений не пришёл: раздел работает, решения и места — сырыми словами, сообщения — исходным text', async () => {
  const problems = [{ code: 'llm_finding', args: { model: 'm1', page: 1, why: 'odd' }, text: 'модель m1: странная просьба' }]
  const files = [fileOf('a.txt', { decision: 'quarantine', location: 'quarantine', decided: 'return', findings: [{ rule: 'executable', level: 'CRITICAL', where: '', quote: 'q' }] })]
  const { app } = await boot({ chunksFail: true, batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], files, { problems }) } })
  await open(app, IDS[0])
  const box = text(batchRow(app, IDS[0]))
  assert.ok(box.includes('accept 12 · review 2'), box)
  assert.ok(box.includes('модель m1: странная просьба'), box)
  assert.deepEqual(cellsOf(fileRows(app, IDS[0])[0]), ['a.txt', 'quarantine', 'return', 'quarantine', '5', 'executable · CRITICAL'])
  assert.ok(app.text().includes('Needs decision')) // остальная вкладка на месте
})

test('раскрытая пачка остаётся раскрытой, когда первая страница перечитана; подробности из-за этого не запрашиваются заново', async () => {
  const { app, server, clock } = await boot({ batches: batches(3), details: detailsFor(IDS[0]) })
  await open(app, IDS[0])
  server.addBatch(batchOf('20261005-000100'))
  await clock.advance(30000)
  await app.settle()
  assert.equal(rowsOfList(app).length, 4)
  assert.equal(headOf(app, IDS[0]).props['aria-expanded'], true)
  assert.equal(fileRows(app, IDS[0]).length, 1)
  assert.deepEqual(server.detailCalls(), [IDS[0]])
})
