// Раздел «Archive» в настройках DSH по-новому: Status, Folders, Intake, Tokens, Delete document, Access journal (FR-84).
// Плагин ставится на подставной DSH, раздел рисуется малым рисовальщиком с состоянием, архив и время подставные.
// Состояние раздел берёт из общего хранилища вкладки и сам сервер не опрашивает.
import assert from 'node:assert/strict'
import test from 'node:test'

import { BATCH, item } from './kit-archive.mjs'
import { boot } from './kit-plugin.mjs'
import { find, flush, reply, text } from './kit.mjs'

const INBOX = 'api/flyarchive.inbox'
const QUEUE = 'api/flyarchive.queue'
const QUARANTINE = 'api/flyarchive.quarantine'
const TOKENS = 'api/flyarchive.tokens'
const REVOKE = 'api/flyarchive.tokens.revoke'
const JOURNAL = 'api/flyarchive.journal'
const REMOVE = 'api/flyarchive.doc.delete'

// токены и журнал — как их отдаёт настоящий сервер: слова состояния русские (данные), подписи экрана — английские
const TOKEN_ROWS = [
  { name: 'dsh-local', level: 'local', created: '2026-10-03T21:04:00Z', expires: null, last_used: '2026-10-04T08:53:32Z', revoked: null, state: 'действует' },
  { name: 'laptop', level: 'read', created: '2026-10-04T09:00:00Z', expires: '2027-01-02T09:00:00Z', last_used: null, revoked: null, state: 'действует' },
  { name: 'old', level: 'full', created: '2026-09-01T09:00:00Z', expires: null, last_used: null, revoked: '2026-09-02T10:00:00Z', state: 'отозван' },
  { name: 'guest', level: 'read', created: '2026-09-01T09:00:00Z', expires: '2026-09-02T09:00:00Z', last_used: null, revoked: null, state: 'просрочен' },
]
const RECORD_ROWS = [
  { ts: '2026-10-04T09:13:59Z', server: 'search', client: 'laptop', tool: '/api/search', params: { q: 'capital', k: '3' }, status: 200, outcome: 'ok', ms: 540, via: 'tailnet', ip: '203.0.113.14' },
  { ts: '2026-10-04T09:14:00Z', server: 'mcp', client: '-', tool: 'mcp', params: {}, status: 401, outcome: 'denied: token needed', ms: 1, via: null, ip: '127.0.0.1' },
]

const setup = async (t, options = {}) => {
  const plugin = await boot({ ...options, archive: { tokens: TOKEN_ROWS, records: RECORD_ROWS, ...(options.archive ?? {}) } })
  t.after(plugin.done)
  return plugin
}

const section = (app, name) => app.find((n) => n.props?.['data-section'] === name)
const inSection = (app, name, match) => find(section(app, name), match)
const button = (app, name, label) => inSection(app, name, (n) => n.type === 'button' && text(n) === label)
const input = (app, name, field) => inSection(app, name, (n) => (n.type === 'input' || n.type === 'select') && n.props.name === field)
const enter = async (app, node, value) => {
  assert.ok(node, 'такого поля на экране нет')
  node.props.onChange({ target: { value } })
  await app.settle()
}
const collect = (node, match, out = []) => {
  if (node === null || node === undefined || typeof node !== 'object') return out
  if (Array.isArray(node)) {
    node.forEach((child) => collect(child, match, out))
    return out
  }
  if (match(node)) out.push(node)
  return collect(node.props.children, match, out)
}
const allIn = (app, name, match) => collect(section(app, name), match)
const alerts = (app) => app.all((n) => n.props?.role === 'alert').map((n) => text(n))
const holdOpen = (server, route) => {
  let release
  server.on[`POST ${route}`] = (body, s) => new Promise((resolve) => { release = () => resolve(reply(200, s.view())) })
  return () => release()
}

// ── состав и порядок ────────────────────────────────────────────
test('раздел встаёт в настройки под именем «Archive» после штатных разделов', async (t) => {
  const { ctx } = await setup(t)
  const [settings] = ctx.live.slots.filter((s) => s.options.name === 'settings.section')
  assert.equal(settings.options.id, 'flyarchive')
  assert.equal(settings.options.label(), 'Archive')
  assert.ok(settings.options.order > 20)
  assert.equal(typeof settings.component, 'function')
})

test('заголовок раздела — «Archive», а части идут в порядке: Status, Folders, Intake, Tokens, Delete document, Access journal', async (t) => {
  const { settings } = await setup(t)
  const app = await settings()
  assert.deepEqual(app.all((n) => n.type === 'h2').map(text), ['Archive'])
  assert.deepEqual(app.all((n) => n.type === 'h3').map(text), ['Status', 'Folders', 'Intake', 'Tokens', 'Delete document', 'Access journal'])
  for (const name of ['status', 'folders', 'intake', 'tokens', 'delete', 'journal']) assert.ok(section(app, name), name)
})

test('очереди и карантина в настройках больше нет: ни таблиц, ни кнопок решений, ни обращений к спискам', async (t) => {
  const { settings, server } = await setup(t, { archive: { queue: [item('queue', 'a.txt')], quarantine: [item('quarantine', 'b.exe')] } })
  const app = await settings()
  for (const label of ['Accept', 'Quarantine', 'Return', 'Delete', 'Accept selected', 'Select all', 'Run now']) assert.equal(app.button(label), null, label)
  assert.ok(!app.text().includes('a.txt') && !app.text().includes('b.exe'))
  assert.ok(!/Needs decision|Review queue|Quarantine/i.test(app.text()), app.text())
  assert.equal(app.all((n) => n.type === 'table' && text(n).includes('Score')).length, 0)
  // списки читает только хранилище, один раз при первом опросе
  assert.equal(server.count(QUEUE), 1)
  assert.equal(server.count(QUARANTINE), 1)
})

