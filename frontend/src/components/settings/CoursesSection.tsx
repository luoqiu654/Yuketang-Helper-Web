import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import MiddleTruncate from '../MiddleTruncate'
import Switch from '../Switch'
import type {
  ChoiceAnswerMode,
  CourseItem,
  CourseSettings,
  CoursesMap,
  NotificationSub,
  ShortAnswerMode,
} from '../../types'

// The three notification "channels" share the same NotificationSub schema;
// only the label and (for PushDeer) the gated-disabled rule differ.
const EMPTY_NOTIFICATION: NotificationSub = { enabled: false, signin: true, problem: true, call: true, danmu: true, red_packet: true }

const NOTIF_CHANNELS = [
  { field: 'notification', labelKey: 'settings.notification' },
  { field: 'voice_notification', labelKey: 'settings.voiceNotification' },
  { field: 'windows_notification', labelKey: 'settings.windowsNotification' },
  { field: 'pushdeer_notification', labelKey: 'settings.pushdeerNotification' },
] as const satisfies ReadonlyArray<{
  field: 'notification' | 'voice_notification' | 'windows_notification' | 'pushdeer_notification'
  labelKey: string
}>

interface CourseState extends CourseSettings {
  courseId: string
  name: string
  saveStatus: 'idle' | 'saving' | 'saved' | 'error'
}

// Drop the UI-only fields so the payload matches backend CourseSettingsModel.
function toPayload({ courseId, name, saveStatus, ...settings }: CourseState): CourseSettings {
  return settings
}

function fingerprint(c: CourseState): string {
  return JSON.stringify(toPayload(c))
}

function buildCourseStates(
  allCourses: CourseItem[],
  settings: CoursesMap,
  defaults: CourseSettings,
): CourseState[] {
  return allCourses.map((c) => ({
    ...(settings[c.classroom_id] ?? defaults),
    courseId: c.classroom_id,
    name: c.name,
    saveStatus: 'idle',
  }))
}

