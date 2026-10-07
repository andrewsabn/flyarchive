// Письмо и архив во вкладке: доводка (FR-83).
// Сервер сообщает больше, чем вкладка показывала: признак обрезки письма, признак программы у вложения, дату, имя письма у вложения.
// Здесь вкладка проверяется на подставных ответах `preview show` по договору; рисует тот же малый рисовальщик с состоянием.
// Время суток письма берётся из самой строки (без пересчёта в пояс браузера), поэтому часовой пояс процесса на ответ не влияет.
import assert from 'node:assert/strict'
import test from 'node:test'

import { attachmentOf, item, listingPreview, mailPreview, metaOf, nonePreview, previewKey, textPreview } from './kit-archive.mjs'
import { boot } from './kit-plugin.mjs'
import { find, html, text } from './kit.mjs'

const CYRILLIC = /[Ѐ-ӿ]/
const EVIL = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=3><a href="javascript:alert(4)">x</a>'
const CUT = 'The message is shown in part: the text or the attachment list is cut.'

const pathOf = (name) => item('queue', name).path
/** Описание файла очереди в подставном архиве: имя файла, цепочка вложений, описание. */
const P = (name, description, member = []) => ({ [previewKey('queue', pathOf(name), member)]: description })
/** Плагин на подставном DSH, тело вкладки смонтировано; в очереди — файлы с этими именами. */
async function open(t, names, ...previews) {
  const p = await boot({ toast: false, archive: { queue: names.map((name) => item('queue', name)), previews: Object.assign({}, ...previews) } })
  t.after(p.done)
  const app = await p.tab()
  return { ...p, app }
}
const rowOf = (app, path) => app.find((n) => n.type === 'li' && n.props['data-path'] === path)
const expand = (app, name) => app.click(find(rowOf(app, pathOf(name)), (n) => n.props?.role === 'button' && n.props['aria-expanded'] !== undefined))
const areaOf = (app, name) => find(app.tree(), (n) => n.props?.['data-preview'] === pathOf(name))
const buttonIn = (area, label) => find(area, (n) => n.type === 'button' && text(n) === label)
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
const attachmentRows = (area) => collect(area, (n) => n.type === 'tr' && n.props['data-attachment'] !== undefined)
const fieldsOf = (area, label) => collect(area, (n) => n.props?.className === 'ba-field' && text(n).startsWith(label)).map(text)
const mailWith = (name, mail, over = {}) => {
  const base = mailPreview(name)
  return { ...base, mail: { ...base.mail, ...mail }, ...over }
}

// ── 1. признак обрезки письма ───────────────────────────────────
test('обрезанное письмо: под текстом строка «The message is shown in part…»', async (t) => {
  const { app } = await open(t, ['cut.eml'], P('cut.eml', mailWith('cut.eml', { truncated: true, attachments: [attachmentOf('a.pdf', 1)] })))
  await expand(app, 'cut.eml')
  const area = areaOf(app, 'cut.eml')
  assert.ok(text(area).includes(CUT), text(area))
  const markup = html(area)
  assert.ok(markup.indexOf('<pre') < markup.indexOf(CUT), 'строка стоит под текстом письма')
  assert.ok(markup.indexOf(CUT) < markup.indexOf('a.pdf'), 'и над списком вложений')
})

test('обрезанное письмо без текста и без вложений: строка всё равно показана', async (t) => {
  const { app } = await open(t, ['cut.eml'], P('cut.eml', mailWith('cut.eml', { truncated: true, text: '', attachments: [] })))
  await expand(app, 'cut.eml')
  assert.ok(text(areaOf(app, 'cut.eml')).includes(CUT))
})

test('целое письмо строки обрезки не имеет: truncated false, нет признака вовсе', async (t) => {
  const { app } = await open(t, ['whole.eml', 'bare.eml'], P('whole.eml', mailWith('whole.eml', { truncated: false })), P('bare.eml', mailPreview('bare.eml')))
  for (const name of ['whole.eml', 'bare.eml']) {
    await expand(app, name)
    assert.ok(text(areaOf(app, name)).includes('Subject: Quarterly numbers'), name)
    assert.ok(!text(areaOf(app, name)).includes('shown in part'), name)
  }
})

