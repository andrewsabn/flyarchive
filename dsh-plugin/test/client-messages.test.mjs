// Английский словарь сообщений системы и названия, известные уже сейчас (FR-80: «сообщение показывается по английскому словарю»).
// Файл client.messages.js подключается из client.js через require.async: хост DSH отдаёт только client.js и соседей client.<имя>.js.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { load } from './kit.mjs'

const CYRILLIC = /[Ѐ-ӿ]/
const messages = async () => {
  const { require } = await load()
  return require.async('./client.messages.js')
}

// ── подключение ─────────────────────────────────────────────────
test('словарь отдаётся соседним файлом client.messages.js под тем же id, что у плагина', async () => {
  // load() проверяет и имя файла в регистрации (chunk), и совпадение id с владельцем, и что сосед самодостаточен
  const m = await messages()
  for (const name of ['format', 'stageName', 'decisionName', 'levelName', 'ruleName'])
    assert.equal(typeof m[name], 'function', name)
})

// ── сообщение {code, args, text} ────────────────────────────────
test('известный код превращается в английскую строку по шаблону с параметрами', async () => {
  const { format } = await messages()
  const text = format({ code: 'llm_finding', args: { model: 'm1', page: 2, why: 'tries to override the rules' }, text: 'модель m1: пытается' })
  assert.equal(text, 'The model m1 reported: tries to override the rules') // страницу показывает where_msg, в тексте находки её нет
})

test('неизвестный код показывается исходным text', async () => {
  const { format } = await messages()
  assert.equal(format({ code: 'no_such_code', args: { a: 1 }, text: 'Русский текст как есть' }), 'Русский текст как есть')
  assert.equal(format({ code: null, args: {}, text: 'старое сообщение без кода' }), 'старое сообщение без кода')
  assert.equal(format({ args: {}, text: 'совсем без кода' }), 'совсем без кода')
})

test('имена из прототипа объекта кодами не считаются', async () => {
  const { format } = await messages()
  for (const code of ['constructor', 'toString', 'hasOwnProperty', '__proto__', 'valueOf'])
    assert.equal(format({ code, args: {}, text: 'text:' + code }), 'text:' + code, code)
})

test('если параметра шаблона нет или он не из простых значений — показывается text, а не дыра в строке', async () => {
  const { format } = await messages()
  const templates = { two: 'A {x} and {y}' }
  assert.equal(format({ code: 'two', args: { x: 1, y: 'b' }, text: 'ru' }, templates), 'A 1 and b')
  assert.equal(format({ code: 'two', args: { x: 1 }, text: 'ru' }, templates), 'ru')
  assert.equal(format({ code: 'two', args: { x: 1, y: null }, text: 'ru' }, templates), 'ru')
  assert.equal(format({ code: 'two', args: { x: 1, y: { z: 1 } }, text: 'ru' }, templates), 'ru')
  assert.equal(format({ code: 'two', text: 'ru' }, templates), 'ru')
  assert.equal(format({ code: 'two', args: { x: 0, y: false }, text: 'ru' }, templates), 'A 0 and false')
})

test('значение параметра вставляется как есть: знаки подстановки не действуют', async () => {
  const { format } = await messages()
  const templates = { one: 'File {name}!' }
  assert.equal(format({ code: 'one', args: { name: '$& $1 $$' }, text: 'ru' }, templates), 'File $& $1 $$!')
  assert.equal(format({ code: 'one', args: { name: '{name}' }, text: 'ru' }, templates), 'File {name}!')
})

test('негодное сообщение не роняет показ', async () => {
  const { format } = await messages()
  assert.equal(format('просто строка'), 'просто строка')
  assert.equal(format(null), '')
  assert.equal(format(undefined), '')
  assert.equal(format({ code: 'x' }), '')
  assert.equal(format({ code: 'x', text: 7 }), '')
  assert.equal(format(42), '')
})

// ── названия, известные уже сейчас ──────────────────────────────
test('этапы хода разбора', async () => {
  const { stageName } = await messages()
  assert.deepEqual(['intake', 'model', 'settle', 'index', 'sources'].map(stageName), [
    'Unpacking and rule checks', 'Model check', 'Filing and de-duplication', 'Indexing', 'Cleaning up sources'])
  assert.equal(stageName('новый_этап'), 'новый_этап') // чужого этапа плагин не знает, но и не прячет
  assert.equal(stageName(null), '')
})

