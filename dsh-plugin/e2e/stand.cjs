#!/usr/bin/env node
'use strict'
/*
 * Одноразовый стенд для сквозных проверок из интерфейса (FR-91).
 *
 * Поднимает с нуля, во временном каталоге и на свободных портах петли: архив с пустым индексом, подставную службу векторов, службы поиска и
 * документов, сервер MCP и DSH с плагином. Свои HOME, DSH_HOME, FLYARCHIVE_HOME, подставной systemctl; проверки моделью нет (адреса моделей —
 * закрытый порт). Службы — дочерние процессы стенда, а не юниты systemd. По окончании всё останавливается и каталог удаляется: после
 * упавшего теста, по Ctrl+C и когда умер родитель (стенд следит за концом канала stdin).
 *
 *   const { start } = require('./stand.cjs');  const stand = await start();  ...  await stand.stop();
 *   node stand.cjs up        поднять стенд и держать, пока не нажато Ctrl+C (для ручных проб); токены и ссылку входа не печатает
 *
 * Откуда берётся код (переключатели окружения):
 *   BA_E2E_TOOLS   worktree (по умолчанию: рабочий каталог репозитория относительно этого сценария) | head (последний коммит, git archive)
 *                  | путь к готовой копии каталога tools (для порчи сервера на копии)
 *   BA_E2E_PLUGIN  каталог плагина (по умолчанию соседний: dsh-plugin); берутся package.json и lib/
 *   BA_E2E_KEEP=1  каталог стенда не удалять (только для отладки: в нём лежат токены стенда)
 * Стенд всегда работает со снимком tools: чужая правка рабочего каталога во время прогона стенд не ломает.
 */
const cp = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const http = require('node:http')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')

const E2E = __dirname
const REPO = path.resolve(E2E, '..', '..')
const PREFIX = 'ba-e2e-'
const MARK = '.ba-e2e-stand'
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// ── что нужно машине: проверка до запуска, с причиной пропуска ───────────────
function which(name, pathVar = process.env.PATH) {
  for (const dir of (pathVar || '').split(path.delimiter)) {
    if (dir === '') continue
    const full = path.join(dir, name)
    try {
      fs.accessSync(full, fs.constants.X_OK)
      if (fs.statSync(full).isFile()) return full
    } catch {
      // нет в этом каталоге
    }
  }
  return null
}

/** Всё ли есть для прогона. Возвращает причину пропуска (строка) или null. Ничего не скачивает и не ставит. */
function missing() {
  if (process.platform !== 'linux') return `the stand needs Linux (DSH, bwrap, systemctl stand-in): this is ${process.platform}`
  if (Number(process.versions.node.split('.')[0]) < 20) return `Node.js 20 or newer is needed (this is ${process.versions.node})`
  let playwright
  try {
    playwright = require('playwright')
  } catch {
    return 'Playwright is not installed (set NODE_PATH to the global node_modules, for example: NODE_PATH=$(npm root -g))'
  }
  let browser
  try {
    browser = playwright.chromium.executablePath()
  } catch {
    browser = ''
  }
  if (!browser || !fs.existsSync(browser)) return 'the Chromium build of Playwright is not installed (install it yourself: npx playwright install chromium; this suite downloads nothing)'
  if (!which('dsh')) return 'the dsh command is not on PATH'
  if (!which('bwrap')) return 'bwrap is not installed: the archive preview needs it'
  // не только есть ли bwrap, но и может ли он здесь поднять песочницу (в контейнерах и под запретом пространств имён — нет)
  const sandbox = cp.spawnSync('bwrap', ['--unshare-all', '--ro-bind', '/', '/', which('true') || '/bin/true'], { encoding: 'utf8' })
  if (sandbox.status !== 0) return 'bwrap cannot start a sandbox on this machine (user namespaces are not allowed?): the archive preview needs it'
  if (!which('python3')) return 'python3 is not on PATH'
  const probe = cp.spawnSync('python3', ['-c', 'import lancedb, pyarrow\ntry:\n import pymupdf\nexcept ImportError:\n import fitz'], { encoding: 'utf8' })
  if (probe.status !== 0) return 'the Python modules lancedb, pyarrow and PyMuPDF are not installed for python3'
  if ((process.env.BA_E2E_TOOLS || 'worktree') === 'head' && !which('git')) return 'git is not installed (BA_E2E_TOOLS=head needs git archive)'
  return null
}