test('признак обрезки — только настоящее true: строка «true», 1, объект и массив строки не дают', async (t) => {
  const odd = ['true', 1, 'yes', {}, [true], null]
  const names = odd.map((_value, i) => `odd${i}.eml`)
  const { app } = await open(t, names, ...odd.map((value, i) => P(names[i], mailWith(names[i], { truncated: value }))))
  for (const [i, name] of names.entries()) {
    await expand(app, name)
    assert.ok(text(areaOf(app, name)).includes('Subject: Quarterly numbers'), name)
    assert.ok(!text(areaOf(app, name)).includes('shown in part'), `truncated=${JSON.stringify(odd[i])}`)
  }
})

test('вложение письма и его собственные строки обрезки не наследуют: у вложения-письма признак свой', async (t) => {
  const { app } = await open(t, ['top.eml'],
    P('top.eml', mailWith('top.eml', { truncated: true, attachments: [attachmentOf('inner.eml', 4, { type: 'eml' })] })),
    P('top.eml', mailWith('inner.eml', { truncated: false, attachments: [] }), [4]))
  await expand(app, 'top.eml')
  assert.ok(text(areaOf(app, 'top.eml')).includes(CUT))
  await app.click(buttonIn(areaOf(app, 'top.eml'), 'inner.eml'))
  assert.ok(text(areaOf(app, 'top.eml')).includes('Name: inner.eml'))
  assert.ok(!text(areaOf(app, 'top.eml')).includes('shown in part'))
})

// ── 2. программа во вложении ────────────────────────────────────
const PROGRAMS = [
  attachmentOf('setup.exe', 1, { type: 'exe', executable: true }),
  attachmentOf('plain.txt', 2, { type: 'txt', executable: false }),
  attachmentOf('no-flag.pdf', 3),
  attachmentOf('string-true.exe', 4, { type: 'exe', executable: 'true' }),
  attachmentOf('number-one.exe', 5, { type: 'exe', executable: 1 }),
  attachmentOf('string-false.pdf', 6, { type: 'pdf', executable: 'false' }),
  attachmentOf('null-flag.pdf', 7, { type: 'pdf', executable: null }),
]
const programsMail = () => P('prog.eml', mailWith('prog.eml', { attachments: PROGRAMS }))

test('программа во вложении: слово «program» рядом с типом и data-program="true" у строки; у остальных ни того, ни другого', async (t) => {
  const { app } = await open(t, ['prog.eml'], programsMail())
  await expand(app, 'prog.eml')
  const rows = attachmentRows(areaOf(app, 'prog.eml'))
  assert.equal(rows.length, PROGRAMS.length)
  const marked = rows.filter((r) => r.props['data-program'] === 'true').map((r) => text(r.props.children[0]))
  assert.deepEqual(marked, ['setup.exe'])
  for (const row of rows) {
    const [name, type, size] = row.props.children.map(text)
    if (name === 'setup.exe') {
      assert.equal(type, 'exe program')
      assert.equal(size, '2 KB')
    } else {
      assert.equal(row.props['data-program'], undefined, name)
      assert.ok(!text(row).includes('program'), name)
    }
  }
  assert.equal(rows[1].props.children.map(text)[1], 'txt') // обычное вложение — как прежде: тип без добавок
})

test('отметка программы стоит и у вложения без типа, и на втором уровне, где вложения только перечислены', async (t) => {
  const odd = attachmentOf('mystery', 1, { type: undefined, executable: true })
  const inner = [attachmentOf('deep.exe', 0, { type: 'exe', executable: true }), attachmentOf('deep.txt', 1, { type: 'txt' })]
  const { app } = await open(t, ['prog.eml'],
    P('prog.eml', mailWith('prog.eml', { attachments: [odd, attachmentOf('inner.eml', 5, { type: 'eml' })] })),
    P('prog.eml', mailWith('inner.eml', { attachments: [attachmentOf('mid.eml', 7, { type: 'eml' })] }), [5]),
    P('prog.eml', mailWith('mid.eml', { attachments: inner }), [5, 7]))
  await expand(app, 'prog.eml')
  assert.equal(text(attachmentRows(areaOf(app, 'prog.eml'))[0].props.children[1]), '— program')
  await app.click(buttonIn(areaOf(app, 'prog.eml'), 'inner.eml'))
  await app.click(buttonIn(areaOf(app, 'prog.eml'), 'mid.eml'))
  const rows = attachmentRows(areaOf(app, 'prog.eml'))
  assert.deepEqual(rows.map((r) => r.props['data-program']), ['true', undefined])
  assert.equal(buttonIn(areaOf(app, 'prog.eml'), 'deep.exe'), null) // на втором уровне вложения не открываются, отметка при этом видна
  assert.ok(text(rows[0]).includes('program'))
})