test('все подписи раздела — из словаря: ни одного ключа без подписи, и в покое, и с открытыми вопросами', async (t) => {
  const { settings, ctx } = await setup(t)
  const app = await settings()
  await enter(app, input(app, 'folders', 'inbox'), '/data/new-inbox')
  await app.click(button(app, 'folders', 'Change'))
  await enter(app, input(app, 'delete', 'path'), 'a.pdf')
  await app.click(button(app, 'delete', 'Remove from archive'))
  await app.click(app.all((n) => n.type === 'button' && text(n) === 'Revoke')[0])
  await enter(app, input(app, 'intake', 'period'), '5')
  assert.deepEqual(ctx.missingKeys, [])
})

// ── раздел берёт состояние из общего хранилища и сам не опрашивает ──
test('раздел не опрашивает сервер сам: состояние читает одно хранилище, сколько бы раз раздел ни открывали и закрывали', async (t) => {
  const { settings, server } = await setup(t)
  assert.equal(server.count(INBOX), 1) // опрос хранилища при установке плагина
  for (let i = 0; i < 3; i++) {
    const app = await settings()
    assert.equal(server.count(INBOX), 1)
    app.unmount()
  }
  assert.equal(server.count(INBOX), 1)
})

test('раздел не заводит таймеров: ожидает один — опрос хранилища; со временем запросов состояния ровно столько, сколько опросов', async (t) => {
  const { settings, server, clock } = await setup(t)
  const app = await settings()
  assert.deepEqual(clock.waits(), [30000])
  await clock.advance(30000)
  await clock.advance(30000)
  assert.equal(server.count(INBOX), 3)
  assert.deepEqual(clock.waits(), [30000])
  app.unmount()
  assert.deepEqual(clock.waits(), [30000]) // опрос принадлежит плагину, а не разделу
})

test('раздел следует за хранилищем: новое состояние попадает в строку Status без запросов из раздела', async (t) => {
  const { settings, server, clock } = await setup(t)
  const app = await settings()
  assert.ok(text(section(app, 'status')).includes('Awaiting a decision: 0'))
  server.status.attention = 4
  server.status.waiting = 9
  await clock.advance(30000)
  await app.settle()
  const line = text(section(app, 'status'))
  assert.ok(line.includes('Awaiting a decision: 4') && line.includes('Waiting in the inbox folder: 9'), line)
  assert.equal(server.count(INBOX), 2) // один опрос хранилища, раздел своего не добавил
})

test('решение на вкладке сразу видно в настройках: тот же снимок хранилища', async (t) => {
  const { settings, tab, server } = await setup(t, { archive: { queue: [item('queue', 'a.txt'), item('queue', 'b.txt')] } })
  const app = await settings()
  const body = await tab()
  assert.ok(text(section(app, 'status')).includes('Awaiting a decision: 2'))
  await body.click(body.button('Accept'))
  await app.settle()
  assert.ok(text(section(app, 'status')).includes('Awaiting a decision: 1'))
  assert.equal(server.count(INBOX), 2)
})

test('без служб правой панели раздел работает: строка Status, папки и остальное не зависят от вкладки', async (t) => {
  for (const without of [['sidebarRight', 'sidebarRightTabs'], ['sidebarRight'], ['sidebarRightTabs']]) {
    const p = await setup(t, { without })
    assert.equal(p.ctx.live.tabs.length, 0, without.join())
    const app = await p.settings()
    const line = text(section(app, 'status'))
    assert.ok(line.includes('Waiting in the inbox folder: 3'), line)
    assert.ok(line.includes('accepted 11, needs review 2'), line) // названия решений — из словаря сообщений: он грузится и без вкладки
    assert.equal(input(app, 'folders', 'inbox').props.value, '/home/x/inbox')
    assert.ok(text(section(app, 'tokens')).includes('laptop'))
    assert.equal(p.server.count(INBOX), 1)
    await p.clock.advance(30000)
    await app.settle()
    assert.equal(p.server.count(INBOX), 2, 'опрос идёт и без вкладки: он нужен разделу')
  }
})

test('без службы языка раздел остаётся английским: подписи берутся из того же словаря напрямую', async (t) => {
  const p = await setup(t, { without: ['locale'] })
  const app = await p.settings()
  assert.deepEqual(app.all((n) => n.type === 'h3').map(text), ['Status', 'Folders', 'Intake', 'Tokens', 'Delete document', 'Access journal'])
  assert.ok(text(section(app, 'status')).includes('Waiting in the inbox folder: 3'))
  const [settings] = p.ctx.live.slots.filter((s) => s.options.name === 'settings.section')
  assert.equal(settings.options.label(), 'Archive')
})

// ── Status ──────────────────────────────────────────────────────
test('Status: одна строка — сколько ждёт во входящей папке, сколько файлов ждёт решения, итог последней пачки — и подсказка про вкладку', async (t) => {
  const { settings } = await setup(t, { archive: { queue: [item('queue', 'a.txt')], quarantine: [item('quarantine', 'b.exe')], status: { waiting: 5 } } })
  const app = await settings()
  const box = section(app, 'status')
  const parts = box.props.children.flat().filter((child) => child !== null && child !== false)
  assert.equal(parts.length, 2) // строка и подсказка
  const [line, hint] = parts.map(text)
  assert.equal(line, `Waiting in the inbox folder: 5 · Awaiting a decision: 2 · Last batch ${BATCH}: accepted 11, needs review 2`)
  assert.ok(/Archive tab/.test(hint) && /right panel/.test(hint), hint)
})

