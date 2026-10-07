// Общая обвязка тестов половины для браузера.
// Отрисовка проверяется маленьким подставным React: функции-компоненты вызываются, разметка собирается в строку.
// Настоящего React в установке DSH отдельным пакетом нет (он внутри сборки страницы), поэтому в настоящем
// DSH раздел проверяется руками — см. каталог тестов.
import assert from 'node:assert/strict'

process.env.TZ = 'UTC'

export const mini = {
  createElement: (type, props, ...children) => ({ type, props: { ...(props ?? {}), children: children.flat(Infinity) } }),
  useState: (initial) => [typeof initial === 'function' ? initial() : initial, () => {}],
  useEffect: () => {},
  useCallback: (fn) => fn,
  useMemo: (make) => make(),
  useRef: (initial) => ({ current: initial }),
  useSyncExternalStore: (_subscribe, getSnapshot) => getSnapshot(),
}
const escape = (text) => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;')

/** Первая отрисовка в строку: без эффектов и без повторных отрисовок. */
export function html(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (Array.isArray(node)) return node.map(html).join('')
  if (typeof node === 'string' || typeof node === 'number') return escape(String(node))
  if (typeof node.type === 'function') return html(node.type(node.props))
  const { children, className, key, ...rest } = node.props
  assert.equal(rest.dangerouslySetInnerHTML, undefined, 'разметка из данных не вставляется')
  let attrs = className ? ` class="${escape(className)}"` : ''
  for (const [name, value] of Object.entries(rest)) {
    if (typeof value === 'function' || value === undefined || value === null || value === false) continue
    attrs += value === true ? ` ${name}` : ` ${name}="${escape(String(value))}"`
  }
  return `<${node.type}${attrs}>${html(children)}</${node.type}>`
}

let counter = 0
const CHUNK = /^client\.[A-Za-z0-9][A-Za-z0-9._-]*\.js$/ // как у загрузчика DSH: соседи client.<имя>.js

/**
 * Загружает модуль плагина так, как это делает загрузчик DSH.
 * Синхронный require пускает только React и модули, которые подставил тест (modules: id → модуль, либо Error — тогда require
 * бросает его, как загрузчик бросает на модуль, которого нет); require.async отдаёт соседей client.<имя>.js и проверяет
 * их регистрацию: тот же id, что у владельца, и поле chunk с именем файла.
 */
export async function load(React = mini, { chunksFail = false, modules = {} } = {}) {
  let definition
  globalThis.window = { __ModuleLoader__: { load: (d) => { definition = d } } }
  await import(`../lib/client.js?load=${process.pid}-${counter++}-${Math.random()}`)
  const owner = definition
  const requested = []
  const asyncRequested = []
  const chunks = new Map()
  const inflight = new Set()
  const require = (id) => {
    requested.push(id)
    if (id === 'react') return React
    if (Object.prototype.hasOwnProperty.call(modules, id)) {
      if (modules[id] instanceof Error) throw modules[id]
      return modules[id]
    }
    throw new Error(`модуль ${id} плагину не положен`)
  }
  require.async = (spec) => {
    asyncRequested.push(spec)
    const work = (async () => {
      if (chunksFail) throw new Error(`соседний файл ${spec} не пришёл`) // сеть подвела: проверка работы без словаря
      if (!spec.startsWith('./') || !CHUNK.test(spec.slice(2))) throw new Error(`негодный запрос соседнего файла ${spec}`)
      if (chunks.has(spec)) return chunks.get(spec)
      let chunk
      globalThis.window = { __ModuleLoader__: { load: (d) => { chunk = d } } }
      await import(`../lib/${spec.slice(2)}?load=${process.pid}-${counter++}-${Math.random()}`)
      assert.ok(chunk, `${spec}: файл не вызвал __ModuleLoader__.load`)
      assert.equal(chunk.id, owner.id, `${spec}: id соседа должен совпадать с id владельца`)
      assert.equal(chunk.chunk, spec.slice(2), `${spec}: в регистрации нужно поле chunk с именем файла`)
      const exports = chunk.factory((id) => { throw new Error(`сосед ${spec} самодостаточен, а просит ${id}`) })
      chunks.set(spec, exports)
      return exports
    })()
    inflight.add(work)
    const forget = () => inflight.delete(work)
    work.then(forget, forget)
    return work
  }
  /** Ждёт, пока догрузятся все соседние файлы, которые плагин успел запросить. */
  const idle = async () => {
    while (inflight.size > 0) await Promise.allSettled([...inflight])
  }
  const exports = definition.factory(require)
  return { definition, exports, requested, asyncRequested, require, idle }
}

