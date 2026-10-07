// Счётчик в заголовке вкладки: «Archive (N)» при N > 0, иначе «Archive»; число берётся из общего хранилища (FR-85).
import assert from 'node:assert/strict'
import test from 'node:test'

import { item } from './kit-archive.mjs'
import { boot } from './kit-plugin.mjs'
import { find, reply } from './kit.mjs'

const INBOX = 'api/flyarchive.inbox'
const names = (app) => app.text().trim()

/** boot, который сам возвращает document; тест кладёт done() в t.after. */
const setup = async (t, options) => {
  const plugin = await boot(options)
  t.after(plugin.done)
  return plugin
}

// ── число и его отсутствие ──────────────────────────────────────
test('файлов на решение больше нуля — заголовок «Archive (N)»', async (t) => {
  const { title } = await setup(t, { archive: { status: { attention: 3 } } })
  assert.equal(names(await title()), 'Archive (3)')
})

test('файлов на решение нет — заголовок просто «Archive», без «(0)»', async (t) => {
  const { title } = await setup(t, { archive: { status: { attention: 0 } } })
  assert.equal(names(await title()), 'Archive')
})

test('одно и много: число не склоняется и не обрезается', async (t) => {
  for (const n of [1, 12, 1234]) {
    const { title } = await setup(t, { archive: { status: { attention: n } } })
    assert.equal(names(await title()), `Archive (${n})`)
  }
})

test('до первого ответа сервера (страница скрыта, опроса не было) — «Archive», без «undefined» и «NaN»', async (t) => {
  const { title, server } = await setup(t, { hidden: true })
  assert.equal(server.calls.length, 0)
  assert.equal(names(await title()), 'Archive')
})

test('сбой первого опроса — «Archive»', async (t) => {
  const { title } = await setup(t, { archive: { offline: true } })
  assert.equal(names(await title()), 'Archive')
})

// ── меняется без переоткрытия вкладки ───────────────────────────
test('заголовок меняется сам, пока тот же экземпляр на экране: счётчик вырос, упал, обнулился', async (t) => {
  const { title, server, clock } = await setup(t, { archive: { status: { attention: 0 } } })
  const app = await title()
  assert.equal(names(app), 'Archive')
  server.status.attention = 5
  await clock.advance(30000)
  await app.settle()
  assert.equal(names(app), 'Archive (5)')
  server.status.attention = 2
  await clock.advance(30000)
  await app.settle()
  assert.equal(names(app), 'Archive (2)')
  server.status.attention = 0
  await clock.advance(30000)
  await app.settle()
  assert.equal(names(app), 'Archive')
})

test('число берётся из общего хранилища: решение на вкладке сразу уменьшает его, лишних запросов заголовок не делает', async (t) => {
  const queue = [item('queue', 'a.txt'), item('queue', 'b.txt')]
  const { title, tab, server } = await setup(t, { archive: { queue } })
  const heading = await title()
  const body = await tab()
  assert.equal(names(heading), 'Archive (2)')
  const calls = server.calls.length
  await body.click(body.button('Accept'))
  assert.equal(names(heading), 'Archive (1)')
  // решение и перечитывание — дело вкладки; заголовок сам ничего не спрашивает
  const own = server.calls.slice(calls).filter((c) => c.method === 'GET' && c.route === INBOX)
  assert.equal(own.length, 1)
})

test('два заголовка (два сеанса) показывают одно и то же число и не удваивают опрос', async (t) => {
  const { title, server } = await setup(t, { archive: { status: { attention: 4 } } })
  const first = await title()
  const second = await title()
  assert.equal(names(first), 'Archive (4)')
  assert.equal(names(second), 'Archive (4)')
  assert.equal(server.count(INBOX), 1)
})

// ── откуда число ────────────────────────────────────────────────
test('старый сервер без поля attention: число — очередь плюс карантин; без них — «Archive»', async (t) => {
  const without = (extra) => ({ ['GET ' + INBOX]: async (_, s) => { const view = { ...s.view(), ...extra }; delete view.attention; return reply(200, view) } })
  const a = await setup(t, { archive: { on: without({ queue: 1, quarantine: 2 }) } })
  assert.equal(names(await a.title()), 'Archive (3)')
  const b = await setup(t, { archive: { on: without({ queue: 0, quarantine: 0 }) } })
  assert.equal(names(await b.title()), 'Archive')
})

test('негодное значение attention не рисуется числом', async (t) => {
  for (const bad of ['3', null, -2, 1.5, Number.NaN, {}, [], true]) {
    const { title } = await setup(t, { archive: { on: { ['GET ' + INBOX]: async (_, s) => reply(200, { ...s.view(), attention: bad, queue: 0, quarantine: 0 }) } } })
    assert.equal(names(await title()), 'Archive', String(bad))
  }
})

// ── словарь ─────────────────────────────────────────────────────
test('подписи — из словаря: «title» и «title.count» с числом n; t от DSH главнее связанного словаря', async (t) => {
  const { title, ctx } = await setup(t, { archive: { status: { attention: 3 } } })
  const marked = await title({ t: (key, params) => `«${key}:${params ? params.n : ''}»` })
  assert.equal(names(marked), '«title.count:3»')
  const { en, zh } = ctx.dictionaries.flyarchive
  assert.equal(en['title.count'], 'Archive ({n})')
  assert.equal(zh['title.count'], 'Archive ({n})')
  assert.equal(en.title, 'Archive')
  const calm = await setup(t, { archive: { status: { attention: 0 } } })
  assert.equal(names(await calm.title({ t: (key) => `«${key}»` })), '«title»')
})

test('тип вкладки в реестре DSH называется просто «Archive»: счётчик живёт только в заголовке на экране', async (t) => {
  const { ctx } = await setup(t, { archive: { status: { attention: 7 } } })
  assert.equal(ctx.live.tabs[0].title(), 'Archive')
})

test('рядом со счётчиком остаётся значок вкладки', async (t) => {
  const { title } = await setup(t, { archive: { status: { attention: 7 } } })
  const app = await title()
  assert.ok(find(app.tree(), (n) => n.type === 'svg'))
})
