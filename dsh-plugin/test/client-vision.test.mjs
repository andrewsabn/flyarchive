// Описание изображений в плагине: настройка в разделе Intake, число ждущих описания в строке Status и состояние описания у файла
// в истории пачек на вкладке (FR-94, FR-92). Плагин ставится на подставной DSH, экран рисуется малым рисовальщиком с состоянием.
import assert from 'node:assert/strict'
import test from 'node:test'

import { BATCH, batchOf, detailOf, fileOf, messageOf, progressOf } from './kit-archive.mjs'
import { boot } from './kit-plugin.mjs'
import { find, flush, html, reply, text } from './kit.mjs'

const INBOX = 'api/flyarchive.inbox'
const CYRILLIC = /[Ѐ-ӿ]/
const BROKEN_TEXT = /NaN|undefined|\[object Object\]/

const setup = async (t, options = {}) => {
  const plugin = await boot(options)
  t.after(plugin.done)
  return plugin
}
/** Состояние входящих с описанием изображений, как его отдаёт flyarchive inbox status --json. */
const VISION = { vision: true, vision_pages: 60, vision_minutes: 10, vision_pending: 0, vision_done: 7 }
const withStatus = (status) => ({ archive: { status: { ...status } } }) // копия: подставной архив правит состояние при Save, образец теста должен остаться целым

// ── раздел настроек ─────────────────────────────────────────────
const section = (app, name) => app.find((n) => n.props?.['data-section'] === name)
const inSection = (app, name, match) => find(section(app, name), match)
const present = (node, what) => {
  assert.ok(node, `на экране нет: ${what}`)
  return node
}
const input = (app, field) => present(inSection(app, 'intake', (n) => (n.type === 'input' || n.type === 'select') && n.props.name === field), `поле ${field}`)
const button = (app, label) => inSection(app, 'intake', (n) => n.type === 'button' && text(n) === label)
const labelOf = (app, field) => present(inSection(app, 'intake', (n) => n.type === 'label' && find(n, (m) => m.type === 'input' && m.props.name === field) !== null), `подпись поля ${field}`)
const enter = async (app, node, value) => {
  assert.ok(node, 'такого поля на экране нет')
  node.props.onChange({ target: { value } })
  await app.settle()
}
const intakeText = (app) => text(section(app, 'intake'))
const HINT = 'Images never leave this machine. Needs a local model with vision.'
const alerts = (app) => app.all((n) => n.props?.role === 'alert').map((n) => text(n))

test('Intake: флажок «Describe images with the local model» и числа «Pages per run» и «Minutes per run» — со значениями из состояния и с подписями', async (t) => {
  const { settings } = await setup(t, withStatus(VISION))
  const app = await settings()
  const check = input(app, 'vision')
  assert.equal(check.props.type, 'checkbox')
  assert.equal(check.props.checked, true)
  assert.equal(input(app, 'vision_pages').props.value, '60')
  assert.equal(input(app, 'vision_minutes').props.value, '10')
  // подпись — это label вокруг поля: у флажка и у каждого числа есть доступное имя
  assert.equal(text(labelOf(app, 'vision')), 'Describe images with the local model')
  assert.equal(text(labelOf(app, 'vision_pages')), 'Pages per run')
  assert.equal(text(labelOf(app, 'vision_minutes')), 'Minutes per run')
})

test('Intake: описание выключено — флажок снят, числа недоступны, подсказки нет', async (t) => {
  const { settings } = await setup(t, withStatus({ ...VISION, vision: false, vision_pages: 5, vision_minutes: 2 }))
  const app = await settings()
  assert.equal(input(app, 'vision').props.checked, false)
  assert.equal(input(app, 'vision').props.disabled, undefined) // сам флажок доступен всегда
  assert.equal(input(app, 'vision_pages').props.disabled, true)
  assert.equal(input(app, 'vision_minutes').props.disabled, true)
  assert.equal(input(app, 'vision_pages').props.value, '5') // числа видны и при выключенном флажке
  assert.ok(!intakeText(app).includes(HINT))
})

