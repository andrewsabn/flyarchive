// Стили плагина подключаются при его установке, а не при открытии вкладки: карточка в путеводителе панели
// рисуется раньше тела вкладки. Найдено в пробном DSH: карточка стояла без оформления, а её кнопка была точкой.
import assert from 'node:assert/strict'
import test from 'node:test'

import { fakeArchive, fakeCtx } from './kit-archive.mjs'
import { createClock, fakePage, load, stateful } from './kit.mjs'

function fakeDocument() {
  const tags = []
  return {
    tags,
    head: { appendChild: (tag) => { tags.push(tag) } },
    createElement: (name) => ({ name, dataset: {}, textContent: '' }),
    querySelector: (selector) => tags.find((tag) => selector.includes(JSON.stringify(tag.dataset.pluginCss))) ?? null,
  }
}

async function withDocument(run) {
  const saved = globalThis.document
  const doc = fakeDocument()
  globalThis.document = doc
  try {
    await run(doc)
  } finally {
    if (saved === undefined) delete globalThis.document
    else globalThis.document = saved
  }
}

const installOn = async (ctx) => {
  const { React } = stateful()
  const loaded = await load(React)
  loaded.exports.parts.install(ctx, { fetch: fakeArchive().doFetch, timers: createClock(), page: fakePage() })
}

test('стили подключены сразу при установке плагина', async () => {
  await withDocument(async (doc) => {
    await installOn(fakeCtx())
    assert.equal(doc.tags.length, 1)
    assert.equal(doc.tags[0].name, 'style')
    assert.equal(doc.tags[0].dataset.plugin, 'flyarchive-dsh-plugin')
    assert.match(doc.tags[0].textContent, /\.ba-guide\{/) // оформление карточки путеводителя
    assert.match(doc.tags[0].textContent, /\.ba-tab/)     // и тела вкладки
  })
})

test('повторная установка второй раз стили не добавляет', async () => {
  await withDocument(async (doc) => {
    await installOn(fakeCtx())
    await installOn(fakeCtx())
    assert.equal(doc.tags.length, 1)
  })
})

test('без служб правой панели стили всё равно подключены: они нужны и разделу настроек', async () => {
  await withDocument(async (doc) => {
    await installOn(fakeCtx({ without: ['sidebarRight', 'sidebarRightTabs'] }))
    assert.equal(doc.tags.length, 1)
  })
})
