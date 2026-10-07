// Общее хранилище вкладки «Archive»: опрос сервера, перечитывание списков, решения по файлам (FR-80, FR-81).
// Время идёт на управляемых таймерах, ответы архива подставные (kit-archive.mjs).
import assert from 'node:assert/strict'
import test from 'node:test'

import { fakeArchive, item, progressOf } from './kit-archive.mjs'
import { createClock, fakePage, flush, load, reply } from './kit.mjs'

const INBOX = 'api/flyarchive.inbox'
const QUEUE = 'api/flyarchive.queue'
const QUARANTINE = 'api/flyarchive.quarantine'
const RUN = 'api/flyarchive.inbox.run'

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

const names = (n, prefix = 'f') => Array.from({ length: n }, (_, i) => `очередь/20260101-120000/${prefix}${i}.txt`)
const deferred = () => {
  let resolve
  const promise = new Promise((r) => { resolve = r })
  return { promise, resolve }
}

// ── опрос ───────────────────────────────────────────────────────
test('опрос: сразу при запуске, потом раз в 30 секунд, пока ничего не идёт', async () => {
  const { server, clock, store } = await setup()
  store.start()
  await flush()
  assert.equal(server.count(INBOX), 1)
  assert.deepEqual(clock.waits(), [30000])
  await clock.advance(29999)
  assert.equal(server.count(INBOX), 1)
  await clock.advance(1)
  assert.equal(server.count(INBOX), 2)
  await clock.advance(60000)
  assert.equal(server.count(INBOX), 4)
})

test('опрос: пока идёт разбор, раз в 2 секунды; кончился — снова раз в 30', async () => {
  const { server, clock, store } = await setup({ progress: progressOf() })
  store.start()
  await flush()
  assert.deepEqual(clock.waits(), [2000])
  await clock.advance(10000)
  assert.equal(server.count(INBOX), 6)
  server.progress = null
  await clock.advance(2000)
  assert.equal(store.getSnapshot().status.progress, null)
  assert.deepEqual(clock.waits(), [30000])
  const before = server.count(INBOX)
  await clock.advance(29999)
  assert.equal(server.count(INBOX), before)
  await clock.advance(1)
  assert.equal(server.count(INBOX), before + 1)
})

test('опрос: начался разбор между двумя опросами — следующий опрос уже частый', async () => {
  const { server, clock, store } = await setup()
  store.start()
  await flush()
  await clock.advance(30000)
  assert.deepEqual(clock.waits(), [30000])
  server.progress = progressOf()
  await clock.advance(30000)
  assert.deepEqual(clock.waits(), [2000])
})

test('опрос: скрытая страница не опрашивает, показанная опрашивает сразу и дальше по расписанию', async () => {
  const { server, clock, page, store } = await setup()
  store.start()
  await flush()
  page.set(true)
  assert.deepEqual(clock.waits(), [])
  await clock.advance(10 * 60 * 1000)
  assert.equal(server.count(INBOX), 1)
  page.set(false)
  await flush()
  assert.equal(server.count(INBOX), 2)
  assert.deepEqual(clock.waits(), [30000])
})

test('опрос: страница скрыта во время ответа — после ответа таймер не ставится', async () => {
  const gate = deferred()
  const { server, clock, page, store } = await setup()
  server.on['GET ' + INBOX] = async () => { await gate.promise; return reply(200, server.view()) }
  store.start()
  await flush()
  page.set(true)
  gate.resolve()
  await flush()
  assert.deepEqual(clock.waits(), [])
  assert.equal(store.getSnapshot().status.waiting, 3) // ответ при этом принят
})

test('опрос: страница скрыта уже при запуске — запросов нет до показа', async () => {
  const { server, clock, page, store } = await setup({}, { hidden: true })
  store.start()
  await flush()
  await clock.advance(120000)
  assert.equal(server.calls.length, 0)
  page.set(false)
  await flush()
  assert.equal(server.count(INBOX), 1)
})