test('Intake: числа доступны только при включённом флажке — и в состоянии, и в черновике до сохранения', async (t) => {
  const on = await setup(t, withStatus(VISION))
  const a = await on.settings()
  assert.equal(input(a, 'vision_pages').props.disabled, false)
  assert.equal(input(a, 'vision_minutes').props.disabled, false)
  await a.check(input(a, 'vision'), false)
  assert.equal(input(a, 'vision_pages').props.disabled, true)
  assert.equal(input(a, 'vision_minutes').props.disabled, true)
  await a.check(input(a, 'vision'), true)
  assert.equal(input(a, 'vision_pages').props.disabled, false)

  const off = await setup(t, withStatus({ ...VISION, vision: false }))
  const b = await off.settings()
  assert.equal(input(b, 'vision_pages').props.disabled, true)
  await b.check(input(b, 'vision'), true)
  assert.equal(input(b, 'vision_pages').props.disabled, false)
  assert.equal(input(b, 'vision_minutes').props.disabled, false)
})

test('Intake: подсказка «Images never leave this machine…» стоит под включённым флажком, и только под ним', async (t) => {
  const on = await setup(t, withStatus(VISION))
  const a = await on.settings()
  assert.ok(intakeText(a).includes(HINT), intakeText(a))
  await a.check(input(a, 'vision'), false)
  assert.ok(!intakeText(a).includes(HINT))
  const off = await setup(t, withStatus({ ...VISION, vision: false }))
  const b = await off.settings()
  assert.ok(!intakeText(b).includes(HINT))
  await b.check(input(b, 'vision'), true)
  assert.ok(intakeText(b).includes(HINT))
  // подсказка не залезла в соседние части раздела
  assert.ok(!text(section(b, 'status')).includes(HINT) && !text(section(b, 'folders')).includes(HINT))
})

test('Intake: Save шлёт ключи vision, vision_pages и vision_minutes только как изменены, без прочих', async (t) => {
  const { settings, server } = await setup(t, withStatus({ ...VISION, vision: false, vision_pages: 60, vision_minutes: 10 }))
  const app = await settings()
  assert.equal(button(app, 'Save').props.disabled, true) // пока ничего не тронуто, Save не нажимается
  await app.check(input(app, 'vision'), true)
  assert.equal(button(app, 'Save').props.disabled, false)
  await enter(app, input(app, 'vision_pages'), '5')
  await enter(app, input(app, 'vision_minutes'), '2')
  await app.click(button(app, 'Save'))
  assert.deepEqual(server.posts(INBOX).map((c) => c.body), [{ vision: true, vision_pages: 5, vision_minutes: 2 }])
  // состояние перечитано: на экране сохранённое, Save снова не нажимается
  assert.equal(input(app, 'vision').props.checked, true)
  assert.equal(input(app, 'vision_pages').props.value, '5')
  assert.equal(input(app, 'vision_minutes').props.value, '2')
  assert.equal(button(app, 'Save').props.disabled, true)
  assert.match(text(app.find((n) => n.props?.role === 'status')), /Intake settings saved/)
})

test('Intake: изменено одно число — уходит только оно; изменён только флажок — уходит только vision', async (t) => {
  const first = await setup(t, withStatus(VISION))
  const a = await first.settings()
  await enter(a, input(a, 'vision_minutes'), '30')
  await a.click(button(a, 'Save'))
  assert.deepEqual(first.server.posts(INBOX).map((c) => c.body), [{ vision_minutes: 30 }])

  const second = await setup(t, withStatus({ ...VISION, vision: false }))
  const b = await second.settings()
  await b.check(input(b, 'vision'), true)
  await b.click(button(b, 'Save'))
  assert.deepEqual(second.server.posts(INBOX).map((c) => c.body), [{ vision: true }])
})

test('Intake: выключить описание и сохранить — уходит vision: false; числа снова недоступны, подсказка ушла', async (t) => {
  const { settings, server } = await setup(t, withStatus(VISION))
  const app = await settings()
  await app.check(input(app, 'vision'), false)
  await app.click(button(app, 'Save'))
  assert.deepEqual(server.posts(INBOX).map((c) => c.body), [{ vision: false }])
  assert.equal(input(app, 'vision').props.checked, false)
  assert.equal(input(app, 'vision_pages').props.disabled, true)
  assert.ok(!intakeText(app).includes(HINT))
})

