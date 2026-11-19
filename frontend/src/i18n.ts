import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

import en from './locales/en.json'
import zh from './locales/zh.json'

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      zh: { translation: zh },
    },
    fallbackLng: 'zh',
    interpolation: {
      escapeValue: false,
    },
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
    },
  })

// A desktop window has a different private origin on every launch, so browser
// localStorage cannot carry its language preference across restarts. The web
// development build continues using the existing browser language detector.
let userLanguageChanges = 0
let desktopWriteQueue: Promise<void> = Promise.resolve()

export function changeAppLanguage(language: 'zh' | 'en') {
  userLanguageChanges += 1
  void i18n.changeLanguage(language)
  if (import.meta.env.VITE_DESKTOP_BUILD === '1') {
    // Serialize rapid toggles so an older request cannot win a write race.
    desktopWriteQueue = desktopWriteQueue.then(async () => {
      await fetch('/api/desktop/preferences', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language }),
      })
    }).catch(() => { /* A preference write must not block the application. */ })
  }
}

if (import.meta.env.VITE_DESKTOP_BUILD === '1') {
  const revisionAtRequest = userLanguageChanges
  void fetch('/api/desktop/preferences')
    .then(r => r.ok ? r.json() : null)
    .then((data: { language?: unknown } | null) => {
      // A quick manual change always wins over a slower initial read.
      if (userLanguageChanges === revisionAtRequest && (data?.language === 'zh' || data?.language === 'en')) {
        void i18n.changeLanguage(data.language)
      }
    }).catch(() => { /* The normal language detector remains available. */ })
}

export default i18n