test('остановка снимает таймер и подписку на видимость страницы', async () => {
  const { server, clock, page, store } = await setup()
  store.start()
  await flush()
  assert.equal(page.listeners(), 1)
  store.stop()
  assert.deepEqual(clock.waits(), [])
  assert.equal(page.listeners(), 0)
  await clock.advance(120000)
  page.set(true)
  page.set(false)
  await flush()
  assert.equal(server.count(INBOX), 1)
})

test('сбой связи не останавливает опрос: ошибка видна, потом сама проходит', async () => {
  const { server, clock, store } = await setup()
  server.offline = true
  store.start()
  await flush()
  assert.equal(store.getSnapshot().error.kind, 'offline')
  assert.equal(store.getSnapshot().status, null)
  assert.deepEqual(clock.waits(), [30000])
  server.offline = false
  await clock.advance(30000)
  assert.equal(store.getSnapshot().error, null)
  assert.equal(store.getSnapshot().status.waiting, 3)
})

test('отказ команды (409) показывается её сообщением и опрос продолжается', async () => {
  const { server, clock, store } = await setup()
  const refusal = { code: 'busy', args: { n: 1 }, text: 'Разбор занят' }
  server.on['GET ' + INBOX] = async () => reply(409, { error: refusal })
  store.start()
  await flush()
  const { error } = store.getSnapshot()
  assert.equal(error.kind, 'refused')
  assert.deepEqual(error.refusal, refusal)
  assert.deepEqual(clock.waits(), [30000])
})

// ── «Run now» ───────────────────────────────────────────────────
test('«Run now»: запрос на запуск, потом сразу свежее состояние', async () => {
  const { server, store } = await setup()
  store.start()
  await flush()
  await store.runNow()
  assert.equal(server.posts(RUN).length, 1)
  assert.deepEqual(server.posts(RUN)[0].body, {})
  assert.equal(server.count(INBOX), 2)
})

test('после «Run now» опрос какое-то время частый, даже если ход ещё не появился, потом возвращается к 30 секундам', async () => {
  const { server, clock, store } = await setup()
  store.start()
  await flush()
  await store.runNow() // состояние снято сразу после запроса: хода ещё нет
  assert.deepEqual(clock.waits(), [2000])
  const before = server.count(INBOX)
  await clock.advance(8000)
  assert.equal(server.count(INBOX) - before, 4)
  assert.deepEqual(clock.waits(), [30000])
})

test('после «Run now» ход появился — дальше опрос ведёт сам ход', async () => {
  const { server, clock, store } = await setup()
  store.start()
  await flush()
  await store.runNow()
  server.progress = progressOf()
  await clock.advance(2000)
  await clock.advance(20000)
  assert.deepEqual(clock.waits(), [2000]) // прошло больше, чем льгота, а опрос всё ещё частый
  server.progress = null
  await clock.advance(2000)
  assert.deepEqual(clock.waits(), [30000])
})

test('«Run now»: отказ показан, запуск не тихий; во время запроса кнопки заняты', async () => {
  const gate = deferred()
  const { server, store } = await setup()
  server.on['POST ' + RUN] = async () => { await gate.promise; return reply(409, { error: { code: 'x', args: {}, text: 'Нельзя сейчас' } }) }
  store.start()
  await flush()
  const running = store.runNow()
  await flush()
  assert.equal(store.getSnapshot().busy, true)
  gate.resolve()
  await running
  assert.equal(store.getSnapshot().busy, false)
  assert.deepEqual(store.getSnapshot().error.refusal, { code: 'x', args: {}, text: 'Нельзя сейчас' })
})

// ── списки очереди и карантина ──────────────────────────────────
test('списки читаются при первом ответе, по одному запросу', async () => {
  const { server, store } = await setup({ queue: [item('queue', 'a.txt')], quarantine: [item('quarantine', 'b.exe')] })
  store.start()
  await flush()
  assert.equal(server.count(QUEUE), 1)
  assert.equal(server.count(QUARANTINE), 1)
  const snap = store.getSnapshot()
  assert.deepEqual(snap.queue.map((i) => i.name), ['a.txt'])
  assert.deepEqual(snap.quarantine.map((i) => i.name), ['b.exe'])
})