test('Intake: число с пробелами по краям уходит числом; пустое число не уходит вовсе — Save ждёт, пока поле заполнят', async (t) => {
  const { settings, server } = await setup(t, withStatus(VISION))
  const app = await settings()
  await enter(app, input(app, 'vision_pages'), ' 12 ')
  await enter(app, input(app, 'vision_minutes'), '')
  assert.equal(button(app, 'Save').props.disabled, true)
  assert.equal(await app.click(button(app, 'Save')), false)
  assert.equal(server.posts(INBOX).length, 0)
  await enter(app, input(app, 'vision_minutes'), '3')
  await app.click(button(app, 'Save'))
  assert.deepEqual(server.posts(INBOX).map((c) => c.body), [{ vision_pages: 12, vision_minutes: 3 }])
})

test('Intake: стёртое число остаётся в поле пустым, а не словом «NaN»', async (t) => {
  const { settings } = await setup(t, withStatus(VISION))
  const app = await settings()
  await enter(app, input(app, 'vision_minutes'), '')
  assert.equal(input(app, 'vision_minutes').props.value, '')
  await enter(app, input(app, 'vision_minutes'), '7')
  assert.equal(input(app, 'vision_minutes').props.value, '7')
})

test('Intake: отказ сервера показан над разделом, введённое не пропадает', async (t) => {
  const { settings, server } = await setup(t, withStatus(VISION))
  server.on[`POST ${INBOX}`] = () => reply(400, { error: 'invalid value for vision_pages' })
  const app = await settings()
  await enter(app, input(app, 'vision_pages'), '1001')
  await app.click(button(app, 'Save'))
  assert.deepEqual(alerts(app), ['invalid value for vision_pages'])
  assert.equal(input(app, 'vision_pages').props.value, '1001')
  assert.equal(button(app, 'Save').props.disabled, false)
})

test('Intake: старый архив без ключей описания — флажок снят, числа пусты и недоступны, строки «Awaiting description» нет', async (t) => {
  const { settings, server } = await setup(t)
  assert.ok(!('vision' in server.view()) && !('vision_pending' in server.view()))
  const app = await settings()
  assert.equal(input(app, 'vision').props.checked, false)
  assert.equal(input(app, 'vision_pages').props.value, '')
  assert.equal(input(app, 'vision_pages').props.disabled, true)
  assert.equal(input(app, 'vision_minutes').props.value, '')
  assert.ok(!intakeText(app).includes(HINT))
  assert.ok(!text(section(app, 'status')).includes('Awaiting description'))
  assert.ok(!BROKEN_TEXT.test(app.text()), app.text())
})

test('Intake: мусор вместо описания в состоянии не роняет раздел и не включает настройку', async (t) => {
  const garbage = [
    { vision: 'да', vision_pages: 'x', vision_minutes: null, vision_pending: {}, vision_done: [] },
    { vision: 1, vision_pages: [], vision_minutes: {}, vision_pending: 'много' },
    { vision: 'true', vision_pages: NaN, vision_minutes: undefined, vision_pending: -3 },
    { vision: null, vision_pages: true, vision_minutes: 'десять', vision_pending: null },
  ]
  for (const status of garbage) {
    const { settings } = await setup(t, withStatus(status))
    const app = await settings()
    const label = JSON.stringify(status)
    assert.equal(input(app, 'vision').props.checked, false, label)
    assert.equal(input(app, 'vision_pages').props.disabled, true, label)
    assert.equal(input(app, 'vision_pages').props.value, '', label)
    assert.equal(input(app, 'vision_minutes').props.value, '', label)
    assert.ok(!BROKEN_TEXT.test(app.text()), `${label}: ${app.text()}`)
    assert.ok(!text(section(app, 'status')).includes('Awaiting description'), label)
  }
})

test('все подписи описания изображений — из словаря, без кириллицы: в покое и с изменённым черновиком', async (t) => {
  const p = await setup(t, withStatus({ ...VISION, vision: false, vision_pending: 3 }))
  const app = await p.settings()
  await app.check(input(app, 'vision'), true)
  await enter(app, input(app, 'vision_pages'), '5')
  assert.ok(!CYRILLIC.test(app.html()), app.html())
  assert.deepEqual(p.ctx.missingKeys, [])
  for (const label of ['Describe images with the local model', 'Pages per run', 'Minutes per run']) assert.ok(intakeText(app).includes(label), label)
})

