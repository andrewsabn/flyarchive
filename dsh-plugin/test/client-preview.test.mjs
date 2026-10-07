// Просмотр содержимого на вкладке «Archive»: обращения, общее хранилище и область просмотра (FR-83).
// Плагин ставится на подставной контекст DSH, вкладка рисуется малым рисовальщиком с состоянием; архив, таймеры, адреса
// объектов (URL.createObjectURL) и blob() подставные. Серверная команда flyarchive preview здесь — подставные ответы по договору.
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  BATCH, attachmentOf, batchIds, batchOf, detailOf, fakeArchive, fakeCtx, fakeObjectUrls, fileOf, imagePreview, item, listingPreview,
  mailPreview, mediaPreview, messageOf, metaOf, nonePreview, pagesPreview, png, previewKey, slotNamed, textPreview,
} from './kit-archive.mjs'
import { createClock, fakePage, find, flush, load, recorder, reply, stateful, text } from './kit.mjs'

const SHOW = 'api/flyarchive.preview'
const PAGE = 'api/flyarchive.preview.page'
const QUEUE = 'api/flyarchive.queue'
const CYRILLIC = /[Ѐ-ӿ]/
const IDS = batchIds(3)
const FILE = `очередь/${BATCH}/a.pdf` // путь файла в очереди, как в списке «Needs decision»
const TARGET = { area: 'queue', path: FILE }
const KEY = previewKey('queue', FILE)
const RENDERING = { kind: 'rendering' }
const REFUSAL = { code: 'preview_failed', args: {}, text: 'Preview worker failed' }

const deferred = () => {
  let resolve
  const promise = new Promise((r) => { resolve = r })
  return { promise, resolve }
}
const urlsFor = (t) => {
  const urls = fakeObjectUrls()
  t.after(() => urls.restore())
  return urls
}

// ── общая обвязка: хранилище и вкладка ──────────────────────────
/** Хранилище на подставных архиве, таймерах, странице и адресах объектов; опрос не запущен: таймеров в нём только свои. */
async function setup(t, initial = {}) {
  const { exports } = await load()
  const urls = urlsFor(t)
  const server = fakeArchive(initial)
  const clock = createClock()
  const client = exports.parts.createClient(server.doFetch)
  const store = exports.parts.createStore({ client, timers: clock, page: fakePage(false) })
  return { exports, server, clock, client, store, urls }
}
/** Запись просмотра файла и вид, который показан сейчас (последний в стопке). */
const entryOf = (store, target = TARGET) => Object.values(store.getSnapshot().previews).find((e) => e.target.area === target.area && e.target.path === target.path) ?? null
const viewOf = (store, target) => entryOf(store, target)?.stack.at(-1) ?? null
const urlOfPage = (urls, store, target) => urls.pageOf(viewOf(store, target).image.url)

/** Плагин на подставном DSH, тело вкладки смонтировано после первого опроса. */
async function boot(t, { chunksFail = false, ...initial } = {}) {
  const { React, mount } = stateful()
  const loaded = await load(React, { chunksFail })
  const urls = urlsFor(t)
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
  const body = slotNamed(ctx, 'sidebar.right.pane.tab')[0].component
  const app = mount(h(body, {}))
  await app.settle()
  return { ...loaded, React, mount, h, server, clock, page, ctx, app, urls, body }
}

// ── у строки «Needs decision» ───────────────────────────────────
const rowOf = (app, path) => app.find((n) => n.type === 'li' && n.props['data-path'] === path)
const toggleOf = (app, path) => find(rowOf(app, path), (n) => n.props?.role === 'button' && n.props['aria-expanded'] !== undefined)
const expand = (app, path) => app.click(toggleOf(app, path))
const areaIn = (node, path) => find(node, (n) => n.props?.['data-preview'] === (path ?? FILE))
const areaOf = (app, path) => areaIn(app.tree(), path)
const inArea = (area, label) => find(area, (n) => n.type === 'button' && text(n) === label)
const imgIn = (area) => find(area, (n) => n.type === 'img')
const enabled = (button) => button !== null && !button.props.disabled
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
const alertsIn = (node) => collect(node, (n) => n.props?.role === 'alert')
const withPreview = (description, path = FILE, area = 'queue', member = []) => ({ [previewKey(area, path, member)]: description })
const queued = (name = 'a.pdf') => item('queue', name)

// ── обращения к серверной половине ──────────────────────────────
test('createClient: описание — POST на свой адрес, тело {area, path, member}, ответ читается как JSON', async () => {
  const { exports } = await load()
  const answer = textPreview('a.pdf', 'hello')
  const { calls, doFetch } = recorder(() => reply(200, answer))
  const client = exports.parts.createClient(doFetch)
  assert.deepEqual(await client.preview({ area: 'queue', path: FILE, member: [2, 1] }), answer)
  assert.equal(calls.length, 1)
  const [route, init] = calls[0]
  assert.equal(route, SHOW)
  assert.equal(init.method, 'POST')
  assert.equal(init.headers['content-type'], 'application/json')
  assert.deepEqual(JSON.parse(init.body), { area: 'queue', path: FILE, member: [2, 1] })
})

test('createClient: страница — POST на другой адрес, тело с номером страницы, результат — blob', async () => {
  const { exports } = await load()
  const blob = { size: 3, type: 'image/png' }
  const { calls, doFetch } = recorder(() => png(blob))
  const client = exports.parts.createClient(doFetch)
  assert.equal(await client.previewPage({ area: 'corpus', path: 'входящие/x/a.pdf', member: [] }, 4), blob)
  const [route, init] = calls[0]
  assert.equal(route, PAGE)
  assert.equal(init.method, 'POST')
  assert.deepEqual(JSON.parse(init.body), { area: 'corpus', path: 'входящие/x/a.pdf', member: [], page: 4 })
})

test('createClient: путь файла и вложения в адрес не попадают — только в тело; адрес без запроса', async () => {
  const { exports } = await load()
  const { calls, doFetch } = recorder((route) => (route === PAGE ? png({}) : reply(200, {})))
  const client = exports.parts.createClient(doFetch)
  const path = 'очередь/20260101-120000/секрет & тайна=1?x#y.pdf'
  await client.preview({ area: 'queue', path, member: [3] })
  await client.previewPage({ area: 'queue', path, member: [3] }, 2)
  for (const [route] of calls) {
    assert.ok([SHOW, PAGE].includes(route), route)
    for (const piece of ['секрет', encodeURIComponent('секрет'), 'тайна', '%D0', '3', '?', '#', 'pdf']) assert.ok(!route.includes(piece), `${piece}: ${route}`)
  }
  assert.equal(JSON.parse(calls[0][1].body).path, path)
})

test('createClient: отказ описания и страницы приходит ошибкой с сообщением, обрыв — offline, вход истёк — auth', async () => {
  const { exports } = await load()
  const refused = exports.parts.createClient(async () => reply(409, { error: REFUSAL }))
  const ref = { area: 'queue', path: FILE, member: [] }
  await assert.rejects(refused.preview(ref), (e) => e.kind === 'refused' && e.refusal.text === REFUSAL.text && e.refusal.code === 'preview_failed')
  await assert.rejects(refused.previewPage(ref, 1), (e) => e.kind === 'refused' && e.refusal.text === REFUSAL.text)
  const offline = exports.parts.createClient(async () => { throw new TypeError('Failed to fetch') })
  await assert.rejects(offline.preview(ref), (e) => e.kind === 'offline')
  await assert.rejects(offline.previewPage(ref, 1), (e) => e.kind === 'offline')
  const signedOut = exports.parts.createClient(async () => ({ ok: false, status: 401, json: async () => { throw new Error('не JSON') }, blob: async () => ({}) }))
  await assert.rejects(signedOut.previewPage(ref, 1), (e) => e.kind === 'auth')
  const broken = exports.parts.createClient(async () => reply(502, null))
  await assert.rejects(broken.previewPage(ref, 1), (e) => e.kind === 'failed' && e.status === 502)
})

test('createClient: картинка читается как blob, а не как JSON: ответ-картинка, у которого json() падает, не ошибка', async () => {
  const { exports } = await load()
  const client = exports.parts.createClient(async () => png({ tag: 'blob' }))
  assert.deepEqual(await client.previewPage({ area: 'queue', path: FILE, member: [] }, 1), { tag: 'blob' })
})

// ── хранилище: описание ─────────────────────────────────────────
test('открытие просмотра: один запрос описания с областью, путём и пустым member; описание ложится в состояние', async (t) => {
  const { server, store } = await setup(t, { previews: withPreview(textPreview('a.pdf', 'hello')) })
  assert.deepEqual(store.getSnapshot().previews, {})
  const opened = store.openPreview(TARGET)
  assert.equal(viewOf(store).phase, 'loading') // пока ответа нет
  await opened
  assert.deepEqual(server.previewCalls(), [{ area: 'queue', path: FILE, member: [] }])
  assert.equal(viewOf(store).phase, 'ready')
  assert.equal(viewOf(store).data.text, 'hello')
  assert.equal(viewOf(store).error, null)
  assert.deepEqual(viewOf(store).member, [])
  assert.deepEqual(server.pageCalls(), []) // текст картинки не просит
})

test('все обращения просмотра — POST на два своих адреса без запроса; путь файла в адрес не попадает', async (t) => {
  const { server, store } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 3)) })
  await store.openPreview(TARGET)
  await store.showPage(TARGET, 2)
  const mine = server.calls.filter((c) => c.route.startsWith('api/flyarchive.preview'))
  assert.equal(mine.length, 3)
  for (const c of mine) {
    assert.equal(c.method, 'POST')
    assert.ok(c.route === SHOW || c.route === PAGE, c.route)
    assert.equal(c.body.path, FILE)
  }
})

test('картинку просят только виды с картинкой: pages, image и media — страницу 1; text, mail, listing и none — ничего', async (t) => {
  const previews = {
    ...withPreview(pagesPreview('p.pdf', 3), 'очередь/b/p.pdf'), ...withPreview(imagePreview('i.png'), 'очередь/b/i.png'),
    ...withPreview(mediaPreview('m.mp4'), 'очередь/b/m.mp4'), ...withPreview(textPreview('t.txt', 'x'), 'очередь/b/t.txt'),
    ...withPreview(mailPreview('e.eml'), 'очередь/b/e.eml'), ...withPreview(listingPreview('z.zip', [{ name: 'a', size: 1 }]), 'очередь/b/z.zip'),
    ...withPreview(nonePreview('x.exe', { code: null, args: {}, text: 'No preview' }), 'очередь/b/x.exe'),
  }
  const { server, store } = await setup(t, { previews })
  const names = ['p.pdf', 'i.png', 'm.mp4', 't.txt', 'e.eml', 'z.zip', 'x.exe']
  for (const name of names) await store.openPreview({ area: 'queue', path: `очередь/b/${name}` })
  await flush()
  assert.deepEqual(server.pageCalls().map((b) => [b.path.split('/').pop(), b.page]), [['p.pdf', 1], ['i.png', 1], ['m.mp4', 1]])
})