test('решения приёмки: порядок показа и названия', async () => {
  const { decisionName, DECISIONS } = await messages()
  assert.deepEqual(Object.keys(DECISIONS), ['accept', 'review', 'quarantine', 'duplicate', 'skip', 'unpacked', 'failed'])
  assert.deepEqual(Object.keys(DECISIONS).map(decisionName), [
    'accepted', 'needs review', 'quarantined', 'duplicates', 'skipped', 'archives unpacked', 'failed'])
  assert.equal(decisionName('unknown'), 'unknown')
})

test('уровни находок', async () => {
  const { levelName } = await messages()
  assert.deepEqual(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map(levelName), ['Critical', 'High', 'Medium', 'Low'])
  assert.equal(levelName('WEIRD'), 'WEIRD')
})

test('у каждого правила находок, которое знает сервер, есть английское название', async () => {
  const { ruleName } = await messages()
  // перечень правил отдаёт сервер: tools/messages.py --rules; что он совпадает с кодом gate.py, llm_check.py и intake.py,
  // проверяет tests/test_messages.py, поэтому исходники здесь не разбираются
  const script = fileURLToPath(new URL('../../tools/messages.py', import.meta.url))
  const emitted = new Set(JSON.parse(execFileSync(process.env.PYTHON || 'python3', [script, '--rules'], { encoding: 'utf8' })))
  assert.ok(emitted.size >= 15, `правил получено ${emitted.size}: перечень сервера не прочитан`)
  for (const rule of emitted) {
    const name = ruleName(rule)
    assert.notEqual(name, rule, `у правила ${rule} нет английского названия`)
    assert.ok(name.length > 0 && !CYRILLIC.test(name), rule)
  }
  assert.equal(new Set([...emitted].map(ruleName)).size, emitted.size, 'у разных правил одинаковые названия')
  assert.equal(ruleName('executable'), 'Executable file')
  assert.equal(ruleName('prompt_injection'), 'Prompt injection')
  assert.equal(ruleName('rule_from_the_future'), 'rule_from_the_future')
})

test('в словарях нет русских слов: экран вкладки английский', async () => {
  const m = await messages()
  for (const table of [m.STAGES, m.DECISIONS, m.LEVELS, m.RULES, m.TEMPLATES]) {
    assert.ok(Object.keys(table).length > 0)
    for (const value of Object.values(table)) assert.ok(typeof value === 'string' && !CYRILLIC.test(value), value)
  }
  for (const [code, params] of Object.entries(m.WORDS))
    for (const [param, kinds] of Object.entries(params))
      for (const [kind, word] of Object.entries(kinds)) assert.ok(typeof word === 'string' && word !== '' && !CYRILLIC.test(word), `${code}.${param}.${kind}`)
})

// ── слова видов ─────────────────────────────────────────────────
test('параметр с видом показывается английским словом; незнакомый вид — как пришёл', async () => {
  const { format } = await messages()
  const templates = { kind: 'Found: {kind} here', other: 'Other: {kind}' }
  const words = { kind: { kind: { pe: 'Windows program', script: 'Script' } } }
  assert.equal(format({ code: 'kind', args: { kind: 'pe' }, text: 'ru' }, templates, words), 'Found: Windows program here')
  assert.equal(format({ code: 'kind', args: { kind: 'script' }, text: 'ru' }, templates, words), 'Found: Script here')
  assert.equal(format({ code: 'kind', args: { kind: 'ufo' }, text: 'ru' }, templates, words), 'Found: ufo here') // вида из будущего плагин не знает, но и не прячет
  assert.equal(format({ code: 'kind', args: { kind: 7 }, text: 'ru' }, templates, words), 'Found: 7 here')
  for (const kind of ['constructor', 'toString', '__proto__']) assert.equal(format({ code: 'kind', args: { kind }, text: 'ru' }, templates, words), `Found: ${kind} here`, kind)
  assert.equal(format({ code: 'other', args: { kind: 'pe' }, text: 'ru' }, templates, words), 'Other: pe') // слова — только у того кода, для которого они объявлены
})