test('списки не перечитываются, пока счётчики и номер последней пачки те же', async () => {
  const { server, clock, store } = await setup({ queue: [item('queue', 'a.txt')], progress: progressOf() })
  store.start()
  await flush()
  server.status.waiting = 9 // меняется другое: ждёт во входящей папке и идёт ход
  await clock.advance(10000)
  server.progress = null
  await clock.advance(60000)
  assert.ok(server.count(INBOX) >= 6)
  assert.equal(server.count(QUEUE), 1)
  assert.equal(server.count(QUARANTINE), 1)
})

const CHANGES = [
  ['счётчик очереди', (s) => { s.status.queue = 5 }],
  ['счётчик карантина', (s) => { s.status.quarantine = 5 }],
  ['номер последней пачки', (s) => { s.status.last_batch = { batch: '20261005-090000', accept: 1 } }],
]
for (const [title, change] of CHANGES) {
  test(`списки перечитываются, когда изменился ${title}: оба и по одному разу`, async () => {
    const { server, clock, store } = await setup()
    store.start()
    await flush()
    change(server)
    await clock.advance(30000)
    assert.equal(server.count(QUEUE), 2)
    assert.equal(server.count(QUARANTINE), 2)
    await clock.advance(30000) // тот же ответ ещё раз — перечитывать уже нечего
    assert.equal(server.count(QUEUE), 2)
    assert.equal(server.count(QUARANTINE), 2)
  })
}

test('сбой чтения списка не прячет состояние и повторяется на следующем опросе', async () => {
  const { server, clock, store } = await setup({ queue: [item('queue', 'a.txt')] })
  let broken = true
  server.on['GET ' + QUEUE] = async () => (broken ? reply(500, null) : reply(200, { items: server.queue }))
  store.start()
  await flush()
  assert.equal(store.getSnapshot().status.waiting, 3)
  assert.equal(store.getSnapshot().error.kind, 'failed')
  assert.equal(store.getSnapshot().queue, null)
  broken = false
  await clock.advance(30000)
  assert.equal(store.getSnapshot().error, null)
  assert.deepEqual(store.getSnapshot().queue.map((i) => i.name), ['a.txt'])
})

// ── решения по файлам ───────────────────────────────────────────
test('решение над несколькими файлами уходит одним запросом, потом списки перечитываются', async () => {
  const q = [item('queue', 'a.txt'), item('queue', 'b.txt'), item('queue', 'c.txt')]
  const { server, store } = await setup({ queue: q })
  store.start()
  await flush()
  await store.decide('queue', 'accept', [q[0].path, q[1].path])
  const posts = server.posts(QUEUE)
  assert.equal(posts.length, 1)
  assert.deepEqual(posts[0].body, { paths: [q[0].path, q[1].path], action: 'accept' })
  assert.deepEqual(store.getSnapshot().queue.map((i) => i.name), ['c.txt'])
  assert.equal(server.count(QUEUE), 2) // и перечитывание после решения
  assert.equal(server.count(QUARANTINE), 2)
})

test('«В карантин» и «Вернуть» идут по своим адресам с своими действиями', async () => {
  const q = [item('queue', 'a.txt')]
  const k = [item('quarantine', 'b.exe')]
  const { server, store } = await setup({ queue: q, quarantine: k })
  store.start()
  await flush()
  await store.decide('queue', 'quarantine', [q[0].path])
  await store.decide('quarantine', 'return', [k[0].path])
  assert.deepEqual(server.posts(QUEUE).map((c) => c.body.action), ['quarantine'])
  assert.deepEqual(server.posts(QUARANTINE).map((c) => c.body), [{ paths: [k[0].path], action: 'return' }])
})

