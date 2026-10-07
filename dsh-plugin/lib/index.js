/**
 * Раздел «Архив» в настройках DeepSeek Harness — серверная половина (FR-50).
 *
 * Регистрирует три адреса в соединении DSH, под /api. Туда пускает сам DSH: проверка Host и
 * Origin плюс подписанная cookie сеанса. Значит, выпускать и отзывать токены может только
 * человек, вошедший в DSH на этой машине; ни другая машина, ни процесс без входа сюда не попадут.
 *
 * Сама работа — у команды flyarchive: плагин её запускает и пересказывает ответ. Хранилище токенов
 * и журнал плагин не читает и формат их не знает.
 *
 *     GET  /api/flyarchive.tokens           список токенов (без значений)
 *     POST /api/flyarchive.tokens           выпуск: {name, level?, days?} → значение, один раз
 *     POST /api/flyarchive.tokens.revoke    отзыв: {name}
 *     GET  /api/flyarchive.journal?n=&client=
 *     GET  /api/flyarchive.inbox            состояние входящих и настройка
 *     POST /api/flyarchive.inbox            настройка: период, модель, порог, пределы архивов, описание изображений (vision, vision_pages, vision_minutes);
 *                                         смена папки — отдельный запрос {path, confirm: true}
 *     POST /api/flyarchive.inbox.run        разобрать сейчас
 *     GET  /api/flyarchive.queue            очередь утверждения
 *     POST /api/flyarchive.queue            решение: {path | paths (до 200), action: accept | quarantine}
 *     GET  /api/flyarchive.quarantine       карантин
 *     POST /api/flyarchive.quarantine       решение: {path | paths (до 200), action: return}, стирание — {path, action: delete}
 *     POST /api/flyarchive.doc.delete       убрать документ из архива и индекса: {path, confirm: true}
 *     GET  /api/flyarchive.batches?limit=&before=   история пачек: limit — целое 1…200 (по умолчанию 30), before — номер пачки
 *     GET  /api/flyarchive.batch?id=        одна пачка: файлы, решения владельца, где что лежит
 *     POST /api/flyarchive.preview          описание просмотра: {area, path, member?} → JSON команды preview show
 *     POST /api/flyarchive.preview.page     страница просмотра: {area, path, member?, page} → PNG (image/png, nosniff, sandbox)
 */
import { execFile } from 'node:child_process'

export const name = 'flyarchive-admin'
export const inject = ['connection']

export const TOKENS_PATH = '/api/flyarchive.tokens'
export const REVOKE_PATH = '/api/flyarchive.tokens.revoke'
export const JOURNAL_PATH = '/api/flyarchive.journal'
export const INBOX_PATH = '/api/flyarchive.inbox'
export const INBOX_RUN_PATH = '/api/flyarchive.inbox.run'
export const QUEUE_PATH = '/api/flyarchive.queue'
export const QUARANTINE_PATH = '/api/flyarchive.quarantine'
export const DELETE_PATH = '/api/flyarchive.doc.delete'
export const BATCHES_PATH = '/api/flyarchive.batches'
export const BATCH_PATH = '/api/flyarchive.batch'
export const PREVIEW_PATH = '/api/flyarchive.preview'
export const PREVIEW_PAGE_PATH = '/api/flyarchive.preview.page'