test('слова видов у настоящих кодов: программа, внедрение указаний, секрет', async () => {
  const { format } = await messages()
  const said = (code, args) => format({ code, args, text: 'РУССКИЙ ТЕКСТ' })
  assert.equal(said('finding.executable', { kind: 'pe' }), 'Windows program')
  assert.equal(said('finding.executable_renamed', { kind: 'pe', ext: 'pdf' }), 'Windows program; the .pdf extension does not match the content')
  assert.equal(said('finding.prompt_injection', { kind: 'cancel_instructions', quoted: true }), 'Attempt to cancel earlier instructions')
  assert.equal(said('finding.secret', { kind: 'aws_key', quoted: true }), 'AWS key')
  assert.equal(said('finding.secret', { kind: 'kind_from_the_future', quoted: true }), 'kind_from_the_future')
})

// ── пустой параметр ─────────────────────────────────────────────
test('пустой параметр: часть шаблона в {? … ?} показывается, только когда все её параметры не пусты; русский text не подставляется', async () => {
  const { format } = await messages()
  const templates = { cite: 'Found: {what}{? on page {page}?}', two: 'A{? {x}?}{? and {y}?}!', fixed: 'Always{? maybe?} here' }
  const said = (code, args) => format({ code, args, text: 'РУССКИЙ ТЕКСТ' }, templates)
  assert.equal(said('cite', { what: 'w', page: 3 }), 'Found: w on page 3')
  assert.equal(said('cite', { what: 'w', page: null }), 'Found: w') // пусто: части нет, а не русский text
  assert.equal(said('cite', { what: 'w', page: '' }), 'Found: w') // пустая строка — тоже пусто
  assert.equal(said('cite', { what: 'w', page: 0 }), 'Found: w on page 0') // ноль и false — значения
  assert.equal(said('cite', { what: 'w', page: false }), 'Found: w on page false')
  assert.equal(said('two', { x: 'p', y: 'q' }), 'A p and q!')
  assert.equal(said('two', { x: null, y: 'q' }), 'A and q!')
  assert.equal(said('two', { x: null, y: null }), 'A!')
  assert.equal(said('fixed', {}), 'Always maybe here') // часть без параметров всегда на месте
})

test('пустой параметр в части шаблона не ломает остальное: незаполненный обязательный параметр и негодное значение — по-прежнему text', async () => {
  const { format } = await messages()
  const templates = { cite: 'Found: {what}{? on page {page}?}' }
  const said = (args) => format({ code: 'cite', args, text: 'ru' }, templates)
  assert.equal(said({ what: null, page: 3 }), 'ru') // обязательный параметр пуст: такое сообщение сломано
  assert.equal(said({ page: 3 }), 'ru') // обязательного параметра нет
  assert.equal(said({ what: 'w' }), 'ru') // параметра части нет вовсе — это не «пусто», а сломанное сообщение
  assert.equal(said({ what: 'w', page: { n: 1 } }), 'ru') // не простое значение
})

test('значения параметров в частях шаблона вставляются как есть: знаки подстановки и скобки не действуют', async () => {
  const { format } = await messages()
  const templates = { cite: 'Found: {what}{? on {page}?}' }
  assert.equal(format({ code: 'cite', args: { what: '{page}', page: '{?x?} $&' }, text: 'ru' }, templates), 'Found: {page} on {?x?} $&')
})

test('находка модели по-английски: страница в тексте не повторяется (её показывает where_msg), пустые слова модели не оставляют двоеточия', async () => {
  const { format } = await messages()
  const said = (args) => format({ code: 'llm_finding', args: { model: 'm1', quoted: true, ...args }, text: 'модель m1: РУССКИЙ' })
  assert.equal(said({ page: null, why: 'asks to ignore the rules' }), 'The model m1 reported: asks to ignore the rules')
  assert.equal(said({ page: 4, why: 'asks to ignore the rules' }), 'The model m1 reported: asks to ignore the rules')
  assert.equal(said({ page: null, why: '' }), 'The model m1 reported')
  assert.equal(said({ page: 4, why: '' }), 'The model m1 reported')
})

// ── название правила в параметре ────────────────────────────────
test('параметр с именем rule показывается английским названием правила; незнакомое правило — как пришло', async () => {
  const { format } = await messages()
  const said = (rule) => format({ code: 'reason.held', args: { rule }, text: 'РУССКИЙ ТЕКСТ' })
  assert.equal(said('prompt_injection'), "Held for the owner's decision. Finding: Prompt injection")
  assert.equal(said('executable'), "Held for the owner's decision. Finding: Executable file")
  assert.equal(said('rule_from_the_future'), "Held for the owner's decision. Finding: rule_from_the_future")
  assert.equal(said(7), "Held for the owner's decision. Finding: 7")
  for (const rule of ['constructor', '__proto__', 'toString']) assert.equal(said(rule), `Held for the owner's decision. Finding: ${rule}`, rule)
})

