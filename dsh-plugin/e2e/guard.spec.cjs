'use strict'
/*
 * FR-91: защита от живого. Перед первым действием каждый тест проверяет, что адрес — порт стенда, а архив лежит во временном каталоге
 * стенда (fixture stand → Stand.assertStandOnly). Здесь проверка сама проверена: на подложных описаниях стенда она падает, не запустив ни команды,
 * ни браузера. Живое не затрагивается: подложные описания указывают на несуществующие каталоги, команда не вызывается.
 */
const os = require('node:os')
const path = require('node:path')
const { test, expect } = require('@playwright/test')
const kit = require('./kit.cjs')

const real = () => kit.connect().info // описание настоящего стенда этого прогона (оно же в файле stand.json)

test.describe('защита от живого', () => {
  test.beforeEach(({}, testInfo) => { // eslint-disable-line no-empty-pattern
    testInfo.skip(Boolean(process.env.BA_E2E_SKIP), process.env.BA_E2E_SKIP || '')
  })

  const forged = (patch) => new kit.Stand({ ...real(), ...patch })
  const failsWith = (stand, words) => {
    let message = ''
    try {
      stand.assertStandOnly()
    } catch (error) {
      message = error.message
    }
    expect(message, 'the check must refuse').toContain('not a stand')
    for (const word of words) expect(message).toContain(word)
  }

  test('проверка «это стенд» отказывает для порта живой службы', () => {
    const info = real()
    failsWith(forged({ ports: { ...info.ports, dsh: 8765 }, dshUrl: 'http://127.0.0.1:8765' }), ['live service port'])
    failsWith(forged({ ports: { ...info.ports, mcp: 8767 } }), ['live service port'])
  })

  test('проверка «это стенд» отказывает для чужого адреса: не петля и не порт стенда', () => {
    failsWith(forged({ dshUrl: 'http://192.0.2.10:' + real().ports.dsh }), ['DSH address'])
    failsWith(forged({ dshUrl: 'http://127.0.0.1:' + (real().ports.dsh + 1) }), ['DSH address'])
  })

  test('проверка «это стенд» отказывает для каталога вне временного и для каталога без метки стенда', () => {
    const home = os.homedir()
    failsWith(forged({ dir: home, archive: path.join(home, 'archive') }), ['stand temp directory'])
    failsWith(forged({ dir: path.join(os.tmpdir(), 'no-such-ba-e2e-dir'), archive: path.join(os.tmpdir(), 'no-such-ba-e2e-dir', 'archive') }), ['stand temp directory'])
    failsWith(forged({ dir: os.tmpdir(), archive: path.join(os.tmpdir(), 'archive') }), ['stand temp directory', 'marker'])
  })

  test('проверка «это стенд» отказывает, когда архив лежит вне каталога стенда', () => {
    failsWith(forged({ archive: path.join(os.homedir(), 'archive') }), ['archive is not inside'])
  })

  test('настоящий стенд проверку проходит', () => {
    kit.connect().assertStandOnly()
  })
})

test.describe('поиск секретов в следах теста', () => {
  const fs = require('node:fs')
  const { scan } = require('./leakscan.cjs')
  const fresh = () => fs.mkdtempSync(path.join(os.tmpdir(), 'ba-e2e-scan-'))

  test('проверка следов находит токен клиента и ссылку входа и не принимает за них затёртые значения', () => {
    const dir = fresh()
    try {
      const made = 'ba_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v' // 43 знака после ba_, как у настоящего токена; выдуманное значение
      expect(made.length).toBe(46)
      fs.writeFileSync(path.join(dir, 'clean.txt'), 'токен ba_*** и ссылка 127.0.0.1:3000/?token=*** затёрты; ba_short; ba_ и ещё слова\n')
      expect(scan(dir), 'masked values are not findings').toEqual([])
      fs.writeFileSync(path.join(dir, 'token.md'), `ошибка: ${made} в тексте\n`)
      fs.writeFileSync(path.join(dir, 'link.json'), JSON.stringify({ url: 'http://127.0.0.1:3097/' + '?tok' + 'en=abcdefghijklmnop0123456789' }))
      fs.writeFileSync(path.join(dir, 'exact.txt'), 'значение: Zq9XyW8vUt7sRq6pOn5mLk4jIh3gFe2d\n')
      const hits = scan(dir, ['Zq9XyW8vUt7sRq6pOn5mLk4jIh3gFe2d']).map((hit) => `${hit.kind} in ${hit.file}`).sort()
      expect(hits).toEqual(['a client token in token.md', 'a known secret value in exact.txt', 'a sign-in link in link.json'])
    } finally {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })
})
