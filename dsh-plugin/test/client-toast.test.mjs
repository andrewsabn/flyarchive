// Всплывающее сообщение об окончании пачки: событие общего хранилища, Toast из примитивов DSH, собственный корень React (FR-86).
// Примитивы и react-dom/client подставные (kit-plugin.mjs); время идёт на управляемых таймерах, архив подставной.
import assert from 'node:assert/strict'
import test from 'node:test'

import { BATCH, item } from './kit-archive.mjs'
import { boot } from './kit-plugin.mjs'
import { flush } from './kit.mjs'

const PRIMITIVES = '@deepseek-ai/dsh-client-ui-primitives'
const DOM = 'react-dom/client'
const INBOX = 'api/flyarchive.inbox'
const CYRILLIC = /[Ѐ-ӿ]/
const batchId = (n) => `20261004-15${String(n).padStart(2, '0')}00`

const setup = async (t, options) => {
  const plugin = await boot(options)
  t.after(plugin.done)
  return plugin
}

/** Пачка кончилась: номер последней пачки сменился, ждёт решения attention файлов, замечаний problems. Время идёт до следующего опроса. */
async function finish({ server, clock }, id, { attention = 0, problems = 0 } = {}) {
  server.status.last_batch = { batch: id, time: '2026-10-04T12:00:00Z', accept: 1 }
  server.status.attention = attention
  server.status.problems = problems
  await clock.advance(30000)
  await flush()
}

const lastToast = (plugin) => plugin.toast.toasts.at(-1)
const labels = (plugin) => (lastToast(plugin).actions ?? []).map((a) => a.label)
const buttons = (plugin) => plugin.toast.handle().buttons().map((b) => b.props.children.join(''))

// ── когда показывается ──────────────────────────────────────────
test('сменился номер последней пачки между двумя опросами, ждут решения 2 файла и есть 1 замечание — сообщение с обоими числами', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 2, problems: 1 })
  assert.equal(p.toast.mounts.length, 1)
  assert.equal(lastToast(p).text, 'Intake finished: 2 files need a decision, 1 problem.')
  assert.ok(p.toast.shown().includes('2 files need a decision'))
})

test('первый опрос после загрузки страницы сообщения не показывает, даже когда есть и файлы на решение, и замечания', async (t) => {
  const p = await setup(t, { archive: { status: { attention: 3, problems: 2 } } })
  assert.equal(p.server.count(INBOX), 1)
  assert.equal(p.toast.mounts.length, 0)
  assert.equal(p.toast.toasts.length, 0)
  assert.equal(p.toast.shown(), '')
})

test('опросы с тем же номером пачки сообщения не дают, что бы ни менялось в счётчиках', async (t) => {
  const p = await setup(t)
  for (const [attention, problems] of [[1, 0], [5, 3], [0, 0], [9, 9]]) {
    p.server.status.attention = attention
    p.server.status.problems = problems
    await p.clock.advance(30000)
  }
  assert.ok(p.server.count(INBOX) >= 5)
  assert.equal(p.toast.mounts.length, 0)
})

test('только файлы на решение: сообщение называет их и не называет замечания', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 3 })
  assert.equal(lastToast(p).text, 'Intake finished: 3 files need a decision.')
})

test('только замечания: сообщение называет их и не называет файлы', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { problems: 2 })
  assert.equal(lastToast(p).text, 'Intake finished: 2 problems.')
})

test('один файл и одно замечание — в единственном числе', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 1 })
  assert.equal(lastToast(p).text, 'Intake finished: 1 file needs a decision.')
  await finish(p, batchId(2), { problems: 1 })
  assert.equal(lastToast(p).text, 'Intake finished: 1 problem.')
})