test('вложение с отметкой «программа» открывается по-прежнему: щелчок по имени, member из записи, сведения с sha256', async (t) => {
  const note = { code: null, args: {}, text: 'The content of a program is not shown' }
  const exe = nonePreview('setup.exe', note, { meta: metaOf('setup.exe', { type: 'exe', sha256: '5c'.repeat(32) }) })
  const { app, server } = await open(t, ['prog.eml'], programsMail(), P('prog.eml', exe, [1]))
  await expand(app, 'prog.eml')
  await app.click(buttonIn(areaOf(app, 'prog.eml'), 'setup.exe'))
  assert.deepEqual(server.previewCalls().map((b) => b.member), [[], [1]])
  const shown = text(areaOf(app, 'prog.eml'))
  assert.ok(shown.includes('SHA-256: ' + '5c'.repeat(32)), shown)
  assert.ok(shown.includes('The content of a program is not shown'))
  assert.ok(buttonIn(areaOf(app, 'prog.eml'), 'Back to message'))
})

test('отметка не отнимает щелчок у обычных вложений: рядом стоящие кнопки живы', async (t) => {
  const { app, server } = await open(t, ['prog.eml'], programsMail(), P('prog.eml', textPreview('plain.txt', 'hello'), [2]))
  await expand(app, 'prog.eml')
  assert.ok(buttonIn(areaOf(app, 'prog.eml'), 'plain.txt'))
  assert.ok(buttonIn(areaOf(app, 'prog.eml'), 'setup.exe'))
  await app.click(buttonIn(areaOf(app, 'prog.eml'), 'plain.txt'))
  assert.deepEqual(server.previewCalls().map((b) => b.member), [[], [2]])
})

// ── 3. дата письма ──────────────────────────────────────────────
const DATES = [
  ['2026-09-30T11:15:00+03:00', '30.09.2026 11:15 +03:00'],
  ['2026-09-30T08:15:00-05:30', '30.09.2026 08:15 -05:30'],
  ['2026-09-30T08:15:00Z', '30.09.2026 08:15 UTC'],
  ['2026-09-30T00:05:00+00:00', '30.09.2026 00:05 UTC'],
  ['2026-09-30T23:59:59.123456+02:00', '30.09.2026 23:59 +02:00'],
  ['2026-09-30 08:15:00+0300', '30.09.2026 08:15 +03:00'],
  ['2026-09-30T08:15:00', '30.09.2026 08:15'],
  ['2026-09-30T08:15', '30.09.2026 08:15'],
  ['2026-09-30', '30.09.2026'],
  ['not a date', 'not a date'],
  ['2026-13-45T99:99:99', '2026-13-45T99:99:99'],
  ['2026-02-30T08:15:00Z', '2026-02-30T08:15:00Z'],
  ['2026-02-29T08:15:00Z', '2026-02-29T08:15:00Z'],
  ['2028-02-29T08:15:00Z', '29.02.2028 08:15 UTC'],
  ['2026-13-10T08:15:00Z', '2026-13-10T08:15:00Z'],
  ['2026-00-10', '2026-00-10'],
  ['2026-09-30T08:15:00+25:00', '2026-09-30T08:15:00+25:00'],
  ['2026-09-30T08:15:00+03:60', '2026-09-30T08:15:00+03:60'],
  ['2026-09-30T24:00:00Z', '2026-09-30T24:00:00Z'],
  ['2026-09-30T08:60:00Z', '2026-09-30T08:60:00Z'],
  ['2026-09-30T08:15:61Z', '2026-09-30T08:15:61Z'],
  ['Wed, 30 Sep 2026 08:15:00 +0000', 'Wed, 30 Sep 2026 08:15:00 +0000'],
  [EVIL, EVIL],
  [null, '—'],
  ['', '—'],
  [undefined, '—'],
  [1790756100, '—'],
  [{ iso: '2026-09-30' }, '—'],
]

test('дата письма: ISO с поясом, без пояса, только день, мусор, null — каждая в своём виде', async (t) => {
  const names = DATES.map((_pair, i) => `d${i}.eml`)
  const { app } = await open(t, names, ...DATES.map(([date], i) => P(names[i], mailWith(names[i], { date }))))
  for (const [i, name] of names.entries()) {
    await expand(app, name)
    const [source, shown] = DATES[i]
    assert.deepEqual(fieldsOf(areaOf(app, name), 'Date:'), ['Date: ' + shown], `дата ${JSON.stringify(source)}`)
  }
})