test('название по имени параметра — общий способ: любой код с параметром rule, таблицы по имени можно подменить', async () => {
  const { format } = await messages()
  const templates = { 'any.code': 'Rule: {rule}', 'other.code': 'Colour: {colour}; rule: {rule}' }
  assert.equal(format({ code: 'any.code', args: { rule: 'macros' }, text: 'ru' }, templates), 'Rule: Macros') // код не из каталога, слов у него нет
  assert.equal(format({ code: 'any.code', args: { rule: 'zzz' }, text: 'ru' }, templates), 'Rule: zzz')
  assert.equal(format({ code: 'other.code', args: { colour: 'r', rule: 'secret' }, text: 'ru' }, templates, {}, { colour: { r: 'Red' } }),
    'Colour: Red; rule: secret') // своя таблица по имени параметра заменяет общую
  assert.equal(format({ code: 'other.code', args: { colour: 'r', rule: 'secret' }, text: 'ru' }, templates, {}, { colour: { r: 'Red' }, rule: { secret: 'Secret thing' } }),
    'Colour: Red; rule: Secret thing')
  // слово вида у кода сильнее общей таблицы по имени параметра
  assert.equal(format({ code: 'any.code', args: { rule: 'macros' }, text: 'ru' }, templates, { 'any.code': { rule: { macros: 'Own word' } } }), 'Rule: Own word')
})

test('у каждого кода каталога с параметром rule все правила сервера показываются английскими названиями', async () => {
  const { format, RULES } = await messages()
  const catalog = serverCatalog('--json')
  const rules = serverCatalog('--rules')
  const codes = Object.entries(catalog).filter(([, params]) => params.includes('rule'))
  assert.ok(codes.length >= 1 && rules.length >= 15, 'каталог или перечень правил не прочитаны')
  for (const [code, params] of codes) {
    for (const rule of rules) {
      const args = Object.fromEntries(params.map((name) => [name, name === 'rule' ? rule : SERVICE.has(name) ? true : 'sample']))
      const line = format({ code, args, text: 'РУССКИЙ ТЕКСТ' })
      assert.ok(line.includes(RULES[rule]) && !CYRILLIC.test(line), `${code} / ${rule}: ${line}`)
    }
  }
})

// ── сверка с каталогом сервера (FR-73в) ─────────────────────────
const SERVICE = new Set(['quoted', 'excerpt']) // служебные параметры: в шаблон не подставляются, у любого кода каталога допустимы
// Параметры каталога, которых нет в английском шаблоне, — только отсюда и только с причиной: код → { параметр: почему }.
// Остальные английские шаблоны используют все неслужебные параметры каталога.
const UNUSED_OK = {
  llm_finding: { page: 'страницу показывает where_msg' },
}
const slots = (template) => new Set([...template.matchAll(/\{(\w+)\}/g)].map((m) => m[1]))
const owns = (table, key) => Object.prototype.hasOwnProperty.call(table, key)

/**
 * Расхождения английского словаря с каталогом сервера: по строке на расхождение, строка начинается с кода.
 * catalog: {код: [имена параметров]} (messages.py --json); words: {код: {параметр: {вид: русское слово}}} (--words);
 * templates и englishWords — словарь плагина; unusedOk — исключения: параметры каталога без места в английском шаблоне.
 */