test('пачка кончилась без файлов на решение и без замечаний — сообщения нет', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 0, problems: 0 })
  assert.equal(p.toast.mounts.length, 0)
  assert.equal(p.toast.shown(), '')
  // и позже, при той же пачке, сообщение уже не придёт: событие — смена номера, а не рост счётчика
  p.server.status.attention = 4
  await p.clock.advance(30000)
  assert.equal(p.toast.mounts.length, 0)
})

test('об одной пачке сообщение показывается один раз: ни повторные опросы, ни закрытие не вызывают его снова', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 2, problems: 1 })
  assert.equal(p.toast.mounts.length, 1)
  await p.clock.advance(30000)
  await p.clock.advance(30000)
  assert.equal(p.toast.mounts.length, 1)
  lastToast(p).onDone() // сообщение погасло само
  await p.toast.handle().settle()
  assert.equal(p.toast.shown(), '')
  await p.clock.advance(30000)
  await p.clock.advance(30000)
  assert.equal(p.toast.mounts.length, 1)
  assert.equal(p.toast.shown(), '')
})

test('возврат к прежнему номеру пачки не повод для нового сообщения: об одной пачке — один раз и при «дрожании» списка', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 2 })
  await finish(p, BATCH, { attention: 2 }) // прежняя пачка снова последняя
  await finish(p, batchId(1), { attention: 2 })
  assert.equal(p.toast.mounts.length, 1)
})

test('следующая пачка — новое сообщение, даже с тем же текстом: сообщение появляется заново, а не продолжает прежнее', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 2, problems: 1 })
  await finish(p, batchId(2), { attention: 2, problems: 1 })
  assert.equal(p.toast.mounts.length, 2)
  assert.deepEqual(p.toast.mounts, ['Intake finished: 2 files need a decision, 1 problem.', 'Intake finished: 2 files need a decision, 1 problem.'])
  assert.equal(p.toast.unmounts.length, 1) // прежнее снято, не наложено
})

test('сравнение идёт с прошлым опросом, а не с первым после загрузки: пачка сменилась, а потом ещё раз', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 1 })
  lastToast(p).onDone()
  await p.toast.handle().settle()
  await finish(p, batchId(2), { attention: 4 })
  assert.equal(lastToast(p).text, 'Intake finished: 4 files need a decision.')
  assert.equal(p.toast.mounts.length, 2)
})

test('первая пачка за всё время (раньше пачек не было) — сообщение, если есть что решать', async (t) => {
  const p = await setup(t, { archive: { status: { last_batch: null } } })
  await finish(p, batchId(1), { attention: 2 })
  assert.equal(p.toast.mounts.length, 1)
})

test('последняя пачка пропала из состояния — сообщения нет', async (t) => {
  const p = await setup(t)
  p.server.status.last_batch = null
  p.server.status.attention = 5
  await p.clock.advance(30000)
  assert.equal(p.toast.mounts.length, 0)
})

test('первый опрос не удался: базой служит первый удавшийся, а не пустота — сообщения о прежней пачке нет', async (t) => {
  const p = await setup(t, { archive: { offline: true, status: { attention: 3, problems: 1 } } })
  assert.equal(p.toast.mounts.length, 0)
  p.server.offline = false
  await p.clock.advance(30000)
  assert.equal(p.toast.mounts.length, 0)
  await finish(p, batchId(1), { attention: 3 })
  assert.equal(p.toast.mounts.length, 1)
})

test('сбой посреди работы: сравнение идёт с последним удавшимся опросом', async (t) => {
  const p = await setup(t)
  p.server.offline = true
  p.server.status.last_batch = { batch: batchId(1), time: '2026-10-04T12:00:00Z', accept: 1 }
  p.server.status.attention = 2
  await p.clock.advance(30000)
  assert.equal(p.toast.mounts.length, 0)
  p.server.offline = false
  await p.clock.advance(30000)
  assert.equal(p.toast.mounts.length, 1)
})