test('Status: пачек не было — так и сказано; решений нет — «0»', async (t) => {
  const { settings } = await setup(t, { archive: { status: { last_batch: null } } })
  const line = text(section(await settings(), 'status'))
  assert.ok(line.includes('No batches yet') && line.includes('Awaiting a decision: 0'), line)
})

test('Status: до первого ответа — «Loading…»; сбой связи сказан здесь же, а не молчанием', async (t) => {
  const hidden = await setup(t, { hidden: true })
  assert.ok(text(section(await hidden.settings(), 'status')).includes('Loading'))
  const offline = await setup(t, { archive: { offline: true } })
  const box = text(section(await offline.settings(), 'status'))
  assert.match(box, /No connection to DSH/)
})

// ── Folders ─────────────────────────────────────────────────────
test('Folders: входящая папка — поле с текущим адресом и «Change»; изменений нет — «Change» не нажимается', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  assert.equal(input(app, 'folders', 'inbox').props.value, '/home/x/inbox')
  assert.equal(button(app, 'folders', 'Change').props.disabled, true)
  assert.equal(await app.click(button(app, 'folders', 'Change')), false)
  assert.equal(server.posts(INBOX).length, 0)
})

test('Folders: пустое поле и тот же адрес — «Change» не нажимается', async (t) => {
  const { settings } = await setup(t)
  const app = await settings()
  for (const value of ['', '   ', '/home/x/inbox', ' /home/x/inbox ']) {
    await enter(app, input(app, 'folders', 'inbox'), value)
    assert.equal(button(app, 'folders', 'Change').props.disabled, true, JSON.stringify(value))
  }
})

test('Folders: «Change» только спрашивает — предупреждение, что архив заберёт всё, что лежит в новой папке; запроса ещё нет', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  await enter(app, input(app, 'folders', 'inbox'), '/data/new-inbox')
  await app.click(button(app, 'folders', 'Change'))
  const box = text(section(app, 'folders'))
  assert.match(box, /take in everything that lies in the new folder/)
  assert.ok(button(app, 'folders', 'Yes, change the inbox folder') && button(app, 'folders', 'Cancel'))
  assert.equal(server.posts(INBOX).length, 0)
})

test('Folders: «Cancel» закрывает вопрос, запроса нет, введённый адрес остаётся для правки', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  await enter(app, input(app, 'folders', 'inbox'), '/data/new-inbox')
  await app.click(button(app, 'folders', 'Change'))
  await app.click(button(app, 'folders', 'Cancel'))
  assert.equal(button(app, 'folders', 'Yes, change the inbox folder'), null)
  assert.equal(input(app, 'folders', 'inbox').props.value, '/data/new-inbox')
  assert.equal(server.posts(INBOX).length, 0)
})

test('Folders: после подтверждения уходит один запрос с ключами path и confirm: true и ничем больше', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  await enter(app, input(app, 'folders', 'inbox'), '  /data/new-inbox  ') // пробелы по краям не часть адреса
  await app.click(button(app, 'folders', 'Change'))
  await app.click(button(app, 'folders', 'Yes, change the inbox folder'))
  assert.deepEqual(server.posts(INBOX).map((c) => c.body), [{ path: '/data/new-inbox', confirm: true }])
  assert.equal(input(app, 'folders', 'inbox').props.value, '/data/new-inbox') // состояние перечитано: адрес из хранилища
  assert.equal(button(app, 'folders', 'Yes, change the inbox folder'), null)
  assert.equal(button(app, 'folders', 'Change').props.disabled, true)
  assert.match(text(app.find((n) => n.props?.role === 'status')), /Inbox folder changed/)
})

test('Folders: правка адреса, пока вопрос открыт, закрывает вопрос: подтверждается тот адрес, о котором предупредили', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  await enter(app, input(app, 'folders', 'inbox'), '/data/one')
  await app.click(button(app, 'folders', 'Change'))
  await enter(app, input(app, 'folders', 'inbox'), '/data/two')
  assert.equal(button(app, 'folders', 'Yes, change the inbox folder'), null)
  assert.equal(server.posts(INBOX).length, 0)
  await app.click(button(app, 'folders', 'Change'))
  await app.click(button(app, 'folders', 'Yes, change the inbox folder'))
  assert.deepEqual(server.posts(INBOX).map((c) => c.body.path), ['/data/two'])
})

test('Folders: пока запрос в пути, кнопки заняты и второй запрос не уходит', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  const release = holdOpen(server, INBOX)
  await enter(app, input(app, 'folders', 'inbox'), '/data/new-inbox')
  await app.click(button(app, 'folders', 'Change'))
  await app.click(button(app, 'folders', 'Yes, change the inbox folder'))
  await flush()
  assert.equal(server.posts(INBOX).length, 1)
  assert.equal(button(app, 'folders', 'Change').props.disabled, true) // идёт запрос: всё занято
  assert.equal(await app.click(button(app, 'folders', 'Change')), false)
  release()
  await app.settle()
  assert.equal(server.posts(INBOX).length, 1)
  assert.equal(button(app, 'folders', 'Change').props.disabled, true) // состояние перечитано, менять нечего
})