test('для листания запрашивается только показанная страница, по одной; остальные страницы документа не просятся никогда', async (t) => {
  const { server, store } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 20)) })
  await store.openPreview(TARGET)
  await flush()
  assert.deepEqual(server.pageCalls().map((b) => b.page), [1])
  await store.showPage(TARGET, 2)
  await store.showPage(TARGET, 3)
  await store.showPage(TARGET, 2)
  assert.deepEqual(server.pageCalls().map((b) => b.page), [1, 2, 3, 2])
  assert.deepEqual(server.pageCalls().map((b) => [b.area, b.path, b.member]), Array(4).fill(['queue', FILE, []]))
  assert.equal(viewOf(store).page, 2)
})

test('номер страницы вне показанных и негодный запроса не порождают; та же страница второй раз — тоже', async (t) => {
  const { server, store } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 143, 20)) })
  await store.openPreview(TARGET)
  await flush()
  for (const bad of [0, -1, 21, 143, 500, 1.5, '2', null, undefined, NaN]) await store.showPage(TARGET, bad)
  await store.showPage(TARGET, 1) // уже показана
  assert.deepEqual(server.pageCalls().map((b) => b.page), [1])
  assert.equal(viewOf(store).page, 1)
  await store.showPage(TARGET, 20) // последняя из показанных
  assert.deepEqual(server.pageCalls().map((b) => b.page), [1, 20])
})

test('показанных страниц не больше, чем в документе: shown больше pages листание не расширяет', async (t) => {
  const { server, store } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 3, 30)) })
  await store.openPreview(TARGET)
  await flush()
  await store.showPage(TARGET, 4)
  await store.showPage(TARGET, 30)
  assert.deepEqual(server.pageCalls().map((b) => b.page), [1])
  await store.showPage(TARGET, 3)
  assert.deepEqual(server.pageCalls().map((b) => b.page), [1, 3])
})

test('листание работает только у вида pages: у текста и письма оно ничего не запрашивает', async (t) => {
  const { server, store } = await setup(t, { previews: { ...withPreview(textPreview('a.pdf', 'x')) } })
  await store.openPreview(TARGET)
  await store.showPage(TARGET, 2)
  assert.deepEqual(server.pageCalls(), [])
})

// ── хранилище: адреса объектов ──────────────────────────────────
test('адрес объекта: при смене страницы прежний освобождается, живёт только адрес показанной страницы', async (t) => {
  const { store, urls } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 5)) })
  await store.openPreview(TARGET)
  await flush()
  assert.equal(urls.live.size, 1)
  const first = viewOf(store).image.url
  assert.equal(urls.pageOf(first), 1)
  await store.showPage(TARGET, 2)
  assert.equal(urls.live.size, 1)
  assert.ok(!urls.live.has(first), 'адрес первой страницы освобождён')
  assert.ok(urls.revoked().includes(first))
  assert.equal(urlOfPage(urls, store, TARGET), 2)
  await store.showPage(TARGET, 3)
  await store.showPage(TARGET, 4)
  assert.equal(urls.live.size, 1)
  assert.equal(urls.created().length, 4)
  assert.equal(urls.revoked().length, 3)
})

test('адрес объекта освобождается при закрытии просмотра, а запись просмотра уходит из состояния', async (t) => {
  const { store, urls } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 5)) })
  await store.openPreview(TARGET)
  await flush()
  const url = viewOf(store).image.url
  store.closePreview(TARGET)
  assert.equal(urls.live.size, 0)
  assert.deepEqual(urls.revoked(), [url])
  assert.deepEqual(store.getSnapshot().previews, {})
})

test('закрытие просмотра без картинки (текст) ничего не освобождает и не падает', async (t) => {
  const { store, urls } = await setup(t, { previews: withPreview(textPreview('a.pdf', 'x')) })
  await store.openPreview(TARGET)
  store.closePreview(TARGET)
  store.closePreview(TARGET) // лишнее закрытие безвредно
  assert.deepEqual(urls.log, [])
  assert.deepEqual(store.getSnapshot().previews, {})
})

test('ответ на ушедшую страницу не показывается и адреса не оставляет, в каком бы порядке ни пришли ответы', async (t) => {
  const { server, store, urls } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 5)) })
  const gates = {}
  server.on['POST ' + PAGE] = (body) => new Promise((resolve) => { gates[body.page] = () => resolve(png({ page: body.page })) })
  await store.openPreview(TARGET) // страница 1 запрошена, ответа нет
  await flush()
  store.showPage(TARGET, 2)
  await flush()
  gates[2]()
  await flush()
  assert.equal(viewOf(store).image.page, 2)
  assert.equal(viewOf(store).image.phase, 'ready')
  gates[1]() // ответ на страницу 1 приходит после ответа на 2
  await flush()
  assert.equal(viewOf(store).image.page, 2)
  assert.deepEqual([...urls.live].map((u) => urls.pageOf(u)), [2])
  assert.equal(urlOfPage(urls, store, TARGET), 2)
})

test('ответ на ушедшую страницу, пришедший первым, тоже отбрасывается; верная страница остаётся', async (t) => {
  const { server, store, urls } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 5)) })
  const gates = {}
  server.on['POST ' + PAGE] = (body) => new Promise((resolve) => { gates[body.page] = () => resolve(png({ page: body.page })) })
  await store.openPreview(TARGET)
  await flush()
  store.showPage(TARGET, 2)
  await flush()
  gates[1]() // устаревший — первым
  await flush()
  assert.equal(viewOf(store).image.phase, 'loading')
  assert.equal(urls.live.size, 0)
  gates[2]()
  await flush()
  assert.deepEqual([...urls.live].map((u) => urls.pageOf(u)), [2])
})

test('ответ на закрытый просмотр ничего не воскрешает: ни записи, ни адреса, ни запроса страницы', async (t) => {
  const { server, store, urls } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 5)) })
  const description = deferred()
  server.on['POST ' + SHOW] = async (body, s) => { await description.promise; return reply(200, s.previews[KEY]) }
  const opened = store.openPreview(TARGET)
  await flush()
  store.closePreview(TARGET)
  description.resolve()
  await opened
  await flush()
  assert.deepEqual(store.getSnapshot().previews, {})
  assert.deepEqual(server.pageCalls(), [])
  assert.deepEqual(urls.log, [])
})

test('страница пришла после закрытия просмотра: адрес не создаётся, записи нет', async (t) => {
  const { server, store, urls } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 5)) })
  const gate = deferred()
  server.on['POST ' + PAGE] = async (body) => { await gate.promise; return png({ page: body.page }) }
  await store.openPreview(TARGET)
  await flush()
  store.closePreview(TARGET)
  gate.resolve()
  await flush()
  assert.deepEqual(store.getSnapshot().previews, {})
  assert.equal(urls.live.size, 0)
  assert.equal(urls.created().length, 0)
})

test('просмотр, открытый двумя телами вкладки: закрытие одного его не снимает, закрытие второго — снимает', async (t) => {
  const { server, store, urls } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 5)) })
  await store.openPreview(TARGET)
  await store.openPreview(TARGET)
  await flush()
  assert.equal(server.previewCalls().length, 1) // второй раз не спрашивали
  assert.equal(server.pageCalls().length, 1)
  store.closePreview(TARGET)
  assert.equal(urls.live.size, 1)
  assert.ok(entryOf(store))
  store.closePreview(TARGET)
  assert.equal(urls.live.size, 0)
  assert.equal(entryOf(store), null)
})

test('два файла — две записи со своими страницами и адресами: закрытие одного не трогает другого', async (t) => {
  const other = { area: 'quarantine', path: `карантин/${BATCH}/b.pdf` }
  const { store, urls } = await setup(t, { previews: { ...withPreview(pagesPreview('a.pdf', 5)), ...withPreview(pagesPreview('b.pdf', 5), other.path, 'quarantine') } })
  await store.openPreview(TARGET)
  await store.openPreview(other)
  await flush()
  await store.showPage(TARGET, 3)
  assert.equal(viewOf(store, TARGET).page, 3)
  assert.equal(viewOf(store, other).page, 1)
  assert.equal(urls.live.size, 2)
  store.closePreview(TARGET)
  assert.equal(urls.live.size, 1)
  assert.equal(urlOfPage(urls, store, other), 1)
})

test('остановка хранилища снимает все адреса и все таймеры просмотров', async (t) => {
  const rendering = { area: 'queue', path: `очередь/${BATCH}/slow.pdf` }
  const { store, urls, clock } = await setup(t, { previews: { ...withPreview(pagesPreview('a.pdf', 5)), ...withPreview(RENDERING, rendering.path) } })
  await store.openPreview(TARGET)
  await store.openPreview(rendering)
  await flush()
  assert.equal(urls.live.size, 1)
  assert.deepEqual(clock.waits(), [2000])
  store.stop()
  assert.equal(urls.live.size, 0)
  assert.deepEqual(clock.waits(), [])
})

// ── хранилище: вложения письма ──────────────────────────────────
const MAIL = [attachmentOf('a.pdf', 3), attachmentOf('inner.eml', 5, { type: 'eml' })] // номера member не совпадают с номерами строк
const MAIL_FILE = `очередь/${BATCH}/mail.eml`
const MAIL_TARGET = { area: 'queue', path: MAIL_FILE }
const mailSetup = (t, extra = {}) => setup(t, {
  previews: {
    ...withPreview(mailPreview('mail.eml', MAIL), MAIL_FILE),
    ...withPreview(pagesPreview('a.pdf', 4), MAIL_FILE, 'queue', [3]),
    ...withPreview(mailPreview('inner.eml', [attachmentOf('deep.pdf', 0), attachmentOf('deep.eml', 7, { type: 'eml' })]), MAIL_FILE, 'queue', [5]),
    ...withPreview(pagesPreview('deep.pdf', 2), MAIL_FILE, 'queue', [5, 0]),
    ...withPreview(mailPreview('deep.eml', [attachmentOf('too-deep.pdf', 0)]), MAIL_FILE, 'queue', [5, 7]),
    ...withPreview(textPreview('too-deep.pdf', 'x'), MAIL_FILE, 'queue', [5, 7, 0]),
    ...extra,
  },
})

test('вложение письма открывается тем же просмотром с member из записи вложения, а не с номером строки', async (t) => {
  const { server, store } = await mailSetup(t)
  await store.openPreview(MAIL_TARGET)
  await store.openAttachment(MAIL_TARGET, 5) // вторая строка списка, но member = 5
  assert.deepEqual(server.previewCalls().map((b) => b.member), [[], [5]])
  assert.deepEqual(viewOf(store, MAIL_TARGET).member, [5])
  assert.equal(viewOf(store, MAIL_TARGET).data.meta.name, 'inner.eml')
  assert.equal(entryOf(store, MAIL_TARGET).stack.length, 2)
})

test('вложение вложения — цепочка из двух: [родитель, вложение], а не [вложение] и не [родитель, родитель]', async (t) => {
  const { server, store } = await mailSetup(t)
  await store.openPreview(MAIL_TARGET)
  await store.openAttachment(MAIL_TARGET, 5)
  await store.openAttachment(MAIL_TARGET, 0)
  assert.deepEqual(server.previewCalls().map((b) => b.member), [[], [5], [5, 0]])
  assert.deepEqual(viewOf(store, MAIL_TARGET).member, [5, 0])
  await flush()
  assert.deepEqual(server.pageCalls().map((b) => [b.member, b.page]), [[[5, 0], 1]]) // картинка — вложения вложения
})