/** Подставной fetch: запоминает обращения и отвечает по сценарию. */
export function recorder(answer) {
  const calls = []
  const doFetch = async (route, init) => {
    calls.push([route, init])
    return answer(route, init)
  }
  return { calls, doFetch }
}

export const reply = (status, data) => ({ ok: status >= 200 && status < 300, status, json: async () => data })

/** Достаёт из дерева первый элемент, подходящий под условие: чтобы нажать кнопку или изменить поле. */
export function find(node, match) {
  if (node === null || node === undefined || typeof node !== 'object') return null
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = find(child, match)
      if (hit) return hit
    }
    return null
  }
  if (typeof node.type === 'function') return find(node.type(node.props), match)
  if (match(node)) return node
  return find(node.props.children, match)
}

export const text = (node) => (node === null || node === undefined || typeof node === 'boolean' ? ''
  : Array.isArray(node) ? node.map(text).join('') : typeof node === 'object' ? text(node.props.children) : String(node))

// ── малый рисовальщик с состоянием ──────────────────────────────
// Вкладке нужен экран, который меняется: галочки, подтверждения, ход разбора. Свой код, без зависимостей.
// Есть useState, useEffect, useRef, useMemo, useCallback, useSyncExternalStore. Перерисовывается всё дерево целиком
// (по микрозадаче после setState); экземпляры компонентов сохраняются по ключу или месту, поэтому состояние живёт
// между перерисовками, а эффекты запускаются и снимаются по зависимостям, как в настоящем React.
const FRAGMENT = Symbol('Fragment')
const tick = () => new Promise((resolve) => setImmediate(resolve))

/** Даёт обещаниям, уже готовым к выполнению, отработать: подставные ответы сети приходят сразу. */
export async function flush(rounds = 3) {
  for (let i = 0; i < rounds; i++) await tick()
}

const walk = (node, visit) => {
  if (node === null || node === undefined || typeof node !== 'object') return
  if (Array.isArray(node)) return node.forEach((child) => walk(child, visit))
  visit(node)
  walk(node.props.children, visit)
}

