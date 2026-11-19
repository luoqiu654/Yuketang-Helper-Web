// Exercise the real desktop i18n module with a delayed API, without a browser.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { stripTypeScriptTypes } = require('node:module')

const source = fs.readFileSync(path.join(__dirname, '../frontend/src/i18n.ts'), 'utf8')
  .replaceAll('import.meta.env.VITE_DESKTOP_BUILD', "'1'")
  .replace(/^import .*$/gm, '')
  .replace('export function changeAppLanguage', 'function changeAppLanguage')
  .replace('export default i18n', 'module.exports = { changeAppLanguage }')
const compiled = stripTypeScriptTypes(source)
const flush = () => new Promise(resolve => setImmediate(resolve))

function load(fetch) {
  const changes = []
  const fakeI18n = {
    use() { return this },
    init() { return this },
    changeLanguage(value) { changes.push(value); return Promise.resolve() },
  }
  const module = { exports: {} }
  vm.runInNewContext(compiled, { module, exports: module.exports, i18n: fakeI18n,
    initReactI18next: {}, LanguageDetector: {}, en: {}, zh: {}, fetch, Promise, JSON })
  return { ...module.exports, changes }
}

async function main() {
  let finishRead
  let finishFirstWrite
  const writes = []
  const api = load((url, options) => {
    if (!options) return new Promise(resolve => { finishRead = resolve })
    writes.push(JSON.parse(options.body).language)
    return writes.length === 1 ? new Promise(resolve => { finishFirstWrite = resolve }) : Promise.resolve({ ok: true })
  })
  api.changeAppLanguage('en')
  api.changeAppLanguage('zh')
  await flush()
  assert.deepEqual(api.changes, ['en', 'zh'])
  assert.deepEqual(writes, ['en']) // The second write waits for the first.
  finishRead({ ok: true, json: async () => ({ language: 'en' }) })
  finishFirstWrite({ ok: true })
  await flush()
  await flush()
  assert.deepEqual(api.changes, ['en', 'zh']) // A late read must not undo the user's choice.
  assert.deepEqual(writes, ['en', 'zh'])

  const restored = load(() => Promise.resolve({ ok: true, json: async () => ({ language: 'en' }) }))
  await flush()
  await flush()
  assert.deepEqual(restored.changes, ['en'])
  process.stdout.write('desktop i18n race checks passed\n')
}

main().catch(error => { console.error(error); process.exitCode = 1 })
