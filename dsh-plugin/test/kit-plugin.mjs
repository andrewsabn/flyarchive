// Плагин целиком на подставном DSH: контекст, архив, время, страница, document, модули примитивов и react-dom.
// Для тестов раздела настроек, заголовка вкладки и всплывающего сообщения.
import { fakeArchive, fakeCtx, slotNamed } from './kit-archive.mjs'
import { createClock, fakePage, flush, load, stateful } from './kit.mjs'

// document, каким он был до подмен: done() возвращает именно его, в каком бы порядке ни снимались подмены
const ORIGINAL_DOCUMENT = Object.getOwnPropertyDescriptor(globalThis, 'document')

/** Подставной document: head и body принимают и отдают элементы, createElement делает их. */
export function fakeDocument() {
  const make = (name) => ({
    name, dataset: {}, children: [], parentNode: null, textContent: '',
    appendChild(child) {
      child.parentNode = this
      this.children.push(child)
      return child
    },
    removeChild(child) {
      this.children.splice(this.children.indexOf(child), 1)
      child.parentNode = null
      return child
    },
    remove() {
      if (this.parentNode) this.parentNode.removeChild(this)
    },
  })
  return { hidden: false, head: make('head'), body: make('body'), createElement: make, querySelector: () => null }
}

/**
 * Подставные модули DSH для всплывающего сообщения: Toast из примитивов и createRoot из react-dom/client.
 * Toast рисует текст и кнопки действий и записывает свои свойства (toasts); createRoot записывает корни (roots) и рисует в них тем же
 * малым рисовальщиком, так что нажатия доходят. root.unmounted — корень снят.
 */
export function fakeToastModules({ React, mount }) {
  const h = React.createElement
  const toasts = [] // свойства при каждой отрисовке
  const mounts = [] // тексты сообщений в момент появления: сколько раз сообщение показано (новый ключ — новое появление)
  const unmounts = []
  const roots = []
  const Toast = (props) => {
    toasts.push(props)
    React.useEffect(() => {
      mounts.push(props.text)
      return () => { unmounts.push(props.text) }
    }, [])
    return h('div', { role: 'alert', 'data-hold': props.holdMs },
      props.text, (props.actions ?? []).map((action) => h('button', { key: action.label, type: 'button', onClick: action.onClick }, action.label)))
  }
  const createRoot = (container) => {
    const root = { container, handle: null, unmounted: false, renders: 0 }
    roots.push(root)
    return {
      render(element) {
        root.renders += 1
        if (root.handle === null) root.handle = mount(element)
        else root.handle.rerender(element)
      },
      unmount() {
        root.unmounted = true
        if (root.handle !== null) root.handle.unmount()
      },
    }
  }
  return {
    Toast, createRoot, toasts, mounts, unmounts, roots,
    modules: { '@deepseek-ai/dsh-client-ui-primitives': { Toast }, 'react-dom/client': { createRoot } },
    /** Что сейчас нарисовано в последнем корне: текст сообщения и кнопки. */
    shown: () => (roots.length === 0 || roots.at(-1).handle === null ? '' : roots.at(-1).handle.text()),
    handle: () => (roots.length === 0 ? null : roots.at(-1).handle),
  }
}

/**
 * Плагин, установленный на подставной DSH, как его ставит DSH. Возвращает всё нужное тестам.
 * Параметры: without — службы, которых нет у DSH; hidden — страница скрыта; archive — начальное состояние подставного архива;
 * modules — подмена модулей (Error — require бросает его); toast: false — примитивов и react-dom/client у DSH нет совсем;
 * withDocument: false — страница без document (не браузер); doc — уже существующий подставной document (та же страница, плагин ставят второй раз).
 * done() возвращает прежний document (то, что было до первой подмены).
 */
export async function boot({ without = [], hidden = false, archive = {}, modules = {}, toast = true, chunksFail = false, withDocument = true, doc: sharedDoc } = {}) {
  const { React, mount } = stateful()
  const stubs = toast ? fakeToastModules({ React, mount }) : null
  const loaded = await load(React, { chunksFail, modules: { ...(stubs ? stubs.modules : {}), ...modules } })
  const doc = sharedDoc ?? fakeDocument()
  if (withDocument) globalThis.document = doc
  else delete globalThis.document
  const server = fakeArchive(archive)
  const clock = createClock()
  const page = fakePage(hidden)
  const ctx = fakeCtx({ without })
  const errors = []
  const savedError = console.error
  console.error = (...args) => errors.push(args)
  try {
    loaded.exports.parts.install(ctx, { fetch: server.doFetch, timers: clock, page })
    await clock.advance(0)
    await loaded.idle()
    await flush()
  } finally {
    console.error = savedError
  }
  const h = React.createElement
  const slot = (name) => {
    const entry = slotNamed(ctx, name)[0]
    return entry === undefined ? undefined : entry.component
  }
  return {
    ...loaded, React, mount, h, server, clock, page, ctx, doc, toast: stubs, errors, slot,
    /** Раздел настроек, смонтированный и успокоенный. */
    async settings(props = {}) {
      const app = mount(h(slot('settings.section'), props))
      await app.settle()
      return app
    },
    /** Тело вкладки. */
    async tab(props = {}) {
      const app = mount(h(slot('sidebar.right.pane.tab'), props))
      await app.settle()
      return app
    },
    /** Заголовок вкладки. */
    async title(props = {}) {
      const app = mount(h(slot('sidebar.right.pane.tab.title'), props))
      await app.settle()
      return app
    },
    done() {
      if (ORIGINAL_DOCUMENT) Object.defineProperty(globalThis, 'document', ORIGINAL_DOCUMENT)
      else delete globalThis.document
    },
  }
}