const LOCAL_NAME = 'dsh-local' // служебный токен самого DSH: отозвать его отсюда — отрезать DSH от архива
const CLIENT_NAME = /^[\p{L}\p{N}._][\p{L}\p{N}._-]{0,63}$/u // как в хранилище токенов, но не с дефиса: иначе это ключ команды
const JOURNAL_CLIENT = /^(-|[\p{L}\p{N}._][\p{L}\p{N}._-]{0,63})$/u // «-» в журнале — запрос без токена
const LEVELS = ['read', 'full'] // уровень local выпускается только командой init-local
const MAX_DAYS = 3650
const JOURNAL_DEFAULT = 100
const JOURNAL_MAX = 500
const TIMEOUT_MS = 20000
// принятие и удаление трогают индекс, а векторы считаются на процессоре: большой документ — это минуты
export const LONG_TIMEOUT_MS = 15 * 60 * 1000
const TOKEN_LIKE = /ba_[A-Za-z0-9_-]{8,}/g
// команда не нашлась: какой настройкой задать путь (строка command плагина в файле настройки оболочки или переменная окружения)
const NOT_FOUND_HINT = '. Set the path to it in the "command" setting of the plugin, or in the FLYARCHIVE_CLI environment variable.'
const PATH_MAX = 1000
const BATCH_MAX = 200 // путей в одном решении; вкладка режет большие выборы на запросы по 200
const BATCH_ID = /^[0-9]{8}-[0-9]{6}(?:-(?:[2-9]|[1-9][0-9]+))?$/ // как в tools/batches.py: ГГГГММДД-ЧЧММСС, при совпадении -2, -3 …
const HISTORY_DEFAULT = 30
const HISTORY_MAX = 200
// просмотр: рабочий процесс может рисовать файл минуты (контейнер, большой документ), а страница — это PNG до десятков мегабайт
export const PREVIEW_TIMEOUT_MS = 300 * 1000
const PREVIEW_MAX_BYTES = 32 * 1024 * 1024
const PREVIEW_AREAS = ['queue', 'quarantine', 'corpus']
const PREVIEW_DEPTH = 2 // вложение письма и вложение во вложении; глубже команда отказывает сама
const PAGE_MAX = 500
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
const whole = (low, high) => (v) => Number.isInteger(v) && v >= low && v <= high
// что можно настроить из раздела и в каких границах; папка входящих меняется только командой
const SETTINGS = {
  period: [(v) => [1, 5, 10, 30, 60].includes(v), '--period'],
  llm: [(v) => typeof v === 'boolean', '--llm'],
  cloud: [(v) => typeof v === 'boolean', '--cloud'],
  threshold: [whole(0, 100), '--threshold'],
  max_gb: [(v) => typeof v === 'number' && Number.isFinite(v) && v >= 0.01 && v <= 500, '--max-gb'],
  max_files: [whole(1, 2000000), '--max-files'],
  max_ratio: [whole(2, 10000), '--max-ratio'],
  depth: [whole(1, 10), '--depth'],
  // описание картинок и сканов локальной моделью со зрением (FR-92, FR-94): включение и пределы одного прохода
  vision: [(v) => typeof v === 'boolean', '--vision'],
  vision_pages: [whole(1, 1000), '--vision-pages'],
  vision_minutes: [whole(1, 240), '--vision-minutes'],
}

/**
 * Отказ с кодом ответа: 400 — запрос негоден, 409 — команда отказала, 502 — команда не сработала.
 * detail — сообщение {code, args, text}, если команда отказала по-новому: тогда в ответ идёт оно, а не строка.
 */
class Refusal extends Error {
  constructor(status, message, detail = null) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

/**
 * Последняя строка stderr как сообщение системы: flyarchive с --json пишет её JSON-ом {"error": {code, args, text}}.
 * Всё прочее (не JSON, нет error, нет text) — не сообщение, и тогда остаётся прежний разбор строки.
 * Значения, похожие на токен, стираются из готового объекта: так их не спрятать и записью через \u.
 */
function messageOf(line) {
  let data
  try {
    data = JSON.parse(line)
  } catch {
    return null
  }
  const error = data !== null && typeof data === 'object' ? data.error : undefined
  if (error === null || typeof error !== 'object' || typeof error.text !== 'string') return null
  const args = error.args !== null && typeof error.args === 'object' && !Array.isArray(error.args) ? error.args : {}
  const message = { code: typeof error.code === 'string' ? error.code : null, args, text: error.text }
  return JSON.parse(JSON.stringify(message).replace(TOKEN_LIKE, 'ba_***'))
}

/**
 * Запускает команду без оболочки: аргументы не разбираются как текст.
 * @returns код возврата и оба потока; ненулевой код — не исключение.
 */
export function runCli(command, args, timeout = TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    execFile(command, args, { timeout, maxBuffer: 16 * 1024 * 1024, encoding: 'utf8' }, (error, stdout, stderr) => {
      if (error && typeof error.code !== 'number') reject(error)
      else resolve({ code: error ? error.code : 0, stdout, stderr })
    })
  })
}