// ── мелочи ───────────────────────────────────────────────────────────────────
/** Свободные порты петли: сначала открываются все сразу, потом закрываются, чтобы не получить один и тот же дважды. */
async function freePorts(count) {
  const servers = await Promise.all(Array.from({ length: count }, () => new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => resolve(server))
  })))
  const ports = servers.map((server) => server.address().port)
  await Promise.all(servers.map((server) => new Promise((resolve) => server.close(resolve))))
  return ports
}

function listening(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: '127.0.0.1', port }, () => {
      socket.destroy()
      resolve(true)
    })
    socket.once('error', () => resolve(false))
  })
}

async function waitPort(port, child, what, timeoutMs = 45000) {
  const until = Date.now() + timeoutMs
  while (Date.now() < until) {
    if (child.exitCode !== null) throw new Error(`${what} exited before it opened port ${port} (code ${child.exitCode})`)
    if (await listening(port)) return
    await sleep(200)
  }
  throw new Error(`${what} did not open port ${port} in ${Math.round(timeoutMs / 1000)} s`)
}

/** Текст журнала без токенов и ссылок входа: так его можно показать при сбое подъёма. */
function scrubbed(text) {
  return String(text)
    .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '')
    .replace(/token=[A-Za-z0-9_-]+/g, 'token=***')
    .replace(/ba_[A-Za-z0-9_-]{8,}/g, 'ba_***')
}

function tail(file, lines = 12) {
  try {
    return scrubbed(fs.readFileSync(file, 'utf8')).trim().split('\n').slice(-lines).join('\n')
  } catch {
    return ''
  }
}

function killGroup(pid, signal) {
  try {
    process.kill(-pid, signal)
  } catch {
    // группы уже нет
  }
}

/** Процессы, унаследовавшие метку стенда в окружении: всё, что осталось от стенда, даже вышедшее из группы. */
function strays(dir) {
  const found = []
  const want = `BA_E2E_DIR=${dir}`
  for (const name of fs.readdirSync('/proc')) {
    if (!/^[0-9]+$/.test(name) || Number(name) === process.pid) continue
    try {
      if (fs.readFileSync(`/proc/${name}/environ`, 'latin1').split('\0').includes(want)) found.push(Number(name))
    } catch {
      // чужой или уже ушёл
    }
  }
  return found
}

function removeDir(dir) {
  fs.rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
}

/** Осиротевшие стенды прошлых прогонов (родитель убит без предупреждения): только наши, по метке и по мёртвому хозяину. */
function sweepOrphans() {
  for (const name of fs.readdirSync(os.tmpdir())) {
    if (!name.startsWith(PREFIX)) continue
    const dir = path.join(os.tmpdir(), name)
    try {
      if (!fs.existsSync(path.join(dir, MARK))) continue
      const owner = Number(fs.readFileSync(path.join(dir, 'supervisor.pid'), 'utf8'))
      let alive = false
      try {
        process.kill(owner, 0)
        alive = true
      } catch {
        // хозяина нет
      }
      if (alive) continue
    } catch {
      // нет файла хозяина: стенд не успел подняться
    }
    for (const pid of strays(dir)) {
      try {
        process.kill(pid, 'SIGKILL')
      } catch {
        // уже ушёл
      }
    }
    removeDir(dir)
  }
}