test('Folders: отказ команды (папки нет, папка внутри архива) показан у поля сообщением команды, введённый адрес остаётся', async (t) => {
  const { settings, server } = await setup(t)
  server.on[`POST ${INBOX}`] = () => reply(409, { error: { code: 'inbox_missing', args: { path: '/data/none' }, text: 'The folder does not exist' } })
  const app = await settings()
  await enter(app, input(app, 'folders', 'inbox'), '/data/none')
  await app.click(button(app, 'folders', 'Change'))
  await app.click(button(app, 'folders', 'Yes, change the inbox folder'))
  const folders = section(app, 'folders')
  const shown = find(folders, (n) => n.props?.role === 'alert')
  assert.ok(shown, 'отказ показан внутри раздела папок')
  assert.equal(text(shown), 'The folder does not exist')
  assert.equal(input(app, 'folders', 'inbox').props.value, '/data/none')
  assert.equal(button(app, 'folders', 'Yes, change the inbox folder'), null) // вопрос закрыт
  assert.deepEqual(alerts(app), ['The folder does not exist']) // и не продублирован над разделом
  // следующая правка или попытка убирает прежний отказ
  await enter(app, input(app, 'folders', 'inbox'), '/data/other')
  assert.equal(find(section(app, 'folders'), (n) => n.props?.role === 'alert'), null)
})

test('Folders: отказ без объекта-сообщения (502 от старой команды), обрыв связи и истёкший вход — тоже у поля, по-английски', async (t) => {
  const cases = [
    [() => reply(502, { error: 'The flyarchive command did not understand the request (exit code 2): the plugin and the archive tools may be out of sync.' }), /did not understand the request/],
    [() => { throw new TypeError('Failed to fetch') }, /No connection to DSH/],
    [() => reply(401, null), /sign-in has expired/],
    [() => reply(500, null), /request failed \(code 500\)/],
  ]
  for (const [answer, pattern] of cases) {
    const { settings, server } = await setup(t)
    server.on[`POST ${INBOX}`] = answer
    const app = await settings()
    await enter(app, input(app, 'folders', 'inbox'), '/data/new-inbox')
    await app.click(button(app, 'folders', 'Change'))
    await app.click(button(app, 'folders', 'Yes, change the inbox folder'))
    const shown = find(section(app, 'folders'), (n) => n.props?.role === 'alert')
    assert.ok(shown && pattern.test(text(shown)), String(pattern))
  }
})

test('Folders: папка возврата и каталог архива — только чтение, у каждой «Copy path»', async (t) => {
  const written = []
  const saved = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  Object.defineProperty(globalThis, 'navigator', { configurable: true, writable: true, value: { clipboard: { writeText: async (v) => { written.push(v) } } } })
  t.after(() => {
    if (saved) Object.defineProperty(globalThis, 'navigator', saved)
    else delete globalThis.navigator
  })
  const { settings } = await setup(t)
  const app = await settings()
  const content = text(section(app, 'folders'))
  assert.ok(content.includes('/home/x/returned') && content.includes('/home/x/flyarchive'), content)
  // полей для их правки нет: поле одно — входящая
  assert.deepEqual(allIn(app, 'folders', (n) => n.type === 'input').map((n) => n.props.name), ['inbox'])
  const copies = allIn(app, 'folders', (n) => n.type === 'button' && text(n) === 'Copy path')
  assert.equal(copies.length, 2)
  await app.click(copies[0])
  await app.click(copies[1])
  assert.deepEqual(written, ['/home/x/returned', '/home/x/flyarchive'])
})

test('Folders: старый сервер без returned и home — строк только для чтения нет, раздел работает', async (t) => {
  const { settings } = await setup(t, { archive: { on: { [`GET ${INBOX}`]: async (_, s) => { const view = s.view(); delete view.returned; delete view.home; return reply(200, view) } } } })
  const app = await settings()
  assert.equal(input(app, 'folders', 'inbox').props.value, '/home/x/inbox')
  assert.equal(app.all((n) => n.type === 'button' && text(n) === 'Copy path').length, 0)
})

test('Folders: до первого ответа поля со значением нет и «Change» не нажимается', async (t) => {
  const { settings } = await setup(t, { hidden: true })
  const app = await settings()
  const change = button(app, 'folders', 'Change')
  assert.ok(change === null || change.props.disabled === true)
})

// ── Intake ──────────────────────────────────────────────────────
test('Intake: период (пять значений, выбран текущий), порог, пределы, проверка моделью и запасная модель — со значениями из состояния', async (t) => {
  const { settings } = await setup(t)
  const app = await settings()
  const period = input(app, 'intake', 'period')
  assert.equal(period.props.value, '30')
  assert.deepEqual(allIn(app, 'intake', (n) => n.type === 'option').map((o) => o.props.value), ['1', '5', '10', '30', '60'])
  for (const [name, value] of [['threshold', '20'], ['max_gb', '2'], ['max_files', '5000'], ['max_ratio', '100'], ['depth', '3']]) {
    assert.equal(input(app, 'intake', name).props.value, value, name)
  }
  assert.equal(input(app, 'intake', 'llm').props.checked, true)
  assert.equal(input(app, 'intake', 'cloud').props.checked, false)
  assert.ok(button(app, 'intake', 'Save'))
})