test('больше 200 путей уходят несколькими запросами по 200, по порядку и без повторов', async () => {
  const q = names(450).map((path, i) => item('queue', `f${i}.txt`, { path }))
  const { server, store } = await setup({ queue: q })
  store.start()
  await flush()
  await store.decide('queue', 'accept', [...q.map((i) => i.path), q[0].path, q[7].path]) // повторы — лишние
  const posts = server.posts(QUEUE)
  assert.deepEqual(posts.map((c) => c.body.paths.length), [200, 200, 50])
  assert.ok(posts.every((c) => c.body.action === 'accept'))
  assert.deepEqual(posts.flatMap((c) => c.body.paths), q.map((i) => i.path))
  assert.deepEqual(store.getSnapshot().queue, [])
})

test('ровно 200 путей — один запрос, пустой список — ни одного', async () => {
  const q = names(200).map((path, i) => item('queue', `f${i}.txt`, { path }))
  const { server, store } = await setup({ queue: q })
  store.start()
  await flush()
  await store.decide('queue', 'accept', [])
  assert.equal(server.posts(QUEUE).length, 0)
  await store.decide('queue', 'accept', q.map((i) => i.path))
  assert.deepEqual(server.posts(QUEUE).map((c) => c.body.paths.length), [200])
})

test('сбой одного пути виден у его строки и не мешает остальным', async () => {
  const q = [item('queue', 'a.txt'), item('queue', 'b.txt'), item('queue', 'c.txt')]
  const { server, store } = await setup({ queue: q })
  const failure = { code: null, args: {}, text: 'Файл изменился после проверки' }
  server.rejects[q[1].path] = failure
  store.start()
  await flush()
  await store.decide('queue', 'accept', q.map((i) => i.path))
  const snap = store.getSnapshot()
  assert.deepEqual(snap.queue.map((i) => i.name), ['b.txt']) // a и c приняты, b осталась
  assert.deepEqual(Object.keys(snap.rowErrors), [q[1].path])
  assert.deepEqual(snap.rowErrors[q[1].path], { kind: 'refused', refusal: failure })
  assert.equal(snap.error, null)
})

test('отказ по пути строкой (без кода) тоже приводится к сообщению', async () => {
  const q = [item('queue', 'a.txt')]
  const { server, store } = await setup({ queue: q })
  server.rejects[q[0].path] = 'старый текст отказа'
  store.start()
  await flush()
  await store.decide('queue', 'accept', [q[0].path])
  assert.deepEqual(store.getSnapshot().rowErrors[q[0].path].refusal, { code: null, args: {}, text: 'старый текст отказа' })
})

test('сбой пути не обрывает следующие запросы', async () => {
  const q = names(250).map((path, i) => item('queue', `f${i}.txt`, { path }))
  const { server, store } = await setup({ queue: q })
  server.rejects[q[3].path] = { code: null, args: {}, text: 'нет' }
  store.start()
  await flush()
  await store.decide('queue', 'accept', q.map((i) => i.path))
  assert.equal(server.posts(QUEUE).length, 2)
  assert.deepEqual(Object.keys(store.getSnapshot().rowErrors), [q[3].path])
  assert.equal(store.getSnapshot().queue.length, 1)
})

test('сбой у строки сходит, когда файл ушёл из списка', async () => {
  const q = [item('queue', 'a.txt'), item('queue', 'b.txt')]
  const { server, store } = await setup({ queue: q })
  server.rejects[q[0].path] = { code: null, args: {}, text: 'нет' }
  store.start()
  await flush()
  await store.decide('queue', 'accept', [q[0].path])
  assert.deepEqual(Object.keys(store.getSnapshot().rowErrors), [q[0].path])
  delete server.rejects[q[0].path]
  await store.decide('queue', 'accept', [q[0].path]) // повтор: теперь проходит
  assert.deepEqual(store.getSnapshot().rowErrors, {})
})