function drift({ catalog, words, templates, englishWords, unusedOk }) {
  const problems = []
  for (const code of Object.keys(catalog)) if (!owns(templates, code)) problems.push(`${code}: в словаре нет английского шаблона`)
  for (const code of Object.keys(templates)) if (!owns(catalog, code)) problems.push(`${code}: код есть в словаре, но нет в каталоге сервера`)
  for (const [code, template] of Object.entries(templates)) {
    if (!owns(catalog, code)) continue
    const used = slots(template)
    for (const name of used) {
      if (SERVICE.has(name)) problems.push(`${code}: служебный параметр {${name}} подставлен в шаблон`)
      else if (!catalog[code].includes(name)) problems.push(`${code}: параметр {${name}} шаблона не объявлен в каталоге`)
    }
    for (const name of catalog[code]) {
      if (SERVICE.has(name) || used.has(name)) continue
      if (!(owns(unusedOk, code) && owns(unusedOk[code], name))) problems.push(`${code}: параметр ${name} каталога не использован в английском шаблоне`)
    }
  }
  for (const [code, params] of Object.entries(unusedOk)) {
    for (const [name, why] of Object.entries(params)) {
      if (typeof why !== 'string' || why.trim() === '') problems.push(`${code}: у исключения для ${name} нет причины`)
      if (!owns(catalog, code) || !catalog[code].includes(name)) problems.push(`${code}: исключение для ${name} устарело: такого параметра нет в каталоге`)
      else if (owns(templates, code) && slots(templates[code]).has(name)) problems.push(`${code}: исключение для ${name} устарело: параметр уже в шаблоне`)
    }
  }
  for (const [code, params] of Object.entries(words)) {
    for (const [name, kinds] of Object.entries(params)) {
      if (owns(templates, code) && !slots(templates[code]).has(name)) problems.push(`${code}: параметр вида ${name} не подставлен в английский шаблон`)
      for (const kind of Object.keys(kinds)) {
        const word = owns(englishWords, code) && owns(englishWords[code], name) && owns(englishWords[code][name], kind) ? englishWords[code][name][kind] : undefined
        if (typeof word !== 'string' || word.trim() === '') problems.push(`${code}: нет английского слова для вида ${kind} параметра ${name}`)
      }
    }
  }
  for (const [code, params] of Object.entries(englishWords)) {
    for (const [name, kinds] of Object.entries(params)) {
      for (const kind of Object.keys(kinds)) {
        if (!(owns(words, code) && owns(words[code], name) && owns(words[code][name], kind)))
          problems.push(`${code}: английское слово для вида ${kind} параметра ${name}, которого нет в словах сервера`)
      }
    }
  }
  return problems
}

const serverCatalog = (flag) => {
  const script = fileURLToPath(new URL('../../tools/messages.py', import.meta.url))
  return JSON.parse(execFileSync(process.env.PYTHON || 'python3', [script, flag], { encoding: 'utf8' }))
}

test('словарь плагина сверен с каталогом сервера: те же коды, те же параметры, слова для каждого вида', async () => {
  const m = await messages()
  const catalog = serverCatalog('--json')
  const words = serverCatalog('--words')
  assert.ok(Object.keys(catalog).length >= 150, `кодов получено ${Object.keys(catalog).length}: каталог сервера не прочитан`)
  assert.ok(Object.keys(words).length >= 3, 'слова видов сервера не прочитаны')
  const problems = drift({ catalog, words, templates: m.TEMPLATES, englishWords: m.WORDS, unusedOk: UNUSED_OK })
  assert.deepEqual(problems, [], `словарь client.messages.js расходится с tools/messages.py:\n${problems.join('\n')}`)
})

test('каждый код каталога по образцу параметров показывается английской строкой со всеми параметрами, а не text сервера', async () => {
  const { format } = await messages()
  const catalog = serverCatalog('--json')
  const ru = 'РУССКИЙ ТЕКСТ СЕРВЕРА'
  for (const [code, params] of Object.entries(catalog)) {
    // значения образца — латиницей и узнаваемые: по ним видно, что параметр дошёл до экрана
    const value = (name) => (SERVICE.has(name) ? true : `sample_${name}`)
    const line = format({ code, args: Object.fromEntries(params.map((name) => [name, value(name)])), text: ru })
    assert.ok(line !== '' && line !== ru && !CYRILLIC.test(line) && !/[{}]/.test(line), `${code}: ${line}`)
    const excused = (name) => owns(UNUSED_OK, code) && owns(UNUSED_OK[code], name) // параметр, которого в английском шаблоне нет по причине из сверки
    for (const name of params.filter((p) => !SERVICE.has(p) && !excused(p)))
      assert.ok(line.includes(String(value(name))), `${code}: параметр ${name} не попал на экран: ${line}`)
  }
})