// ── Status ──────────────────────────────────────────────────────
const statusLine = (app) => text(section(app, 'status').props.children.flat().filter((child) => child !== null && child !== false)[0])
const BASE_LINE = `Waiting in the inbox folder: 5 · Awaiting a decision: 0 · Last batch ${BATCH}: accepted 11, needs review 2`

test('Status: документы ждут описания — число стоит в строке состояния, подсказка и состав раздела прежние', async (t) => {
  const { settings } = await setup(t, withStatus({ ...VISION, waiting: 5, vision_pending: 3 }))
  const app = await settings()
  const box = section(app, 'status')
  const parts = box.props.children.flat().filter((child) => child !== null && child !== false)
  assert.equal(parts.length, 2) // строка и подсказка, как прежде: описание не завело третьей части
  assert.equal(text(parts[0]), `Waiting in the inbox folder: 5 · Awaiting a decision: 0 · Awaiting description: 3 · Last batch ${BATCH}: accepted 11, needs review 2`)
  assert.match(text(parts[1]), /Archive tab/)
})

test('Status: ноль, нет ключа и не число — «Awaiting description» не добавляется, строка прежняя', async (t) => {
  const control = await setup(t, withStatus({ waiting: 5, vision_pending: 7 }))
  assert.match(statusLine(await control.settings()), /Awaiting description: 7/) // число ставится, значит отказы ниже — по значению
  const absent = await setup(t, withStatus({ waiting: 5 }))
  assert.equal(statusLine(await absent.settings()), BASE_LINE)
  for (const value of [0, -1, 2.5, null, '3', '0', 'да', true, false, [], [3], {}, { n: 3 }, 1e300]) {
    const p = await setup(t, withStatus({ waiting: 5, vision_pending: value }))
    const app = await p.settings()
    assert.equal(statusLine(app), BASE_LINE, JSON.stringify(value))
    assert.ok(!app.text().includes('Awaiting description'), JSON.stringify(value))
  }
})

test('Status: число следует за хранилищем — растёт, потом исчезает, когда долг отдан', async (t) => {
  const { settings, server, clock } = await setup(t, withStatus({ waiting: 5, vision_pending: 2 }))
  const app = await settings()
  assert.match(statusLine(app), /Awaiting description: 2 · Last batch/)
  server.status.vision_pending = 11
  await clock.advance(30000)
  await app.settle()
  assert.match(statusLine(app), /Awaiting description: 11 · Last batch/)
  server.status.vision_pending = 0
  await clock.advance(30000)
  await app.settle()
  assert.equal(statusLine(app), BASE_LINE)
  assert.equal(server.count(INBOX), 3) // свои запросы раздел не добавил
})

// ── вкладка: строка состояния ───────────────────────────────────
const tabBox = (app) => app.find((n) => n.props?.className === 'ba-box' && text(n).includes('Waiting in the inbox folder'))
const tabLines = (app) => tabBox(app).props.children.flat().filter((child) => child !== null && child !== false).map(text)
// вкладка при старом архиве (в состоянии нет vision_pending): так она рисовалась до описания изображений
const TAB_BOX = '<div class="ba-box"><div>Waiting in the inbox folder: 3</div><div>Runs every 30 min</div><div class="ba-dim">Last batch 20260101-120000: accepted 11, needs review 2</div><div class="ba-dim">Problems in the last batch: 0</div><div class="ba-row"><button type="button">Run now</button></div><div class="ba-row"><span class="ba-dim">Inbox folder:</span><span class="ba-token">/home/x/inbox</span><button type="button">Copy path</button></div></div>'

test('Вкладка: документы ждут описания — «Awaiting description: N» стоит в строке состояния сразу после «Waiting in the inbox folder»', async (t) => {
  const p = await setup(t, withStatus({ vision_pending: 3 }))
  const app = await p.tab()
  assert.deepEqual(tabLines(app).slice(0, 3), ['Waiting in the inbox folder: 3', 'Awaiting description: 3', 'Runs every 30 min'])
  // у остальных строк состояния всё как раньше: ничего не пропало
  assert.equal(html(tabBox(app)), TAB_BOX.replace('</div><div>Runs every', '</div><div>Awaiting description: 3</div><div>Runs every'))
  assert.ok(!CYRILLIC.test(app.html()))
  assert.deepEqual(p.ctx.missingKeys, [])
})