test('дата письма не зависит от часового пояса браузера: время суток берётся из самой строки', async (t) => {
  const saved = process.env.TZ
  t.after(() => { process.env.TZ = saved })
  process.env.TZ = 'Asia/Tokyo'
  const { app } = await open(t, ['tz.eml'], P('tz.eml', mailWith('tz.eml', { date: '2026-09-30T08:15:00-05:30' })))
  await expand(app, 'tz.eml')
  assert.deepEqual(fieldsOf(areaOf(app, 'tz.eml'), 'Date:'), ['Date: 30.09.2026 08:15 -05:30'])
})

test('дата выводится текстом: разметка в строке даты остаётся буквами', async (t) => {
  const { app } = await open(t, ['evil.eml'], P('evil.eml', mailWith('evil.eml', { date: EVIL })))
  await expand(app, 'evil.eml')
  const html = app.html()
  for (const raw of ['<img src=x', '<script', '<svg', '<a href']) assert.ok(!html.includes(raw), raw)
  assert.ok(html.includes('&lt;script&gt;'))
  for (const node of app.all(() => true)) {
    for (const bad of ['dangerouslySetInnerHTML', 'innerHTML', 'href']) assert.equal(node.props[bad], undefined, bad)
  }
})

// ── 4. сведения о файле без пустых строк ────────────────────────
const META = { type: 'application/pdf', size: 5 * 1024 * 1024, sha256: '01'.repeat(32), batch: '20260101-120000' }
const metaCases = (origin) => [
  ['text', { ...textPreview('f.txt', 'x'), meta: metaOf('f.txt', { ...META, origin }) }],
  ['listing', { ...listingPreview('f.zip', [{ name: 'a', size: 1 }]), meta: metaOf('f.zip', { ...META, origin }) }],
  ['none', { ...nonePreview('f.exe', { code: null, args: {}, text: 'No preview' }), meta: metaOf('f.exe', { ...META, origin }) }],
  ['mail', mailWith('f.eml', { cc: ['c@example.test'] }, { meta: metaOf('f.eml', { ...META, origin }) })], // с копией: в шапке письма нет прочерков
]
const OTHER_ROWS = ['Name:', 'Type: application/pdf', 'Size: 5.0 MB', 'SHA-256: ' + '01'.repeat(32), 'Batch: 20260101-120000']

test('происхождение из архива: обе строки — «Archive:» и «Path inside the archive:» — на месте, и остальные тоже', async (t) => {
  for (const [kind, description] of metaCases({ archive: 'bundle.zip', inner: 'docs/a.pdf' })) {
    const { app } = await open(t, ['f.bin'], P('f.bin', description))
    await expand(app, 'f.bin')
    const shown = text(areaOf(app, 'f.bin'))
    for (const part of ['Archive: bundle.zip', 'Path inside the archive: docs/a.pdf', ...OTHER_ROWS]) assert.ok(shown.includes(part), `${kind}: ${part}: ${shown}`)
  }
})

test('без происхождения строк про архив нет вовсе, и «—» на их месте тоже: остальные строки целы', async (t) => {
  const origins = [{ archive: null, inner: null }, null, undefined, {}, { archive: '', inner: '' }, { archive: 5, inner: ['x'] }]
  for (const origin of origins) {
    for (const [kind, description] of metaCases(origin)) {
      const { app } = await open(t, ['f.bin'], P('f.bin', description))
      await expand(app, 'f.bin')
      const shown = text(areaOf(app, 'f.bin'))
      const label = `${kind} ${JSON.stringify(origin)}`
      assert.ok(!shown.includes('Archive:'), `${label}: ${shown}`)
      assert.ok(!shown.includes('Path inside the archive'), `${label}: ${shown}`)
      assert.ok(!shown.includes('—'), `${label}: ${shown}`)
      for (const part of OTHER_ROWS) assert.ok(shown.includes(part), `${label}: ${part}`)
    }
  }
})

test('одно из двух значений: архив без пути или путь без архива — показана только строка, у которой есть значение', async (t) => {
  const { app } = await open(t, ['only-archive.bin', 'only-inner.bin'],
    P('only-archive.bin', textPreview('x', 'x', { meta: metaOf('x', { origin: { archive: 'bundle.zip', inner: null } }) })),
    P('only-inner.bin', textPreview('x', 'x', { meta: metaOf('x', { origin: { archive: null, inner: 'docs/a.pdf' } }) })))
  await expand(app, 'only-archive.bin')
  const first = text(areaOf(app, 'only-archive.bin'))
  assert.ok(first.includes('Archive: bundle.zip') && !first.includes('Path inside the archive'), first)
  await expand(app, 'only-inner.bin')
  const second = text(areaOf(app, 'only-inner.bin'))
  assert.ok(second.includes('Path inside the archive: docs/a.pdf') && !second.includes('Archive:'), second)
})