test('скрытая страница не опрашивает, а потому и не показывает; показанная опрашивает сразу и показывает, если пачка сменилась', async (t) => {
  const p = await setup(t)
  p.page.set(true)
  p.server.status.last_batch = { batch: batchId(1), time: '2026-10-04T12:00:00Z', accept: 1 }
  p.server.status.attention = 2
  await p.clock.advance(10 * 60 * 1000)
  assert.equal(p.toast.mounts.length, 0)
  p.page.set(false)
  await flush()
  assert.equal(p.toast.mounts.length, 1)
})

test('решения на вкладке меняют счётчики, но не номер пачки: сообщений они не вызывают', async (t) => {
  const p = await setup(t, { archive: { queue: [item('queue', 'a.txt')] } })
  const body = await p.tab()
  await body.click(body.button('Accept'))
  assert.equal(p.toast.mounts.length, 0)
})

// ── вид сообщения ───────────────────────────────────────────────
test('сообщение держится несколько секунд: срок показа задан и разумен, а текст — английский, из словаря', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 2, problems: 3 })
  const { holdMs, text } = lastToast(p)
  assert.ok(Number.isInteger(holdMs) && holdMs >= 5000 && holdMs <= 20000, String(holdMs))
  assert.ok(!CYRILLIC.test(text), text)
  assert.deepEqual(p.ctx.missingKeys, [])
  const { en, zh } = p.ctx.dictionaries.flyarchive
  for (const key of ['toast.text', 'toast.attention.one', 'toast.attention.many', 'toast.problems.one', 'toast.problems.many', 'toast.open', 'toast.close']) {
    assert.ok(typeof en[key] === 'string' && en[key] !== '' && zh[key] === en[key], key)
  }
})

test('срок показа не растёт от перерисовок: он один и тот же при каждой отрисовке', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 2 })
  await p.clock.advance(30000)
  await p.clock.advance(30000)
  assert.equal(new Set(p.toast.toasts.map((props) => props.holdMs)).size, 1)
})

test('уходит по крестику: кнопка «×» убирает сообщение, и оно не возвращается', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 2 })
  assert.ok(labels(p).includes('×'))
  await p.toast.handle().click(p.toast.handle().button('×'))
  assert.equal(p.toast.shown(), '')
  await p.clock.advance(30000)
  assert.equal(p.toast.mounts.length, 1)
  assert.equal(p.toast.shown(), '')
})

test('уходит сам: когда Toast сообщает, что погас, сообщение снимается', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 2 })
  assert.notEqual(p.toast.shown(), '')
  lastToast(p).onDone()
  await p.toast.handle().settle()
  assert.equal(p.toast.shown(), '')
  assert.equal(p.toast.unmounts.length, 1)
})

test('новое сообщение, пока прежнее ещё на экране, заменяет его: на экране одно', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 1 })
  await finish(p, batchId(2), { attention: 7 })
  assert.equal(p.toast.handle().all((n) => n.props?.role === 'alert').length, 1)
  assert.ok(p.toast.shown().includes('7 files'))
})

test('Toast прежнего сообщения гаснет уже после того, как его заменило новое: новое остаётся на экране', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 1 })
  const first = lastToast(p)
  await finish(p, batchId(2), { attention: 7 })
  first.onDone() // запоздалый вызов от прежнего сообщения
  await p.toast.handle().settle()
  assert.ok(p.toast.shown().includes('7 files'), p.toast.shown())
  assert.equal(p.toast.unmounts.length, 1) // снято было одно, прежнее
})

// ── кнопка «Open» ───────────────────────────────────────────────
test('есть сеанс на экране — в сообщении кнопка «Open»; она открывает вкладку Archive через службу правой панели и убирает сообщение', async (t) => {
  const p = await setup(t)
  p.ctx.session('session-1')
  await finish(p, batchId(1), { attention: 2 })
  assert.deepEqual(labels(p), ['Open', '×'])
  await p.toast.handle().click(p.toast.handle().button('Open'))
  assert.equal(p.ctx.opened.length, 1)
  assert.equal(p.ctx.opened[0][0], p.ctx.live.tabs[0].kind) // вид вкладки — тот, под которым она зарегистрирована
  assert.equal(p.toast.shown(), '')
})

