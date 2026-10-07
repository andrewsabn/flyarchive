// Запасной путь команды в плагине (FR-50). Запуск: node --test dsh-plugin/test
// Путь к команде — настройка плагина (строку command пишет установка), иначе переменная окружения FLYARCHIVE_CLI, иначе имя `flyarchive` из пути
// поиска команд (PATH): раньше запасным был путь внутри каталога архива, где кода давно нет. Запуск по-прежнему через execFile, без оболочки.
// Не нашлась команда — прежнее сообщение плагина о том, что команда не запустилась, и подсказка, какой настройкой задать путь.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test, { after } from 'node:test'

import { TOKENS_PATH, createApi, defaultCommand, routes, runCli } from '../lib/index.js'

const CYRILLIC = /[Ѐ-ӿ]/
const POSIX = process.platform !== 'win32'
const made = []
after(() => made.forEach((folder) => fs.rmSync(folder, { recursive: true, force: true })))

/** Каталог с исполняемой заглушкой flyarchive; PATH на время вызова указывает только на него (без оболочки: execFile ищет по PATH сам). */
async function withPath(folder, body) {
  const before = process.env.PATH
  process.env.PATH = folder
  try {
    return await body()
  } finally {
    process.env.PATH = before
  }
}

function stub(script) {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'flyarchive-command-'))
  made.push(folder)
  fs.writeFileSync(path.join(folder, 'flyarchive'), script, { mode: 0o755 })
  return folder
}

const tokensRoute = (command) => routes(createApi({ command })).find((r) => r.path === TOKENS_PATH)
const get = (route) => route.fetch(new Request(`http://127.0.0.1:3080${TOKENS_PATH}`))

test('запасное значение — имя команды, а не путь внутри каталога архива', () => {
  assert.equal(defaultCommand({}, {}), 'flyarchive')
  assert.ok(!path.isAbsolute(defaultCommand({}, {})) && !defaultCommand({}, {}).includes(path.sep))
  assert.ok(!defaultCommand({}, {}).startsWith(os.homedir()), 'каталог архива в запасное значение не входит')
})

test('настройка плагина и переменная окружения главнее запасного имени', () => {
  assert.equal(defaultCommand({ command: '/x/flyarchive' }, { FLYARCHIVE_CLI: '/y/flyarchive' }), '/x/flyarchive')
  assert.equal(defaultCommand({}, { FLYARCHIVE_CLI: '/y/flyarchive' }), '/y/flyarchive')
  assert.equal(defaultCommand({ command: '' }, { FLYARCHIVE_CLI: '' }), 'flyarchive', 'пустое значение — то же, что не задано')
})

test('apply без настройки берёт запасное имя: команда запускается по PATH без оболочки', { skip: !POSIX }, async () => {
  const folder = stub('#!/bin/sh\nprintf \'%s\\n\' "[]"\n')
  const result = await withPath(folder, () => runCli(defaultCommand({}, {}), ['token', 'list', '--json']))
  assert.deepEqual(result, { code: 0, stdout: '[]\n', stderr: '' })
})

test('имя из PATH запускается как программа: метасимволы оболочки в аргументах остаются данными', { skip: !POSIX }, async () => {
  const folder = stub('#!/bin/sh\nfor a in "$@"; do printf \'%s|\' "$a"; done\n')
  const marker = path.join(folder, 'создано-оболочкой')
  const result = await withPath(folder, () => runCli('flyarchive', ['journal', `; touch ${marker}`, '$(id)']))
  assert.equal(result.stdout, `journal|; touch ${marker}|$(id)|`)
  assert.ok(!fs.existsSync(marker), 'оболочки нет: ничего не выполнилось')
})

test('команды нет в PATH — 502, сообщение плагина по-английски называет команду и настройку, которой задать путь', { skip: !POSIX }, async () => {
  const empty = fs.mkdtempSync(path.join(os.tmpdir(), 'flyarchive-command-'))
  made.push(empty)
  const response = await withPath(empty, () => get(tokensRoute(defaultCommand({}, {}))))
  assert.equal(response.status, 502)
  const { error } = await response.json()
  assert.match(error, /^The flyarchive command could not be started \(flyarchive\): ENOENT/)
  assert.match(error, /"command" setting/)
  assert.match(error, /FLYARCHIVE_CLI/)
  assert.ok(!CYRILLIC.test(error), error)
  assert.ok(!error.includes(os.homedir()), 'домашний каталог в сообщение не попадает')
})

test('подсказка есть и у заданного, но неверного пути', async () => {
  const dead = async () => { throw Object.assign(new Error('spawn /nowhere/flyarchive ENOENT'), { code: 'ENOENT' }) }
  const route = routes(createApi({ command: '/nowhere/flyarchive', run: dead })).find((r) => r.path === TOKENS_PATH)
  const { error } = await (await get(route)).json()
  assert.match(error, /^The flyarchive command could not be started \(\/nowhere\/flyarchive\): ENOENT/)
  assert.match(error, /"command" setting/)
})

test('другой сбой запуска — прежнее сообщение без подсказки до знака', async () => {
  const denied = async () => { throw Object.assign(new Error('spawn EACCES'), { code: 'EACCES' }) }
  const route = routes(createApi({ command: '/opt/flyarchive/tools/flyarchive', run: denied })).find((r) => r.path === TOKENS_PATH)
  const response = await get(route)
  assert.equal(response.status, 502)
  assert.equal((await response.json()).error, 'The flyarchive command could not be started (/opt/flyarchive/tools/flyarchive): EACCES')
})