test('сведения без прочих значений: имя, тип, sha256 и пачка по-прежнему показаны прочерком, когда их нет', async (t) => {
  const { app } = await open(t, ['bare.bin'], P('bare.bin', { kind: 'text', meta: { name: 'bare.bin' }, text: 'x' }))
  await expand(app, 'bare.bin')
  const shown = text(areaOf(app, 'bare.bin'))
  for (const part of ['Name: bare.bin', 'Type: —', 'SHA-256: —', 'Batch: —']) assert.ok(shown.includes(part), `${part}: ${shown}`)
  assert.ok(!shown.includes('Archive:'))
  assert.ok(!/null|undefined|NaN/.test(shown), shown)
})

test('у вложения письма строк про архив нет: origin вложения всегда пустой', async (t) => {
  const { app } = await open(t, ['top.eml'],
    P('top.eml', mailWith('top.eml', { attachments: [attachmentOf('a.txt', 2, { type: 'txt' })] })),
    P('top.eml', textPreview('a.txt', 'hello'), [2]))
  await expand(app, 'top.eml')
  await app.click(buttonIn(areaOf(app, 'top.eml'), 'a.txt'))
  const shown = text(areaOf(app, 'top.eml'))
  assert.ok(shown.includes('Name: a.txt'))
  assert.ok(!shown.includes('Archive:') && !shown.includes('Path inside the archive') && !shown.includes('—'), shown)
})

// ── 5. вложение знает своё письмо ───────────────────────────────
const LINE = (name) => 'Attachment of: ' + name
const chain = () => [
  P('top.eml', mailWith('Parent letter.eml', { attachments: [attachmentOf('a.txt', 3, { type: 'txt' }), attachmentOf('inner.eml', 5, { type: 'eml' })] })),
  P('top.eml', textPreview('own-name.txt', 'hello'), [3]),
  P('top.eml', mailWith('Inner letter.eml', { attachments: [attachmentOf('deep.txt', 0, { type: 'txt' })] }), [5]),
  P('top.eml', textPreview('deep-own-name.txt', 'deep'), [5, 0]),
]

test('вложение показывает имя письма из его meta.name, а не своё и не имя файла в очереди', async (t) => {
  const { app } = await open(t, ['top.eml'], ...chain())
  await expand(app, 'top.eml')
  await app.click(buttonIn(areaOf(app, 'top.eml'), 'a.txt'))
  const shown = text(areaOf(app, 'top.eml'))
  assert.ok(shown.includes(LINE('Parent letter.eml')), shown)
  assert.ok(!shown.includes(LINE('own-name.txt')) && !shown.includes(LINE('top.eml')), shown)
  assert.ok(shown.includes('Name: own-name.txt'))
})

test('строка стоит над сведениями о файле, не над письмом и не в тексте вложения', async (t) => {
  const { app } = await open(t, ['top.eml'], ...chain())
  await expand(app, 'top.eml')
  await app.click(buttonIn(areaOf(app, 'top.eml'), 'a.txt'))
  const markup = html(areaOf(app, 'top.eml'))
  assert.ok(markup.includes(LINE('Parent letter.eml')))
  assert.ok(markup.indexOf(LINE('Parent letter.eml')) < markup.indexOf('Name:'), 'над сведениями')
  assert.ok(markup.indexOf('hello') < markup.indexOf(LINE('Parent letter.eml')), 'под телом вложения')
})

test('письмо верхнего уровня строки не показывает; «Back to message» убирает её', async (t) => {
  const { app } = await open(t, ['top.eml'], ...chain())
  await expand(app, 'top.eml')
  assert.ok(!text(areaOf(app, 'top.eml')).includes('Attachment of'))
  await app.click(buttonIn(areaOf(app, 'top.eml'), 'a.txt'))
  assert.ok(text(areaOf(app, 'top.eml')).includes('Attachment of'))
  await app.click(buttonIn(areaOf(app, 'top.eml'), 'Back to message'))
  assert.ok(!text(areaOf(app, 'top.eml')).includes('Attachment of'))
  assert.ok(text(areaOf(app, 'top.eml')).includes('Subject: Quarterly numbers'))
})