test('сеанса на экране нет (стартовый экран) — кнопки «Open» нет, остаётся крестик', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 2 })
  assert.deepEqual(labels(p), ['×'])
  assert.equal(p.toast.handle().button('Open'), null)
  assert.deepEqual(p.ctx.opened, [])
})

test('сеанс появился или ушёл, пока сообщение на экране, — кнопка появляется и исчезает сама', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 2 })
  assert.equal(p.toast.handle().button('Open'), null)
  p.ctx.session('session-1')
  await p.toast.handle().settle()
  assert.ok(p.toast.handle().button('Open'))
  p.ctx.session(undefined)
  await p.toast.handle().settle()
  assert.equal(p.toast.handle().button('Open'), null)
})

test('кнопка «Open» нажата, а службе открывать нечего (сеанс ушёл, пока тянулась рука): без падения, ошибка записана, сообщение снято', async (t) => {
  const p = await setup(t)
  p.ctx.session('session-1')
  await finish(p, batchId(1), { attention: 2 })
  const open = p.toast.handle().button('Open')
  p.ctx.sidebarRight.openTab = () => { throw new Error('no session on screen') }
  const logged = []
  const savedError = console.error
  console.error = (...args) => logged.push(args)
  try {
    await p.toast.handle().click(open)
  } finally {
    console.error = savedError
  }
  assert.equal(logged.length, 1)
  assert.ok(!CYRILLIC.test(String(logged[0][0])), String(logged[0][0]))
  assert.equal(p.toast.shown(), '')
})

test('у DSH нет служб правой панели: сообщение есть, кнопки «Open» нет', async (t) => {
  const p = await setup(t, { without: ['sidebarRight', 'sidebarRightTabs'] })
  await finish(p, batchId(1), { attention: 2 })
  assert.equal(p.toast.mounts.length, 1)
  assert.deepEqual(labels(p), ['×'])
})

// ── свой корень React ───────────────────────────────────────────
test('Toast рисуется в собственном корне React плагина: createRoot на элементе в document.body, один корень на плагин', async (t) => {
  const p = await setup(t)
  assert.equal(p.toast.roots.length, 1)
  const [root] = p.toast.roots
  assert.equal(root.container.parentNode, p.doc.body)
  assert.ok(p.doc.body.children.includes(root.container))
  assert.ok(root.renders >= 1) // корень поднят заранее: сообщению не нужно ждать его
  await finish(p, batchId(1), { attention: 2 })
  await finish(p, batchId(2), { attention: 2 })
  assert.equal(p.toast.roots.length, 1) // новые сообщения корень не плодят
  assert.equal(p.doc.body.children.length, 1)
})

test('снятие плагина убирает корень, элемент и сообщение, а новых опросов и сообщений больше нет', async (t) => {
  const p = await setup(t)
  await finish(p, batchId(1), { attention: 2 })
  assert.notEqual(p.toast.shown(), '')
  const [root] = p.toast.roots
  const calls = p.server.calls.length
  p.ctx.dispose()
  assert.equal(root.unmounted, true)
  assert.ok(!p.doc.body.children.includes(root.container))
  assert.equal(p.doc.body.children.length, 0)
  assert.equal(p.toast.shown(), '')
  await finish(p, batchId(2), { attention: 9 })
  await p.clock.advance(5 * 60 * 1000)
  assert.equal(p.server.calls.length, calls)
  assert.equal(p.toast.roots.length, 1)
})

test('снятие плагина без единого сообщения тоже убирает корень и элемент', async (t) => {
  const p = await setup(t)
  const [root] = p.toast.roots
  p.ctx.dispose()
  assert.equal(root.unmounted, true)
  assert.equal(p.doc.body.children.length, 0)
})

