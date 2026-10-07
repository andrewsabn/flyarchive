// Серверная половина раздела «Архив»: настройка описания изображений локальной моделью (FR-94, FR-92).
// Ключи vision, vision_pages и vision_minutes превращаются в --vision on|off, --vision-pages N и --vision-minutes N команды inbox set.
import assert from 'node:assert/strict'
import test from 'node:test'

import { INBOX_PATH, createApi, routes } from '../lib/index.js'

const CMD = '/opt/flyarchive/tools/flyarchive'
const ok = (data) => ({ code: 0, stdout: typeof data === 'string' ? data : JSON.stringify(data), stderr: '' })
const STATUS = { inbox: '/mnt/c/Users/x/flyarchive-inbox', period: 30, llm: true, cloud: false, threshold: 20, max_gb: 2, max_files: 5000,
  max_ratio: 100, depth: 3, timer: true, waiting: 2, queue: 1, quarantine: 0, pending: 0, last_batch: null,
  vision: true, vision_pages: 60, vision_minutes: 10, vision_pending: 4, vision_done: 9 }

function cli(script = () => ok('…')) {
  const calls = []
  const run = async (command, args) => {
    calls.push(args)
    return script(args)
  }
  const table = Object.fromEntries(routes(createApi({ command: CMD, run })).map((r) => [r.path, r]))
  const post = async (body) => {
    const response = await table[INBOX_PATH].fetch(new Request(`http://127.0.0.1:3080${INBOX_PATH}`, { method: 'POST', body: JSON.stringify(body) }))
    return { status: response.status, data: await response.json() }
  }
  return { calls, post }
}
const answer = (args) => (args[1] === 'status' ? ok(STATUS) : ok('…'))

test('описание изображений: три ключа уходят командой inbox set, в ответ — новое состояние с долгом описания', async () => {
  const { calls, post } = cli(answer)
  const r = await post({ vision: true, vision_pages: 5, vision_minutes: 2 })
  assert.equal(r.status, 200)
  assert.deepEqual(calls, [
    ['inbox', 'set', '--vision', 'on', '--vision-pages', '5', '--vision-minutes', '2'],
    ['inbox', 'status', '--json']])
  assert.equal(r.data.vision_pending, 4)
  assert.equal(r.data.vision_pages, 60)
})

test('описание изображений: булево превращается в on и off так же, как у проверки моделью', async () => {
  for (const [value, word] of [[true, 'on'], [false, 'off']]) {
    const { calls, post } = cli(answer)
    assert.equal((await post({ vision: value })).status, 200, String(value))
    assert.deepEqual(calls[0], ['inbox', 'set', '--vision', word], String(value))
  }
})

test('описание изображений: меняется только названное, соседние ключи идут вместе с ним', async () => {
  const one = cli(answer)
  assert.equal((await one.post({ vision_pages: 100 })).status, 200)
  assert.deepEqual(one.calls[0], ['inbox', 'set', '--vision-pages', '100'])
  const two = cli(answer)
  assert.equal((await two.post({ vision_minutes: 30 })).status, 200)
  assert.deepEqual(two.calls[0], ['inbox', 'set', '--vision-minutes', '30'])
  const mixed = cli(answer)
  assert.equal((await mixed.post({ llm: false, vision: true, depth: 2 })).status, 200)
  assert.deepEqual(mixed.calls[0], ['inbox', 'set', '--llm', 'off', '--depth', '2', '--vision', 'on'])
})