// ── подставная служба векторов: слова → хеш → вектор из 1024 чисел (как bge-m3 по размеру; похожие по словам тексты близки) ──
function vector(text) {
  const v = new Array(1024).fill(0)
  for (const word of String(text).toLowerCase().match(/[\p{L}\p{N}_]+/gu) || []) {
    const digest = crypto.createHash('md5').update(word.slice(0, 6), 'utf8').digest()
    v[digest.readUInt32BE(0) % 1024] += digest[4] % 2 ? 1 : -1
  }
  const norm = Math.sqrt(v.reduce((sum, x) => sum + x * x, 0)) || 1
  return v.map((x) => x / norm)
}

/** Служба векторов. Задержка (мс) читается из файла control/embed-delay-ms при каждом запросе: тест замедляет индексацию, чтобы увидеть ход разбора. */
function startEmbed(dir) {
  const server = http.createServer((request, response) => {
    const chunks = []
    request.on('data', (chunk) => chunks.push(chunk))
    request.on('end', async () => {
      let delay = 0
      try {
        delay = Number(fs.readFileSync(path.join(dir, 'control', 'embed-delay-ms'), 'utf8')) || 0
      } catch {
        // файла нет: без задержки
      }
      if (delay > 0) await sleep(delay)
      let body = {}
      try {
        body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')
      } catch {
        // пустой запрос
      }
      const items = typeof body.input === 'string' ? [body.input] : Array.isArray(body.input) ? body.input : []
      const cpuOnly = body.options && body.options.num_gpu === 0
      fs.appendFileSync(path.join(dir, 'logs', 'embed.log'), JSON.stringify({ n: items.length, cpuOnly }) + '\n')
      const data = JSON.stringify({ embeddings: items.map(vector) })
      response.writeHead(200, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data) })
      response.end(data)
    })
  })
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => resolve(server))
  })
}

// ── подъём ───────────────────────────────────────────────────────────────────
function snapshotTools(dir) {
  const want = process.env.BA_E2E_TOOLS || 'worktree'
  const target = path.join(dir, 'tools')
  if (want === 'head') {
    const archive = cp.spawnSync('git', ['-c', 'safe.directory=*', '-C', REPO, 'archive', 'HEAD', 'tools'], { maxBuffer: 256 * 1024 * 1024 })
    if (archive.status !== 0) throw new Error('git archive HEAD tools failed: ' + scrubbed(archive.stderr))
    const unpack = cp.spawnSync('tar', ['-x', '-C', dir], { input: archive.stdout })
    if (unpack.status !== 0) throw new Error('tar failed on the tools snapshot')
  } else {
    const source = want === 'worktree' ? path.join(REPO, 'tools') : path.resolve(want)
    if (!fs.existsSync(path.join(source, 'flyarchive'))) throw new Error(`BA_E2E_TOOLS: no flyarchive command in ${source}`)
    fs.cpSync(source, target, { recursive: true, filter: (from) => !from.split(path.sep).includes('__pycache__') })
  }
  fs.chmodSync(path.join(target, 'flyarchive'), 0o755)
  return target
}

const FAKE_SYSTEMCTL = (dir, tools) => `#!/bin/bash
# подставной systemctl стенда: пишет вызовы в журнал; «разобрать сейчас» запускает разбор в фоне, остальное — успех
echo "$@" >> "${dir}/systemctl.log"
case " $* " in
  *" start "*flyarchive-inbox.service*) nohup python3 "${tools}/flyarchive" inbox run >> "${dir}/logs/inbox-run.log" 2>&1 & ;;
esac
exit 0
`

// контейнер для Office и HTML стенду не нужен (в сценариях только письма, PDF, текст, архивы и программы): настоящий docker на машине не трогается
const FAKE_DOCKER = (dir) => `#!/bin/bash
echo "$@" >> "${dir}/docker.log"
echo "docker is switched off in the e2e stand" >&2
exit 1
`

const PATCH = `# накладка DSH стенда: только плагин архива
- insert:
    - id: flyarchive-admin
      name: 'flyarchive-dsh-plugin'
`

