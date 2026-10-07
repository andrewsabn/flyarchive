// Подставной архив и подставной контекст DSH для тестов вкладки. Образцы выдуманные.
import { reply } from './kit.mjs'

export const BATCH = '20260101-120000'

/** Состояние входящих так, как его отдаст flyarchive inbox status --json. */
export const STATUS = {
  inbox: '/home/x/inbox', returned: '/home/x/returned', home: '/home/x/flyarchive', period: 30, llm: true, cloud: false,
  threshold: 20, max_gb: 2, max_files: 5000, max_ratio: 100, depth: 3, timer: true, waiting: 3, queue: 0, quarantine: 0, pending: 0,
  attention: 0, problems: 0, progress: null, last_batch: { batch: BATCH, time: '2026-10-04T11:18:40Z', accept: 11, review: 2 },
}

/** Сообщение системы как объект {code, args, text}: text — русский, как у сервера. */
export const messageOf = (code, args, text) => ({ code, args, text })

/** Запись очереди или карантина, как её отдаёт review._list. */
export function item(area, name, over = {}) {
  const folder = area === 'queue' ? 'очередь' : 'карантин'
  return {
    path: `${folder}/${BATCH}/${name}`, name, batch: BATCH, size: 2048, score: 55, sha256: 'ab'.repeat(32), type: 'txt',
    time: '2026-10-04T11:18:40Z', reason: '', checked_by: 'rules',
    findings: [{ rule: 'prompt_injection', level: 'HIGH', where: 'line 2', quote: 'ignore all previous instructions' }],
    ...over,
  }
}

export const progressOf = (over = {}) => ({
  state: 'running', batch: BATCH, stage: 'intake', done: 12, total: 40, current: 'report.txt',
  started: '2026-10-04T11:18:00Z', updated: '2026-10-04T11:18:30Z', pid: 4242, ...over,
})

// ── история пачек ──────────────────────────────────
/** Номера пачек от новой к старой: 20261004-235900, 20261004-235800, … (не больше суток). */
export const batchIds = (n) => Array.from({ length: n }, (_, i) => {
  const minutes = 1439 - i
  return `20261004-${String(Math.floor(minutes / 60)).padStart(2, '0')}${String(minutes % 60).padStart(2, '0')}00`
})

/** Сводка пачки, как её отдаёт flyarchive inbox batches --json. */
export const batchOf = (id, over = {}) => ({
  id, time: '2026-10-04T18:11:56Z', seconds: 65, counts: { accept: 12, review: 2 }, problems: 0, files: 14, ...over,
})

/** Запись файла пачки, как её отдаёт flyarchive inbox batch ID --json: квитанция приёмки плюс решение владельца и место. */
export const fileOf = (name, over = {}) => ({
  name, sha256: 'cd'.repeat(32), size: 2048, type: 'txt', decision: 'accept', score: 5, findings: [], reason: '', checked_by: 'rules',
  path: `входящие/${BATCH}/${name}`, batch: BATCH, time: '2026-10-04T18:11:56Z', date: '2026-09-30', date_source: 'файл', verified: true,
  indexed: true, archive: null, archive_sha256: null, inner: null, duplicate_of: null, returned: null,
  decided: null, decided_at: null, location: 'corpus', ...over,
})

/** Подробности пачки: счётчики считаются по файлам. */
export function detailOf(id, files, over = {}) {
  const counts = {}
  for (const f of files) counts[f.decision] = (counts[f.decision] ?? 0) + 1
  return { id, time: '2026-10-04T18:11:56Z', seconds: 65, counts, problems: [], files, ...over }
}

// ── просмотр ───────────────────────────────────────
/** Ключ описания в подставном архиве: область, путь и цепочка вложений. */
export const previewKey = (area, path, member = []) => `${area}|${path}|${member.join('.')}`