test('Intake: без проверки моделью запасная модель недоступна, а предупреждение об облаке есть только при обеих галочках', async (t) => {
  const off = await setup(t, { archive: { status: { llm: false, cloud: true } } })
  const a = await off.settings()
  assert.equal(input(a, 'intake', 'cloud').props.disabled, true)
  assert.ok(!/leaves this machine/.test(text(section(a, 'intake'))))
  const on = await setup(t, { archive: { status: { llm: true, cloud: true } } })
  const b = await on.settings()
  assert.equal(input(b, 'intake', 'cloud').props.disabled, false)
  assert.match(text(section(b, 'intake')), /180 seconds.*leaves this machine/)
})

test('Intake: таймер не поставлен — сказано; поставлен — не сказано', async (t) => {
  const none = await setup(t, { archive: { status: { timer: false } } })
  assert.match(text(section(await none.settings(), 'intake')), /No timer: runs only on request/)
  const some = await setup(t, { archive: { status: { timer: true } } })
  assert.ok(!/No timer/.test(text(section(await some.settings(), 'intake'))))
})

test('Intake: «Save» не нажимается, пока ничего не изменено; Save уходит одним запросом только с изменённым и без path и confirm', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  assert.equal(button(app, 'intake', 'Save').props.disabled, true)
  await enter(app, input(app, 'intake', 'period'), '5')
  assert.equal(button(app, 'intake', 'Save').props.disabled, false)
  await enter(app, input(app, 'intake', 'threshold'), '35')
  await app.click(button(app, 'intake', 'Save'))
  assert.deepEqual(server.posts(INBOX).map((c) => c.body), [{ period: 5, threshold: 35 }])
  assert.equal(input(app, 'intake', 'period').props.value, '5') // состояние перечитано
  assert.equal(button(app, 'intake', 'Save').props.disabled, true)
  assert.match(text(app.find((n) => n.props?.role === 'status')), /Intake settings saved/)
})

test('Intake: галочки и числа с запятой идут как значения; пустое число уходит как есть, решает сервер', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  await app.check(input(app, 'intake', 'llm'), false)
  await enter(app, input(app, 'intake', 'max_gb'), '0,5')
  await app.click(button(app, 'intake', 'Save'))
  assert.deepEqual(server.posts(INBOX).map((c) => c.body), [{ llm: false, max_gb: 0.5 }])
})

test('Intake: отказ сервера показан над разделом его сообщением, а введённое не пропадает', async (t) => {
  const { settings, server } = await setup(t)
  server.on[`POST ${INBOX}`] = () => reply(400, { error: 'invalid value for threshold' })
  const app = await settings()
  await enter(app, input(app, 'intake', 'threshold'), '500')
  await app.click(button(app, 'intake', 'Save'))
  assert.deepEqual(alerts(app), ['invalid value for threshold'])
  assert.equal(input(app, 'intake', 'threshold').props.value, '500')
})

// ── Intake: числовые поля ───────────────────────────────────────
// Поле показывает то, что набрано, а число из него делается при сохранении. Стёртое поле остаётся пустым (раньше в нём стояло слово «NaN»,
// и дописать цифру было нельзя), а пустое или не число поле владелец видит названным под полями, Save при этом не нажимается: на сервер
// ничего не уходит (раньше уходил null, и сервер отвечал 400 «invalid value for …» без слов о том, какое поле не заполнено).
const NUMBER_FIELDS = [['threshold', '20', 'Score threshold'], ['max_gb', '2', 'Archive size, GB'], ['max_files', '5000', 'Files'],
  ['max_ratio', '100', 'Compression, to 1'], ['depth', '3', 'Nesting depth'], ['vision_pages', '60', 'Pages per run'], ['vision_minutes', '10', 'Minutes per run']]
const withVision = () => ({ archive: { status: { vision: true, vision_pages: 60, vision_minutes: 10 } } }) // каждый раз новый: Save правит состояние подставного архива
const fillMessage = (app) => alerts(app).filter((line) => line.startsWith('Enter a number'))

test('Intake: стёртое число остаётся пустым в каждом числовом поле — в целых и в дробном max_gb, — а не словом «NaN»', async (t) => {
  const { settings } = await setup(t, withVision())
  const app = await settings()
  for (const [name, shown] of NUMBER_FIELDS) {
    assert.equal(input(app, 'intake', name).props.value, shown, name)
    await enter(app, input(app, 'intake', name), '')
    assert.equal(input(app, 'intake', name).props.value, '', name)
  }
  assert.ok(!app.html().includes('NaN'), 'слова «NaN» на экране нет')
})

test('Intake: после стирания число вводится заново: поле показывает набранное, Save шлёт его числом', async (t) => {
  for (const [name, , label] of NUMBER_FIELDS) {
    const { settings, server } = await setup(t, withVision())
    const app = await settings()
    await enter(app, input(app, 'intake', name), '')
    await enter(app, input(app, 'intake', name), name === 'max_gb' ? '0,5' : '7')
    assert.equal(input(app, 'intake', name).props.value, name === 'max_gb' ? '0,5' : '7', label) // как набрано, запятая тоже
    await app.click(button(app, 'intake', 'Save'))
    assert.deepEqual(server.posts(INBOX).map((c) => c.body), [{ [name]: name === 'max_gb' ? 0.5 : 7 }], label)
  }
})

test('Intake: набранное не числом остаётся в поле как набрано, а не «NaN»; поле помечено и названо под полями', async (t) => {
  const { settings } = await setup(t)
  const app = await settings()
  for (const typed of ['abc', '1e', '--', '5 5', '12abc']) {
    await enter(app, input(app, 'intake', 'depth'), typed)
    assert.equal(input(app, 'intake', 'depth').props.value, typed)
    assert.equal(input(app, 'intake', 'depth').props['aria-invalid'], 'true', typed)
    assert.deepEqual(fillMessage(app), ['Enter a number for: Nesting depth'], typed)
  }
})