test('у каждого вида из слов сервера есть английское слово, и оно показывается вместо кода вида', async () => {
  const { format, WORDS } = await messages()
  const words = serverCatalog('--words')
  const catalog = serverCatalog('--json')
  let checked = 0
  for (const [code, params] of Object.entries(words)) {
    for (const [name, kinds] of Object.entries(params)) {
      for (const kind of Object.keys(kinds)) {
        const word = WORDS[code][name][kind]
        assert.ok(typeof word === 'string' && word !== '' && !CYRILLIC.test(word) && word !== kind, `${code}.${name}.${kind}`)
        // остальные параметры кода — образцом: без них сообщение неполное, и на экране остался бы русский text сервера
        const rest = Object.fromEntries(catalog[code].filter((p) => p !== name && !SERVICE.has(p)).map((p) => [p, `sample_${p}`]))
        const args = { ...rest, [name]: kind, quoted: true, ext: 'x' }
        assert.ok(format({ code, args, text: 'РУССКИЙ ТЕКСТ' }).includes(word), `${code}: слово вида ${kind} не показано`)
        checked++
      }
    }
  }
  assert.ok(checked >= 20, `видов проверено ${checked}`)
})

test('сверка красная и называет код: нет шаблона, лишний код, чужой параметр, неиспользованный параметр, нет слова вида', () => {
  const base = {
    catalog: { 'a.one': ['x'], 'a.two': ['y', 'quoted'], 'a.kind': ['kind'] },
    words: { 'a.kind': { kind: { pe: 'программа Windows', elf: 'программа Linux' } } },
    templates: { 'a.one': 'One {x}', 'a.two': 'Two {y}', 'a.kind': 'Kind {kind}' },
    englishWords: { 'a.kind': { kind: { pe: 'Windows program', elf: 'Linux program' } } },
    unusedOk: {},
  }
  assert.deepEqual(drift(base), []) // исходные данные сходятся: красным станет только испорченное
  const broken = (change) => drift({ ...base, ...change })

  assert.deepEqual(broken({ templates: { 'a.one': 'One {x}', 'a.kind': 'Kind {kind}' } }), ['a.two: в словаре нет английского шаблона'])
  assert.deepEqual(broken({ templates: { ...base.templates, 'a.extra': 'Extra' } }), ['a.extra: код есть в словаре, но нет в каталоге сервера'])
  assert.deepEqual(broken({ templates: { ...base.templates, 'a.one': 'One {z}' } }), [
    'a.one: параметр {z} шаблона не объявлен в каталоге', 'a.one: параметр x каталога не использован в английском шаблоне'])
  assert.deepEqual(broken({ templates: { ...base.templates, 'a.two': 'Two {y} and {x}' } }), ['a.two: параметр {x} шаблона не объявлен в каталоге'])
  assert.deepEqual(broken({ templates: { ...base.templates, 'a.one': 'One' } }), ['a.one: параметр x каталога не использован в английском шаблоне'])
  assert.deepEqual(broken({ englishWords: { 'a.kind': { kind: { pe: 'Windows program' } } } }), ['a.kind: нет английского слова для вида elf параметра kind'])
  assert.deepEqual(broken({ englishWords: { 'a.kind': { kind: { pe: 'Windows program', elf: ' ' } } } }), ['a.kind: нет английского слова для вида elf параметра kind'])
  assert.deepEqual(broken({ englishWords: {} }), [
    'a.kind: нет английского слова для вида pe параметра kind', 'a.kind: нет английского слова для вида elf параметра kind'])
  assert.deepEqual(broken({ englishWords: { 'a.kind': { kind: { pe: 'W', elf: 'L', mach: 'M' } } } }),
    ['a.kind: английское слово для вида mach параметра kind, которого нет в словах сервера'])
  assert.deepEqual(broken({ words: { 'a.kind': { kind: { pe: 'п', elf: 'л' } } }, templates: { ...base.templates, 'a.kind': 'Kind' } }), [
    'a.kind: параметр kind каталога не использован в английском шаблоне', 'a.kind: параметр вида kind не подставлен в английский шаблон'])
})

test('служебные параметры: у любого кода каталога допустимы без места в шаблоне, а подставлять их в шаблон нельзя', () => {
  const base = { catalog: { 'a.q': ['quoted', 'excerpt'], 'a.p': ['n', 'excerpt'] }, words: {}, englishWords: {}, unusedOk: {},
    templates: { 'a.q': 'Quoted text', 'a.p': 'Count {n}' } }
  assert.deepEqual(drift(base), [])
  assert.deepEqual(drift({ ...base, templates: { ...base.templates, 'a.q': 'Quoted {quoted}' } }), ['a.q: служебный параметр {quoted} подставлен в шаблон'])
  // служебный параметр, которого каталог не знает (excerpt появится позже), в шаблоне не нужен и каталог не краснит
  assert.deepEqual(drift({ ...base, catalog: { 'a.q': ['quoted'], 'a.p': ['n'] } }), [])
})