function NotificationSection({
  label,
  value,
  onChange,
  disabled = false,
  disabledTitle,
}: {
  label: string
  value: NotificationSub
  onChange: (v: NotificationSub) => void
  disabled?: boolean
  disabledTitle?: string
}) {
  const { t } = useTranslation()
  const subKeys: (keyof Omit<NotificationSub, 'enabled'>)[] = ['signin', 'problem', 'call', 'danmu', 'red_packet']
  const effectiveEnabled = !disabled && value.enabled

  return (
    <div className="notif-section">
      <div className="form-row">
        <label className="form-label">{label}</label>
        <span className="switch-wrap" data-tooltip={disabled && disabledTitle ? disabledTitle : undefined}>
          <Switch
            label={label}
            checked={effectiveEnabled}
            onChange={(enabled) => onChange({ ...value, enabled })}
            disabled={disabled}
          />
        </span>
      </div>
      {effectiveEnabled && (
        <div className="notif-suboptions">
          {subKeys.map((key) => (
            <label key={key} className="notif-sub-item">
              <input
                type="checkbox"
                checked={value[key]}
                onChange={(e) => onChange({ ...value, [key]: e.target.checked })}
              />
              <span>{t(`events.${key}`)}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

function QuizModeSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: { value: T; label: string; disabled?: boolean }[]
  onChange: (v: T) => void
}) {
  return (
    <div className="form-row">
      <label className="form-label">{label}</label>
      <select className="form-select" value={value} onChange={(e) => onChange(e.target.value as T)}>
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>
        ))}
      </select>
    </div>
  )
}

export default function CoursesSection({
  base,
  allCourses,
  settings,
  defaults,
  pushdeerKeyCount,
}: {
  base: string
  allCourses: CourseItem[]
  settings: CoursesMap
  defaults: CourseSettings
  pushdeerKeyCount: number
}) {
  const { t } = useTranslation()
  const [autoCapability, setAutoCapability] = useState<'loading' | 'available' | 'unavailable'>('loading')
  useEffect(() => {
    const controller = new AbortController()
    setAutoCapability('loading')
    fetch('/api/quiz/capabilities', { signal: controller.signal })
      .then(r => { if (!r.ok) throw new Error('capability unavailable'); return r.json() })
      .then(data => setAutoCapability(Array.isArray(data.answer_modes) && data.answer_modes.includes('ai_auto') ? 'available' : 'unavailable'))
      .catch(() => { if (!controller.signal.aborted) setAutoCapability('unavailable') })
    return () => controller.abort()
  }, [base])
  const [courses, setCourses] = useState(() => buildCourseStates(allCourses, settings, defaults))
  // Fingerprint of what the server holds for each course; Apply is offered
  // only while the card differs from it.
  const [saved, setSaved] = useState(() =>
    Object.fromEntries(courses.map((c) => [c.courseId, fingerprint(c)])),
  )
  // Initially collapse disabled courses. Keep their in-progress edits mounted.
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(courses.filter(c => !c.course_enabled).map(c => c.courseId)))
  const toggleCollapsed = (id: string) => setCollapsed(prev => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  type ApplyAll = { courseId: string; status: 'success' | 'error' }
  const [appliedAll, setAppliedAll] = useState<ApplyAll | null>(null)

  const isDirty = (course: CourseState) => fingerprint(course) !== saved[course.courseId]

  const updateField = <K extends keyof CourseSettings>(
    courseId: string,
    field: K,
    value: CourseSettings[K],
  ) => {
    setCourses((prev) =>
      prev.map((c) =>
        c.courseId === courseId ? { ...c, [field]: value, saveStatus: 'idle' } : c,
      ),
    )
  }

  const patchCourse = (courseId: string, patch: Partial<CourseState>) =>
    setCourses((prev) => prev.map((c) => (c.courseId === courseId ? { ...c, ...patch } : c)))

  const handleSave = async (course: CourseState) => {
    patchCourse(course.courseId, { saveStatus: 'saving' })
    try {
      const resp = await fetch(`${base}/courses/settings/${course.courseId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(toPayload(course)),
      })
      if (!resp.ok) throw new Error('Save failed')
      setSaved((prev) => ({ ...prev, [course.courseId]: fingerprint(course) }))
      setCollapsed(prev => { const next = new Set(prev); if (course.course_enabled) next.delete(course.courseId); else next.add(course.courseId); return next })
      patchCourse(course.courseId, { saveStatus: 'saved' })
      setTimeout(() => patchCourse(course.courseId, { saveStatus: 'idle' }), 2000)
    } catch {
      patchCourse(course.courseId, { saveStatus: 'error' })
    }
  }

  const applyToAll = async (source: CourseState) => {
    const payload = toPayload(source)
    const results = await Promise.all(
      courses.map((c) =>
        fetch(`${base}/courses/settings/${c.courseId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
          .then((r) => r.ok)
          .catch(() => false),
      ),
    )
    const applied = courses.filter((_, i) => results[i])
    const appliedIds = new Set(applied.map((c) => c.courseId))
    setCollapsed(prev => { const next = new Set(prev); for (const id of appliedIds) { if (payload.course_enabled) next.delete(id); else next.add(id) } return next })
    // A course that rejected the broadcast keeps its fields and is marked, so
    // the user can see which one(s) didn't take it.
    setCourses((prev) =>
      prev.map((c) =>
        appliedIds.has(c.courseId) ? { ...c, ...payload, saveStatus: 'idle' } : { ...c, saveStatus: 'error' },
      ),
    )
    setSaved((prev) => ({
      ...prev,
      ...Object.fromEntries(applied.map((c) => [c.courseId, fingerprint({ ...c, ...payload })])),
    }))
    setAppliedAll({ courseId: source.courseId, status: applied.length === courses.length ? 'success' : 'error' })
    setTimeout(() => setAppliedAll(null), 2000)
  }

  const resetToDefault = (courseId: string) => {
    patchCourse(courseId, { ...defaults, saveStatus: 'idle' })
  }

  const choiceModes: { value: ChoiceAnswerMode; label: string; disabled?: boolean }[] = [
    { value: 'ai_auto', label: t('settings.aiAuto'), disabled: autoCapability !== 'available' },
    { value: 'ai_suggest_confirm', label: t('settings.aiSuggestConfirm') },
    { value: 'notify_only', label: t('settings.notifyOnly') },
  ]
  const shortAnswerModes: { value: ShortAnswerMode; label: string; disabled?: boolean }[] = choiceModes

  const saveButtonClass = (s: CourseState['saveStatus']) =>
    s === 'saved' ? 'btn-success'
    : s === 'error' ? 'btn-danger'
    : 'btn-primary'

  return (
    <section className="settings-section">
      <h2 className="settings-section-title">{t('settings.title')}</h2>

      {!defaults.windows_notification && <p role="status" className="settings-card-hint">{t('settings.windowsNotificationRestart')}</p>}
      {courses.length === 0 ? (
        <div className="card">
          <p className="empty-message">{t('settings.noCourses')}</p>
        </div>
      ) : (
        <div className="course-grid">
          {courses.map((course) => (
            <div key={course.courseId} className="course-card">
              <div className="course-card-header course-card-header--collapsible">
                <h3 className="course-card-title"><MiddleTruncate text={course.name || course.courseId} /></h3>
                {!course.course_enabled && <span className="badge badge-gray">{t('settings.courseDisabled')}</span>}
                <button type="button" className="btn btn-sm btn-secondary" aria-expanded={!collapsed.has(course.courseId)}
                  aria-controls={`course-settings-${course.courseId}`} onClick={() => toggleCollapsed(course.courseId)}>
                  {collapsed.has(course.courseId) ? t('settings.expandCourse') : t('settings.collapseCourse')}
                </button>
              </div>

              <div id={`course-settings-${course.courseId}`} hidden={collapsed.has(course.courseId)}>
              <div className="course-card-body">
                <div className="settings-group">
                  <div className="form-row">
                    <label className="form-label">
                      {t('settings.courseEnabled')}
                      <span className="tooltip-trigger" data-tooltip={t('settings.courseEnabledDesc')}>?</span>
                    </label>
                    <Switch
                      label={t('settings.courseEnabled')}
                      checked={course.course_enabled}
                      onChange={(v) => updateField(course.courseId, 'course_enabled', v)}
                    />
                  </div>
                </div>

                <div className="settings-group">
                  <span className="settings-group-label">{t('settings.quizModes')}</span>
                  {autoCapability === 'unavailable' && <p role="status" className="settings-card-hint">{t('settings.aiAutoRestart')}</p>}
                  {([course.type1, course.type2, course.type3, course.type5].includes('ai_auto')) && <p className="settings-card-hint">{t('settings.aiAutoNotice')}</p>}
                  {([1, 2, 3] as const).map((n) => {
                    const field = `type${n}` as const
                    return (
                      <QuizModeSelect
                        key={n}
                        label={t(`events.problemType${n}`)}
                        value={course[field]}
                        options={choiceModes}
                        onChange={(v) => updateField(course.courseId, field, v)}
                      />
                    )
                  })}
                  <div className="form-row">
                    <label className="form-label">{t('events.problemType4')}</label>
                    <span className="badge badge-gray">{t('settings.reserved')}</span>
                  </div>
                  <QuizModeSelect
                    label={t('events.problemType5')}
                    value={course.type5}
                    options={shortAnswerModes}
                    onChange={(v) => updateField(course.courseId, 'type5', v)}
                  />
                </div>

                <div className="settings-group">
                  <div className="form-row">
                    <label className="form-label">
                      {t('settings.answerNearDeadline')}
                      <span className="tooltip-trigger" data-tooltip={t('settings.answerNearDeadlineDesc')}>?</span>
                    </label>
                    <Switch
                      label={t('settings.answerNearDeadline')}
                      checked={course.answer_near_deadline}
                      onChange={(v) => updateField(course.courseId, 'answer_near_deadline', v)}
                    />
                  </div>
                  <div className="form-row">
                    <label className="form-label">{t('settings.autoDanmu')}</label>
                    <Switch
                      label={t('settings.autoDanmu')}
                      checked={course.auto_danmu}
                      onChange={(v) => updateField(course.courseId, 'auto_danmu', v)}
                    />
                  </div>
                  {course.auto_danmu && (
                    <div className="form-row form-row-sub">
                      <label className="form-label">{t('settings.danmuThreshold')}</label>
                      <div className="input-with-unit">
                        <input
                          type="number"
                          className="form-input-number"
                          min={1}
                          max={99}
                          value={course.danmu_threshold}
                          onChange={(e) =>
                            updateField(course.courseId, 'danmu_threshold', Math.max(1, parseInt(e.target.value, 10) || 1))
                          }
                        />
                        <span className="input-unit">{t('settings.times')}</span>
                      </div>
                    </div>
                  )}
                  <div className="form-row">
                    <label className="form-label">{t('settings.autoRedpacket')}</label>
                    <Switch
                      label={t('settings.autoRedpacket')}
                      checked={course.auto_redpacket}
                      onChange={(v) => updateField(course.courseId, 'auto_redpacket', v)}
                    />
                  </div>
                </div>

                <div className="settings-group">
                  <span className="settings-group-label">{t('settings.notifications')}</span>
                  {NOTIF_CHANNELS.map((ch) => (
                    <NotificationSection
                      key={ch.field}
                      label={t(ch.labelKey)}
                      value={course[ch.field] ?? EMPTY_NOTIFICATION}
                      onChange={(v) => updateField(course.courseId, ch.field, v)}
                      disabled={(ch.field === 'pushdeer_notification' && pushdeerKeyCount === 0) || (ch.field === 'windows_notification' && !defaults.windows_notification)}
                      disabledTitle={ch.field === 'pushdeer_notification' ? t('settings.pushdeerNoKey') : ch.field === 'windows_notification' && !defaults.windows_notification ? t('settings.windowsNotificationRestart') : undefined}
                    />
                  ))}
                </div>
              </div>

              <div className="course-card-footer">
                <button className="btn btn-ghost" onClick={() => resetToDefault(course.courseId)}>
                  {t('settings.default')}
                </button>
                <div className="footer-spacer" />
                {courses.length > 1 && (() => {
                  const isThis = appliedAll?.courseId === course.courseId
                  const cls = isThis
                    ? (appliedAll?.status === 'success' ? 'btn-success' : 'btn-danger')
                    : 'btn-secondary'
                  const label = isThis
                    ? (appliedAll?.status === 'success' ? t('settings.applied') : t('settings.applyToAllFailed'))
                    : t('settings.applyToAll')
                  return (
                    <button
                      className={`btn ${cls}`}
                      onClick={() => applyToAll(course)}
                      disabled={appliedAll !== null}
                    >
                      {label}
                    </button>
                  )
                })()}
                <button
                  className={`btn ${saveButtonClass(course.saveStatus)}`}
                  onClick={() => handleSave(course)}
                  disabled={course.saveStatus === 'saving' || !isDirty(course)}
                >
                  {course.saveStatus === 'saving'
                    ? t('settings.applying')
                    : course.saveStatus === 'saved'
                      ? t('settings.applied')
                      : t('settings.apply')}
                </button>
              </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