test('глубже двух уровней вложения не раскрываются: запроса нет, стопка не растёт', async (t) => {
  const { server, store } = await mailSetup(t)
  await store.openPreview(MAIL_TARGET)
  await store.openAttachment(MAIL_TARGET, 5)
  await store.openAttachment(MAIL_TARGET, 7)
  assert.deepEqual(viewOf(store, MAIL_TARGET).member, [5, 7])
  const before = server.previewCalls().length
  await store.openAttachment(MAIL_TARGET, 0) // третий уровень
  assert.equal(server.previewCalls().length, before)
  assert.deepEqual(viewOf(store, MAIL_TARGET).member, [5, 7])
  assert.equal(entryOf(store, MAIL_TARGET).stack.length, 3)
  assert.ok(server.previewCalls().every((b) => b.member.length <= 2))
})

test('«Back to message»: возврат на уровень выше без нового запроса; адрес картинки вложения освобождён', async (t) => {
  const { server, store, urls } = await mailSetup(t)
  await store.openPreview(MAIL_TARGET)
  await store.openAttachment(MAIL_TARGET, 3)
  await flush()
  assert.equal(urls.live.size, 1)
  store.backToMessage(MAIL_TARGET)
  assert.equal(urls.live.size, 0)
  assert.deepEqual(viewOf(store, MAIL_TARGET).member, [])
  assert.equal(viewOf(store, MAIL_TARGET).data.meta.name, 'mail.eml')
  assert.equal(server.previewCalls().length, 2) // письмо из памяти
  store.backToMessage(MAIL_TARGET) // выше некуда
  assert.deepEqual(viewOf(store, MAIL_TARGET).member, [])
  assert.equal(entryOf(store, MAIL_TARGET).stack.length, 1)
})

test('из вложения вложения — по одному уровню назад; повторное раскрытие вложения спрашивает заново', async (t) => {
  const { server, store } = await mailSetup(t)
  await store.openPreview(MAIL_TARGET)
  await store.openAttachment(MAIL_TARGET, 5)
  await store.openAttachment(MAIL_TARGET, 0)
  store.backToMessage(MAIL_TARGET)
  assert.deepEqual(viewOf(store, MAIL_TARGET).member, [5])
  store.backToMessage(MAIL_TARGET)
  assert.deepEqual(viewOf(store, MAIL_TARGET).member, [])
  await store.openAttachment(MAIL_TARGET, 3)
  assert.deepEqual(server.previewCalls().map((b) => b.member), [[], [5], [5, 0], [3]])
})

test('переход от вложения к другому вложению оставляет живым только один адрес', async (t) => {
  const { store, urls } = await mailSetup(t)
  await store.openPreview(MAIL_TARGET)
  await store.openAttachment(MAIL_TARGET, 3)
  await flush()
  store.backToMessage(MAIL_TARGET)
  await store.openAttachment(MAIL_TARGET, 5)
  await store.openAttachment(MAIL_TARGET, 0)
  await flush()
  assert.equal(urls.live.size, 1)
  store.closePreview(MAIL_TARGET)
  assert.equal(urls.live.size, 0)
})

test('вложение открывается только из готового письма и только по целому от 0', async (t) => {
  const { server, store } = await mailSetup(t, { ...withPreview(textPreview('a.pdf', 'x')), ...withPreview(RENDERING, `очередь/${BATCH}/slow.eml`) })
  await store.openPreview(TARGET) // это текст, а не письмо
  await store.openAttachment(TARGET, 0)
  assert.equal(server.previewCalls().length, 1)
  await store.openPreview(MAIL_TARGET)
  for (const bad of [-1, 1.5, '3', null, undefined, NaN, Infinity, 2 ** 53, [3]]) await store.openAttachment(MAIL_TARGET, bad)
  assert.equal(server.previewCalls().length, 2)
  const pending = { area: 'queue', path: `очередь/${BATCH}/slow.eml` }
  await store.openPreview(pending) // письмо ещё рисуется
  const calls = server.previewCalls().length
  await store.openAttachment(pending, 0)
  assert.equal(server.previewCalls().length, calls)
  await store.openAttachment({ area: 'queue', path: 'очередь/нет-такого' }, 0) // просмотр не открыт
  assert.equal(server.previewCalls().length, calls)
})

// ── хранилище: rendering ────────────────────────────────────────
test('rendering: повтор раз в 2 с, пока не готово; таймер один, и живёт только пока просмотр ждёт', async (t) => {
  const answers = [RENDERING, RENDERING, RENDERING]
  const { server, store, clock } = await setup(t, { previews: withPreview(() => answers.shift() ?? pagesPreview('a.pdf', 2)) })
  await store.openPreview(TARGET)
  assert.equal(viewOf(store).phase, 'rendering')
  assert.equal(viewOf(store).data, null)
  assert.deepEqual(clock.waits(), [2000])
  await clock.advance(1999)
  assert.equal(server.previewCalls().length, 1)
  await clock.advance(1)
  assert.equal(server.previewCalls().length, 2)
  assert.deepEqual(clock.waits(), [2000])
  await clock.advance(2000)
  assert.equal(server.previewCalls().length, 3)
  await clock.advance(2000) // четвёртый ответ — готово
  assert.equal(server.previewCalls().length, 4)
  assert.equal(viewOf(store).phase, 'ready')
  assert.equal(viewOf(store).data.kind, 'pages')
  assert.deepEqual(clock.waits(), []) // дальше таймера нет
  await clock.advance(60000)
  assert.equal(server.previewCalls().length, 4)
  assert.ok(server.previewCalls().every((b) => b.path === FILE && b.member.length === 0))
})

test('rendering без конца: не дольше трёх минут — 91 запрос (первый и 90 повторов), потом состояние late и тишина', async (t) => {
  const { server, store, clock } = await setup(t, { previews: withPreview(() => RENDERING) })
  await store.openPreview(TARGET)
  await clock.advance(179000)
  assert.equal(server.previewCalls().length, 90)
  assert.equal(viewOf(store).phase, 'rendering')
  assert.equal(clock.waits().length, 1)
  await clock.advance(1000) // 180 с
  assert.equal(server.previewCalls().length, 91)
  assert.equal(viewOf(store).phase, 'late')
  assert.deepEqual(clock.waits(), [])
  await clock.advance(30 * 60000)
  assert.equal(server.previewCalls().length, 91)
  assert.deepEqual(clock.waits(), [])
})

test('«Try again» после late: один новый запрос, отсчёт трёх минут начинается заново', async (t) => {
  const { server, store, clock } = await setup(t, { previews: withPreview(() => RENDERING) })
  await store.openPreview(TARGET)
  await clock.advance(180000)
  assert.equal(viewOf(store).phase, 'late')
  const before = server.previewCalls().length
  await store.retryPreview(TARGET)
  assert.equal(server.previewCalls().length, before + 1)
  assert.equal(viewOf(store).phase, 'rendering')
  await clock.advance(180000)
  assert.equal(server.previewCalls().length, before + 91)
  assert.equal(viewOf(store).phase, 'late')
  server.previews[KEY] = pagesPreview('a.pdf', 3)
  await store.retryPreview(TARGET)
  assert.equal(viewOf(store).phase, 'ready')
  assert.deepEqual(clock.waits(), [])
})

test('повторный запрос во время rendering не показывает «загрузку»: вид остаётся в rendering, пока ответа нет', async (t) => {
  const { server, store, clock } = await setup(t, { previews: withPreview(() => RENDERING) })
  await store.openPreview(TARGET)
  const gate = deferred()
  server.on['POST ' + SHOW] = async () => { await gate.promise; return reply(200, RENDERING) }
  await clock.advance(2000)
  assert.equal(viewOf(store).phase, 'rendering') // повтор в полёте, а на экране по-прежнему «Rendering…»
  gate.resolve()
  await flush()
  assert.equal(viewOf(store).phase, 'rendering')
  assert.deepEqual(clock.waits(), [2000])
})

test('повтор, пока просмотр не в late и не в сбое, ничего не запрашивает', async (t) => {
  const { server, store } = await setup(t, { previews: withPreview(textPreview('a.pdf', 'x')) })
  await store.openPreview(TARGET)
  await store.retryPreview(TARGET)
  assert.equal(server.previewCalls().length, 1)
  assert.deepEqual(server.pageCalls(), [])
})

test('закрытие просмотра во время rendering гасит таймер: больше ни одного запроса', async (t) => {
  const { server, store, clock } = await setup(t, { previews: withPreview(() => RENDERING) })
  await store.openPreview(TARGET)
  await clock.advance(4000)
  assert.equal(server.previewCalls().length, 3)
  store.closePreview(TARGET)
  assert.deepEqual(clock.waits(), [])
  await clock.advance(60000)
  assert.equal(server.previewCalls().length, 3)
  assert.deepEqual(store.getSnapshot().previews, {})
})

test('возврат из вложения, которое ещё рисуется, гасит его таймер; письмо остаётся', async (t) => {
  const { server, store, clock } = await mailSetup(t, withPreview(() => RENDERING, MAIL_FILE, 'queue', [3]))
  await store.openPreview(MAIL_TARGET)
  await store.openAttachment(MAIL_TARGET, 3)
  assert.equal(viewOf(store, MAIL_TARGET).phase, 'rendering')
  assert.deepEqual(clock.waits(), [2000])
  store.backToMessage(MAIL_TARGET)
  assert.deepEqual(clock.waits(), [])
  const calls = server.previewCalls().length
  await clock.advance(60000)
  assert.equal(server.previewCalls().length, calls)
  assert.equal(viewOf(store, MAIL_TARGET).phase, 'ready')
})

test('ответ rendering, пришедший после закрытия, таймера не заводит', async (t) => {
  const { server, store, clock } = await setup(t, { previews: withPreview(() => RENDERING) })
  const gate = deferred()
  server.on['POST ' + SHOW] = async () => { await gate.promise; return reply(200, RENDERING) }
  const opened = store.openPreview(TARGET)
  await flush()
  store.closePreview(TARGET)
  gate.resolve()
  await opened
  assert.deepEqual(clock.waits(), [])
})

test('сбой во время повторов: просмотр в состоянии failed, повторов больше нет', async (t) => {
  const { server, store, clock } = await setup(t, { previews: withPreview(() => RENDERING) })
  await store.openPreview(TARGET)
  server.offline = true
  await clock.advance(2000)
  assert.equal(viewOf(store).phase, 'failed')
  assert.equal(viewOf(store).error.kind, 'offline')
  assert.deepEqual(clock.waits(), [])
  server.offline = false
  server.previews[KEY] = textPreview('a.pdf', 'готово')
  await store.retryPreview(TARGET)
  assert.equal(viewOf(store).phase, 'ready')
})