test('Intake: пустое или негодное число — поле помечено, названо в подписи, Save не нажимается и на сервер ничего не уходит', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  assert.equal(input(app, 'intake', 'threshold').props['aria-invalid'], undefined)
  assert.deepEqual(fillMessage(app), [])
  await app.check(input(app, 'intake', 'llm'), false) // рядом годное изменение: оно не должно уйти без негодного
  await enter(app, input(app, 'intake', 'depth'), 'abc')
  await enter(app, input(app, 'intake', 'threshold'), '')
  assert.equal(input(app, 'intake', 'threshold').props['aria-invalid'], 'true')
  assert.equal(input(app, 'intake', 'depth').props['aria-invalid'], 'true')
  assert.equal(input(app, 'intake', 'max_files').props['aria-invalid'], undefined) // чужое поле не помечено
  assert.deepEqual(fillMessage(app), ['Enter a number for: Score threshold; Nesting depth']) // порядок полей на экране, а не набора
  assert.equal(button(app, 'intake', 'Save').props.disabled, true)
  assert.equal(await app.click(button(app, 'intake', 'Save')), false)
  assert.equal(server.posts(INBOX).length, 0)
  assert.equal(server.count(INBOX), 1, 'и состояние лишний раз не перечитывалось')
  // заполнено одно — названо второе
  await enter(app, input(app, 'intake', 'threshold'), '25')
  assert.deepEqual(fillMessage(app), ['Enter a number for: Nesting depth'])
  assert.equal(input(app, 'intake', 'threshold').props['aria-invalid'], undefined)
  assert.equal(button(app, 'intake', 'Save').props.disabled, true)
  // заполнено всё — подпись ушла, Save нажимается и шлёт только числа и годное соседнее изменение
  await enter(app, input(app, 'intake', 'depth'), '2')
  assert.deepEqual(fillMessage(app), [])
  assert.equal(button(app, 'intake', 'Save').props.disabled, false)
  await app.click(button(app, 'intake', 'Save'))
  assert.deepEqual(server.posts(INBOX).map((c) => c.body), [{ llm: false, depth: 2, threshold: 25 }])
})

test('Intake: число с пробелами по краям и с запятой — число: на сервер идёт число, а не строка', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  await enter(app, input(app, 'intake', 'max_gb'), ' 0,5 ')
  await enter(app, input(app, 'intake', 'max_files'), ' 7 ')
  assert.deepEqual(fillMessage(app), [])
  await app.click(button(app, 'intake', 'Save'))
  assert.deepEqual(server.posts(INBOX).map((c) => c.body), [{ max_gb: 0.5, max_files: 7 }])
})

test('Intake: числа описания изображений при выключенном флажке недоступны; их пустая правка не держит Save и на сервер не идёт', async (t) => {
  const { settings, server } = await setup(t, withVision())
  const app = await settings()
  await enter(app, input(app, 'intake', 'vision_pages'), '')
  assert.deepEqual(fillMessage(app), ['Enter a number for: Pages per run'])
  assert.equal(button(app, 'intake', 'Save').props.disabled, true)
  await app.check(input(app, 'intake', 'vision'), false) // поле стало недоступным: чинить его нечем
  assert.deepEqual(fillMessage(app), [])
  assert.equal(button(app, 'intake', 'Save').props.disabled, false)
  await app.click(button(app, 'intake', 'Save'))
  assert.deepEqual(server.posts(INBOX).map((c) => c.body), [{ vision: false }])
})

test('Intake: старый сервер без числовых ключей — поля пусты, без слов «undefined» и «NaN»; чужое не число в состоянии тоже пусто', async (t) => {
  const { settings } = await setup(t, { archive: { status: { max_gb: undefined, depth: undefined, max_files: null, max_ratio: 'много', threshold: NaN } } })
  const app = await settings()
  for (const name of ['max_gb', 'depth', 'max_files', 'max_ratio', 'threshold']) assert.equal(input(app, 'intake', name).props.value, '', name)
  assert.ok(!/undefined|NaN|null/.test(app.html()), app.html())
  assert.deepEqual(fillMessage(app), []) // в состоянии пусто, но владелец ничего не правил: Save и так выключен
  assert.equal(button(app, 'intake', 'Save').props.disabled, true)
})

test('Intake: подпись о незаполненном поле — из словаря, без кириллицы', async (t) => {
  const p = await setup(t, withVision())
  const app = await p.settings()
  await enter(app, input(app, 'intake', 'max_gb'), '')
  await enter(app, input(app, 'intake', 'vision_minutes'), 'x')
  assert.equal(fillMessage(app).length, 1)
  assert.ok(!/[Ѐ-ӿ]/.test(app.html()), app.html())
  assert.deepEqual(p.ctx.missingKeys, [])
  assert.equal(find(section(app, 'intake'), (n) => n.props?.role === 'alert').props.className, 'ba-error')
})

// ── Tokens ──────────────────────────────────────────────────────
test('Tokens: список без значений — имя, уровень, выпуск, срок, последний вызов, состояние; слова состояния переведены', async (t) => {
  const { settings } = await setup(t)
  const app = await settings()
  const box = section(app, 'tokens')
  const content = text(box)
  for (const word of ['dsh-local', 'laptop', 'old', 'guest', 'service', 'read', 'full', 'active', 'revoked', 'expired', 'no expiry', 'never used']) {
    assert.ok(content.includes(word), word)
  }
  assert.ok(content.includes('04.10.2026') && content.includes('02.01.2027'))
  assert.ok(!/ba_/.test(app.html()))
  for (const header of ['Name', 'Level', 'Issued', 'Expires', 'Last used', 'State']) assert.ok(content.includes(header), header)
})