/**
 * Поднимает стенд. Возвращает {dir, ports, ..., stop} — без секретов: ссылка входа DSH лежит только в файле secrets/dsh-url.txt (0600).
 * Вызывать надо один раз; stop() останавливает всё и удаляет каталог.
 */
async function bringUp() {
  const reason = missing()
  if (reason) throw new Error('cannot start the stand: ' + reason)
  sweepOrphans()
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), PREFIX)))
  fs.writeFileSync(path.join(dir, MARK), crypto.randomBytes(8).toString('hex'))
  fs.writeFileSync(path.join(dir, 'supervisor.pid'), String(process.pid))
  const children = []
  let embed = null
  let stopped = false

  const stop = async () => {
    if (stopped) return
    stopped = true
    for (const child of children) if (child.exitCode === null) killGroup(child.pid, 'SIGTERM')
    const until = Date.now() + 4000
    while (Date.now() < until && children.some((child) => child.exitCode === null)) await sleep(100)
    for (const child of children) killGroup(child.pid, 'SIGKILL')
    for (const pid of strays(dir)) {
      try {
        process.kill(pid, 'SIGKILL')
      } catch {
        // уже ушёл
      }
    }
    if (embed) await new Promise((resolve) => embed.close(resolve))
    if (process.env.BA_E2E_KEEP !== '1') removeDir(dir)
  }

  try {
    for (const sub of ['home', 'dsh-home', 'archive', 'inbox', 'bin', 'logs', 'secrets', 'control', 'workspace', 'tmp']) fs.mkdirSync(path.join(dir, sub), { recursive: true })
    fs.chmodSync(path.join(dir, 'secrets'), 0o700)
    const home = path.join(dir, 'home')
    const archive = path.join(dir, 'archive')
    const tools = snapshotTools(dir)
    const realPath = process.env.PATH || '/usr/bin:/bin'
    const userSite = cp.execFileSync('python3', ['-c', 'import site; print(site.getusersitepackages())'], { encoding: 'utf8' }).trim()
    const dshBin = which('dsh')
    fs.writeFileSync(path.join(dir, 'bin', 'systemctl'), FAKE_SYSTEMCTL(dir, tools), { mode: 0o755 })
    fs.writeFileSync(path.join(dir, 'bin', 'docker'), FAKE_DOCKER(dir), { mode: 0o755 })

    embed = await startEmbed(dir)
    const embedPort = embed.address().port
    const [dshPort, searchPort, officePort, mcpPort] = await freePorts(4)
    const env = {
      PATH: `${path.join(dir, 'bin')}${path.delimiter}${realPath}`,
      HOME: home, USER: os.userInfo().username, LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8', TMPDIR: path.join(dir, 'tmp'),
      DSH_HOME: path.join(dir, 'dsh-home'), FLYARCHIVE_HOME: archive, FLYARCHIVE_CLI: path.join(tools, 'flyarchive'),
      PYTHONPATH: userSite, PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1',
      FLYARCHIVE_EMBED_URL: `http://127.0.0.1:${embedPort}/api/embed`,
      FLYARCHIVE_LLM_LOCAL_URL: 'http://127.0.0.1:9', FLYARCHIVE_LLM_CLOUD_URL: 'http://127.0.0.1:9',
      DSH_TELEMETRY_MODE: 'DISABLED', DSH_TELEMETRY_OTLP_URL: 'http://127.0.0.1:9/v1/logs',
      BA_E2E_DIR: dir, BA_E2E_TOOLS: tools,
      BA_E2E_SEARCH_PORT: String(searchPort), BA_E2E_OFFICE_PORT: String(officePort), BA_E2E_MCP_PORT: String(mcpPort),
    }
    const run = (args, input) => {
      const result = cp.spawnSync('python3', [path.join(tools, 'flyarchive'), ...args], { env, encoding: 'utf8', input, cwd: dir })
      if (result.status !== 0) throw new Error(`flyarchive ${args[0]} ${args[1] || ''} failed: ${scrubbed(result.stderr).slice(-400)}`)
      return result.stdout
    }

    // архив заводит сама команда init: каталоги, служебный токен, база известного, пустая таблица индекса с полнотекстовым индексом;
    // затем входящая папка стенда без проверки модели; файлы разбираются сразу, без выстойки
    run(['init'])
    run(['inbox', 'set', '--path', path.join(dir, 'inbox'), '--llm', 'off', '--cloud', 'off', '--period', '30'])
    const configFile = path.join(archive, 'inbox.json')
    const config = JSON.parse(fs.readFileSync(configFile, 'utf8'))
    config.stable_seconds = 0
    fs.writeFileSync(configFile, JSON.stringify(config), { mode: 0o600 })

    const spawnService = (name, command, args, extra = {}) => {
      const log = fs.openSync(path.join(dir, 'logs', name + '.log'), 'a')
      const child = cp.spawn(command, args, { cwd: extra.cwd || dir, env, detached: true, stdio: ['ignore', log, log] })
      fs.closeSync(log)
      children.push(child)
      return child
    }
    const services = path.join(E2E, 'stand_services.py')
    const search = spawnService('search', 'python3', ['-u', services, 'search'])
    await waitPort(searchPort, search, 'the search service')
    const office = spawnService('office', 'python3', ['-u', services, 'office'])
    await waitPort(officePort, office, 'the documents service')
    const mcp = spawnService('mcp', 'python3', ['-u', services, 'mcp'])
    await waitPort(mcpPort, mcp, 'the MCP server')

    // DSH: профиль, плагин из рабочего каталога (снимок), накладка с одним плагином
    cp.spawnSync(dshBin, ['--profile', 'web', '--dump-config'], { env, stdio: 'ignore', timeout: 60000 })
    const pluginSource = path.resolve(process.env.BA_E2E_PLUGIN || path.join(E2E, '..'))
    const pluginTarget = path.join(dir, 'dsh-home', 'profiles', 'web', 'node_modules', 'flyarchive-dsh-plugin')
    fs.mkdirSync(pluginTarget, { recursive: true })
    fs.copyFileSync(path.join(pluginSource, 'package.json'), path.join(pluginTarget, 'package.json'))
    fs.cpSync(path.join(pluginSource, 'lib'), path.join(pluginTarget, 'lib'), { recursive: true })
    fs.writeFileSync(path.join(dir, 'patch.yml'), PATCH)
    const dsh = spawnService('dsh', dshBin, ['--profile', 'web', '--patch', path.join(dir, 'patch.yml'), '--no-open', '--host', '127.0.0.1',
      '--port', String(dshPort)], { cwd: path.join(dir, 'workspace') })
    await waitPort(dshPort, dsh, 'DSH', 90000)
    let link = null
    const until = Date.now() + 60000
    while (Date.now() < until && link === null) {
      const found = new RegExp(`http://127\\.0\\.0\\.1:${dshPort}/\\?token=[A-Za-z0-9_-]+`).exec(fs.readFileSync(path.join(dir, 'logs', 'dsh.log'), 'latin1'))
      if (found) link = found[0]
      else await sleep(300)
    }
    if (link === null) throw new Error('DSH did not print its sign-in link: ' + tail(path.join(dir, 'logs', 'dsh.log')))
    fs.writeFileSync(path.join(dir, 'secrets', 'dsh-url.txt'), link + '\n', { mode: 0o600 })

    const info = {
      dir, archive, inbox: path.join(dir, 'inbox'), tools, home, pid: process.pid,
      ports: { dsh: dshPort, search: searchPort, office: officePort, mcp: mcpPort, embed: embedPort },
      dshUrl: `http://127.0.0.1:${dshPort}`, mcpUrl: `http://127.0.0.1:${mcpPort}/mcp`,
    }
    fs.writeFileSync(path.join(dir, 'stand.json'), JSON.stringify(info))
    return { info, stop }
  } catch (error) {
    await stop()
    throw error
  }
}