/** Сведения о файле, как их кладёт в ответ flyarchive preview show. Имя по умолчанию — последняя часть пути. */
export const metaOf = (name, over = {}) => ({
  name, type: 'txt', size: 2048, sha256: 'ef'.repeat(32), batch: BATCH, origin: { archive: null, inner: null }, ...over,
})
export const textPreview = (name, text, over = {}) => ({ kind: 'text', meta: metaOf(name), text, truncated: false, ...over })
export const pagesPreview = (name, pages, shown = pages, over = {}) => ({ kind: 'pages', meta: metaOf(name, { type: 'pdf' }), pages, shown, ...over })
export const imagePreview = (name, over = {}) => ({ kind: 'image', meta: metaOf(name, { type: 'png' }), ...over })
export const mediaPreview = (name, over = {}) => ({ kind: 'media', meta: metaOf(name, { type: 'mp4' }), media: { seconds: 65, width: 1920, height: 1080 }, ...over })
export const listingPreview = (name, listing, over = {}) => ({ kind: 'listing', meta: metaOf(name, { type: 'zip' }), listing, truncated: false, ...over })
export const nonePreview = (name, note, over = {}) => ({ kind: 'none', meta: metaOf(name, { type: 'exe' }), note, ...over })
export const attachmentOf = (name, member, over = {}) => ({ name, size: 1536, type: 'pdf', member, ...over })
export const mailPreview = (name, attachments = [], over = {}) => ({
  kind: 'mail', meta: metaOf(name, { type: 'eml' }),
  mail: { from: 'ann@example.test', to: ['bob@example.test', 'cy@example.test'], cc: [], date: '2026-09-30T08:15:00Z', subject: 'Quarterly numbers', text: 'Hello,\nsee the attachments.', attachments },
  ...over,
})

/** Ответ-картинка: у настоящего fetch тело читается blob()-ом, а json() на PNG падает. */
export const png = (blob) => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('PNG is not JSON') }, blob: async () => blob })

const REAL_URLS = { create: URL.createObjectURL, revoke: URL.revokeObjectURL }

/**
 * Подставные адреса объектов: URL.createObjectURL и URL.revokeObjectURL заменены записью. live — адреса, которые созданы
 * и ещё не освобождены; log — всё по порядку. restore() возвращает настоящие.
 */
export function fakeObjectUrls() {
  const live = new Set()
  const log = []
  let counter = 0
  URL.createObjectURL = (blob) => {
    const url = `blob:fake/${++counter}`
    live.add(url)
    log.push({ op: 'create', url, blob })
    return url
  }
  URL.revokeObjectURL = (url) => {
    log.push({ op: 'revoke', url })
    live.delete(url)
  }
  return {
    live, log,
    created: () => log.filter((e) => e.op === 'create'),
    revoked: () => log.filter((e) => e.op === 'revoke').map((e) => e.url),
    /** Страница, которой принадлежит адрес: подставной blob несёт номер. */
    pageOf: (url) => log.find((e) => e.op === 'create' && e.url === url)?.blob?.page,
    restore() {
      URL.createObjectURL = REAL_URLS.create // настоящие, как они были при загрузке файла: порядок снятия нескольких подмен не важен
      URL.revokeObjectURL = REAL_URLS.revoke
    },
  }
}

/**
 * Подставной архив по ту сторону адресов плагина. Списки и счётчики живые: решение переносит записи.
 * on['POST api/…'] подменяет ответ целиком (ключ — метод и адрес без запроса; для адресов с запросом годится и полный);
 * rejects[путь] — отказ по одному пути; offline — обрыв связи.
 * История: batches — сводки от новой к старой, details — подробности по номеру пачки; addBatch кладёт новую пачку наверх
 * и переставляет last_batch в состоянии; batchCalls() и detailCalls() — что у архива спрашивали.
 * Просмотр: previews[previewKey(area, path, member)] — описание или функция (body, s) => описание | готовый ответ; без записи
 * файл «без просмотра» (kind none с английским пояснением). Страница отдаётся подставным blob {page, path, member};
 * previewCalls() и pageCalls() — тела запросов.
 */
