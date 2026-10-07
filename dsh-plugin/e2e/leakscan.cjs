'use strict'
/*
 * Поиск секретов в каталоге: значения токенов клиентов (ba_ и 43 знака), ссылка входа DSH (адрес с ?token=…) и точные значения, которые знает вызывающий.
 * Ничего не печатает из найденного: возвращает только имена файлов и вид находки. Используется в тесте набора и в run.sh после прогона
 * (node leakscan.cjs <каталог>…; код возврата 1, если что-то найдено).
 */
const fs = require('node:fs')
const path = require('node:path')

// токен клиента: ba_ и ровно 43 знака base64url (secrets.token_urlsafe(32)); пометки вида ba_*** сюда не попадают
const CLIENT_TOKEN = /(?<![A-Za-z0-9_])ba_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])/
// ссылка входа DSH: адрес петли со знаком ?token= и значением
const SIGN_IN_LINK = /127\.0\.0\.1:\d+\/?\?token=[A-Za-z0-9_-]{8,}/

function* walk(dir) {
  let entries
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) yield* walk(full)
    else if (entry.isFile()) yield full
  }
}

/** Находки в каталоге: [{file, kind}]. exact — значения, которые надо искать дословно (в том числе в закодированном виде). */
function scan(dir, exact = []) {
  const found = []
  const needles = exact.filter((value) => typeof value === 'string' && value.length >= 8)
  for (const file of walk(dir)) {
    let text
    try {
      text = fs.readFileSync(file, 'latin1')
    } catch {
      continue
    }
    const utf = Buffer.from(text, 'latin1').toString('utf8')
    const rel = path.relative(dir, file)
    if (CLIENT_TOKEN.test(text) || CLIENT_TOKEN.test(utf)) found.push({ file: rel, kind: 'a client token' })
    if (SIGN_IN_LINK.test(text) || SIGN_IN_LINK.test(utf)) found.push({ file: rel, kind: 'a sign-in link' })
    for (const value of needles) {
      if (text.includes(value) || utf.includes(value) || text.includes(encodeURIComponent(value)) ||
        text.includes(Buffer.from(value).toString('base64'))) found.push({ file: rel, kind: 'a known secret value' })
    }
  }
  return found
}

module.exports = { scan, CLIENT_TOKEN, SIGN_IN_LINK }

if (require.main === module) {
  const dirs = process.argv.slice(2)
  let bad = 0
  for (const dir of dirs) {
    for (const hit of scan(dir)) {
      bad += 1
      process.stdout.write(`secret scan: ${hit.kind} in ${path.join(dir, hit.file)}\n`)
    }
  }
  process.stdout.write(bad === 0 ? `secret scan: clean (${dirs.length} director${dirs.length === 1 ? 'y' : 'ies'})\n` : `secret scan: ${bad} finding(s)\n`)
  process.exit(bad === 0 ? 0 : 1)
}