// ── режим «supervisor»: отдельный процесс, который держит стенд и убирает его при любом конце родителя ──
async function serve() {
  let stand = null
  let leaving = false
  const leave = async (code) => {
    if (leaving) return
    leaving = true
    try {
      if (stand) await stand.stop()
    } finally {
      process.exit(code)
    }
  }
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(signal, () => leave(0))
  process.on('uncaughtException', (error) => {
    process.stderr.write('stand: ' + scrubbed(error && error.stack ? error.stack : error) + '\n')
    leave(1)
  })
  try {
    stand = await bringUp()
  } catch (error) {
    process.stdout.write(JSON.stringify({ error: scrubbed(error.message) }) + '\n')
    return leave(1)
  }
  process.stdout.write(JSON.stringify({ ready: stand.info }) + '\n')
  // родитель держит канал stdin открытым; его конец (в том числе при убитом родителе) значит «убирай»; слово stop — то же по-хорошему
  process.stdin.on('data', (chunk) => {
    if (String(chunk).includes('stop')) leave(0)
  })
  process.stdin.on('end', () => leave(0))
  process.stdin.on('close', () => leave(0))
  process.stdin.resume()
}

/** Запускает стенд отдельным процессом и ждёт готовности. stop() останавливает его и ждёт, пока каталог не будет удалён. */
function start() {
  return new Promise((resolve, reject) => {
    const child = cp.spawn(process.execPath, [__filename, 'serve'], { stdio: ['pipe', 'pipe', 'inherit'], env: process.env })
    let buffer = ''
    let settled = false
    const done = (fn, value) => {
      if (settled) return
      settled = true
      fn(value)
    }
    const exited = new Promise((resolveExit) => child.once('exit', resolveExit))
    child.stdin.on('error', () => {}) // стенд мог уйти сам (например, по сигналу): запись в закрытый канал не ошибка
    child.stdout.on('data', (chunk) => {
      buffer += chunk
      const line = buffer.split('\n')[0]
      if (!buffer.includes('\n')) return
      let message
      try {
        message = JSON.parse(line)
      } catch {
        return done(reject, new Error('the stand answered with something unreadable'))
      }
      if (message.error) return done(reject, new Error('the stand did not start: ' + message.error))
      const stop = async () => {
        try {
          child.stdin.write('stop\n')
          child.stdin.end()
        } catch {
          // канал уже закрыт
        }
        await Promise.race([exited, sleep(30000)])
      }
      done(resolve, { ...message.ready, stop })
    })
    child.once('exit', (code) => done(reject, new Error(`the stand process ended before it was ready (code ${code})`)))
    // родитель уходит — канал закрывается сам, стенд убирает всё; явный запрос тоже работает
    process.once('exit', () => {
      try {
        child.stdin.end()
      } catch {
        // уже закрыт
      }
    })
  })
}

module.exports = { start, missing, which, scrubbed, vector, PREFIX, MARK }

if (require.main === module) {
  const command = process.argv[2]
  if (command === 'serve') {
    serve()
  } else if (command === 'up') {
    start().then((stand) => {
      process.stdout.write(`stand is up in ${stand.dir}\n  DSH ${stand.dshUrl}  MCP ${stand.mcpUrl}\nCtrl+C stops it and removes the directory\n`)
      process.on('SIGINT', () => stand.stop().then(() => process.exit(0)))
      process.on('SIGTERM', () => stand.stop().then(() => process.exit(0)))
      setInterval(() => {}, 1 << 30)
    }, (error) => {
      process.stderr.write(error.message + '\n')
      process.exit(1)
    })
  } else {
    process.stderr.write('usage: node stand.cjs up\n')
    process.exit(2)
  }
}