// границы: каждый ключ проверяется парой — годные значения проходят (так тест красен, пока ключа нет), негодные дают 400 с названием ключа
const LIMITS = [
  ['vision_pages', '--vision-pages', [1, 2, 999, 1000], [0, 1001, -1, 1e9]],
  ['vision_minutes', '--vision-minutes', [1, 2, 239, 240], [0, 241, -1, 1e9]],
]
for (const [key, flag, good, bad] of LIMITS) {
  test(`${key}: целые от ${good[0]} до ${good.at(-1)} проходят, а ${bad.join(', ')} — 400 до вызова команды`, async () => {
    for (const value of good) {
      const { calls, post } = cli(answer)
      const r = await post({ [key]: value })
      assert.equal(r.status, 200, `${key}=${value}`)
      assert.deepEqual(calls[0], ['inbox', 'set', flag, String(value)], `${key}=${value}`)
    }
    for (const value of bad) {
      const { calls, post } = cli(answer)
      const r = await post({ [key]: value })
      assert.equal(r.status, 400, `${key}=${value}`)
      assert.match(r.data.error, new RegExp(`invalid value for ${key}`), `${key}=${value}`) // отказ по значению, а не «такой настройки нет»
      assert.deepEqual(calls, [], `${key}=${value}`)
    }
  })

  test(`${key}: не целое — дробь, строка, null, булево, список, объект — 400 до вызова команды`, async () => {
    const control = cli(answer)
    assert.equal((await control.post({ [key]: good[1] })).status, 200) // ключ принят, значит отказы ниже — по значению
    for (const value of [1.5, 2.0000001, '5', '', 'пять', null, true, false, [5], { n: 5 }]) {
      const { calls, post } = cli(answer)
      const r = await post({ [key]: value })
      assert.equal(r.status, 400, `${key}=${JSON.stringify(value)}`)
      assert.match(r.data.error, new RegExp(`invalid value for ${key}`), `${key}=${JSON.stringify(value)}`)
      assert.deepEqual(calls, [], `${key}=${JSON.stringify(value)}`)
    }
  })
}

test('пустое число (null — так JSON передаёт NaN) в любом числовом ключе — 400 до вызова команды: сервер остаётся последним заслоном, хотя браузер такое не шлёт', async () => {
  for (const key of ['threshold', 'max_gb', 'max_files', 'max_ratio', 'depth', 'vision_pages', 'vision_minutes']) {
    const { calls, post } = cli(answer)
    const r = await post({ [key]: null })
    assert.equal(r.status, 400, key)
    assert.match(r.data.error, new RegExp(`invalid value for ${key}`), key)
    assert.deepEqual(calls, [], key)
  }
})

test('vision: только true и false; строка, число, null, список — 400 до вызова команды', async () => {
  for (const value of [true, false]) assert.equal((await cli(answer).post({ vision: value })).status, 200, String(value))
  for (const value of ['on', 'off', 'true', 'да', 1, 0, null, [], [true], {}]) {
    const { calls, post } = cli(answer)
    const r = await post({ vision: value })
    assert.equal(r.status, 400, JSON.stringify(value))
    assert.match(r.data.error, /invalid value for vision/, JSON.stringify(value))
    assert.deepEqual(calls, [], JSON.stringify(value))
  }
})

test('негодное значение vision-ключа в смешанном запросе отклоняет весь запрос: команда не вызывается ни для годных соседей', async () => {
  const control = cli(answer)
  assert.equal((await control.post({ period: 5, vision: true, vision_pages: 1000 })).status, 200)
  for (const body of [{ period: 5, vision: true, vision_pages: 1001 }, { vision: 'on', threshold: 30 }, { vision_minutes: 0, llm: true }]) {
    const { calls, post } = cli(answer)
    const r = await post(body)
    assert.equal(r.status, 400, JSON.stringify(body))
    assert.match(r.data.error, /invalid value for vision/, JSON.stringify(body))
    assert.deepEqual(calls, [], JSON.stringify(body))
  }
})

test('описание изображений вместе со сменой папки одним запросом не принимается: смена папки — отдельный запрос', async () => {
  const control = cli(answer)
  assert.equal((await control.post({ vision: true })).status, 200)
  const { calls, post } = cli(answer)
  const r = await post({ path: '/tmp/x', confirm: true, vision: true })
  assert.equal(r.status, 400)
  assert.deepEqual(calls, [])
})

test('отказ команды на настройку описания доходит объектом-сообщением', async () => {
  const text = 'Image description needs a number of pages from 1 to 1000'
  const { post } = cli(() => ({ code: 1, stdout: '', stderr: `${JSON.stringify({ error: { code: 'cli.bad_value', args: {}, text } })}\n` }))
  const r = await post({ vision_pages: 5 })
  assert.equal(r.status, 409)
  assert.deepEqual(r.data.error, { code: 'cli.bad_value', args: {}, text })
})
