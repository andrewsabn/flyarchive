'use strict'
/*
 * Помощники сквозных проверок: клиент стенда (команда flyarchive, MCP, файлы, хранилище токенов) и приёмы работы с интерфейсом DSH.
 * Стенд поднимает global-setup.cjs; тесты находят его по переменной окружения BA_E2E_STAND (каталог стенда).
 * Правило набора: значения токенов и ссылка входа нигде не печатаются; проверки на утечку строятся на булевых значениях,
 * чтобы сообщение о провале не повторило искомое.
 */
const cp = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const E2E = __dirname
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const TOKEN_LIKE = /ba_[A-Za-z0-9_-]{8,}/g
const LINK_LIKE = /token=[A-Za-z0-9_-]+/g

/** Текст без токенов и ссылок входа: так его можно положить в сообщение теста. */
const scrub = (text) => String(text).replace(TOKEN_LIKE, 'ba_***').replace(LINK_LIKE, 'token=***')

/** Метка для имён и текста файлов: латинские буквы и цифры, одна на тест. */
const nonce = () => 'q' + crypto.randomBytes(5).toString('hex')

/** Имя клиента токена: одно на тест, чтобы токены разных тестов не пересекались. */
const clientName = (prefix) => `${prefix}-${crypto.randomBytes(3).toString('hex')}`

class Stand {
  constructor(info) {
    this.info = info
    this.dir = info.dir
    this.archive = info.archive
    this.tools = info.tools
    this.inbox = info.inbox
    this.ports = info.ports
    this.dshUrl = info.dshUrl
    this.mcpUrl = info.mcpUrl
    this.userSite = cp.execFileSync('python3', ['-c', 'import site; print(site.getusersitepackages())'], { encoding: 'utf8' }).trim()
  }

  /** Окружение команд стенда: то же, что у DSH и служб. */
  get env() {
    return {
      PATH: `${path.join(this.dir, 'bin')}${path.delimiter}${process.env.PATH}`,
      HOME: this.info.home, USER: os.userInfo().username, LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8', TMPDIR: path.join(this.dir, 'tmp'),
      DSH_HOME: path.join(this.dir, 'dsh-home'), FLYARCHIVE_HOME: this.archive, FLYARCHIVE_CLI: path.join(this.tools, 'flyarchive'),
      PYTHONPATH: this.userSite, PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1',
      FLYARCHIVE_EMBED_URL: `http://127.0.0.1:${this.ports.embed}/api/embed`,
      FLYARCHIVE_LLM_LOCAL_URL: 'http://127.0.0.1:9', FLYARCHIVE_LLM_CLOUD_URL: 'http://127.0.0.1:9',
      BA_E2E_DIR: this.dir, BA_E2E_TOOLS: this.tools,
    }
  }

  /** Команда flyarchive стенда. Возвращает {code, out, err}; сообщения очищены от токенов. */
  cli(args, input) {
    const result = cp.spawnSync('python3', [path.join(this.tools, 'flyarchive'), ...args], {
      env: this.env, encoding: 'utf8', input, cwd: this.dir, timeout: 180000,
    })
    return { code: result.status, out: result.stdout || '', err: scrub(result.stderr || '') }
  }

  /** То же с разбором JSON из stdout; ненулевой код — исключение с очищенным текстом. */
  cliJson(args) {
    const result = this.cli(args)
    if (result.code !== 0) throw new Error(`flyarchive ${args.slice(0, 2).join(' ')} failed (${result.code}): ${result.err.slice(-300)}`)
    return JSON.parse(result.out)
  }

  /** Python стенда: исполняет код в окружении стенда; stdout — результат. */
  python(code, args = []) {
    const result = cp.spawnSync('python3', ['-c', code, ...args], {
      env: { ...this.env, PYTHONPATH: `${this.tools}${path.delimiter}${this.userSite}` }, encoding: 'utf8', cwd: this.dir, timeout: 60000,
    })
    if (result.status !== 0) throw new Error('python failed: ' + scrub(result.stderr).slice(-400))
    return result.stdout
  }

  status() {
    return this.cliJson(['inbox', 'status', '--json'])
  }

  /** Токен клиента по команде (для тестов, где выпуск токена не предмет проверки). Значение запоминается для проверки на утечку. */
  issue(name, level = 'read', days = null) {
    const answer = this.cliJson(['token', 'add', name, '--level', level, '--json', ...(days === null ? [] : ['--days', String(days)])])
    this.remember(answer.token)
    return answer.token
  }

  tokens() {
    return this.cliJson(['token', 'list', '--json'])
  }

  tokenRow(name) {
    return this.tokens().filter((row) => row.name === name).pop() || null
  }

  /** Записи журнала обращений (команда journal), по желанию одного клиента. */
  journal(n = 200, client = null) {
    return this.cliJson(['journal', '--json', '-n', String(n), ...(client === null ? [] : ['--client', client])])
  }

  /** Значение токена — в закрытый файл стенда: в конце набора по нему ищут утечки в каталоге отчёта. В вывод оно не попадает. */
  remember(value) {
    fs.appendFileSync(path.join(this.dir, 'secrets', 'seen.txt'), value + '\n', { mode: 0o600 })
  }