test('Tokens: отозвать можно только действующий токен клиента, служебный нельзя; отзыв — в два нажатия', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  const revokes = app.all((n) => n.type === 'button' && text(n) === 'Revoke')
  assert.equal(revokes.length, 1) // laptop: dsh-local служебный, old отозван, guest просрочен
  await app.click(revokes[0])
  assert.equal(server.posts(REVOKE).length, 0)
  assert.ok(button(app, 'tokens', 'Yes, close access') && button(app, 'tokens', 'Cancel'))
  await app.click(button(app, 'tokens', 'Cancel'))
  assert.equal(server.posts(REVOKE).length, 0)
  await app.click(app.all((n) => n.type === 'button' && text(n) === 'Revoke')[0])
  await app.click(button(app, 'tokens', 'Yes, close access'))
  assert.deepEqual(server.posts(REVOKE).map((c) => c.body), [{ name: 'laptop' }])
  assert.equal(app.all((n) => n.type === 'button' && text(n) === 'Revoke').length, 0)
  assert.ok(text(section(app, 'tokens')).includes('revoked'))
})

test('Tokens: выпуск — имя, уровень, срок; значение показано один раз и пропадает после «Dismiss»', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  const form = (name) => input(app, 'tokens', name)
  assert.ok(!app.html().includes('ba_q'))
  await enter(app, form('name'), 'tablet')
  await enter(app, form('level'), 'full')
  await enter(app, form('days'), '30')
  assert.equal(button(app, 'tokens', 'Issue').props.type, 'submit')
  inSection(app, 'tokens', (n) => n.type === 'form').props.onSubmit({ preventDefault() {} })
  await app.settle()
  assert.deepEqual(server.posts(TOKENS).map((c) => c.body), [{ name: 'tablet', level: 'full', days: 30 }])
  const issued = text(section(app, 'tokens'))
  assert.ok(issued.includes('ba_' + 'q'.repeat(43)))
  assert.match(issued, /not shown a second time/)
  assert.equal(server.count(TOKENS), 2) // список перечитан после выпуска
  await app.click(button(app, 'tokens', 'Dismiss'))
  assert.ok(!app.html().includes('ba_q'))
})

test('Tokens: срок пустой — без срока (days: null); форма очищается; кнопка «Issue» ждёт имени', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  assert.equal(button(app, 'tokens', 'Issue').props.disabled, true)
  await enter(app, input(app, 'tokens', 'name'), 'phone')
  await enter(app, input(app, 'tokens', 'days'), '')
  inSection(app, 'tokens', (n) => n.type === 'form').props.onSubmit({ preventDefault() {} })
  await app.settle()
  assert.deepEqual(server.posts(TOKENS).map((c) => c.body), [{ name: 'phone', level: 'read', days: null }])
  assert.equal(input(app, 'tokens', 'name').props.value, '')
})

test('Tokens: отзыв токена, значение которого на экране, убирает его с экрана', async (t) => {
  const { settings } = await setup(t)
  const app = await settings()
  await enter(app, input(app, 'tokens', 'name'), 'tablet')
  inSection(app, 'tokens', (n) => n.type === 'form').props.onSubmit({ preventDefault() {} })
  await app.settle()
  assert.ok(app.html().includes('ba_q'))
  const revoke = app.all((n) => n.type === 'button' && text(n) === 'Revoke').at(-1)
  await app.click(revoke)
  await app.click(button(app, 'tokens', 'Yes, close access'))
  assert.ok(!app.html().includes('ba_q'))
})

test('Tokens: отказ выпуска показан над разделом его сообщением', async (t) => {
  const { settings, server } = await setup(t)
  server.on[`POST ${TOKENS}`] = () => reply(409, { error: { code: 'token_name_taken', args: {}, text: 'The name is taken by an active token' } })
  const app = await settings()
  await enter(app, input(app, 'tokens', 'name'), 'laptop')
  inSection(app, 'tokens', (n) => n.type === 'form').props.onSubmit({ preventDefault() {} })
  await app.settle()
  assert.deepEqual(alerts(app), ['The name is taken by an active token'])
})

test('Tokens: пустой список и загрузка называются словами', async (t) => {
  const none = await setup(t, { archive: { tokens: [] } })
  assert.ok(text(section(await none.settings(), 'tokens')).includes('No tokens'))
  const slow = await setup(t)
  slow.server.on[`GET ${TOKENS}`] = () => new Promise(() => {})
  assert.ok(text(section(await slow.settings(), 'tokens')).includes('Loading'))
})

// ── Delete document ─────────────────────────────────────────────
test('Delete document: путь и «Remove from archive»; без пути кнопка не нажимается, первое нажатие только спрашивает', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  assert.equal(button(app, 'delete', 'Remove from archive').props.disabled, true)
  await enter(app, input(app, 'delete', 'path'), '  folder/report.pdf ')
  await app.click(button(app, 'delete', 'Remove from archive'))
  assert.ok(button(app, 'delete', 'Yes, remove from archive and index') && button(app, 'delete', 'Cancel'))
  assert.equal(server.posts(REMOVE).length, 0)
  await app.click(button(app, 'delete', 'Cancel'))
  assert.equal(server.posts(REMOVE).length, 0)
  assert.ok(button(app, 'delete', 'Remove from archive'))
})