// ── хранилище: сбои ─────────────────────────────────────────────
test('отказ описания: состояние failed с сообщением системы; связь — offline; ответ не того вида — failed без исключения', async (t) => {
  const { server, store } = await setup(t)
  server.on['POST ' + SHOW] = async () => reply(409, { error: REFUSAL })
  await store.openPreview(TARGET)
  assert.equal(viewOf(store).phase, 'failed')
  assert.equal(viewOf(store).error.kind, 'refused')
  assert.deepEqual(viewOf(store).error.refusal, REFUSAL)
  assert.equal(viewOf(store).data, null)
  store.closePreview(TARGET)
  server.on['POST ' + SHOW] = async () => { throw new TypeError('Failed to fetch') }
  await store.openPreview(TARGET)
  assert.equal(viewOf(store).error.kind, 'offline')
  store.closePreview(TARGET)
  for (const odd of [null, [], 'text', 7, { meta: {} }, { kind: 5 }]) {
    server.on['POST ' + SHOW] = async () => reply(200, odd)
    await store.openPreview(TARGET)
    assert.equal(viewOf(store).phase, 'failed', JSON.stringify(odd))
    assert.ok(viewOf(store).error)
    store.closePreview(TARGET)
  }
  assert.equal(store.getSnapshot().error, null) // вкладку целиком сбой просмотра не задевает
})

test('сбой страницы: описание остаётся, у картинки — ошибка; «Try again» просит только эту страницу', async (t) => {
  const { server, store, urls } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 5)) })
  server.on['POST ' + PAGE] = async () => reply(409, { error: REFUSAL })
  await store.openPreview(TARGET)
  await flush()
  assert.equal(viewOf(store).phase, 'ready')
  assert.equal(viewOf(store).image.phase, 'failed')
  assert.deepEqual(viewOf(store).image.error.refusal, REFUSAL)
  assert.equal(viewOf(store).image.url, null)
  assert.equal(store.getSnapshot().error, null)
  delete server.on['POST ' + PAGE]
  await store.retryPreview(TARGET)
  assert.equal(viewOf(store).image.phase, 'ready')
  assert.equal(server.previewCalls().length, 1) // описание заново не спрашивали
  assert.deepEqual(server.pageCalls().map((b) => b.page), [1, 1])
  assert.equal(urls.live.size, 1)
})

test('адрес объекта не создался (createObjectURL бросил): ошибка у картинки, а не исключение', async (t) => {
  const { store, urls } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 2)) })
  URL.createObjectURL = () => { throw new Error('quota') }
  await store.openPreview(TARGET)
  await flush()
  assert.equal(viewOf(store).image.phase, 'failed')
  assert.equal(urls.live.size, 0)
})

test('негодная цель не открывается: чужая область, пустой и нестроковый путь — ни запроса, ни записи', async (t) => {
  const { server, store } = await setup(t)
  for (const bad of [{ area: 'returned', path: 'x/y' }, { area: 'deleted', path: 'x/y' }, { area: 'inbox', path: 'x/y' }, { area: '', path: 'x' },
    { area: 'queue', path: '' }, { area: 'queue', path: 5 }, { area: 'queue' }, { area: undefined, path: 'x' }]) {
    await store.openPreview(bad)
    store.closePreview(bad)
  }
  assert.deepEqual(server.calls, [])
  assert.deepEqual(store.getSnapshot().previews, {})
})

test('снимок просмотров неизменяем: после листания и закрытия прежний снимок остаётся прежним', async (t) => {
  const { store } = await setup(t, { previews: withPreview(pagesPreview('a.pdf', 5)) })
  await store.openPreview(TARGET)
  await flush()
  const before = store.getSnapshot()
  const frozen = JSON.stringify(before.previews)
  await store.showPage(TARGET, 2)
  store.closePreview(TARGET)
  assert.notEqual(store.getSnapshot(), before)
  assert.equal(JSON.stringify(before.previews), frozen)
  assert.equal(before.previews[Object.keys(before.previews)[0]].stack[0].page, 1)
})

test('открытие и закрытие просмотра уведомляет подписчиков; чужие части снимка остаются теми же объектами', async (t) => {
  const { store } = await setup(t, { previews: withPreview(textPreview('a.pdf', 'x')) })
  const first = store.getSnapshot()
  let notified = 0
  store.subscribe(() => { notified += 1 })
  await store.openPreview(TARGET)
  assert.ok(notified >= 2) // вид «loading» и готовое описание
  assert.equal(store.getSnapshot().history, first.history)
  const mid = notified
  store.closePreview(TARGET)
  assert.ok(notified > mid)
})

// ── на экране: строка «Needs decision» ──────────────────────────
test('строка списка раскрывается нажатием на имя: просмотр подгружается при раскрытии, а не раньше', async (t) => {
  const { app, server } = await boot(t, { queue: [queued()], previews: withPreview(textPreview('a.pdf', 'hello world')) })
  const toggle = toggleOf(app, FILE)
  assert.ok(toggle, 'у имени строки есть кнопка раскрытия')
  assert.equal(toggle.props['aria-expanded'], false)
  assert.equal(areaOf(app), null)
  assert.deepEqual(server.previewCalls(), []) // свёрнутая строка запросов не шлёт
  await app.click(toggle)
  assert.equal(toggleOf(app, FILE).props['aria-expanded'], true)
  assert.deepEqual(server.previewCalls(), [{ area: 'queue', path: FILE, member: [] }])
  assert.ok(text(areaOf(app)).includes('hello world'))
  await app.click(toggleOf(app, FILE))
  assert.equal(areaOf(app), null)
  assert.equal(toggleOf(app, FILE).props['aria-expanded'], false)
})

test('строка карантина просит просмотр своей области', async (t) => {
  const row = item('quarantine', 'b.exe')
  const { app, server } = await boot(t, { quarantine: [row], previews: withPreview(textPreview('b.exe', 'x'), row.path, 'quarantine') })
  await expand(app, row.path)
  assert.deepEqual(server.previewCalls(), [{ area: 'quarantine', path: row.path, member: [] }])
})

test('имя строки открывается с клавиатуры: Enter и пробел, другие клавиши — нет', async (t) => {
  const { app, server } = await boot(t, { queue: [queued()], previews: withPreview(textPreview('a.pdf', 'x')) })
  const press = async (key) => {
    let prevented = false
    toggleOf(app, FILE).props.onKeyDown({ key, preventDefault: () => { prevented = true } })
    await app.settle()
    return prevented
  }
  assert.equal(toggleOf(app, FILE).props.tabIndex, 0)
  assert.equal(await press('Tab'), false)
  assert.equal(await press('a'), false)
  assert.equal(server.previewCalls().length, 0)
  assert.equal(await press('Enter'), true)
  assert.equal(toggleOf(app, FILE).props['aria-expanded'], true)
  assert.equal(await press(' '), true)
  assert.equal(toggleOf(app, FILE).props['aria-expanded'], false)
})

test('раскрытая строка показывает находки и сведения из списка, а под ними — просмотр', async (t) => {
  const row = { ...queued(), reason: 'Причина-из-списка', checked_by: 'model-test', date: '2026-09-30' }
  const { app } = await boot(t, { queue: [row], previews: withPreview(textPreview('a.pdf', 'body')) })
  await expand(app, FILE)
  const t1 = text(rowOf(app, FILE))
  for (const part of ['High · Prompt injection', 'ignore all previous instructions', 'Checked by: model-test', 'Document date: 30.09.2026', 'Size: 2 KB', 'Path: ' + FILE]) {
    assert.ok(t1.includes(part), `${part}: ${t1}`)
  }
  assert.ok(areaIn(rowOf(app, FILE)), 'просмотр внутри раскрытой строки')
})

test('раскрытая строка очереди показывает сообщения находки, причины и примечаний по словарю: выдержка в кавычках, русского на экране нет', async (t) => {
  const row = {
    ...queued(), reason: 'архив не принят целиком', reason_msg: messageOf('reason.archive_rejected', {}, 'архив не принят целиком'),
    notes: ['внутри был исполняемый файл: run.exe'], notes_msg: [messageOf('note.executable_inside', { name: 'run.exe' }, 'внутри был исполняемый файл: run.exe')],
    findings: [{ rule: 'prompt_injection', level: 'HIGH', where: 'строка 2', quote: 'отмена прежних указаний: ignore all previous instructions',
      msg: messageOf('finding.prompt_injection', { kind: 'cancel_instructions', quoted: true, excerpt: 'ignore all previous instructions' }, 'отмена прежних указаний'),
      where_msg: messageOf('where.line', { line: 2 }, 'строка 2') }],
  }
  const { app } = await boot(t, { queue: [row], previews: withPreview(textPreview('a.pdf', 'body')) })
  await expand(app, FILE)
  const t1 = text(rowOf(app, FILE)).replace('Path: ' + FILE, '')
  for (const part of ['High · Prompt injection', 'Attempt to cancel earlier instructions “ignore all previous instructions”', 'Line 2',
    'Reason: The archive was rejected as a whole', 'Notes: An executable file was inside: run.exe'])
    assert.ok(t1.includes(part), `${part}: ${t1}`)
  assert.ok(!CYRILLIC.test(t1), t1)
})

test('кнопки решений у раскрытой строки остаются на месте и работают; решение убирает строку вместе с просмотром', async (t) => {
  const { app, server, urls } = await boot(t, { queue: [queued()], previews: withPreview(pagesPreview('a.pdf', 3)) })
  await expand(app, FILE)
  assert.equal(urls.live.size, 1)
  const accept = find(rowOf(app, FILE), (n) => n.type === 'button' && text(n) === 'Accept')
  assert.ok(enabled(accept))
  await app.click(accept)
  assert.deepEqual(server.posts(QUEUE).map((c) => c.body), [{ paths: [FILE], action: 'accept' }])
  assert.equal(rowOf(app, FILE), null)
  assert.equal(urls.live.size, 0) // ушла строка — освобождён адрес страницы
  assert.equal(server.pageCalls().length, 1)
})

test('кнопки строки и просмотр не зависят друг от друга: отказ просмотра не мешает решению', async (t) => {
  const { app, server } = await boot(t, { queue: [queued()], on: { ['POST ' + SHOW]: async () => reply(409, { error: REFUSAL }) } })
  await expand(app, FILE)
  assert.match(text(areaOf(app)), /Preview worker failed/)
  assert.equal(alertsIn(app.tree()).length, 1) // над вкладкой ошибки нет: одна, внутри области просмотра
  assert.equal(alertsIn(areaOf(app)).length, 1)
  await app.click(find(rowOf(app, FILE), (n) => n.type === 'button' && text(n) === 'Quarantine'))
  assert.equal(server.posts(QUEUE).length, 1)
})

test('закрытие тела вкладки освобождает адреса просмотров', async (t) => {
  const { app, urls } = await boot(t, { queue: [queued()], previews: withPreview(pagesPreview('a.pdf', 3)) })
  await expand(app, FILE)
  assert.equal(urls.live.size, 1)
  app.unmount()
  assert.equal(urls.live.size, 0)
})

test('два тела вкладки с одним раскрытым файлом: запрос один; закрытие одного тела просмотр второго не снимает', async (t) => {
  const { app, server, urls, mount, h, body } = await boot(t, { queue: [queued()], previews: withPreview(pagesPreview('a.pdf', 3)) })
  const second = mount(h(body, {}))
  await second.settle()
  await expand(app, FILE)
  await expand(second, FILE)
  assert.equal(server.previewCalls().length, 1)
  assert.equal(urls.live.size, 1)
  app.unmount()
  assert.equal(urls.live.size, 1)
  assert.ok(imgIn(areaOf(second)))
  second.unmount()
  assert.equal(urls.live.size, 0)
})