test('вложение второго уровня показывает имя письма первого уровня, а не верхнего; шаг назад возвращает имя верхнего', async (t) => {
  const { app, server } = await open(t, ['top.eml'], ...chain())
  await expand(app, 'top.eml')
  await app.click(buttonIn(areaOf(app, 'top.eml'), 'inner.eml'))
  assert.ok(text(areaOf(app, 'top.eml')).includes(LINE('Parent letter.eml')))
  await app.click(buttonIn(areaOf(app, 'top.eml'), 'deep.txt'))
  const deep = text(areaOf(app, 'top.eml'))
  assert.ok(deep.includes(LINE('Inner letter.eml')), deep)
  assert.ok(!deep.includes('Parent letter.eml'), deep)
  await app.click(buttonIn(areaOf(app, 'top.eml'), 'Back to message'))
  assert.ok(text(areaOf(app, 'top.eml')).includes(LINE('Parent letter.eml')))
  assert.deepEqual(server.previewCalls().map((b) => b.member), [[], [5], [5, 0]]) // имя взято из стопки видов: новых запросов нет
})

test('у родителя нет имени: строка есть, на месте имени прочерк', async (t) => {
  const bare = { ...mailPreview('x'), meta: { type: 'eml' }, mail: { ...mailPreview('x').mail, attachments: [attachmentOf('a.txt', 3, { type: 'txt' })] } }
  const { app } = await open(t, ['top.eml'], P('top.eml', bare), P('top.eml', textPreview('a.txt', 'hello'), [3]))
  await expand(app, 'top.eml')
  await app.click(buttonIn(areaOf(app, 'top.eml'), 'a.txt'))
  assert.ok(text(areaOf(app, 'top.eml')).includes(LINE('—')))
})

test('имя письма, имя вложения и дата с разметкой выходят текстом: ни тегов, ни ссылок, ни обработчиков', async (t) => {
  const evil = P('top.eml', mailWith(EVIL, { date: EVIL, subject: EVIL, attachments: [attachmentOf(EVIL, 3, { type: EVIL, executable: true })] }))
  const { app } = await open(t, ['top.eml'], evil, P('top.eml', textPreview(EVIL, EVIL, { meta: metaOf(EVIL, { type: EVIL, sha256: EVIL, batch: EVIL }) }), [3]))
  await expand(app, 'top.eml')
  await app.click(buttonIn(areaOf(app, 'top.eml'), EVIL))
  assert.ok(text(areaOf(app, 'top.eml')).includes(LINE(EVIL)), 'имя письма — буквами')
  const html = app.html()
  for (const raw of ['<img src=x', '<script', '<svg', '<a href', 'onerror=alert(1)>']) assert.ok(!html.includes(raw), raw)
  assert.ok(html.includes('&lt;script&gt;'))
  for (const node of app.all(() => true)) {
    for (const bad of ['dangerouslySetInnerHTML', 'innerHTML', 'href', 'srcDoc', 'srcdoc']) assert.equal(node.props[bad], undefined, bad)
    assert.ok(!['a', 'iframe', 'object', 'embed', 'script', 'svg', 'style', 'link'].includes(node.type), node.type)
  }
})

// ── 6. словарь ──────────────────────────────────────────────────
test('новые подписи — в словаре вкладки по-английски; экран не просит ключей, которых там нет', async (t) => {
  const first = mailWith('top.eml', { truncated: true, attachments: [attachmentOf('setup.exe', 1, { type: 'exe', executable: true })] })
  const { app, ctx } = await open(t, ['top.eml'], P('top.eml', first), P('top.eml', textPreview('setup.exe', 'x'), [1]))
  await expand(app, 'top.eml')
  assert.ok(text(areaOf(app, 'top.eml')).includes('program'))
  await app.click(buttonIn(areaOf(app, 'top.eml'), 'setup.exe'))
  assert.ok(text(areaOf(app, 'top.eml')).includes('Attachment of: top.eml'))
  assert.deepEqual([...new Set(ctx.missingKeys)], [])
  const { zh, en } = ctx.dictionaries.flyarchive
  assert.deepEqual(zh, en)
  assert.equal(en['preview.mail.truncated'], CUT)
  assert.equal(en['preview.mail.program'], 'program')
  assert.equal(en['preview.mail.inside'], 'Attachment of: {name}')
  for (const key of ['preview.mail.truncated', 'preview.mail.program', 'preview.mail.inside']) assert.ok(!CYRILLIC.test(en[key]), key)
  assert.ok(!CYRILLIC.test(text(areaOf(app, 'top.eml'))))
})