test('отказ запроса на один путь показан у строки, на несколько — над списком, остальные запросы не уходят', async () => {
  const q = names(250).map((path, i) => item('queue', `f${i}.txt`, { path }))
  const { server, store } = await setup({ queue: q })
  const refusal = { code: 'locked', args: {}, text: 'Идёт индексация' }
  server.on['POST ' + QUEUE] = async () => reply(409, { error: refusal })
  store.start()
  await flush()
  await store.decide('queue', 'accept', [q[0].path])
  assert.deepEqual(store.getSnapshot().rowErrors, { [q[0].path]: { kind: 'refused', refusal } })
  assert.equal(store.getSnapshot().error, null)
  await store.decide('queue', 'accept', q.slice(0, 250).map((i) => i.path))
  assert.equal(server.posts(QUEUE).length, 2) // один за первое решение и один (первая порция) за второе
  assert.equal(store.getSnapshot().error.kind, 'refused')
  assert.deepEqual(store.getSnapshot().error.refusal, refusal)
})

test('обрыв связи при решении — отдельная ошибка над списком, а не у строк', async () => {
  const q = [item('queue', 'a.txt')]
  const { server, store } = await setup({ queue: q })
  store.start()
  await flush()
  server.offline = true
  await store.decide('queue', 'accept', [q[0].path])
  assert.equal(store.getSnapshot().error.kind, 'offline')
  assert.deepEqual(store.getSnapshot().rowErrors, {})
})

test('стирание — один путь прежним видом запроса, после него список перечитывается', async () => {
  const k = [item('quarantine', 'a.exe'), item('quarantine', 'b.exe')]
  const { server, store } = await setup({ quarantine: k })
  store.start()
  await flush()
  await store.remove(k[0].path)
  const posts = server.posts(QUARANTINE)
  assert.deepEqual(posts.map((c) => c.body), [{ path: k[0].path, action: 'delete' }])
  assert.deepEqual(store.getSnapshot().quarantine.map((i) => i.name), ['b.exe'])
})

test('отказ стирания показан у строки', async () => {
  const k = [item('quarantine', 'a.exe')]
  const { server, store } = await setup({ quarantine: k })
  const refusal = { code: null, args: {}, text: 'Файла уже нет' }
  server.on['POST ' + QUARANTINE] = async () => reply(409, { error: refusal })
  store.start()
  await flush()
  await store.remove(k[0].path)
  assert.deepEqual(store.getSnapshot().rowErrors[k[0].path], { kind: 'refused', refusal })
})

test('массового стирания нет: сочетание «карантин + delete» хранилище не принимает', async () => {
  const k = [item('quarantine', 'a.exe'), item('quarantine', 'b.exe')]
  const { server, store } = await setup({ quarantine: k })
  store.start()
  await flush()
  await assert.rejects(store.decide('quarantine', 'delete', k.map((i) => i.path)))
  await assert.rejects(store.decide('queue', 'return', [k[0].path]))
  await assert.rejects(store.decide('queue', 'delete', [k[0].path]))
  assert.equal(server.posts(QUARANTINE).length + server.posts(QUEUE).length, 0)
})

test('во время решения хранилище занято, потом свободно', async () => {
  const gate = deferred()
  const q = [item('queue', 'a.txt')]
  const { server, store } = await setup({ queue: q })
  server.on['POST ' + QUEUE] = async () => { await gate.promise; return reply(200, { results: [{ path: q[0].path, ok: true }] }) }
  store.start()
  await flush()
  const work = store.decide('queue', 'accept', [q[0].path])
  await flush()
  assert.equal(store.getSnapshot().busy, true)
  gate.resolve()
  await work
  assert.equal(store.getSnapshot().busy, false)
})

// ── снимок состояния ────────────────────────────────────────────
test('снимок не меняется, пока ничего не произошло, и сообщает подписчикам об изменении', async () => {
  const { store } = await setup()
  const first = store.getSnapshot()
  assert.equal(store.getSnapshot(), first)
  let heard = 0
  const stop = store.subscribe(() => { heard++ })
  store.start()
  await flush()
  assert.ok(heard >= 1)
  assert.notEqual(store.getSnapshot(), first)
  const seen = heard
  stop()
  await store.refresh()
  assert.equal(heard, seen)
})

test('модуль словаря кладётся в хранилище и виден в снимке', async () => {
  const { store } = await setup()
  assert.equal(store.getSnapshot().messages, null)
  const dictionary = { format: () => 'x' }
  store.setMessages(dictionary)
  assert.equal(store.getSnapshot().messages, dictionary)
})