/**
 * То же, но stdout читается как есть, байтами: страница просмотра — PNG, в текст его перекодировать нельзя.
 * Предел вывода 32 МБ (текстовый запуск — 16); stderr остаётся строкой, там сообщение об отказе.
 */
export function runCliBinary(command, args, timeout = TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    execFile(command, args, { timeout, maxBuffer: PREVIEW_MAX_BYTES, encoding: 'buffer' }, (error, stdout, stderr) => {
      if (error && typeof error.code !== 'number') reject(error)
      else resolve({ code: error ? error.code : 0, stdout, stderr: Buffer.from(stderr).toString('utf8') })
    })
  })
}

/**
 * Путь к команде flyarchive: настройка плагина (строку command пишет установка), переменная окружения, а без них — имя `flyarchive`:
 * execFile найдёт его в пути поиска команд (PATH), оболочки при этом нет. Каталога архива здесь нет: кода в нём давно не лежит.
 */
export function defaultCommand(config = {}, env = process.env) {
  return config.command || env.FLYARCHIVE_CLI || 'flyarchive'
}

function clientName(value, pattern = CLIENT_NAME) {
  if (typeof value !== 'string' || !pattern.test(value)) {
    throw new Refusal(400, 'client name: 1 to 64 letters, digits or . _ - characters, not starting with a hyphen')
  }
  return value
}

/** Путь файла из списка или из выдачи поиска: строка без управляющих знаков, не похожая на ключ команды. */
function filePath(value) {
  const bad = typeof value !== 'string' || value.length === 0 || value.length > PATH_MAX || value.startsWith('-') ||
    [...value].some((ch) => ch.charCodeAt(0) < 32)
  if (bad) throw new Refusal(400, 'a path from the list is required')
  return value
}

/** Список путей решения: от 1 до 200, каждый проходит ту же проверку, повторов нет. Вся проверка — до вызова команды. */
function filePaths(value) {
  if (!Array.isArray(value) || value.length === 0 || value.length > BATCH_MAX) {
    throw new Refusal(400, `a list of 1 to ${BATCH_MAX} paths is required`)
  }
  const list = value.map(filePath)
  if (new Set(list).size !== list.length) throw new Refusal(400, 'the path list has duplicates')
  return list
}

/** Значение параметра запроса: нет — null; повтор — отказ (какое имелось в виду, неизвестно); пустое значение остаётся строкой и не пройдёт проверку. */
function single(query, key) {
  const values = query.getAll(key)
  if (values.length > 1) throw new Refusal(400, `parameter ${key} is given more than once`)
  return values.length === 0 ? null : values[0]
}

/** Номер пачки из запроса: только по виду ГГГГММДД-ЧЧММСС[-N]; такое значение не похоже на ключ команды. */
function batchId(value, key) {
  if (value === null || !BATCH_ID.test(value)) throw new Refusal(400, `${key}: a batch id like YYYYMMDD-HHMMSS, with -N at the end on a clash`)
  return value
}

/** Цель просмотра из тела запроса: область, путь файла, цепочка вложений (до двух, целые от 0). Вся проверка — до вызова команды. */
function previewTarget(body) {
  const { area, path: file, member } = objectOf(body)
  if (!PREVIEW_AREAS.includes(area)) throw new Refusal(400, `area: ${PREVIEW_AREAS.join(', ')}`)
  const members = member === undefined ? [] : member
  if (!Array.isArray(members) || members.length > PREVIEW_DEPTH || !members.every((m) => Number.isSafeInteger(m) && m >= 0)) {
    throw new Refusal(400, `member: a list of at most ${PREVIEW_DEPTH} integers from 0`)
  }
  return { area, file: filePath(file), members }
}

/** Номер страницы: целое от 1 до 500. */
function pageNumber(value) {
  if (!whole(1, PAGE_MAX)(value)) throw new Refusal(400, `page: an integer from 1 to ${PAGE_MAX}`)
  return value
}