test('Вкладка: ноль, нет ключа и не число — «Awaiting description» не добавляется, разметка строки состояния прежняя', async (t) => {
  const control = await setup(t, withStatus({ vision_pending: 5 }))
  assert.ok(tabLines(await control.tab()).includes('Awaiting description: 5')) // число ставится, значит отказы ниже — по значению
  const old = await setup(t) // архив без ключей описания
  assert.equal(html(tabBox(await old.tab())), TAB_BOX)
  for (const value of [0, -1, 2.5, null, '3', '0', 'да', true, false, [], [3], {}, { n: 3 }, 1e300, NaN, undefined]) {
    const p = await setup(t, withStatus({ vision_pending: value }))
    const app = await p.tab()
    assert.equal(html(tabBox(app)), TAB_BOX, String(JSON.stringify(value)))
    assert.ok(!app.text().includes('Awaiting description'), String(JSON.stringify(value)))
  }
})

test('Вкладка: число следует за хранилищем — растёт, потом исчезает, когда долг отдан; свой запрос вкладка не добавляет', async (t) => {
  const { tab, server, clock } = await setup(t, withStatus({ vision_pending: 2 }))
  const app = await tab()
  assert.ok(tabLines(app).includes('Awaiting description: 2'))
  server.status.vision_pending = 9
  await clock.advance(30000)
  await app.settle()
  assert.ok(tabLines(app).includes('Awaiting description: 9'))
  server.status.vision_pending = 0
  await clock.advance(30000)
  await app.settle()
  assert.equal(html(tabBox(app)), TAB_BOX)
  assert.equal(server.count(INBOX), 3)
})

test('Вкладка: пока идёт разбор, число ждущих описания по-прежнему в строке состояния, ход и «Run now» на месте', async (t) => {
  const p = await setup(t, { archive: { status: { vision_pending: 4 }, progress: progressOf({ stage: 'vision', done: 1, total: 4, current: 'scan.png' }) } })
  const app = await p.tab()
  const lines = tabLines(app)
  assert.ok(lines.includes('Awaiting description: 4'), lines.join(' | '))
  assert.ok(app.text().includes('Describing images') && app.text().includes('1 of 4'))
  assert.equal(app.button('Run now').props.disabled, true)
})

// ── ход разбора ─────────────────────────────────────────────────
test('Ход разбора: этап описания изображений назван «Describing images», видны число страниц и текущий файл', async (t) => {
  const p = await setup(t, { archive: { progress: progressOf({ stage: 'vision', done: 2, total: 5, current: 'scan.png' }) } })
  const app = await p.tab()
  const shown = app.text()
  assert.ok(shown.includes('Describing images') && shown.includes('2 of 5') && shown.includes('Last file: scan.png'), shown)
  assert.ok(!CYRILLIC.test(app.html()), app.html())
})

// ── история пачек ───────────────────────────────────────────────
const described = (over = {}) => ({ state: 'described', pages: 3, total: 3, truncated: false, model: 'qwen-vl-test', seconds: 17.5, date: '2026-10-05T10:00:00Z', skipped: [], ...over })
const waiting = (over = {}) => ({
  state: 'waiting', pages: 0, total: null, reason: 'ждёт описания',
  reason_msg: messageOf('vision.model_not_loaded', {}, 'локальная модель не загружена: документы ждут описания'), ...over,
})

/** Вкладка с одной пачкой, раскрытой; files — файлы пачки. */
async function openBatch(t, files, options = {}) {
  const p = await setup(t, { archive: { batches: [batchOf(BATCH, { files: files.length })], details: { [BATCH]: detailOf(BATCH, files, options.detail) } } })
  const app = await p.tab()
  await app.click(app.find((n) => n.type === 'button' && n.props.className === 'ba-batch-head'))
  return { ...p, app }
}
const rowOf = (app, name) => app.find((n) => n.type === 'tr' && n.props['data-file'] === name)
const nameCell = (app, name) => rowOf(app, name).props.children[0]
const cells = (app, name) => rowOf(app, name).props.children.map(text)
const stateOf = (app, name) => find(nameCell(app, name), (n) => n.props?.['data-vision'] !== undefined)
const PLAIN_ROW = '<tr data-file="plain.txt"><td class="ba-wide"><button class="ba-link" type="button">plain.txt</button></td><td>accepted</td><td>—</td><td>In the archive</td><td>5</td><td>—</td></tr>'