// ── обращения к серверной половине (клиент) ─────────────────────
test('решение пачкой идёт телом {paths, action}, ответ отдаётся как есть', async () => {
  const { exports } = await load()
  const answer = { results: [{ path: 'очередь/a/b', ok: true }, { path: 'очередь/a/c', ok: false, error: { code: 'x', args: {}, text: 'нет' } }] }
  const server = fakeArchive({ on: { 'POST api/flyarchive.queue': async () => reply(200, answer) } })
  const client = exports.parts.createClient(server.doFetch)
  assert.deepEqual(await client.decide('queue', 'quarantine', ['очередь/a/b', 'очередь/a/c']), answer)
  await client.decide('quarantine', 'return', ['карантин/a/b'])
  assert.deepEqual(server.calls.map((c) => [c.route, c.method, c.body]), [
    ['api/flyarchive.queue', 'POST', { paths: ['очередь/a/b', 'очередь/a/c'], action: 'quarantine' }],
    ['api/flyarchive.quarantine', 'POST', { paths: ['карантин/a/b'], action: 'return' }]])
})

test('отказ с объектом-сообщением: текст в message, разбор в refusal, вид отказа в kind', async () => {
  const { exports } = await load()
  const refusal = { code: 'file_changed', args: { name: 'a.txt' }, text: 'Файл a.txt изменился' }
  const client = exports.parts.createClient(async () => reply(409, { error: refusal }))
  await assert.rejects(client.tokens(), (e) => e.message === refusal.text && e.kind === 'refused' && e.status === 409
    && JSON.stringify(e.refusal) === JSON.stringify(refusal))
})

test('отказ строкой тоже даёт refusal, а негодный объект — общий сбой', async () => {
  const { exports } = await load()
  const plain = exports.parts.createClient(async () => reply(409, { error: 'имя занято' }))
  await assert.rejects(plain.tokens(), (e) => e.kind === 'refused' && e.message === 'имя занято'
    && JSON.stringify(e.refusal) === JSON.stringify({ code: null, args: {}, text: 'имя занято' }))
  const odd = exports.parts.createClient(async () => reply(500, { error: { code: 1 } }))
  await assert.rejects(odd.tokens(), (e) => e.kind === 'failed' && e.status === 500 && e.refusal === undefined)
})

test('вид сбоя: нет связи, вход истёк, прочее', async () => {
  const { exports } = await load()
  const kind = async (answer) => {
    const client = exports.parts.createClient(answer)
    try {
      await client.tokens()
    } catch (e) {
      return e.kind
    }
    return null
  }
  assert.equal(await kind(async () => { throw new TypeError('Failed to fetch') }), 'offline')
  assert.equal(await kind(async () => ({ ok: false, status: 401, json: async () => { throw new Error('не JSON') } })), 'auth')
  assert.equal(await kind(async () => reply(503, null)), 'failed')
})

// ── дополнения по порче кода ────────────────────────────────────
test('повторный запуск не ставит второй опрос и вторую подписку на страницу', async () => {
  const { server, clock, page, store } = await setup()
  store.start()
  store.start()
  await flush()
  assert.equal(server.count(INBOX), 1)
  assert.equal(page.listeners(), 1)
  assert.deepEqual(clock.waits(), [30000])
})

test('остановка во время ответа: ответ принят, но таймер после него не ставится', async () => {
  const gate = deferred()
  const { server, clock, store } = await setup()
  server.on['GET ' + INBOX] = async () => { await gate.promise; return reply(200, server.view()) }
  store.start()
  await flush()
  store.stop()
  gate.resolve()
  await flush()
  assert.deepEqual(clock.waits(), [])
  await clock.advance(120000)
  assert.equal(server.count(INBOX), 1)
})

test('опросы идут по одному: второй не уходит, пока не вернулся первый', async () => {
  const gates = [deferred(), deferred()]
  let started = 0
  const { server, store } = await setup()
  server.on['GET ' + INBOX] = async () => { const gate = gates[started++]; await gate.promise; return reply(200, server.view()) }
  const first = store.refresh()
  const second = store.refresh()
  await flush()
  assert.equal(started, 1) // второй ждёт первого
  gates[0].resolve()
  await first
  await flush()
  assert.equal(started, 2)
  gates[1].resolve()
  await second
})