// ── на экране: виды просмотра ───────────────────────────────────
test('сведения о файле показаны всегда: имя, тип, размер, sha256, пачка, архив и путь внутри', async (t) => {
  const meta = metaOf('a.pdf', { type: 'application/pdf', size: 5 * 1024 * 1024, sha256: '0123456789abcdef'.repeat(4), batch: BATCH, origin: { archive: 'bundle.zip', inner: 'docs/a.pdf' } })
  const { app } = await boot(t, { queue: [queued()], previews: withPreview({ ...textPreview('a.pdf', 'x'), meta }) })
  await expand(app, FILE)
  const t1 = text(areaOf(app))
  for (const part of ['Name: a.pdf', 'Type: application/pdf', 'Size: 5.0 MB', 'SHA-256: ' + '0123456789abcdef'.repeat(4), 'Batch: ' + BATCH,
    'Archive: bundle.zip', 'Path inside the archive: docs/a.pdf']) assert.ok(t1.includes(part), `${part}: ${t1}`)
})

test('сведения без происхождения и с пропусками: прочерки, а не «null» и «undefined»; строк про архив нет вовсе', async (t) => {
  const { app } = await boot(t, { queue: [queued()], previews: withPreview({ kind: 'text', meta: { name: 'a.pdf', origin: null }, text: 'x' }) })
  await expand(app, FILE)
  const t1 = text(areaOf(app))
  for (const part of ['Type: —', 'SHA-256: —', 'Batch: —']) assert.ok(t1.includes(part), `${part}: ${t1}`)
  assert.ok(!t1.includes('Archive:') && !t1.includes('Path inside the archive'), t1) // пустого происхождения строк нет
  assert.ok(!/null|undefined|NaN/.test(t1), t1)
  const bare = await boot(t, { queue: [queued()], previews: withPreview({ kind: 'text', text: 'x' }) })
  await expand(bare.app, FILE)
  assert.ok(!/null|undefined|NaN/.test(text(areaOf(bare.app))))
})

test('kind text: текст как есть в элементе pre, признак обрезки показан только когда он есть', async (t) => {
  const body = 'line 1\n  indented line 2\n\tline 3'
  const cut = await boot(t, { queue: [queued()], previews: withPreview(textPreview('a.pdf', body, { truncated: true })) })
  await expand(cut.app, FILE)
  const pre = find(areaOf(cut.app), (n) => n.type === 'pre')
  assert.equal(text(pre), body)
  assert.match(text(areaOf(cut.app)), /The text is truncated/)
  const whole = await boot(t, { queue: [queued()], previews: withPreview(textPreview('a.pdf', body)) })
  await expand(whole.app, FILE)
  assert.equal(text(find(areaOf(whole.app), (n) => n.type === 'pre')), body)
  assert.ok(!text(areaOf(whole.app)).includes('truncated'))
})

test('текст просмотра рисуется моноширинным шрифтом из стилей плагина', async (t) => {
  const saved = globalThis.document
  const tags = []
  globalThis.document = { head: { appendChild: (tag) => tags.push(tag) }, createElement: (name) => ({ name, dataset: {}, textContent: '' }), querySelector: () => null }
  try {
    const { app } = await boot(t, { queue: [queued()], previews: withPreview(textPreview('a.pdf', 'x')) })
    await expand(app, FILE)
    const pre = find(areaOf(app), (n) => n.type === 'pre')
    assert.match(pre.props.className, /ba-preview-text/)
    const css = tags.map((tag) => tag.textContent).join('')
    assert.match(css, /\.ba-preview-text\{[^}]*monospace/)
    assert.match(css, /\.ba-preview-text\{[^}]*white-space:pre-wrap/) // строки как есть, без потери переводов
  } finally {
    if (saved === undefined) delete globalThis.document
    else globalThis.document = saved
  }
})

test('kind pages: картинка страницы, «Page N of M», «Previous» и «Next», масштаб; листание запрашивает только нужную страницу', async (t) => {
  const { app, server, urls } = await boot(t, { queue: [queued()], previews: withPreview(pagesPreview('a.pdf', 3)) })
  await expand(app, FILE)
  let area = areaOf(app)
  assert.ok(text(area).includes('Page 1 of 3'), text(area))
  assert.equal(enabled(inArea(area, 'Previous')), false)
  assert.equal(enabled(inArea(area, 'Next')), true)
  assert.equal(imgIn(area).props.src, [...urls.live][0])
  assert.equal(urls.pageOf(imgIn(area).props.src), 1)
  assert.deepEqual(['Fit width', '100%', '150%'].map((label) => inArea(area, label)?.props['aria-pressed']), [true, false, false])
  await app.click(inArea(areaOf(app), 'Next'))
  area = areaOf(app)
  assert.ok(text(area).includes('Page 2 of 3'), text(area))
  assert.equal(urls.pageOf(imgIn(area).props.src), 2)
  assert.equal(enabled(inArea(area, 'Previous')), true)
  await app.click(inArea(areaOf(app), 'Next'))
  area = areaOf(app)
  assert.ok(text(area).includes('Page 3 of 3'))
  assert.equal(enabled(inArea(area, 'Next')), false)
  assert.equal(await app.click(inArea(area, 'Next')), false) // дальше последней не листается
  await app.click(inArea(areaOf(app), 'Previous'))
  assert.ok(text(areaOf(app)).includes('Page 2 of 3'))
  assert.deepEqual(server.pageCalls().map((b) => b.page), [1, 2, 3, 2])
  assert.equal(urls.live.size, 1)
})

test('«Next» несколько раз подряд просит ровно показанные страницы и ни одной лишней', async (t) => {
  const { app, server } = await boot(t, { queue: [queued()], previews: withPreview(pagesPreview('a.pdf', 20)) })
  await expand(app, FILE)
  for (let i = 0; i < 4; i++) await app.click(inArea(areaOf(app), 'Next'))
  assert.deepEqual(server.pageCalls().map((b) => b.page), [1, 2, 3, 4, 5])
  assert.ok(text(areaOf(app)).includes('Page 5 of 20'))
})

test('показаны не все страницы документа: «First N pages shown» и «Page N of M» считает по показанным; все — строки нет', async (t) => {
  const cut = await boot(t, { queue: [queued()], previews: withPreview(pagesPreview('a.pdf', 143, 20)) })
  await expand(cut.app, FILE)
  assert.ok(text(areaOf(cut.app)).includes('First 20 pages shown'), text(areaOf(cut.app)))
  assert.ok(text(areaOf(cut.app)).includes('Page 1 of 20'))
  const whole = await boot(t, { queue: [queued()], previews: withPreview(pagesPreview('a.pdf', 20, 20)) })
  await expand(whole.app, FILE)
  assert.ok(!text(areaOf(whole.app)).includes('pages shown'))
  const small = await boot(t, { queue: [queued()], previews: withPreview({ ...pagesPreview('a.pdf', 5), shown: undefined }) })
  await expand(small.app, FILE)
  assert.ok(text(areaOf(small.app)).includes('Page 1 of 5'))
  assert.ok(!text(areaOf(small.app)).includes('pages shown'))
})

test('масштаб: «Fit width», «100%» и «150%» переключаются, ширина — по натуральной ширине страницы', async (t) => {
  const { app } = await boot(t, { queue: [queued()], previews: withPreview(pagesPreview('a.pdf', 2)) })
  await expand(app, FILE)
  const fit = imgIn(areaOf(app)).props.style
  assert.equal(fit.maxWidth, '100%')
  imgIn(areaOf(app)).props.onLoad({ target: { naturalWidth: 800 } }) // страница загрузилась: ширина известна
  await app.settle()
  await app.click(inArea(areaOf(app), '100%'))
  assert.equal(imgIn(areaOf(app)).props.style.width, '800px')
  assert.equal(inArea(areaOf(app), '100%').props['aria-pressed'], true)
  assert.equal(inArea(areaOf(app), 'Fit width').props['aria-pressed'], false)
  await app.click(inArea(areaOf(app), '150%'))
  assert.equal(imgIn(areaOf(app)).props.style.width, '1200px')
  await app.click(inArea(areaOf(app), 'Fit width'))
  assert.equal(imgIn(areaOf(app)).props.style.maxWidth, '100%')
  assert.equal(imgIn(areaOf(app)).props.style.width, undefined)
})

test('пока страница грузится — «Loading…» вместо картинки; не получилась — ошибка и «Try again», описание цело', async (t) => {
  const { app, server, urls } = await boot(t, { queue: [queued()], previews: withPreview(pagesPreview('a.pdf', 3)) })
  const gate = deferred()
  server.on['POST ' + PAGE] = async (body) => { await gate.promise; return reply(409, { error: REFUSAL }) }
  await expand(app, FILE)
  assert.equal(imgIn(areaOf(app)), null)
  assert.ok(text(areaOf(app)).includes('Loading'))
  assert.ok(text(areaOf(app)).includes('Page 1 of 3'))
  gate.resolve()
  await app.settle()
  assert.equal(alertsIn(areaOf(app)).length, 1)
  assert.match(text(alertsIn(areaOf(app))[0]), /Preview worker failed/)
  assert.ok(text(areaOf(app)).includes('Name: a.pdf')) // сведения на месте
  delete server.on['POST ' + PAGE]
  await app.click(inArea(areaOf(app), 'Try again'))
  assert.ok(imgIn(areaOf(app)))
  assert.equal(alertsIn(areaOf(app)).length, 0)
  assert.equal(urls.live.size, 1)
})

test('kind image: одна картинка без листания; kind media: кадр, длительность и размер кадра', async (t) => {
  const img = await boot(t, { queue: [queued('i.png')], previews: withPreview(imagePreview('i.png'), `очередь/${BATCH}/i.png`) })
  await expand(img.app, `очередь/${BATCH}/i.png`)
  const area = areaOf(img.app, `очередь/${BATCH}/i.png`)
  assert.ok(imgIn(area))
  assert.equal(inArea(area, 'Next'), null)
  assert.ok(!text(area).includes('Page 1'))
  const media = await boot(t, { queue: [queued('m.mp4')], previews: withPreview(mediaPreview('m.mp4'), `очередь/${BATCH}/m.mp4`) })
  await expand(media.app, `очередь/${BATCH}/m.mp4`)
  const m = areaOf(media.app, `очередь/${BATCH}/m.mp4`)
  assert.ok(imgIn(m))
  assert.ok(text(m).includes('Duration: 1 min 5 s'), text(m))
  assert.ok(text(m).includes('1920 × 1080'), text(m))
  assert.equal(inArea(m, 'Next'), null)
})

test('media без сведений о кадре и без длительности не рисует «null» и «NaN»', async (t) => {
  const { app } = await boot(t, { queue: [queued()], previews: withPreview({ ...mediaPreview('a.pdf'), media: { seconds: null } }) })
  await expand(app, FILE)
  assert.ok(!/null|NaN|undefined/.test(text(areaOf(app))), text(areaOf(app)))
})