test('История: описанный файл — «Described by the model: N pages» в ячейке с именем; столбцов не прибавилось', async (t) => {
  const { app } = await openBatch(t, [fileOf('scan.png', { vision: described({ pages: 3, total: 3 }) }), fileOf('plain.txt')])
  const state = stateOf(app, 'scan.png')
  assert.ok(state, 'состояние описания не показано у файла')
  assert.equal(state.props['data-vision'], 'described')
  assert.equal(text(state), 'Described by the model: 3 pages')
  // таблица осталась шестистолбцовой: широкой колонки не прибавилось, подпись живёт под именем файла
  assert.deepEqual(app.all((n) => n.type === 'th').map(text), ['File', 'Decision', 'Owner decision', 'Location', 'Score', 'Main rule'])
  assert.equal(rowOf(app, 'scan.png').props.children.length, 6)
  assert.equal(nameCell(app, 'scan.png').props.className, 'ba-wide')
  assert.ok(text(nameCell(app, 'scan.png')).startsWith('scan.png'))
  // остальные ячейки строки те же, что у файла без описания
  assert.deepEqual(cells(app, 'scan.png').slice(1), cells(app, 'plain.txt').slice(1))
  // у файла без описания подписи нет
  assert.equal(stateOf(app, 'plain.txt'), null)
})

test('История: одна описанная страница — «1 page», а не «1 pages»', async (t) => {
  const { app } = await openBatch(t, [fileOf('one.png', { vision: described({ pages: 1, total: 1 }) })])
  assert.equal(text(stateOf(app, 'one.png')), 'Described by the model: 1 page')
})

test('История: описан не целиком — к числу страниц добавлено «first N of TOTAL»', async (t) => {
  const { app } = await openBatch(t, [
    fileOf('long.pdf', { vision: described({ pages: 20, total: 31, truncated: true }) }),
    fileOf('whole.pdf', { vision: described({ pages: 20, total: 20, truncated: false }) })])
  assert.equal(text(stateOf(app, 'long.pdf')), 'Described by the model: 20 pages · first 20 of 31')
  assert.equal(text(stateOf(app, 'whole.pdf')), 'Described by the model: 20 pages')
})

test('История: ждёт описания — «Awaiting description» и причина по коду из английского словаря, а не русский text', async (t) => {
  const { app } = await openBatch(t, [fileOf('scan.pdf', { vision: waiting() })])
  const state = present(stateOf(app, 'scan.pdf'), 'состояние описания')
  assert.equal(state.props['data-vision'], 'waiting')
  assert.equal(text(state), 'Awaiting description') // слова состояния достаточно, чтобы отличить его без цвета и значка
  const cell = text(nameCell(app, 'scan.pdf'))
  assert.ok(cell.includes('The local model is not loaded: documents wait for a description'), cell)
  assert.ok(!CYRILLIC.test(cell), cell)
  assert.ok(!cell.includes('Described by the model'))
  assert.equal(state.props['aria-hidden'], undefined)
})

test('История: причина ожидания с параметрами — по шаблону; незнакомый код и сообщение без кода — их text; старое поле reason — как есть', async (t) => {
  const { app } = await openBatch(t, [
    fileOf('a.pdf', { vision: waiting({ reason_msg: messageOf('vision.model_down', { why: 'connection refused' }, 'локальная модель не отвечает (connection refused)') }) }),
    fileOf('b.pdf', { vision: waiting({ reason_msg: messageOf('vision.from_the_future', {}, 'Модель ушла в отпуск') }) }),
    fileOf('c.pdf', { vision: waiting({ reason_msg: undefined, reason: 'Waiting for the model' }) }),
    fileOf('d.pdf', { vision: waiting({ reason_msg: messageOf('vision.model_busy', { loaded: 'other-model' }, 'видеокарта занята') }) })])
  assert.ok(text(nameCell(app, 'a.pdf')).includes('The local model is not responding (connection refused): documents wait for a description'))
  assert.ok(text(nameCell(app, 'b.pdf')).includes('Модель ушла в отпуск'))
  assert.ok(text(nameCell(app, 'c.pdf')).includes('Waiting for the model'))
  assert.ok(text(nameCell(app, 'd.pdf')).includes('The GPU is busy with another model (other-model): documents wait for a description'))
  for (const name of ['a.pdf', 'b.pdf', 'c.pdf', 'd.pdf']) assert.equal(text(stateOf(app, name)), 'Awaiting description', name)
})