test('после «Run now» ход появился и сразу кончился: опрос возвращается к 30 секундам, а не доживает льготу', async () => {
  const { server, clock, store } = await setup()
  store.start()
  await flush()
  await store.runNow()
  server.progress = progressOf()
  await clock.advance(2000) // ход виден
  assert.deepEqual(clock.waits(), [2000])
  server.progress = null
  await clock.advance(2000) // ход кончился
  assert.deepEqual(clock.waits(), [30000])
})

test('пустое решение не трогает сервер вообще: ни решения, ни перечитывания', async () => {
  const { server, store } = await setup({ queue: [item('queue', 'a.txt')] })
  store.start()
  await flush()
  const before = server.calls.length
  await store.decide('queue', 'accept', [])
  assert.equal(server.calls.length, before)
})

test('в ответе без поля ok запись не считается сбоем: виновного не назначаем', async () => {
  const q = [item('queue', 'a.txt')]
  const { server, store } = await setup({ queue: q })
  server.on['POST ' + QUEUE] = async () => reply(200, { results: [{ path: q[0].path }, null, 'мусор', { ok: false }] })
  store.start()
  await flush()
  await store.decide('queue', 'accept', [q[0].path])
  assert.deepEqual(store.getSnapshot().rowErrors, {}) // в записи без path и без ok виновного нет
})

test('сбой по пути без пояснения получает пустое сообщение: вкладка подставит свой текст', async () => {
  const q = [item('queue', 'a.txt')]
  const { server, store } = await setup({ queue: q })
  server.on['POST ' + QUEUE] = async () => reply(200, { results: [{ path: q[0].path, ok: false }] })
  store.start()
  await flush()
  await store.decide('queue', 'accept', [q[0].path])
  assert.deepEqual(store.getSnapshot().rowErrors[q[0].path], { kind: 'refused', refusal: { code: null, args: {}, text: '' } })
})

test('после решения списки читаются заново и тогда, когда счётчики остались прежними', async () => {
  const q = [item('queue', 'a.txt'), item('queue', 'b.txt')]
  const { server, store } = await setup({ queue: q, status: { queue: 2, quarantine: 0 } }) // счётчики закреплены: сами не изменятся
  store.start()
  await flush()
  await store.decide('queue', 'accept', [q[0].path])
  assert.equal(server.count(QUEUE), 2)
  assert.deepEqual(store.getSnapshot().queue.map((i) => i.name), ['b.txt'])
  const k = [item('quarantine', 'c.exe')]
  server.quarantine.push(...k)
  server.status.quarantine = 0
  await store.remove(k[0].path)
  assert.equal(server.count(QUARANTINE), 3)
})

test('сбой у строки уходит, когда файл пропал из списка, и остаётся, пока файл на месте', async () => {
  const q = [item('queue', 'a.txt'), item('queue', 'b.txt')]
  const { server, clock, store } = await setup({ queue: q })
  server.rejects[q[0].path] = { code: null, args: {}, text: 'нет' }
  server.rejects[q[1].path] = { code: null, args: {}, text: 'тоже нет' }
  store.start()
  await flush()
  await store.decide('queue', 'accept', [q[0].path, q[1].path])
  assert.deepEqual(Object.keys(store.getSnapshot().rowErrors).sort(), [q[0].path, q[1].path].sort())
  server.queue.splice(0, 1) // a ушёл другим путём
  await clock.advance(30000)
  assert.deepEqual(Object.keys(store.getSnapshot().rowErrors), [q[1].path])
})

test('сбой по пути, которого уже нет в списках, не показывается', async () => {
  const q = [item('queue', 'a.txt')]
  const { server, store } = await setup({ queue: q })
  server.on['POST ' + QUEUE] = async () => reply(200, { results: [{ path: q[0].path, ok: true }, { path: 'очередь/20260101-120000/нет-такого.txt', ok: false, error: 'x' }] })
  store.start()
  await flush()
  await store.decide('queue', 'accept', [q[0].path])
  assert.deepEqual(store.getSnapshot().rowErrors, {})
})