export function stateful() {
  let current = null // компонент, который рисуется сейчас
  let hookIndex = 0

  const slotOf = (init) => {
    assert.ok(current, 'хук вызван вне компонента')
    const i = hookIndex++
    if (!(i in current.hooks)) current.hooks[i] = init()
    return [current.hooks[i], current]
  }
  const changed = (slot, deps) => !slot.deps || !deps || deps.some((d, k) => !Object.is(d, slot.deps[k]))

  const React = {
    Fragment: FRAGMENT,
    createElement: (type, props, ...children) => ({ type, props: { ...(props ?? {}), children: children.flat(Infinity) } }),
    useState(initial) {
      const [slot, inst] = slotOf(() => ({ value: typeof initial === 'function' ? initial() : initial }))
      slot.set ??= (next) => {
        const value = typeof next === 'function' ? next(slot.value) : next
        if (Object.is(value, slot.value)) return
        slot.value = value
        inst.root.schedule()
      }
      return [slot.value, slot.set]
    },
    useRef(initial) {
      const [slot] = slotOf(() => ({ ref: { current: initial } }))
      return slot.ref
    },
    useMemo(make, deps) {
      const [slot] = slotOf(() => ({}))
      if (changed(slot, deps)) {
        slot.value = make()
        slot.deps = deps
      }
      return slot.value
    },
    useCallback: (fn, deps) => React.useMemo(() => fn, deps),
    useEffect(run, deps) {
      const [slot, inst] = slotOf(() => ({}))
      slot.inst = inst
      if (changed(slot, deps)) {
        slot.deps = deps
        slot.run = run
        inst.root.effects.push(slot)
      }
    },
    useSyncExternalStore(subscribe, getSnapshot) {
      const value = getSnapshot()
      const latest = React.useRef(value)
      latest.current = value
      const root = current.root
      React.useEffect(() => {
        const check = () => {
          if (!Object.is(getSnapshot(), latest.current)) root.schedule()
        }
        const stop = subscribe(check)
        check() // между отрисовкой и подпиской состояние могло уйти вперёд
        return stop
      }, [subscribe, getSnapshot])
      return value
    },
  }

  const keyOf = (node) => (node && typeof node === 'object' && !Array.isArray(node) ? node.props?.key : undefined)

  function unmount(r) {
    if (!r) return
    if (r.kind === 'comp') {
      if (r.unmounted) return
      r.unmounted = true
      for (const slot of r.hooks) {
        if (typeof slot.cleanup === 'function') slot.cleanup()
        slot.cleanup = undefined
      }
      unmount(r.child)
    } else if (r.kind === 'host') unmount(r.children)
    else if (r.kind === 'list') r.items.forEach(unmount)
  }

  function renderList(items, prevList, root) {
    const before = prevList ? prevList.items : []
    const byKey = new Map()
    for (const p of before) if (p && p.key !== undefined) byKey.set(p.key, p)
    const taken = new Set()
    const out = items.map((item, i) => {
      const key = keyOf(item)
      const prev = key !== undefined ? byKey.get(key) : (before[i] && before[i].key === undefined ? before[i] : undefined)
      if (prev) taken.add(prev)
      return render(item, prev, root)
    })
    for (const p of before) if (p && !taken.has(p)) unmount(p)
    return { kind: 'list', items: out }
  }

  function render(node, prev, root) {
    if (node === null || node === undefined || typeof node === 'boolean') {
      unmount(prev)
      return null
    }
    if (typeof node === 'string' || typeof node === 'number') {
      if (prev && prev.kind !== 'text') unmount(prev)
      return { kind: 'text', value: String(node) }
    }
    if (Array.isArray(node)) {
      if (prev && prev.kind !== 'list') unmount(prev)
      return renderList(node, prev && prev.kind === 'list' ? prev : null, root)
    }
    const { type, props } = node
    if (type === FRAGMENT) return render(props.children, prev, root)
    const reusable = Boolean(prev) && prev.type === type && prev.key === props.key
    if (prev && !reusable) unmount(prev)
    if (typeof type === 'function') {
      const inst = reusable ? prev : { kind: 'comp', type, key: props.key, hooks: [], root, child: null, unmounted: false }
      const saved = [current, hookIndex]
      current = inst
      hookIndex = 0
      let out
      try {
        out = type(props)
      } finally {
        ;[current, hookIndex] = saved
      }
      inst.child = render(out, inst.child, root)
      return inst
    }
    const { children, key, ...rest } = props
    const host = reusable ? prev : { kind: 'host', type, key, props: null, children: null }
    host.props = rest
    host.children = renderList(children, host.children, root)
    return host
  }

  const treeOf = (r) => {
    if (!r) return null
    if (r.kind === 'text') return r.value
    if (r.kind === 'comp') return treeOf(r.child)
    if (r.kind === 'list') return r.items.map(treeOf).flat(Infinity).filter((x) => x !== null)
    return { type: r.type, props: { ...r.props, children: treeOf(r.children) } }
  }

  /** Рисует элемент и возвращает ручку для проверок и нажатий. */
  function mount(element) {
    const root = {
      element, rendered: null, effects: [], scheduled: false, gone: false, burst: 0, resetting: false,
      schedule() {
        if (root.scheduled || root.gone) return
        root.scheduled = true
        queueMicrotask(() => {
          root.scheduled = false
          if (!root.gone) root.flush()
        })
      },
      flush() {
        if (++root.burst > 200) throw new Error('слишком много перерисовок подряд: экран не успокаивается')
        if (!root.resetting) {
          root.resetting = true
          setImmediate(() => {
            root.burst = 0
            root.resetting = false
          })
        }
        root.rendered = render(root.element, root.rendered, root)
        const batch = root.effects.splice(0)
        for (const slot of batch) {
          if (typeof slot.cleanup === 'function') slot.cleanup()
          slot.cleanup = undefined
        }
        for (const slot of batch) {
          if (slot.inst.unmounted) continue
          const result = slot.run()
          slot.cleanup = typeof result === 'function' ? result : undefined
        }
      },
    }
    root.flush()
    const handle = {
      tree: () => treeOf(root.rendered),
      html: () => html(handle.tree()),
      text: () => text(handle.tree()),
      find: (match) => find(handle.tree(), match),
      all(match) {
        const hits = []
        walk(handle.tree(), (n) => { if (match(n)) hits.push(n) })
        return hits
      },
      button: (label) => handle.find((n) => n.type === 'button' && text(n) === label),
      buttons: () => handle.all((n) => n.type === 'button'),
      /** Ждёт, пока экран успокоится после ответов сети и перерисовок. */
      async settle() {
        for (let quiet = 0; quiet < 3;) {
          await tick()
          quiet = root.scheduled ? 0 : quiet + 1
        }
      },
      /** Нажатие, как в браузере: выключенная кнопка не отзывается. Возвращает, дошло ли нажатие. */
      async click(node) {
        assert.ok(node, 'нечего нажимать: такого элемента на экране нет')
        if (node.props.disabled) return false
        node.props.onClick({ preventDefault() {}, stopPropagation() {} })
        await handle.settle()
        return true
      },
      async check(node, checked) {
        assert.ok(node, 'нет такого поля')
        node.props.onChange({ target: { checked } })
        await handle.settle()
      },
      rerender(next) {
        root.element = next
        root.flush()
      },
      unmount() {
        root.gone = true
        unmount(root.rendered)
        root.rendered = null
      },
    }
    return handle
  }
  return { React, mount }
}