test('kind listing: таблица имён и размеров; признак обрезки', async (t) => {
  const listing = [{ name: 'docs/a.txt', size: 1536 }, { name: 'b.bin', size: 3 * 1024 * 1024 }, { name: 'c', size: null }]
  const { app } = await boot(t, { queue: [queued()], previews: withPreview(listingPreview('a.pdf', listing, { truncated: true })) })
  await expand(app, FILE)
  const rows = collect(areaOf(app), (n) => n.type === 'tr').map((r) => r.props.children.map(text))
  assert.deepEqual(rows.slice(1), [['docs/a.txt', '2 KB'], ['b.bin', '3.0 MB'], ['c', '—']])
  assert.match(text(areaOf(app)), /The list is truncated: only the first 3 entries are shown/)
  const whole = await boot(t, { queue: [queued()], previews: withPreview(listingPreview('a.pdf', listing)) })
  await expand(whole.app, FILE)
  assert.ok(!text(areaOf(whole.app)).includes('truncated'))
})

test('kind none: сведения и пояснение по словарю; неизвестный код — исходный текст; без пояснения — общая фраза', async (t) => {
  const known = await boot(t, { queue: [queued()], previews: withPreview(nonePreview('a.pdf', { code: 'llm_finding', args: { model: 'm1', page: 2, why: 'odd' }, text: 'модель m1: странно' })) })
  await expand(known.app, FILE)
  assert.ok(text(areaOf(known.app)).includes('The model m1 reported: odd'), text(areaOf(known.app)))
  assert.ok(!text(areaOf(known.app)).includes('модель m1'))
  assert.ok(text(areaOf(known.app)).includes('Name: a.pdf'))
  const unknown = await boot(t, { queue: [queued()], previews: withPreview(nonePreview('a.pdf', { code: 'code_from_the_future', args: {}, text: 'Нужен преобразователь' })) })
  await expand(unknown.app, FILE)
  assert.ok(text(areaOf(unknown.app)).includes('Нужен преобразователь'))
  const missing = await boot(t, { queue: [queued()], previews: withPreview({ kind: 'none', meta: metaOf('a.pdf') }) })
  await expand(missing.app, FILE)
  assert.ok(text(areaOf(missing.app)).includes('No preview is available'), text(areaOf(missing.app)))
  assert.equal(imgIn(areaOf(missing.app)), null)
})

test('вид, которого плагин не знает, показывается как «none»: сведения и пояснение, без падения', async (t) => {
  const { app } = await boot(t, { queue: [queued()], previews: withPreview({ kind: 'hologram', meta: metaOf('a.pdf'), note: { code: null, args: {}, text: 'Holograms are not supported' } }) })
  await expand(app, FILE)
  assert.ok(text(areaOf(app)).includes('Holograms are not supported'))
  assert.ok(text(areaOf(app)).includes('Name: a.pdf'))
})

// ── на экране: письмо ───────────────────────────────────────────
const MAIL_ROW = item('queue', 'mail.eml')
const mailBoot = (t, extra = {}) => boot(t, {
  queue: [MAIL_ROW],
  previews: {
    ...withPreview(mailPreview('mail.eml', MAIL), MAIL_ROW.path),
    ...withPreview(pagesPreview('a.pdf', 4), MAIL_ROW.path, 'queue', [3]),
    ...withPreview(mailPreview('inner.eml', [attachmentOf('deep.pdf', 0), attachmentOf('deep.eml', 7, { type: 'eml' })]), MAIL_ROW.path, 'queue', [5]),
    ...withPreview(pagesPreview('deep.pdf', 2), MAIL_ROW.path, 'queue', [5, 0]),
    ...withPreview(mailPreview('deep.eml', [attachmentOf('too-deep.pdf', 0), attachmentOf('also-deep.txt', 1, { type: 'txt' })]), MAIL_ROW.path, 'queue', [5, 7]),
    ...extra,
  },
})
const mailArea = (app) => areaOf(app, MAIL_ROW.path)

test('kind mail: от кого, кому, копия, дата, тема, текст письма и список вложений (имя, тип, размер)', async (t) => {
  const { app } = await mailBoot(t)
  await expand(app, MAIL_ROW.path)
  const t1 = text(mailArea(app))
  for (const part of ['From: ann@example.test', 'To: bob@example.test, cy@example.test', 'Cc: —', 'Date: 30.09.2026 08:15 UTC', 'Subject: Quarterly numbers']) {
    assert.ok(t1.includes(part), `${part}: ${t1}`)
  }
  assert.equal(text(find(mailArea(app), (n) => n.type === 'pre')), 'Hello,\nsee the attachments.')
  const rows = collect(mailArea(app), (n) => n.type === 'tr' && n.props['data-attachment'] !== undefined).map((r) => r.props.children.map(text))
  assert.deepEqual(rows, [['a.pdf', 'pdf', '2 KB'], ['inner.eml', 'eml', '2 KB']])
})

test('письмо: адресаты строкой или списком, копия — когда есть, пустые поля — прочерк; вложений нет — таблицы нет', async (t) => {
  const mail = { from: 'a@example.test', to: 'b@example.test', cc: ['c@example.test', 'd@example.test'], date: null, subject: '', text: '', attachments: [] }
  const { app } = await mailBoot(t, withPreview({ ...mailPreview('mail.eml'), mail }, MAIL_ROW.path))
  await expand(app, MAIL_ROW.path)
  const t1 = text(mailArea(app))
  for (const part of ['To: b@example.test', 'Cc: c@example.test, d@example.test', 'Date: —', 'Subject: —']) assert.ok(t1.includes(part), `${part}: ${t1}`)
  assert.equal(collect(mailArea(app), (n) => n.type === 'tr' && n.props['data-attachment'] !== undefined).length, 0)
  assert.ok(!/null|undefined|NaN/.test(t1), t1)
})

test('щелчок по вложению открывает его тем же просмотром со строкой «Back to message»; «Back» возвращает письмо', async (t) => {
  const { app, server } = await mailBoot(t)
  await expand(app, MAIL_ROW.path)
  assert.equal(inArea(mailArea(app), 'Back to message'), null) // на самом письме возврата нет
  await app.click(inArea(mailArea(app), 'a.pdf'))
  assert.deepEqual(server.previewCalls().map((b) => b.member), [[], [3]])
  assert.ok(inArea(mailArea(app), 'Back to message'))
  assert.ok(text(mailArea(app)).includes('Name: a.pdf'))
  assert.ok(text(mailArea(app)).includes('Page 1 of 4'))
  assert.ok(!text(mailArea(app)).includes('Subject: Quarterly numbers'))
  assert.deepEqual(server.pageCalls().map((b) => [b.member, b.page]), [[[3], 1]])
  await app.click(inArea(mailArea(app), 'Back to message'))
  assert.equal(inArea(mailArea(app), 'Back to message'), null)
  assert.ok(text(mailArea(app)).includes('Subject: Quarterly numbers'))
  assert.equal(server.previewCalls().length, 2) // письмо взято из памяти
})

test('вложение с member, не равным номеру строки, открывается с настоящим member; вложение во вложении — цепочка из двух', async (t) => {
  const { app, server } = await mailBoot(t)
  await expand(app, MAIL_ROW.path)
  await app.click(inArea(mailArea(app), 'inner.eml')) // вторая строка, member 5
  await app.click(inArea(mailArea(app), 'deep.pdf')) // его вложение с member 0
  assert.deepEqual(server.previewCalls().map((b) => b.member), [[], [5], [5, 0]])
  assert.deepEqual(server.pageCalls().map((b) => b.member), [[5, 0]])
  await app.click(inArea(mailArea(app), 'Back to message'))
  assert.ok(text(mailArea(app)).includes('Name: inner.eml'))
  await app.click(inArea(mailArea(app), 'Back to message'))
  assert.ok(text(mailArea(app)).includes('Name: mail.eml'))
})

test('на втором уровне вложения показаны списком без раскрытия: ни кнопок, ни запросов', async (t) => {
  const { app, server } = await mailBoot(t)
  await expand(app, MAIL_ROW.path)
  await app.click(inArea(mailArea(app), 'inner.eml'))
  await app.click(inArea(mailArea(app), 'deep.eml')) // письмо во вложении письма: member [5, 7]
  const area = mailArea(app)
  assert.ok(text(area).includes('too-deep.pdf') && text(area).includes('also-deep.txt'), text(area))
  assert.equal(inArea(area, 'too-deep.pdf'), null)
  assert.equal(inArea(area, 'also-deep.txt'), null)
  assert.equal(collect(area, (n) => n.props?.['data-attachment'] !== undefined && find(n, (x) => x.type === 'button')).length, 0)
  assert.ok(text(area).includes('cannot be opened'))
  assert.deepEqual(collect(area, (n) => n.type === 'button').map(text), ['Back to message']) // единственная кнопка — возврат
  assert.ok(server.previewCalls().every((b) => b.member.length <= 2))
})

test('на первом и втором уровне вложения — кнопки; у вложения без member или с негодным — просто строка', async (t) => {
  const odd = [attachmentOf('ok.pdf', 3), attachmentOf('no-member.pdf', undefined), attachmentOf('negative.pdf', -1), attachmentOf('fraction.pdf', 1.5), attachmentOf('text-member.pdf', '2')]
  const { app } = await mailBoot(t, withPreview(mailPreview('mail.eml', odd), MAIL_ROW.path))
  await expand(app, MAIL_ROW.path)
  assert.ok(inArea(mailArea(app), 'ok.pdf'))
  for (const name of ['no-member.pdf', 'negative.pdf', 'fraction.pdf', 'text-member.pdf']) {
    assert.equal(inArea(mailArea(app), name), null, name)
    assert.ok(text(mailArea(app)).includes(name), name)
  }
})

test('вложение, которое ещё рисуется: «Rendering…» и возврат к письму гасит повторы', async (t) => {
  const { app, clock, server } = await mailBoot(t, withPreview(() => RENDERING, MAIL_ROW.path, 'queue', [3]))
  await expand(app, MAIL_ROW.path)
  await app.click(inArea(mailArea(app), 'a.pdf'))
  assert.ok(text(mailArea(app)).includes('Rendering…'))
  assert.ok(inArea(mailArea(app), 'Back to message'))
  await app.click(inArea(mailArea(app), 'Back to message'))
  const calls = server.previewCalls().length
  await clock.advance(60000)
  assert.equal(server.previewCalls().length, calls)
})

test('отказ вложения показан в области просмотра, возврат к письму остаётся возможным', async (t) => {
  const { app } = await mailBoot(t, withPreview(() => reply(409, { error: REFUSAL }), MAIL_ROW.path, 'queue', [3]))
  await expand(app, MAIL_ROW.path)
  await app.click(inArea(mailArea(app), 'a.pdf'))
  assert.match(text(alertsIn(mailArea(app))[0]), /Preview worker failed/)
  assert.ok(inArea(mailArea(app), 'Back to message'))
  await app.click(inArea(mailArea(app), 'Back to message'))
  assert.ok(text(mailArea(app)).includes('Subject: Quarterly numbers'))
})