/**
 * Входящая папка из запроса на смену: строка, абсолютный путь (с «/»), без управляющих знаков, не длиннее 1000 знаков.
 * Начало с «/» заодно значит, что значение не похоже на ключ команды. Существует ли каталог и лежит ли он вне архива, решает команда.
 */
function inboxFolder(value) {
  const bad = typeof value !== 'string' || !value.startsWith('/') || value.length > PATH_MAX || /\p{Cc}/u.test(value)
  if (bad) throw new Refusal(400, `path: an absolute folder path starting with /, up to ${PATH_MAX} characters, without control characters`)
  return value
}

const memberArgs = (members) => members.flatMap((m) => ['--member', String(m)])

function objectOf(body) {
  if (typeof body !== 'object' || body === null || Array.isArray(body)) throw new Refusal(400, 'an object is required')
  return body
}

/** Операции раздела поверх команды flyarchive. */
export function createApi({ command, run = runCli, runBinary = runCliBinary }) {
  const exec = async (args, timeout, runner = run) => {
    let result
    try {
      result = await runner(command, args, timeout)
    } catch (error) {
      const hint = error.code === 'ENOENT' ? NOT_FOUND_HINT : ''
      throw new Refusal(502, `The flyarchive command could not be started (${command}): ${error.code ?? error.message}${hint}`)
    }
    if (result.code !== 0) {
      // код 2 — разбор аргументов: у него нет объекта-сообщения, даже если последняя строка похожа на него
      const detail = result.code === 2 ? null : messageOf(String(result.stderr ?? '').trim().split('\n').pop())
      if (detail !== null) throw new Refusal(409, detail.text, detail)
      // сырой stderr (usage, трассировки, имена файлов) наружу не уходит: человеку хватает кода возврата
      throw new Refusal(502, result.code === 2
        ? 'The flyarchive command did not understand the request (exit code 2): the plugin and the archive tools may be out of sync.'
        : `The flyarchive command failed (exit code ${result.code}).`)
    }
    return result.stdout
  }
  const call = async (args, timeout) => {
    const stdout = await exec(args, timeout)
    try {
      return JSON.parse(stdout)
    } catch {
      throw new Refusal(502, 'The flyarchive command did not answer with JSON: an old version may be installed.')
    }
  }
  /**
   * Решение по файлам очереди или карантина: пути уходят после разделителя, как данные, а не как ключи.
   * Один path — прежний вид запроса. Список paths — до 200 путей одной командой, ответ {"results": [...]} отдаётся как есть;
   * принятие списком идёт с --defer-index: перенос сразу, индексация потом. Стирание — только по одному файлу.
   */
  const decide = (area, actions) => async (body) => {
    const { path: file, paths, action } = objectOf(body)
    if (!actions.includes(action)) throw new Refusal(400, `action: ${actions.join(' or ')}`)
    if (paths === undefined) {
      return call([area, action, '--json', '--', filePath(file)], area === 'queue' && action === 'accept' ? LONG_TIMEOUT_MS : undefined)
    }
    if (file !== undefined) throw new Refusal(400, 'give either path or paths')
    if (action === 'delete') throw new Refusal(400, 'deleting works on one file at a time, with the path key')
    const flags = area === 'queue' && action === 'accept' ? ['--defer-index', '--json'] : ['--json']
    return call([area, action, ...flags, '--', ...filePaths(paths)], LONG_TIMEOUT_MS) // до 200 файлов: хэши и перенос занимают время
  }
  return {
    inboxStatus: () => call(['inbox', 'status', '--json']),
    async setInbox(body) {
      const names = Object.keys(objectOf(body))
      if (names.length === 0) throw new Refusal(400, 'nothing to change')
      if (names.includes('path')) {
        // смена папки — отдельный запрос: только path и confirm, всё до вызова команды; каталоги из плагина не создаются
        if (names.some((key) => key !== 'path' && key !== 'confirm')) throw new Refusal(400, 'changing the inbox folder goes in its own request: only path and confirm')
        if (body.confirm !== true) throw new Refusal(400, 'changing the inbox folder needs confirmation')
        await exec(['inbox', 'set', '--path', inboxFolder(body.path), '--must-exist', '--json'])
        return call(['inbox', 'status', '--json'])
      }
      const args = ['inbox', 'set']
      for (const key of names) {
        if (!(key in SETTINGS)) throw new Refusal(400, `this setting cannot be changed here: ${key}`)
      }
      for (const [key, [valid, flag]] of Object.entries(SETTINGS)) {
        if (!(key in body)) continue
        const value = body[key]
        if (!valid(value)) throw new Refusal(400, `invalid value for ${key}`)
        args.push(flag, typeof value === 'boolean' ? (value ? 'on' : 'off') : String(value))
      }
      await exec(args)
      return call(['inbox', 'status', '--json'])
    },
    runInbox: () => call(['inbox', 'kick', '--json']),
    queue: async () => ({ items: await call(['queue', 'list', '--json']) }),
    decideQueue: decide('queue', ['accept', 'quarantine']),
    quarantine: async () => ({ items: await call(['quarantine', 'list', '--json']) }),
    decideQuarantine: decide('quarantine', ['delete', 'return']),
    async deleteDoc(body) {
      const { path: file, confirm } = objectOf(body)
      if (confirm !== true) throw new Refusal(400, 'deleting a document needs confirmation')
      return call(['doc', 'delete', '--yes', '--json', '--', filePath(file)], LONG_TIMEOUT_MS)
    },
    /** История пачек: страница от новой к старой. Значения проверены до вызова и уходят отдельными аргументами. */
    async batches(query) {
      const limit = single(query, 'limit')
      const before = single(query, 'before')
      if (limit !== null && !(/^[1-9][0-9]{0,2}$/.test(limit) && Number(limit) <= HISTORY_MAX)) {
        throw new Refusal(400, `limit: an integer from 1 to ${HISTORY_MAX}`)
      }
      const args = ['inbox', 'batches', '--json', '--limit', String(limit === null ? HISTORY_DEFAULT : Number(limit))]
      if (before !== null) args.push('--before', batchId(before, 'before'))
      return call(args)
    },
    async batch(query) {
      return call(['inbox', 'batch', batchId(single(query, 'id'), 'id'), '--json'])
    },
    /** Описание просмотра: ответ команды как есть. Страница в теле не нужна; если она есть, то негодная — отказ. */
    async previewShow(body) {
      const { area, file, members } = previewTarget(body)
      if (body.page !== undefined) pageNumber(body.page)
      return call(['preview', 'show', ...memberArgs(members), '--area', area, '--json', '--', file], PREVIEW_TIMEOUT_MS)
    },
    /** Страница просмотра: байты PNG. Что не начинается с подписи PNG, наружу не уходит. */
    async previewPage(body) {
      const { area, file, members } = previewTarget(body)
      const page = pageNumber(body.page)
      const bytes = await exec(['preview', 'page', ...memberArgs(members), '--area', area, '--', file, String(page)], PREVIEW_TIMEOUT_MS, runBinary)
      if (!Buffer.isBuffer(bytes) || bytes.length < PNG_SIGNATURE.length || !bytes.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE)) {
        throw new Refusal(502, 'The flyarchive command did not return a PNG.')
      }
      return bytes
    },
    async listTokens() {
      return { tokens: await call(['token', 'list', '--json']) }
    },
    async addToken(body) {
      if (typeof body !== 'object' || body === null || Array.isArray(body)) throw new Refusal(400, 'an object with a name field is required')
      const name = clientName(body.name)
      const level = body.level ?? 'read'
      if (!LEVELS.includes(level)) throw new Refusal(400, 'level: read (search and read) or full (also create files)')
      const days = body.days ?? null
      if (days !== null && !(Number.isInteger(days) && days >= 1 && days <= MAX_DAYS)) {
        throw new Refusal(400, `days: an integer from 1 to ${MAX_DAYS}, or none for no expiry`)
      }
      return call(['token', 'add', name, '--level', level, '--json', ...(days === null ? [] : ['--days', String(days)])])
    },
    async revokeToken(body) {
      if (typeof body !== 'object' || body === null || Array.isArray(body)) throw new Refusal(400, 'an object with a name field is required')
      const name = clientName(body.name)
      if (name === LOCAL_NAME) {
        throw new Refusal(400, 'this is the DSH service token: without it DSH loses the archive. Reissue it with: flyarchive token init-local')
      }
      return call(['token', 'revoke', name, '--json'])
    },
    async journal(query) {
      let n = JOURNAL_DEFAULT
      if (query.has('n')) {
        if (!/^[1-9][0-9]{0,9}$/.test(query.get('n'))) throw new Refusal(400, 'n: a positive integer number of records')
        n = Math.min(Number(query.get('n')), JOURNAL_MAX)
      }
      const args = ['journal', '--json', '-n', String(n)]
      if (query.has('client')) args.push('--client', clientName(query.get('client'), JOURNAL_CLIENT))
      return { records: await call(args) }
    },
  }
}