  seen() {
    try {
      return fs.readFileSync(path.join(this.dir, 'secrets', 'seen.txt'), 'utf8').split('\n').filter(Boolean)
    } catch {
      return []
    }
  }

  /** Ссылка входа DSH: только из файла стенда, и только для входа. */
  link() {
    return fs.readFileSync(path.join(this.dir, 'secrets', 'dsh-url.txt'), 'utf8').trim()
  }

  statePath() {
    return path.join(this.dir, 'secrets', 'browser-state.json')
  }

  /** Запрос MCP. Возвращает {status, body}: тело — разобранный JSON или null. */
  async mcp(token, method, params = {}, options = {}) {
    const headers = { 'content-type': 'application/json' }
    if (token) headers.authorization = 'Bearer ' + token
    const response = await fetch(this.mcpUrl, {
      method: 'POST', headers, body: JSON.stringify({ jsonrpc: '2.0', id: options.id ?? 1, method, params }),
    })
    const text = await response.text()
    let body = null
    try {
      body = JSON.parse(text)
    } catch {
      // не JSON
    }
    return { status: response.status, body }
  }

  async toolNames(token) {
    const answer = await this.mcp(token, 'tools/list')
    return { status: answer.status, names: answer.body && answer.body.result ? answer.body.result.tools.map((tool) => tool.name) : [] }
  }

  /** Поиск по MCP: список находок {path, ...}. */
  async search(token, query, k = 8) {
    const answer = await this.mcp(token, 'tools/call', { name: 'search_archive', arguments: { q: query, k } })
    if (answer.status !== 200 || !answer.body || !answer.body.result) throw new Error(`search_archive answered ${answer.status}`)
    const text = answer.body.result.content[0].text
    if (answer.body.result.isError) throw new Error('search_archive refused: ' + scrub(text).slice(0, 200))
    return JSON.parse(text).results
  }

  /** Кладёт выдуманный файл во входящую папку (вид и метка — как у fixtures.py). Возвращает имена файлов. */
  drop(kind, tag, count = 1, folder = this.inbox) {
    const result = cp.spawnSync('python3', [path.join(E2E, 'fixtures.py'), kind, folder, tag, String(count)], {
      env: { ...this.env, PYTHONPATH: this.userSite }, encoding: 'utf8',
    })
    if (result.status !== 0) throw new Error('fixtures.py failed: ' + result.stderr.slice(-300))
    return result.stdout.split('\n').filter(Boolean)
  }

  /** Идёт ли в стенде разбор входящих (команда inbox run, запущенная кнопкой, подставным systemctl или тестом): ищется среди процессов стенда. */
  running() {
    const want = `BA_E2E_DIR=${this.dir}`
    for (const name of fs.readdirSync('/proc')) {
      if (!/^[0-9]+$/.test(name)) continue
      try {
        const cmd = fs.readFileSync(`/proc/${name}/cmdline`, 'latin1').split('\0')
        const at = cmd.findIndex((part) => part.endsWith('/flyarchive'))
        if (at < 0 || cmd[at + 1] !== 'inbox' || cmd[at + 2] !== 'run') continue
        if (fs.readFileSync(`/proc/${name}/environ`, 'latin1').split('\0').includes(want)) return true
      } catch {
        // процесс ушёл
      }
    }
    return false
  }

  /** Ждёт, пока в стенде не кончится разбор: тест не должен оставлять фоновый проход соседнему. */
  async idle(timeoutMs = 120000) {
    const until = Date.now() + timeoutMs
    while (this.running()) {
      if (Date.now() > until) throw new Error('the intake run in the stand did not finish')
      await sleep(300)
    }
  }

  /** Разбор входящей папки командой (то же, что кнопка, но без браузера): для подготовки. Ждёт конца; идущий разбор не перебивает. */
  async runInbox() {
    await this.idle()
    const result = this.cli(['inbox', 'run'])
    if (result.code !== 0) throw new Error('inbox run failed: ' + result.err.slice(-300))
    if (/уже идёт/.test(result.out)) throw new Error('the intake run was skipped: another run holds the lock')
  }

  queueItems() {
    return this.cliJson(['queue', 'list', '--json'])
  }

  quarantineItems() {
    return this.cliJson(['quarantine', 'list', '--json'])
  }

  /** Убирает за тестом всё, что осталось у решения владельца: файлы с меткой из очереди (в карантин) и из карантина (стереть). */
  sweep(tag) {
    for (const item of this.queueItems()) if (item.name.includes(tag)) this.cli(['queue', 'quarantine', '--json', '--', item.path])
    for (const item of this.quarantineItems()) if (item.name.includes(tag)) this.cli(['quarantine', 'delete', '--json', '--', item.path])
  }

  /** Ждёт, пока условие по результатам поиска не выполнится (индексация идёт в фоне). Возвращает последние находки. */
  async waitSearch(token, query, predicate, timeoutMs = 60000) {
    const until = Date.now() + timeoutMs
    let found = []
    while (Date.now() < until) {
      found = await this.search(token, query)
      if (predicate(found)) return found
      await sleep(1000)
    }
    return found
  }