// ── на экране: rendering ────────────────────────────────────────
test('rendering: «Rendering…» и повтор раз в 2 с, потом готовый просмотр; Previous и Next появляются только с готовым', async (t) => {
  const answers = [RENDERING, RENDERING]
  const { app, clock, server } = await boot(t, { queue: [queued()], previews: withPreview((b, s) => answers.shift() ?? pagesPreview('a.pdf', 2)) })
  await expand(app, FILE)
  assert.ok(text(areaOf(app)).includes('Rendering…'))
  assert.equal(inArea(areaOf(app), 'Next'), null)
  await clock.advance(2000)
  await app.settle()
  assert.ok(text(areaOf(app)).includes('Rendering…'))
  await clock.advance(2000)
  await app.settle()
  assert.ok(!text(areaOf(app)).includes('Rendering…'))
  assert.ok(text(areaOf(app)).includes('Page 1 of 2'))
  assert.equal(server.previewCalls().length, 3)
})

test('rendering дольше трёх минут: сообщение и «Try again»; нажатие запрашивает заново и снова показывает «Rendering…»', async (t) => {
  const { app, clock, server } = await boot(t, { queue: [queued()], previews: withPreview(() => RENDERING) })
  await expand(app, FILE)
  await clock.advance(180000)
  await app.settle()
  assert.ok(!text(areaOf(app)).includes('Rendering…'))
  assert.match(text(areaOf(app)), /still not ready/)
  assert.ok(enabled(inArea(areaOf(app), 'Try again')))
  const before = server.previewCalls().length
  await app.click(inArea(areaOf(app), 'Try again'))
  assert.equal(server.previewCalls().length, before + 1)
  assert.ok(text(areaOf(app)).includes('Rendering…'))
  assert.equal(inArea(areaOf(app), 'Try again'), null)
})

test('строка свёрнута во время rendering: повторы прекращаются', async (t) => {
  const { app, clock, server } = await boot(t, { queue: [queued()], previews: withPreview(() => RENDERING) })
  await expand(app, FILE)
  await app.click(toggleOf(app, FILE))
  const before = server.previewCalls().length
  await clock.advance(60000)
  assert.equal(server.previewCalls().length, before)
  assert.deepEqual(clock.waits(), [30000]) // остался только опрос состояния, свой таймер просмотра погашен
})

// ── на экране: сбои ─────────────────────────────────────────────
test('отказ просмотра показан в его области сообщением системы: известный код — по словарю, неизвестный — текстом', async (t) => {
  const known = await boot(t, { queue: [queued()], on: { ['POST ' + SHOW]: async () => reply(409, { error: { code: 'llm_finding', args: { model: 'm1', page: 1, why: 'odd' }, text: 'ru' } }) } })
  await expand(known.app, FILE)
  assert.equal(text(alertsIn(areaOf(known.app))[0]).includes('The model m1 reported: odd'), true)
  const unknown = await boot(t, { queue: [queued()], on: { ['POST ' + SHOW]: async () => reply(409, { error: REFUSAL }) } })
  await expand(unknown.app, FILE)
  assert.ok(text(alertsIn(areaOf(unknown.app))[0]).includes('Preview worker failed'))
  assert.ok(inArea(areaOf(unknown.app), 'Try again'))
})

test('сбой связи и вход, который истёк, — свои английские тексты в области просмотра; вкладка работает', async (t) => {
  const { app, server } = await boot(t, { queue: [queued()], on: { ['POST ' + SHOW]: async () => { throw new TypeError('Failed to fetch') } } })
  await expand(app, FILE)
  assert.match(text(alertsIn(areaOf(app))[0]), /No connection to DSH/)
  assert.ok(!CYRILLIC.test(text(alertsIn(areaOf(app))[0])))
  assert.equal(alertsIn(app.tree()).length, 1)
  server.on['POST ' + SHOW] = async () => ({ ok: false, status: 401, json: async () => { throw new Error('не JSON') } })
  await app.click(inArea(areaOf(app), 'Try again'))
  assert.match(text(alertsIn(areaOf(app))[0]), /sign-in has expired/)
  server.on['POST ' + SHOW] = async () => reply(502, null)
  await app.click(inArea(areaOf(app), 'Try again'))
  assert.match(text(alertsIn(areaOf(app))[0]), /request failed \(code 502\)/)
  delete server.on['POST ' + SHOW]
  await app.click(inArea(areaOf(app), 'Try again'))
  assert.equal(alertsIn(areaOf(app)).length, 0)
  assert.ok(text(areaOf(app)).includes('Name: a.pdf'))
})

test('ответ описания не того вида: английский текст в области просмотра, а не исключение', async (t) => {
  const { app } = await boot(t, { queue: [queued()], on: { ['POST ' + SHOW]: async () => reply(200, { meta: 5 }) } })
  await expand(app, FILE)
  assert.equal(text(alertsIn(areaOf(app))[0]), 'Something went wrong while reading the archive.')
})

// ── на экране: файлы пачки ──────────────────────────────────────
const batchRow = (app, id) => app.find((n) => n.type === 'li' && n.props['data-batch'] === id)
const headOf = (app, id) => find(batchRow(app, id), (n) => n.type === 'button' && n.props['aria-expanded'] !== undefined)
const openBatch = (app, id) => app.click(headOf(app, id))
const nameButton = (app, id, name) => find(batchRow(app, id), (n) => n.type === 'tr' && n.props['data-file'] === name && find(n, (x) => x.type === 'button'))
const toggleFile = (app, id, name) => app.click(find(nameButton(app, id, name), (n) => n.type === 'button'))
const infoOf = (app, id, name) => find(batchRow(app, id), (n) => n.type === 'tr' && n.props['data-file-info'] === name)
const batchBoot = (t, files, extra = {}) => boot(t, {
  batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], files) }, ...extra,
})
const corpusPath = (name) => `входящие/${IDS[0]}/${name}`

test('файл пачки в корпусе: просмотр по path квитанции, область corpus', async (t) => {
  const file = fileOf('c.pdf', { location: 'corpus', path: corpusPath('c.pdf') })
  const { app, server } = await batchBoot(t, [file], { previews: withPreview(textPreview('c.pdf', 'corpus text'), corpusPath('c.pdf'), 'corpus') })
  await openBatch(app, IDS[0])
  assert.deepEqual(server.previewCalls(), []) // файл не раскрыт — запросов нет
  await toggleFile(app, IDS[0], 'c.pdf')
  assert.deepEqual(server.previewCalls(), [{ area: 'corpus', path: corpusPath('c.pdf'), member: [] }])
  assert.ok(text(areaIn(infoOf(app, IDS[0], 'c.pdf'), corpusPath('c.pdf'))).includes('corpus text'))
  await toggleFile(app, IDS[0], 'c.pdf') // свернули
  assert.equal(infoOf(app, IDS[0], 'c.pdf'), null)
})

test('файл пачки в очереди и в карантине: путь складывается как в списках — очередь/<пачка>/<имя>, карантин/<пачка>/<имя>', async (t) => {
  const files = [fileOf('q.pdf', { location: 'queue', path: `очередь/${IDS[0]}/q.pdf` }), fileOf('k.pdf', { location: 'quarantine', path: `карантин/${IDS[0]}/k.pdf` }),
    fileOf('dir/inner.txt', { location: 'queue', path: `очередь/${IDS[0]}/dir/inner.txt` })]
  const { app, server } = await batchBoot(t, files)
  await openBatch(app, IDS[0])
  for (const f of files) await toggleFile(app, IDS[0], f.name)
  assert.deepEqual(server.previewCalls().map((b) => [b.area, b.path]), [
    ['queue', `очередь/${IDS[0]}/q.pdf`], ['quarantine', `карантин/${IDS[0]}/k.pdf`], ['queue', `очередь/${IDS[0]}/dir/inner.txt`]])
})

test('место файла — не путь квитанции: принятый владельцем файл лежит в корпусе, хотя в квитанции записана очередь', async (t) => {
  const file = fileOf('r.pdf', { decision: 'review', location: 'corpus', decided: 'accept', path: `очередь/${IDS[0]}/r.pdf` })
  const { app, server } = await batchBoot(t, [file])
  await openBatch(app, IDS[0])
  await toggleFile(app, IDS[0], 'r.pdf')
  assert.deepEqual(server.previewCalls().map((b) => [b.area, b.path]), [['corpus', corpusPath('r.pdf')]])
})

for (const location of ['returned', 'deleted', 'missing', null, 'elsewhere']) {
  test(`файл пачки с location ${JSON.stringify(location)}: просмотра нет, запросов нет, сведения из квитанции показаны`, async (t) => {
    const file = fileOf('x.pdf', { location, path: location === 'returned' ? null : corpusPath('x.pdf'), returned: location === 'returned' ? `${IDS[0]}/x.pdf` : null, sha256: 'ab'.repeat(32) })
    const { app, server } = await batchBoot(t, [file])
    await openBatch(app, IDS[0])
    await toggleFile(app, IDS[0], 'x.pdf')
    assert.deepEqual(server.previewCalls(), [])
    assert.deepEqual(server.pageCalls(), [])
    const info = infoOf(app, IDS[0], 'x.pdf')
    assert.ok(info, 'раскрытая строка есть')
    assert.equal(areaIn(info, corpusPath('x.pdf')), null)
    assert.equal(find(info, (n) => n.props?.['data-preview'] !== undefined), null)
    assert.ok(text(info).includes('SHA-256: ' + 'ab'.repeat(32)), text(info)) // сведения из квитанции
  })
}

test('файл без имени просмотра не получает; корпус без path квитанции — путь складывается по пачке и имени', async (t) => {
  const { app, server } = await batchBoot(t, [fileOf('', { location: 'queue' })])
  await openBatch(app, IDS[0])
  await toggleFile(app, IDS[0], '')
  assert.deepEqual(server.previewCalls(), [])
  const nopath = await batchBoot(t, [fileOf('no-path.pdf', { location: 'corpus', path: null })])
  await openBatch(nopath.app, IDS[0])
  await toggleFile(nopath.app, IDS[0], 'no-path.pdf')
  // путь корпуса складывается из пачки и имени: просмотр есть, и он ищет файл там, где ему положено лежать
  assert.deepEqual(nopath.server.previewCalls().map((b) => [b.area, b.path]), [['corpus', corpusPath('no-path.pdf')]])
})

test('разные пути — два просмотра: закрытие одного второй не трогает', async (t) => {
  const row = queued('c.pdf')
  const file = fileOf('c.pdf', { decision: 'review', location: 'queue', path: row.path })
  const { app, server, urls } = await boot(t, {
    queue: [row], batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], [file]) },
    previews: { ...withPreview(pagesPreview('c.pdf', 3), row.path), ...withPreview(pagesPreview('c.pdf', 3), `очередь/${IDS[0]}/c.pdf`) },
  })
  assert.notEqual(row.path, `очередь/${IDS[0]}/c.pdf`) // строка очереди и файл пачки названы по-разному
  await expand(app, row.path)
  await openBatch(app, IDS[0])
  await toggleFile(app, IDS[0], 'c.pdf')
  assert.equal(urls.live.size, 2) // пути разные — два просмотра
  await app.click(toggleOf(app, row.path))
  assert.equal(urls.live.size, 1)
  await toggleFile(app, IDS[0], 'c.pdf')
  assert.equal(urls.live.size, 0)
  assert.equal(server.previewCalls().length, 2)
})