test('Delete document: подтверждённое удаление уходит с confirm: true, путь без пробелов по краям; итог сказан, поле очищено', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  await enter(app, input(app, 'delete', 'path'), '  folder/report.pdf ')
  await app.click(button(app, 'delete', 'Remove from archive'))
  await app.click(button(app, 'delete', 'Yes, remove from archive and index'))
  assert.deepEqual(server.posts(REMOVE).map((c) => c.body), [{ path: 'folder/report.pdf', confirm: true }])
  assert.equal(input(app, 'delete', 'path').props.value, '')
  assert.match(text(app.find((n) => n.props?.role === 'status')), /Document removed from the archive: 2 index rows, file moved to deleted\/2026-10-04\/x\./)
})

test('Delete document: правка пути закрывает вопрос; отказ показан над разделом', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  await enter(app, input(app, 'delete', 'path'), 'a.pdf')
  await app.click(button(app, 'delete', 'Remove from archive'))
  await enter(app, input(app, 'delete', 'path'), 'b.pdf')
  assert.equal(button(app, 'delete', 'Yes, remove from archive and index'), null)
  server.on[`POST ${REMOVE}`] = () => reply(409, { error: { code: 'doc_missing', args: {}, text: 'No such document' } })
  await app.click(button(app, 'delete', 'Remove from archive'))
  await app.click(button(app, 'delete', 'Yes, remove from archive and index'))
  assert.deepEqual(alerts(app), ['No such document'])
})

test('Delete document: объяснение — документ уходит из поиска и корпуса, файл не стирается', async (t) => {
  const { settings } = await setup(t)
  assert.match(text(section(await settings(), 'delete')), /leaves search and the corpus.*not erased/)
})

// ── Access journal ──────────────────────────────────────────────
test('Access journal: последние записи, свежие сверху, подписи английские', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  assert.equal(server.calls.filter((c) => c.route.startsWith(JOURNAL)).map((c) => c.route)[0], 'api/flyarchive.journal?n=100')
  const box = text(section(app, 'journal'))
  for (const word of ['laptop', '/api/search', 'q=capital', 'tailnet', '203.0.113.14', '401', 'denied: token needed', 'Time', 'Client', 'Service', 'Tool', 'Outcome', 'From', 'Parameters']) {
    assert.ok(box.includes(word), word)
  }
  assert.ok(box.indexOf('09:14') < box.indexOf('09:13'))
})

test('Access journal: отбор по клиенту — «All», токены и «no token» для записей без токена; выбор перечитывает журнал с client', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  const select = input(app, 'journal', 'client')
  const options = allIn(app, 'journal', (n) => n.type === 'option').map((o) => [o.props.value, text(o)])
  assert.deepEqual(options[0], ['', 'All'])
  assert.ok(options.some(([value, label]) => value === '-' && label === 'no token'))
  assert.ok(options.some(([value, label]) => value === 'laptop' && label === 'laptop'))
  await enter(app, select, 'laptop')
  assert.ok(server.calls.some((c) => c.route === 'api/flyarchive.journal?n=100&client=laptop'))
  assert.ok(!text(section(app, 'journal')).includes('denied: token needed'))
})

test('Access journal: пустой — «No records.»', async (t) => {
  const { settings } = await setup(t, { archive: { records: [] } })
  assert.ok(text(section(await settings(), 'journal')).includes('No records.'))
})

test('Access journal: текст из журнала вставляется как текст, а не как разметка', async (t) => {
  const evil = [{ ...RECORD_ROWS[0], params: { q: '<img src=x onerror=alert(1)>' }, client: '<b>x</b>' }]
  const { settings } = await setup(t, { archive: { records: evil } })
  const out = (await settings()).html()
  assert.ok(!out.includes('<img') && !out.includes('<b>x</b>'))
  assert.ok(out.includes('&lt;img'))
})

// ── обновление и ошибки раздела ─────────────────────────────────
test('«Refresh» перечитывает токены и журнал и просит хранилище обновить состояние: один запрос состояния, не свой', async (t) => {
  const { settings, server } = await setup(t)
  const app = await settings()
  const before = { tokens: server.count(TOKENS), journal: server.calls.filter((c) => c.route.startsWith(JOURNAL)).length, inbox: server.count(INBOX) }
  await app.click(app.button('Refresh'))
  assert.equal(server.count(TOKENS), before.tokens + 1)
  assert.equal(server.calls.filter((c) => c.route.startsWith(JOURNAL)).length, before.journal + 1)
  assert.equal(server.count(INBOX), before.inbox + 1)
})

test('сбой чтения токенов: обрыв связи, истёкший вход и отказ сервера — отдельные английские тексты над разделом', async (t) => {
  const cases = [
    [() => { throw new TypeError('Failed to fetch') }, 'No connection to DSH. The tab keeps trying.'],
    [() => reply(401, null), 'Your DSH sign-in has expired. Open DSH again from a fresh link.'],
    [() => reply(500, null), 'The archive request failed (code 500).'],
    [() => reply(409, { error: { code: null, args: {}, text: 'The archive is busy' } }), 'The archive is busy'],
  ]
  for (const [answer, expected] of cases) {
    const p = await setup(t)
    p.server.on[`GET ${TOKENS}`] = answer
    const app = await p.settings()
    assert.ok(alerts(app).includes(expected), `${expected} ← ${JSON.stringify(alerts(app))}`)
  }
})