export function fakeArchive(initial = {}) {
  const s = { status: {}, queue: [], quarantine: [], progress: null, offline: false, on: {}, rejects: {}, calls: [], batches: [], details: {}, previews: {}, tokens: [], records: [], ...initial }
  s.queue = [...s.queue] // решения переносят записи; образцы теста остаются целыми
  s.quarantine = [...s.quarantine]
  s.batches = [...s.batches]
  s.details = { ...s.details }
  s.tokens = s.tokens.map((token) => ({ ...token }))
  s.view = () => ({ ...STATUS, queue: s.queue.length, quarantine: s.quarantine.length, attention: s.queue.length + s.quarantine.length, ...s.status, progress: s.progress })
  s.count = (route, method = 'GET') => s.calls.filter((c) => c.route === route && c.method === method).length
  s.posts = (route) => s.calls.filter((c) => c.route === route && c.method === 'POST')
  const gets = (base) => s.calls.filter((c) => c.method === 'GET' && c.route.split('?')[0] === base)
  const query = (c) => new URLSearchParams(c.route.split('?')[1] ?? '')
  s.batchCalls = () => gets('api/flyarchive.batches').map((c) => ({ limit: query(c).get('limit'), before: query(c).get('before'), route: c.route }))
  s.detailCalls = () => gets('api/flyarchive.batch').map((c) => query(c).get('id'))
  s.previewCalls = () => s.calls.filter((c) => c.method === 'POST' && c.route === 'api/flyarchive.preview').map((c) => c.body)
  s.pageCalls = () => s.calls.filter((c) => c.method === 'POST' && c.route === 'api/flyarchive.preview.page').map((c) => c.body)
  s.addBatch = (summary, detail) => {
    s.batches.unshift(summary)
    if (detail) s.details[summary.id] = detail
    s.status.last_batch = { batch: summary.id, time: summary.time, ...summary.counts }
  }
  s.doFetch = async (route, init) => {
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(init.body) : null
    s.calls.push({ route, method, body })
    if (s.offline) throw new TypeError('Failed to fetch')
    const [base, search = ''] = route.split('?')
    const custom = s.on[`${method} ${route}`] ?? s.on[`${method} ${base}`]
    if (custom) return custom(body, s)
    if (method === 'GET' && route === 'api/flyarchive.inbox') return reply(200, s.view())
    if (method === 'GET' && base === 'api/flyarchive.batches') {
      const params = new URLSearchParams(search)
      const limit = Number(params.get('limit') ?? 30)
      const rest = params.has('before') ? s.batches.slice(s.batches.findIndex((b) => b.id === params.get('before')) + 1) : s.batches
      return reply(200, JSON.parse(JSON.stringify({ batches: rest.slice(0, limit), more: rest.length > limit })))
    }
    if (method === 'GET' && base === 'api/flyarchive.batch') {
      const detail = s.details[new URLSearchParams(search).get('id')]
      return detail ? reply(200, JSON.parse(JSON.stringify(detail))) : reply(409, { error: { code: null, args: {}, text: 'Нет такой пачки' } })
    }
    if (method === 'POST' && route === 'api/flyarchive.preview') {
      const known = s.previews[previewKey(body.area, body.path, body.member)]
      const answer = typeof known === 'function' ? await known(body, s) : known
      if (answer === undefined) {
        const parts = String(body.path).split('/')
        return reply(200, nonePreview(parts.at(-1), { code: null, args: {}, text: 'No preview for this test file' }, { meta: metaOf(parts.at(-1), { batch: parts[1] ?? BATCH }) }))
      }
      return answer.ok !== undefined ? answer : reply(200, JSON.parse(JSON.stringify(answer)))
    }
    if (method === 'POST' && route === 'api/flyarchive.preview.page') return png({ page: body.page, area: body.area, path: body.path, member: body.member })
    if (method === 'POST' && route === 'api/flyarchive.inbox.run') return reply(200, { started: true })
    // раздел настроек: настройка и смена папки, токены, журнал, удаление документа
    if (method === 'POST' && route === 'api/flyarchive.inbox') {
      if ('path' in body) s.status.inbox = body.path
      else Object.assign(s.status, body)
      return reply(200, s.view())
    }
    if (method === 'GET' && route === 'api/flyarchive.tokens') return reply(200, { tokens: JSON.parse(JSON.stringify(s.tokens)) })
    if (method === 'POST' && route === 'api/flyarchive.tokens') {
      const expires = body.days ? '2027-01-02T09:00:00Z' : null
      s.tokens.push({ name: body.name, level: body.level, created: '2026-10-04T09:00:00Z', expires, last_used: null, revoked: null, state: 'действует' })
      return reply(200, { name: body.name, level: body.level, token: 'ba_' + 'q'.repeat(43), expires })
    }
    if (method === 'POST' && route === 'api/flyarchive.tokens.revoke') {
      for (const token of s.tokens) if (token.name === body.name && token.state === 'действует') Object.assign(token, { state: 'отозван', revoked: '2026-10-04T10:00:00Z' })
      return reply(200, { name: body.name, revoked: true })
    }
    if (method === 'GET' && base === 'api/flyarchive.journal') {
      const params = new URLSearchParams(search)
      const client = params.get('client')
      return reply(200, { records: JSON.parse(JSON.stringify(s.records.filter((r) => client === null || r.client === client))) })
    }
    if (method === 'POST' && route === 'api/flyarchive.doc.delete') return reply(200, { path: body.path, rows: 2, moved_to: 'deleted/2026-10-04/x' })
    if (method === 'GET' && route === 'api/flyarchive.queue') return reply(200, { items: s.queue })
    if (method === 'GET' && route === 'api/flyarchive.quarantine') return reply(200, { items: s.quarantine })
    if (method === 'POST' && (route === 'api/flyarchive.queue' || route === 'api/flyarchive.quarantine')) {
      const area = route.endsWith('.queue') ? 'queue' : 'quarantine'
      if (!Array.isArray(body.paths)) { // прежний вид запроса: один путь, например стирание
        s[area] = s[area].filter((i) => i.path !== body.path)
        return reply(200, { path: body.path })
      }
      const results = body.paths.map((path) => {
        if (s.rejects[path]) return { path, ok: false, error: s.rejects[path] }
        const at = s[area].findIndex((i) => i.path === path)
        if (at < 0) return { path, ok: false, error: { code: null, args: {}, text: 'файла уже нет' } }
        const [moved] = s[area].splice(at, 1)
        if (area === 'queue' && body.action === 'quarantine') s.quarantine.push({ ...moved, path: moved.path.replace('очередь/', 'карантин/') })
        return { path, ok: true }
      })
      return reply(200, { results })
    }
    return reply(404, { error: `нет такого адреса: ${route}` })
  }
  return s
}

