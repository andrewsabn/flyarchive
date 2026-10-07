// Проверка самого рисовальщика с состоянием и управляемых таймеров: на них стоят тесты вкладки.
import assert from 'node:assert/strict'
import test from 'node:test'

import { createClock, fakePage, flush, stateful } from './kit.mjs'

test('нажатие меняет состояние, экран перерисовывается', async () => {
  const { React, mount } = stateful()
  const h = React.createElement
  const Counter = () => {
    const [n, setN] = React.useState(0)
    return h('div', null, h('span', null, 'n=' + n), h('button', { onClick: () => setN((v) => v + 1) }, '+'))
  }
  const app = mount(h(Counter))
  assert.equal(app.text(), 'n=0+')
  await app.click(app.button('+'))
  await app.click(app.button('+'))
  assert.equal(app.text(), 'n=2+')
})

test('выключенная кнопка не отзывается, как в браузере', async () => {
  const { React, mount } = stateful()
  let pressed = 0
  const app = mount(React.createElement('button', { disabled: true, onClick: () => { pressed++ } }, 'x'))
  assert.equal(await app.click(app.button('x')), false)
  assert.equal(pressed, 0)
})

test('эффект снимается при смене зависимостей и при размонтировании', async () => {
  const { React, mount } = stateful()
  const log = []
  const Watch = ({ id }) => {
    React.useEffect(() => {
      log.push('on ' + id)
      return () => log.push('off ' + id)
    }, [id])
    return React.createElement('i', null, id)
  }
  const app = mount(React.createElement(Watch, { id: 1 }))
  app.rerender(React.createElement(Watch, { id: 1 }))
  app.rerender(React.createElement(Watch, { id: 2 }))
  app.unmount()
  assert.deepEqual(log, ['on 1', 'off 1', 'on 2', 'off 2'])
})

test('состояние элемента с ключом переживает перестановку, а убранный элемент размонтируется', async () => {
  const { React, mount } = stateful()
  const h = React.createElement
  const gone = []
  const Row = ({ name }) => {
    const [n, setN] = React.useState(0)
    React.useEffect(() => () => gone.push(name), [])
    return h('button', { onClick: () => setN(n + 1) }, name + n)
  }
  const list = (names) => h('div', null, names.map((name) => h(Row, { key: name, name })))
  const app = mount(list(['a', 'b']))
  await app.click(app.button('b0'))
  app.rerender(list(['b', 'a']))
  assert.deepEqual(app.buttons().map((b) => b.props.children.join('')), ['b1', 'a0'])
  app.rerender(list(['b']))
  assert.deepEqual(gone, ['a'])
})

test('внешнее хранилище: подписка, перерисовка по изменению, отписка при размонтировании', async () => {
  const { React, mount } = stateful()
  let value = 1
  const listeners = new Set()
  const store = { get: () => value, subscribe: (fn) => (listeners.add(fn), () => listeners.delete(fn)) }
  const View = () => React.createElement('b', null, 'v' + React.useSyncExternalStore(store.subscribe, store.get))
  const app = mount(React.createElement(View))
  assert.equal(app.text(), 'v1')
  value = 2
  listeners.forEach((fn) => fn())
  await app.settle()
  assert.equal(app.text(), 'v2')
  app.unmount()
  assert.equal(listeners.size, 0)
})

test('управляемые таймеры идут по порядку, вложенные успевают, снятые молчат', async () => {
  const clock = createClock()
  const log = []
  clock.setTimeout(() => log.push('b@' + clock.now()), 200)
  clock.setTimeout(() => {
    log.push('a@' + clock.now())
    clock.setTimeout(() => log.push('a2@' + clock.now()), 50)
  }, 100)
  const dropped = clock.setTimeout(() => log.push('dropped'), 150)
  clock.clearTimeout(dropped)
  await clock.advance(120)
  assert.deepEqual(log, ['a@100'])
  assert.deepEqual(clock.waits(), [30, 80])
  await clock.advance(1000)
  assert.deepEqual(log, ['a@100', 'a2@150', 'b@200'])
  assert.equal(clock.now(), 1120)
})

test('подставная страница: скрытие доходит до подписчиков', () => {
  const page = fakePage()
  const seen = []
  const stop = page.subscribe(() => seen.push(page.isVisible()))
  page.set(true)
  page.set(false)
  stop()
  page.set(true)
  assert.deepEqual(seen, [false, true])
})

test('flush даёт готовым обещаниям отработать', async () => {
  let done = false
  Promise.resolve().then(() => Promise.resolve()).then(() => { done = true })
  await flush()
  assert.equal(done, true)
})