test('новое решение сразу убирает прежний сбой строки и прежнюю ошибку над списком, пока идёт запрос', async () => {
  const gate = deferred()
  const q = [item('queue', 'a.txt'), item('queue', 'b.txt')]
  const k = [item('quarantine', 'c.exe')]
  const { server, store } = await setup({ queue: q, quarantine: k })
  server.rejects[q[0].path] = { code: null, args: {}, text: 'нет' }
  store.start()
  await flush()
  await store.decide('queue', 'accept', [q[0].path])
  assert.deepEqual(Object.keys(store.getSnapshot().rowErrors), [q[0].path])
  server.offline = true
  await store.decide('queue', 'accept', [q[1].path])
  assert.equal(store.getSnapshot().error.kind, 'offline')
  server.offline = false
  delete server.rejects[q[0].path]
  server.on['POST ' + QUEUE] = async () => { await gate.promise; return reply(200, { results: [] }) }
  const work = store.decide('queue', 'accept', [q[0].path])
  await flush()
  assert.deepEqual(store.getSnapshot().rowErrors, {}) // прежний сбой строки снят в начале нового решения
  assert.equal(store.getSnapshot().error, null) // и прежняя ошибка тоже
  gate.resolve()
  await work
  // стирание так же
  server.on['POST ' + QUARANTINE] = async () => reply(409, { error: 'нельзя' })
  await store.remove(k[0].path)
  assert.deepEqual(Object.keys(store.getSnapshot().rowErrors), [k[0].path])
  const hold = deferred()
  server.on['POST ' + QUARANTINE] = async () => { await hold.promise; return reply(200, {}) }
  const again = store.remove(k[0].path)
  await flush()
  assert.deepEqual(store.getSnapshot().rowErrors, {})
  hold.resolve()
  await again
})

test('ответ не того вида (не объект, список не списком) — сбой в снимке, а не исключение; опрос продолжается', async () => {
  const { server, clock, store } = await setup()
  server.on['GET ' + INBOX] = async () => reply(200, null)
  store.start()
  await flush()
  assert.equal(store.getSnapshot().error.kind, 'failed')
  assert.equal(store.getSnapshot().status, null)
  assert.deepEqual(clock.waits(), [30000])
  server.on['GET ' + INBOX] = async () => reply(200, 'строка вместо объекта') // не null: тут обращение к полям не падает само
  await clock.advance(30000)
  assert.equal(store.getSnapshot().error.kind, 'failed')
  assert.equal(store.getSnapshot().status, null)
  delete server.on['GET ' + INBOX]
  server.on['GET ' + QUEUE] = async () => reply(200, { items: 'не список' })
  await clock.advance(30000)
  assert.equal(store.getSnapshot().error.kind, 'failed')
  assert.equal(store.getSnapshot().status.waiting, 3) // состояние принято, список — нет
  assert.equal(store.getSnapshot().queue, null)
  delete server.on['GET ' + QUEUE]
  await clock.advance(30000)
  assert.equal(store.getSnapshot().error, null)
  assert.deepEqual(store.getSnapshot().queue, [])
})

test('после стирания хранилище свободно', async () => {
  const k = [item('quarantine', 'a.exe')]
  const { store } = await setup({ quarantine: k })
  store.start()
  await flush()
  await store.remove(k[0].path)
  assert.equal(store.getSnapshot().busy, false)
})

test('пустое или безымянное сообщение об отказе отказом не считается: это общий сбой', async () => {
  const { exports } = await load()
  for (const error of ['', { text: '' }, { code: 'x' }, null, 7]) {
    const client = exports.parts.createClient(async () => reply(409, { error }))
    await assert.rejects(client.tokens(), (e) => e.kind === 'failed' && e.status === 409 && e.refusal === undefined, JSON.stringify(error))
  }
})