test('корень не снимается (unmount бросает): снятие плагина не падает, ошибка записана, элемент всё равно убран', async (t) => {
  const p = await setup(t, { modules: { [DOM]: { createRoot: () => ({ render() {}, unmount() { throw new Error('не снялся') } }) } } })
  assert.equal(p.doc.body.children.length, 1)
  const logged = []
  const savedError = console.error
  console.error = (...args) => logged.push(args)
  try {
    p.ctx.dispose()
  } finally {
    console.error = savedError
  }
  assert.equal(p.doc.body.children.length, 0)
  assert.equal(logged.length, 1)
  assert.ok(!CYRILLIC.test(String(logged[0][0])), String(logged[0][0]))
})

test('повторная установка (плагин перезагрузили) даёт один новый корень, а не второй рядом со старым', async (t) => {
  const first = await setup(t)
  assert.equal(first.doc.body.children.length, 1)
  first.ctx.dispose()
  assert.equal(first.doc.body.children.length, 0)
  const second = await setup(t, { doc: first.doc }) // та же страница
  assert.equal(second.doc.body.children.length, 1)
  assert.equal(second.toast.roots.length, 1)
  second.ctx.dispose()
  assert.equal(first.doc.body.children.length, 0)
})

// ── без Toast или react-dom/client ──────────────────────────────
// третье значение — сколько записей в console.error: нет модуля — это нормальная старая версия DSH и тишина; упавший корень — одна запись
const WITHOUT = [
  ['нет примитивов (require бросает)', { toast: false }, 0],
  ['нет react-dom/client (require бросает)', { modules: { [DOM]: new Error('нет модуля') } }, 0],
  ['нет и того, и другого', { modules: { [PRIMITIVES]: new Error('нет модуля'), [DOM]: new Error('нет модуля') } }, 0],
  ['в примитивах нет Toast', { modules: { [PRIMITIVES]: {} } }, 0],
  ['Toast не компонент', { modules: { [PRIMITIVES]: { Toast: 'не функция' } } }, 0],
  ['в react-dom/client нет createRoot', { modules: { [DOM]: {} } }, 0],
  ['createRoot бросает', { modules: { [DOM]: { createRoot: () => { throw new Error('нет места') } } } }, 1],
  ['корень не умеет рисовать', { modules: { [DOM]: { createRoot: () => ({}) } } }, 1],
  ['модуль — null', { modules: { [PRIMITIVES]: null, [DOM]: null } }, 0],
]
for (const [title, options, logged] of WITHOUT) {
  test(`${title}: плагин работает без сообщения и не падает — вкладка, раздел настроек и опрос на месте`, async (t) => {
    const p = await setup(t, options)
    await finish(p, batchId(1), { attention: 2, problems: 1 }) // событие есть, показывать нечем
    assert.equal(p.doc.body.children.length, 0) // и лишнего элемента в body не осталось: ни от несостоявшегося корня, ни от упавшего
    assert.equal(p.errors.length, logged, JSON.stringify(p.errors.map((e) => String(e[0]))))
    const settings = await p.settings()
    assert.ok(settings.text().includes('Status'))
    const body = await p.tab()
    assert.ok(body.text().includes('Waiting in the inbox folder'))
    await p.clock.advance(30000)
    assert.ok(p.server.count(INBOX) >= 3)
    p.ctx.dispose() // и снимается без ошибок
    assert.equal(p.doc.body.children.length, 0)
  })
}

test('страница без document (не браузер): плагин работает без сообщения', async (t) => {
  const p = await setup(t, { withDocument: false })
  await finish(p, batchId(1), { attention: 2 })
  assert.equal(p.toast.roots.length, 0)
  assert.deepEqual(p.errors, []) // не браузер — не ошибка, а тишина
  p.ctx.dispose()
})