test('История: замечания notes стоят под строкой по словарю — страница, не пропущенная правилами, названа английски', async (t) => {
  const notes = [
    messageOf('vision.blocked', { rel: 'scans/plan.pdf', page: 2, rule: 'prompt_injection' }, 'scans/plan.pdf: описание страницы 2 не попало в индекс'),
    messageOf('vision.blocked', { rel: 'scans/plan.pdf', page: 5, rule: 'secret' }, 'scans/plan.pdf: описание страницы 5 не попало в индекс')]
  const { app } = await openBatch(t, [fileOf('plan.pdf', { vision: described({ pages: 6, total: 6, notes }) }), fileOf('plain.txt')])
  const cell = text(nameCell(app, 'plan.pdf'))
  assert.ok(cell.includes('scans/plan.pdf: the description of page 2 was kept out of the index: the rules found Prompt injection'), cell)
  assert.ok(cell.includes('scans/plan.pdf: the description of page 5 was kept out of the index: the rules found'), cell)
  assert.ok(!CYRILLIC.test(cell), cell)
  assert.ok(cell.indexOf('Described by the model') < cell.indexOf('page 2'), 'замечания идут после состояния, под ним')
  assert.ok(!text(nameCell(app, 'plain.txt')).includes('kept out of the index'))
})

// дополнительная проверка: часть страниц описана и не пропущена правилами, остальные ещё ждут — замечания видны и у ждущего файла
test('История: замечания notes показаны и у файла, который ещё ждёт описания, — под причиной ожидания', async (t) => {
  const notes = [messageOf('vision.blocked', { rel: 'scans/long.pdf', page: 3, rule: 'secret' }, 'scans/long.pdf: описание страницы 3 не попало в индекс')]
  const { app } = await openBatch(t, [fileOf('long.pdf', { vision: waiting({ pages: 4, total: 9, notes }) })])
  const cell = text(nameCell(app, 'long.pdf'))
  assert.equal(text(stateOf(app, 'long.pdf')), 'Awaiting description')
  assert.ok(cell.includes('scans/long.pdf: the description of page 3 was kept out of the index: the rules found'), cell)
  assert.ok(cell.indexOf('Awaiting description') < cell.indexOf('page 3'), 'замечания идут после состояния и причины')
  assert.ok(!CYRILLIC.test(cell), cell)
})

test('История: файл без поля vision выглядит как раньше — разметка строки не изменилась', async (t) => {
  const { app } = await openBatch(t, [fileOf('plain.txt')])
  assert.equal(html(rowOf(app, 'plain.txt')), PLAIN_ROW)
})

test('История: поле vision не объектом (строка, null, число, список, true) строку файла не меняет', async (t) => {
  const junk = ['да', null, 7, [], ['described'], true, false, '']
  for (const value of junk) {
    const { app } = await openBatch(t, [fileOf('plain.txt', { vision: value })])
    assert.equal(html(rowOf(app, 'plain.txt')), PLAIN_ROW, JSON.stringify(value))
  }
  // и пустой объект, и объект с неизвестным состоянием: сказать нечего
  for (const value of [{}, { state: 'weird' }, { state: 7 }, { state: null }, { state: ['described'] }]) {
    const { app } = await openBatch(t, [fileOf('plain.txt', { vision: value })])
    assert.equal(html(rowOf(app, 'plain.txt')), PLAIN_ROW, JSON.stringify(value))
  }
})