test('пачка и строка с одним путём делят один просмотр', async (t) => {
  const row = item('queue', 'c.pdf', { path: `очередь/${IDS[0]}/c.pdf`, batch: IDS[0] })
  const file = fileOf('c.pdf', { decision: 'review', location: 'queue' })
  const { app, server, urls } = await boot(t, {
    queue: [row], batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], [file]) }, previews: withPreview(pagesPreview('c.pdf', 3), row.path),
  })
  await expand(app, row.path)
  await openBatch(app, IDS[0])
  await toggleFile(app, IDS[0], 'c.pdf')
  assert.equal(server.previewCalls().length, 1)
  assert.equal(urls.live.size, 1)
  await toggleFile(app, IDS[0], 'c.pdf')
  assert.equal(urls.live.size, 1) // строка очереди всё ещё показывает свой просмотр
  await app.click(toggleOf(app, row.path))
  assert.equal(urls.live.size, 0)
})

test('свёрнутая пачка закрывает просмотры своих файлов; раскрыли снова — файлы свёрнуты, адреса не копятся', async (t) => {
  const file = fileOf('c.pdf', { location: 'corpus', path: corpusPath('c.pdf') })
  const { app, urls } = await batchBoot(t, [file], { previews: withPreview(pagesPreview('c.pdf', 3), corpusPath('c.pdf'), 'corpus') })
  await openBatch(app, IDS[0])
  await toggleFile(app, IDS[0], 'c.pdf')
  assert.equal(urls.live.size, 1)
  await openBatch(app, IDS[0]) // пачка свёрнута
  assert.equal(urls.live.size, 0)
  await openBatch(app, IDS[0])
  assert.equal(urls.live.size, 0)
  await toggleFile(app, IDS[0], 'c.pdf')
  assert.equal(urls.live.size, 1)
})

test('файл сменил место (владелец принял его): просмотр переезжает в корпус, адрес старого освобождается', async (t) => {
  const row = item('queue', 'c.pdf', { path: `очередь/${IDS[0]}/c.pdf`, batch: IDS[0] })
  const file = fileOf('c.pdf', { decision: 'review', location: 'queue', path: row.path })
  const previews = { ...withPreview(pagesPreview('c.pdf', 3), row.path), ...withPreview(pagesPreview('c.pdf', 3), corpusPath('c.pdf'), 'corpus') }
  const { app, server, urls, clock } = await boot(t, { queue: [row], batches: [batchOf(IDS[0])], details: { [IDS[0]]: detailOf(IDS[0], [file]) }, previews })
  await openBatch(app, IDS[0])
  await toggleFile(app, IDS[0], 'c.pdf')
  assert.deepEqual(server.previewCalls().map((b) => b.area), ['queue'])
  const first = [...urls.live][0]
  server.details[IDS[0]].files[0] = { ...file, decided: 'accept', decided_at: '2026-10-04T20:00:00Z', location: 'corpus' }
  server.queue.push(item('queue', 'other.pdf'))
  await clock.advance(30000)
  await app.settle()
  assert.deepEqual(server.previewCalls().map((b) => b.area), ['queue', 'corpus'])
  assert.equal(urls.live.size, 1)
  assert.ok(!urls.live.has(first))
})

// ── безопасность вывода ─────────────────────────────────────────
const EVIL = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=3><a href="javascript:alert(4)">x</a>'
const walkAll = (app) => app.all(() => true)

test('всё из содержимого файла выводится только текстом: имена, письмо, вложения, список архива, сведения, текст и пояснение', async (t) => {
  const evilMeta = metaOf(EVIL, { type: EVIL, sha256: EVIL, batch: EVIL, origin: { archive: EVIL, inner: EVIL } })
  const mail = { from: EVIL, to: [EVIL, EVIL], cc: [EVIL], date: EVIL, subject: EVIL, text: EVIL, attachments: [attachmentOf(EVIL, 1, { type: EVIL }), attachmentOf(EVIL, 2, { type: EVIL })] }
  const row = queued('mail.eml')
  const cases = [
    { kind: 'text', meta: evilMeta, text: EVIL, truncated: true },
    { kind: 'mail', meta: evilMeta, mail },
    { kind: 'listing', meta: evilMeta, listing: [{ name: EVIL, size: 1 }], truncated: true },
    { kind: 'none', meta: evilMeta, note: { code: null, args: {}, text: EVIL } },
    { kind: 'media', meta: evilMeta, media: { seconds: 5, width: 1, height: 1 } },
    { kind: 'pages', meta: evilMeta, pages: 2, shown: 2 },
    { kind: 'image', meta: evilMeta },
  ]
  const check = (app, label, escaped) => {
    const html = app.html()
    for (const raw of ['<img src=x', '<script', '<svg', '<a href', 'onerror=alert(1)>']) assert.ok(!html.includes(raw), `${label}: ${raw}`)
    if (escaped) assert.ok(html.includes('&lt;script&gt;'), label)
    for (const node of walkAll(app)) {
      for (const bad of ['dangerouslySetInnerHTML', 'innerHTML', 'href', 'srcDoc', 'srcdoc', 'formAction']) assert.equal(node.props[bad], undefined, `${label}: ${bad}`)
      assert.ok(!['a', 'iframe', 'object', 'embed', 'script', 'video', 'audio', 'link', 'style', 'svg'].includes(node.type), `${label}: <${node.type}>`)
      if (node.props.src !== undefined) assert.ok(node.type === 'img' && String(node.props.src).startsWith('blob:'), `${label}: src у <${node.type}>`)
    }
  }
  for (const description of cases) {
    const { app } = await boot(t, { queue: [row], previews: withPreview(description, row.path) })
    await expand(app, row.path)
    check(app, description.kind, true)
    if (description.kind === 'mail') {
      await app.click(inArea(areaOf(app, row.path), EVIL)) // вложение с вредным именем: его просмотр тоже без разметки
      check(app, 'mail → вложение', false)
    }
  }
})

test('разметка в сообщении об отказе тоже остаётся текстом', async (t) => {
  const row = queued('mail.eml')
  const bad = { code: null, args: {}, text: EVIL }
  const { app } = await boot(t, { queue: [row], on: { ['POST ' + SHOW]: async () => reply(409, { error: bad }) } })
  await expand(app, row.path)
  assert.ok(app.html().includes('&lt;script&gt;'))
  assert.ok(!app.html().includes('<script'))
  assert.ok(!app.html().includes('<img src=x'))
})

test('в областях просмотра нет ни одной ссылки, ни одного адреса из содержимого: у картинки только адрес объекта', async (t) => {
  const row = queued('mail.eml')
  const { app, urls } = await boot(t, { queue: [row], previews: withPreview(pagesPreview('mail.eml', 2, 2, { meta: metaOf('https://evil.example/x.png', { origin: { archive: 'http://evil.example/', inner: 'file:///etc/passwd' } }) }), row.path) })
  await expand(app, row.path)
  const area = areaOf(app, row.path)
  const sources = collect(area, (n) => n.props?.src !== undefined)
  assert.deepEqual(sources.map((n) => n.props.src), [...urls.live])
  assert.equal(collect(area, (n) => n.type === 'a' || n.props?.href !== undefined).length, 0)
  assert.ok(!app.html().includes('href='))
})

// ── язык и словарь ──────────────────────────────────────────────
test('все подписи просмотра берутся из словаря: ключей, которых в словаре нет, не запрашивается', async (t) => {
  const row = queued('mail.eml')
  const states = [
    textPreview('a', 'x', { truncated: true }), pagesPreview('a', 143, 20), imagePreview('a'), mediaPreview('a'), listingPreview('a', [{ name: 'n', size: 1 }], { truncated: true }),
    nonePreview('a', { code: null, args: {}, text: 'x' }), { kind: 'none', meta: {} }, mailPreview('a', MAIL), { kind: 'weird' },
  ]
  for (const description of states) {
    const { app, ctx } = await boot(t, { queue: [row], previews: withPreview(description, row.path) })
    await expand(app, row.path)
    for (const label of ['Next', 'Previous', 'Fit width', '100%', '150%', 'a.pdf', 'inner.eml']) {
      const button = inArea(areaOf(app, row.path), label)
      if (button !== null) await app.click(button)
    }
    assert.deepEqual([...new Set(ctx.missingKeys)], [], description.kind)
  }
  const failing = await boot(t, { queue: [row], on: { ['POST ' + SHOW]: async () => { throw new TypeError('x') } } })
  await expand(failing.app, row.path)
  const rendering = await boot(t, { queue: [row], previews: withPreview(() => RENDERING, row.path) })
  await expand(rendering.app, row.path)
  await rendering.clock.advance(180000)
  await rendering.app.settle()
  const pagesFail = await boot(t, { queue: [row], previews: withPreview(pagesPreview('a', 3), row.path), on: { ['POST ' + PAGE]: async () => reply(409, { error: REFUSAL }) } })
  await expand(pagesFail.app, row.path)
  for (const { ctx } of [failing, rendering, pagesFail]) assert.deepEqual([...new Set(ctx.missingKeys)], [])
})

test('на экране просмотра нет русских слов, кроме данных из архива (на этом образце их нет)', async (t) => {
  const row = queued('mail.eml')
  const states = [textPreview('a', 'x', { truncated: true }), pagesPreview('a', 143, 20), mediaPreview('a'), listingPreview('a', [{ name: 'n', size: 1 }], { truncated: true }),
    { kind: 'none', meta: metaOf('a') }, mailPreview('a', MAIL)]
  for (const description of states) {
    const { app } = await boot(t, { queue: [row], previews: withPreview(description, row.path) })
    await expand(app, row.path)
    const area = text(areaOf(app, row.path))
    assert.ok(!CYRILLIC.test(area), `${description.kind}: ${area}`)
  }
})

test('словарь сообщений не пришёл: просмотр работает, пояснение — исходным text сервера', async (t) => {
  const row = queued('a.pdf')
  const { app } = await boot(t, { chunksFail: true, queue: [row], previews: withPreview(nonePreview('a.pdf', { code: 'llm_finding', args: { model: 'm1', page: 1, why: 'w' }, text: 'модель m1: странно' }), row.path) })
  await expand(app, row.path)
  assert.ok(text(areaOf(app, row.path)).includes('модель m1: странно'))
  assert.ok(text(areaOf(app, row.path)).includes('Name: a.pdf'))
})

test('словарь вкладки: подписи просмотра есть, наборы ключей zh и en по-прежнему совпадают', async (t) => {
  const { ctx } = await boot(t)
  const { zh, en } = ctx.dictionaries.flyarchive
  assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort())
  assert.deepEqual(zh, en)
  for (const key of ['preview.rendering', 'preview.retry', 'preview.page', 'preview.previous', 'preview.next', 'preview.firstPages', 'preview.mail.back']) {
    assert.ok(key in en, key)
  }
  for (const [key, value] of Object.entries(en)) assert.ok(typeof value === 'string' && value !== '' && !CYRILLIC.test(value), key)
})

test('постоянных таймеров просмотр не заводит: пока ничего не рисуется, живёт только опрос состояния', async (t) => {
  const { app, clock } = await boot(t, { queue: [queued()], previews: withPreview(pagesPreview('a.pdf', 3)) })
  assert.deepEqual(clock.waits(), [30000])
  await expand(app, FILE)
  await app.click(inArea(areaOf(app), 'Next'))
  assert.deepEqual(clock.waits(), [30000])
  await app.click(toggleOf(app, FILE))
  assert.deepEqual(clock.waits(), [30000])
})