function json(status, data) {
  return Response.json(data, { status, headers: { 'cache-control': 'no-store' } })
}

/** Страница просмотра: PNG, который браузер не должен ни угадывать, ни исполнять. */
function pngResponse(bytes) {
  return new Response(bytes, {
    status: 200,
    headers: {
      'content-type': 'image/png',
      'x-content-type-options': 'nosniff',
      'content-security-policy': 'sandbox',
      'cache-control': 'no-store',
    },
  })
}

/** Читает тело запроса как JSON; негодное тело — отказ, а не падение. */
async function bodyOf(request) {
  try {
    return JSON.parse(await request.text())
  } catch {
    throw new Refusal(400, 'the request body is not JSON')
  }
}

/** Адреса раздела в виде, который принимает ctx.connection.fetch.register. */
export function routes(api) {
  const route = (routePath, handlers) => ({
    path: routePath,
    methods: Object.keys(handlers),
    requestBody: 'buffered',
    async fetch(request) {
      const handler = handlers[request.method]
      if (handler === undefined) return json(405, { error: `method ${request.method} is not supported here` })
      try {
        const result = await handler(request)
        return result instanceof Response ? result : json(200, result)
      } catch (error) {
        if (error instanceof Refusal) return json(error.status, { error: error.detail ?? error.message })
        return json(502, { error: 'The Archive section failed.' })
      }
    },
  })
  return [
    route(TOKENS_PATH, {
      GET: () => api.listTokens(),
      POST: async (request) => api.addToken(await bodyOf(request)),
    }),
    route(REVOKE_PATH, { POST: async (request) => api.revokeToken(await bodyOf(request)) }),
    route(JOURNAL_PATH, { GET: (request) => api.journal(new URL(request.url).searchParams) }),
    route(INBOX_PATH, { GET: () => api.inboxStatus(), POST: async (request) => api.setInbox(await bodyOf(request)) }),
    route(INBOX_RUN_PATH, { POST: () => api.runInbox() }),
    route(QUEUE_PATH, { GET: () => api.queue(), POST: async (request) => api.decideQueue(await bodyOf(request)) }),
    route(QUARANTINE_PATH, { GET: () => api.quarantine(), POST: async (request) => api.decideQuarantine(await bodyOf(request)) }),
    route(DELETE_PATH, { POST: async (request) => api.deleteDoc(await bodyOf(request)) }),
    route(BATCHES_PATH, { GET: (request) => api.batches(new URL(request.url).searchParams) }),
    route(BATCH_PATH, { GET: (request) => api.batch(new URL(request.url).searchParams) }),
    route(PREVIEW_PATH, { POST: async (request) => api.previewShow(await bodyOf(request)) }),
    route(PREVIEW_PAGE_PATH, { POST: async (request) => pngResponse(await api.previewPage(await bodyOf(request))) }),
  ]
}

/**
 * Подключает раздел к DSH. Адреса живут, пока жив плагин.
 * @param ctx - контекст Cordis с соединением DSH.
 * @param config - необязательный путь к команде: { command }.
 */
export function apply(ctx, config = {}) {
  const api = createApi({ command: defaultCommand(config ?? {}) })
  for (const route of routes(api)) ctx.effect(() => ctx.connection.fetch.register(route))
}