test('исключения для неиспользованных параметров: действуют только с причиной и пока параметр правда не используется', () => {
  const base = { catalog: { 'a.one': ['x', 'y'] }, words: {}, englishWords: {}, templates: { 'a.one': 'One {x}' } }
  assert.deepEqual(drift({ ...base, unusedOk: {} }), ['a.one: параметр y каталога не использован в английском шаблоне'])
  assert.deepEqual(drift({ ...base, unusedOk: { 'a.one': { y: 'shown by another message' } } }), [])
  assert.deepEqual(drift({ ...base, unusedOk: { 'a.one': { y: '' } } }), ['a.one: у исключения для y нет причины'])
  assert.deepEqual(drift({ ...base, unusedOk: { 'a.one': { y: 'why', x: 'why' } } }), ['a.one: исключение для x устарело: параметр уже в шаблоне'])
  assert.deepEqual(drift({ ...base, unusedOk: { 'a.one': { y: 'why', w: 'why' } } }), ['a.one: исключение для w устарело: такого параметра нет в каталоге'])
  assert.deepEqual(drift({ ...base, unusedOk: { 'a.gone': { y: 'why' } }, templates: { 'a.one': 'One {x} {y}' } }), [
    'a.gone: исключение для y устарело: такого параметра нет в каталоге'])
})

test('список исключений сверки короток и назван поимённо, у каждого исключения есть причина', () => {
  const entries = Object.entries(UNUSED_OK).flatMap(([code, params]) => Object.entries(params).map(([name, why]) => ({ code, name, why })))
  assert.ok(entries.length <= 5, 'исключений должно быть мало: каждое — повод подумать, а не обойти сверку')
  assert.deepEqual(entries.map(({ code, name }) => `${code}.${name}`), ['llm_finding.page']) // новое исключение — осознанная правка этой строки
  for (const { code, name, why } of entries) assert.ok(typeof why === 'string' && why.trim().length >= 10, `${code}.${name}`)
})

test('сообщение с кодом, которого нет в словаре, показывается своим text', async () => {
  const { format, TEMPLATES } = await messages()
  assert.ok(!owns(TEMPLATES, 'finding.from_the_future'))
  assert.equal(format({ code: 'finding.from_the_future', args: { kind: 'pe' }, text: 'Новая находка: подробности' }), 'Новая находка: подробности')
})

// ── архив заводится с пустого каталога (FR-103) ─────
test('таблицы индекса нет: отказ и замечание пачки по-английски называют причину и команду flyarchive init', async () => {
  const { format } = await messages()
  const said = (code, args = {}) => format({ code, args, text: 'РУССКИЙ ТЕКСТ' })
  assert.equal(said('index.no_table'), 'The index table does not exist: run flyarchive init')
  assert.equal(
    said('problem.index_no_table', { rel: 'inbox/20261006-100000/a.docx' }),
    'Index: inbox/20261006-100000/a.docx is in the corpus, but the index table does not exist; the document stays in the indexing backlog. Run: flyarchive init'
  )
  assert.match(said('known.not_built'), /Run: flyarchive known build \(for a new archive: flyarchive init\)$/)
})

test('шаги и остаток команды init по-английски: путь, размерность, что осталось сделать', async () => {
  const { format } = await messages()
  const said = (code, args = {}) => format({ code, args, text: 'РУССКИЙ ТЕКСТ' })
  assert.equal(said('init.dir_created', { path: '/a/corpus' }), 'Created the directory /a/corpus')
  assert.equal(said('init.table_created', { path: '/a/index/lance', dim: 1024 }), 'The index table docs was created: /a/index/lance (vector size 1024); the full-text index on the text was built')
  assert.equal(said('init.table_exists', { path: '/a/index/lance' }), 'The index table docs already exists and was not touched: /a/index/lance')
  assert.equal(said('init.todo_inbox'), 'Choose the inbox folder: flyarchive inbox set --path FOLDER')
  assert.equal(said('init.step_failed', { what: 'table', why: 'ImportError: no library' }), 'The step "table" failed: ImportError: no library')
})