  /** Задержка службы векторов (мс): ею тест растягивает индексацию, чтобы увидеть ход разбора. 0 — без задержки. */
  setEmbedDelay(ms) {
    const file = path.join(this.dir, 'control', 'embed-delay-ms')
    if (ms > 0) fs.writeFileSync(file, String(ms))
    else fs.rmSync(file, { force: true })
  }

  /** Сдвигает срок токена в прошлое в хранилище стенда (secrets/tokens.json) — средствами самого хранилища, под его замком. */
  expire(name) {
    this.python(`
import os, sys, time
import tokens
store = tokens.Store(os.path.join(os.environ["FLYARCHIVE_HOME"], "secrets", "tokens.json"))
past = time.time() - 3600
with store._locked() as rows:
    live = [r for r in rows if r["name"] == sys.argv[1] and not r["revoked"]]
    assert live, "нет действующего токена"
    live[-1]["expires_at"], live[-1]["expires"] = past, tokens.stamp(past)
    store._write(rows)
`, [name])
  }

  /** Каталоги, которые тест сам создал в стенде и должен убрать: удаляются в конце. */
  scratch(name) {
    const folder = path.join(this.dir, 'scratch', name)
    fs.mkdirSync(folder, { recursive: true })
    return folder
  }

  /** Проверка перед первым действием: адрес и каталоги стендовые. Иначе исключение, и тест ничего не нажимает. */
  assertStandOnly() {
    const realOf = (p) => {
      try {
        return fs.realpathSync(p)
      } catch {
        return null
      }
    }
    const problems = []
    const url = new URL(this.dshUrl)
    const tmp = realOf(os.tmpdir())
    const dir = realOf(this.dir)
    const archive = realOf(this.archive)
    if (url.hostname !== '127.0.0.1' || Number(url.port) !== this.ports.dsh) problems.push('the DSH address is not the stand port')
    if (!dir || !tmp || !dir.startsWith(path.join(tmp, 'ba-e2e-'))) problems.push('the stand directory is not a stand temp directory')
    if (!dir || !fs.existsSync(path.join(dir, '.ba-e2e-stand'))) problems.push('no stand marker in the stand directory')
    if (!dir || !archive || !archive.startsWith(dir + path.sep)) problems.push('the archive is not inside the stand directory')
    // порты, на которые по умолчанию смотрят службы и модель продукта (поиск, документы, MCP, DSH, модель, векторы): ни один не должен быть нашим
    const live = [3000, 8765, 8766, 8767, 8080, 11434]
    if (Object.values(this.ports).some((port) => live.includes(port))) problems.push('a stand port equals a live service port')
    // команду со стендовым окружением запускают только после того, как адрес и каталоги признаны стендовыми
    if (problems.length === 0) {
      const status = this.status()
      if (status.home !== this.archive) problems.push('the command works on another archive')
      if (!String(status.inbox).startsWith(this.dir + path.sep)) problems.push('the inbox folder is outside the stand directory')
    }
    if (problems.length > 0) throw new Error('not a stand: ' + problems.join('; '))
  }
}

function connect() {
  const dir = process.env.BA_E2E_STAND
  if (!dir) throw new Error('BA_E2E_STAND is not set: the stand is started by global-setup.cjs (run the suite with run.sh)')
  return new Stand(JSON.parse(fs.readFileSync(path.join(dir, 'stand.json'), 'utf8')))
}

// ── интерфейс DSH ────────────────────────────────────────────────────────────
const ESC = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/**
 * Снимает окна начала работы: «Continue» (уведомление о предварительной версии), «Configure later» (ключ провайдера). Выходит, когда
 * основной экран спокоен: окон нет в течение паузы. Окно ключа DSH показывает при каждой загрузке страницы.
 */
async function dismissOnboarding(page, quietMs = 1500, limitMs = 40000) {
  const until = Date.now() + limitMs
  let quietSince = Date.now()
  while (Date.now() < until) {
    let acted = false
    for (const label of ['Continue', 'Configure later']) {
      const button = page.getByRole('button', { name: label, exact: true })
      if (await button.count() && await button.first().isVisible()) {
        await button.first().click()
        acted = true
        await sleep(500)
      }
    }
    if (acted) quietSince = Date.now()
    else if (Date.now() - quietSince >= quietMs) return
    else await sleep(250)
  }
  throw new Error('the start dialogs of DSH did not settle')
}

/** Вход по ссылке. Ссылка нигде не печатается: сообщение о сбое очищено. */
async function signIn(page, stand) {
  try {
    await page.goto(stand.link(), { waitUntil: 'domcontentloaded' })
  } catch (error) {
    throw new Error('DSH sign-in failed: ' + scrub(error.message).split('\n')[0])
  }
}

module.exports = { Stand, connect, scrub, nonce, clientName, sleep, ESC, dismissOnboarding, signIn, TOKEN_LIKE, LINK_LIKE }