/** Управляемые таймеры: время идёт только когда его двигает тест. */
export function createClock() {
  let now = 0
  let seq = 0
  const timers = new Map()
  const clock = {
    setTimeout(fn, ms = 0) {
      const id = ++seq
      timers.set(id, { at: now + ms, fn })
      return id
    },
    clearTimeout(id) {
      timers.delete(id)
    },
    now: () => now,
    /** Сколько мс до каждого ожидающего таймера, по возрастанию. */
    waits: () => [...timers.values()].map((t) => t.at - now).sort((a, b) => a - b),
    /** Двигает время, исполняя таймеры по порядку; после каждого даёт ответам сети дойти. */
    async advance(ms) {
      const end = now + ms
      await flush()
      for (;;) {
        let next = null
        for (const [id, t] of timers) {
          if (t.at <= end && (next === null || t.at < next.t.at || (t.at === next.t.at && id < next.id))) next = { id, t }
        }
        if (next === null) break
        timers.delete(next.id)
        now = Math.max(now, next.t.at)
        next.t.fn()
        await flush()
      }
      now = end
    },
  }
  return clock
}

/** Подставная видимость страницы: скрыть и показать. */
export function fakePage(hidden = false) {
  const listeners = new Set()
  const page = {
    hidden,
    isVisible: () => !page.hidden,
    subscribe(fn) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    set(value) {
      page.hidden = value
      for (const fn of [...listeners]) fn()
    },
    listeners: () => listeners.size,
  }
  return page
}