const fill = (template, params = {}) => String(template).replace(/\{(\w+)\}/g, (_, name) => String(params[name]))

/**
 * Подставной контекст DSH. Службы можно убрать: without: ['sidebarRightTabs'].
 * Записывает, что и под какими ключами зарегистрировано; disposers снимают всё, как снимает DSH вместе с плагином.
 */
export function fakeCtx({ without = [] } = {}) {
  const dicts = {}
  let onScreen // сеанс на экране: undefined, пока его нет (стартовый экран)
  const watchers = new Set()
  const live = { tabs: [], slots: [], dictionaries: [] }
  const ctx = {
    effects: [],
    missingKeys: [],
    live,
    effect(make, label) {
      const dispose = make()
      ctx.effects.push({ label, dispose })
      return dispose
    },
    dispose: () => ctx.effects.splice(0).reverse().forEach((e) => e.dispose?.()),
    slots: {
      inject: (slot, make) => make(),
      register(options, component) {
        const entry = { options, component }
        live.slots.push(entry)
        return () => live.slots.splice(live.slots.indexOf(entry), 1)
      },
    },
    locale: {
      register(namespace, dictionary) {
        dicts[namespace] = dictionary
        live.dictionaries.push(namespace)
        return () => live.dictionaries.splice(live.dictionaries.indexOf(namespace), 1)
      },
      bind: (namespace) => (key, params) => {
        const en = dicts[namespace]?.en
        if (!en || !(key in en)) ctx.missingKeys.push(key)
        return fill(en?.[key] ?? key, params)
      },
    },
    opened: [], // что просили открыть через службу правой панели
    session(id) { // сеанс появился на экране (id) или ушёл (undefined)
      onScreen = id
      for (const fn of [...watchers]) fn()
    },
    sidebarRight: {
      openTab(...args) {
        if (onScreen === undefined) throw new Error('no session on screen')
        ctx.opened.push(args)
      },
      mounted: {
        getSnapshot: () => onScreen,
        subscribe: (fn) => (watchers.add(fn), () => watchers.delete(fn)),
      },
    },
    sidebarRightTabs: {
      register(definition) {
        if (live.tabs.some((t) => t.id === definition.id)) throw new Error(`id ${definition.id} уже занят`)
        live.tabs.push(definition)
        return () => live.tabs.splice(live.tabs.indexOf(definition), 1)
      },
    },
  }
  ctx.dictionaries = dicts
  for (const name of without) delete ctx[name]
  return ctx
}

/** Подставной t: подписи из словаря en, ключ без подписи записывается в missing. */
export function translator(en) {
  const missing = []
  const t = (key, params) => {
    if (!(key in en)) missing.push(key)
    return fill(en[key] ?? key, params)
  }
  t.missing = missing
  return t
}

/** Нужная запись слота по имени. */
export const slotNamed = (ctx, name) => ctx.live.slots.filter((s) => s.options.name === name)