test('История: негодные значения внутри vision не роняют вкладку и не дают NaN, undefined и [object Object]', async (t) => {
  const cases = [
    [{ state: 'described' }, 'Described by the model'],
    [{ state: 'described', pages: 'x', total: 'y', truncated: true }, 'Described by the model'],
    [{ state: 'described', pages: -1, total: -1, truncated: true }, 'Described by the model'],
    [{ state: 'described', pages: 2.5, total: 9, truncated: true }, 'Described by the model'],
    [{ state: 'described', pages: 20, total: 'x', truncated: true }, 'Described by the model: 20 pages'],
    [{ state: 'described', pages: 20, total: null, truncated: 'yes' }, 'Described by the model: 20 pages'],
    [{ state: 'described', pages: null, total: 3, truncated: false, model: 7, seconds: 'долго', notes: 'x' }, 'Described by the model'],
    [{ state: 'described', pages: 3, total: 3, notes: [null, 7, {}, { code: 5, args: 'x', text: 9 }, [], { code: 'vision.blocked', args: {}, text: 7 }] }, 'Described by the model: 3 pages'],
    [{ state: 'waiting' }, 'Awaiting description'],
    [{ state: 'waiting', reason_msg: 7, reason: { a: 1 } }, 'Awaiting description'],
    [{ state: 'waiting', reason_msg: { code: 5, args: 'x', text: 9 }, reason: null }, 'Awaiting description'],
    [{ state: 'waiting', reason_msg: { code: 'vision.model_down', args: {}, text: 'модель не отвечает' }, pages: 'x' }, 'Awaiting description'],
  ]
  for (const [value, expected] of cases) {
    const { app } = await openBatch(t, [fileOf('x.png', { vision: value }), fileOf('plain.txt')])
    const label = JSON.stringify(value)
    assert.equal(text(stateOf(app, 'x.png')), expected, label)
    assert.ok(!BROKEN_TEXT.test(app.text()), `${label}: ${app.text()}`)
    assert.equal(html(rowOf(app, 'plain.txt')), PLAIN_ROW, label) // соседний файл не задет
  }
})

test('История: недоверенные значения — модель, причина, замечания, имя файла — попадают на экран только текстом', async (t) => {
  const evil = '<img src=x onerror=alert(1)><script>alert(2)</script>'
  const { app } = await openBatch(t, [
    fileOf('<b>a</b>.png', { vision: described({ model: evil, notes: [messageOf('vision.from_the_future', {}, evil), messageOf('vision.blocked', { rel: evil, page: 1, rule: evil }, 'ru')] }) }),
    fileOf('b.pdf', { vision: waiting({ reason: evil, reason_msg: messageOf('vision.from_the_future', {}, evil) }) }),
    fileOf('c.pdf', { vision: waiting({ reason: evil, reason_msg: undefined }) })])
  const markup = app.html() // рисовальщик отказывает, если встретит dangerouslySetInnerHTML
  assert.ok(!markup.includes('<img') && !markup.includes('<script') && !markup.includes('<b>a</b>'), markup)
  assert.ok(markup.includes('&lt;img src=x onerror=alert(1)&gt;'))
  assert.ok(app.all((n) => n.type === 'img' || n.type === 'script').length === 0)
})

test('История: все подписи состояния описания — из словаря, без кириллицы при английских данных', async (t) => {
  const p = await openBatch(t, [
    fileOf('a.png', { vision: described({ pages: 20, total: 31, truncated: true, notes: [messageOf('vision.blocked', { rel: 'a.png', page: 3, rule: 'secret' }, 'ru')] }) }),
    fileOf('b.pdf', { vision: waiting({ reason_msg: messageOf('vision.waiting', {}, 'ждёт описания') }) }),
    fileOf('c.pdf', { vision: described({ pages: 1, total: 1 }) }),
    fileOf('d.pdf', { vision: described({ pages: 'x' }) })])
  for (const name of ['a.png', 'b.pdf', 'c.pdf', 'd.pdf']) assert.ok(stateOf(p.app, name), `${name}: состояние описания не показано`)
  assert.ok(!CYRILLIC.test(p.app.html()), p.app.html())
  assert.deepEqual(p.ctx.missingKeys, [])
})

test('История: состояние описания видно сразу в строке пачки, раскрывать файл не нужно; раскрытый файл показывает прежние сведения', async (t) => {
  const { app } = await openBatch(t, [fileOf('scan.png', { vision: described() })])
  assert.equal(app.all((n) => n.type === 'tr' && n.props['data-file-info'] !== undefined).length, 0)
  assert.ok(stateOf(app, 'scan.png'))
  await app.click(find(nameCell(app, 'scan.png'), (n) => n.type === 'button'))
  const info = app.find((n) => n.type === 'tr' && n.props['data-file-info'] === 'scan.png')
  assert.ok(info, 'файл раскрылся')
  assert.ok(text(info).includes('SHA-256:'))
  await flush()
})
