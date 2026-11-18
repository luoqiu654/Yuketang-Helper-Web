import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

type LessonHealth = { lesson_id: number; state: string; last_connected_at: string | null; last_frame_at: string | null; reconnect_count: number }
type Health = { state: string; last_poll_success_at: string | null; last_error_code?: string | null; lessons: LessonHealth[] }

export default function HealthPanel({ accountId, browserConnected }: { accountId: string; browserConnected: boolean }) {
  const { i18n } = useTranslation()
  const zh = i18n.language.startsWith('zh')
  const [health, setHealth] = useState<Health | null>(null)
  const [failed, setFailed] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  useEffect(() => {
    let active = true
    let inFlight = false
    let pending = false
    let controller: AbortController | null = null
    setHealth(null)
    const load = async () => {
      if (!active) return
      // Monitor events and the periodic poll can arrive while a manual refresh
      // is running. Coalesce them so an older response cannot replace a newer
      // snapshot or re-enable the button while another request is in flight.
      if (inFlight) { pending = true; return }
      inFlight = true
      setRefreshing(true)
      controller = new AbortController()
      const timeout = setTimeout(() => controller?.abort(), 10000)
      try {
        const response = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/monitor/health`, { signal: controller.signal })
        if (!response.ok) throw new Error('health unavailable')
        const data = await response.json() as Health
        if (active) { setHealth(data); setFailed(false) }
      } catch { if (active) setFailed(true) }
      finally {
        clearTimeout(timeout)
        inFlight = false
        controller = null
        if (active && pending) { pending = false; void load() }
        else if (active) setRefreshing(false)
      }
    }
    void load()
    const onHealthChanged = () => { void load() }
    window.addEventListener('yuketang-monitor-health', onHealthChanged)
    const timer = setInterval(load, 5000)
    return () => { active = false; controller?.abort(); clearInterval(timer); window.removeEventListener('yuketang-monitor-health', onHealthChanged) }
  }, [accountId])
  const time = (value: string | null) => value ? new Date(value).toLocaleString() : (zh ? '暂无' : 'Unavailable')
  return <section className="card monitor-health">
    <div className="monitor-health__heading">
      <h2 className="card-title">{zh ? '课堂监听状态' : 'Class monitor health'}</h2>
      <button type="button" className="btn btn-sm btn-secondary" disabled={refreshing} onClick={() => window.dispatchEvent(new Event('yuketang-monitor-health'))}>
        {refreshing ? (zh ? '刷新中…' : 'Refreshing…') : (zh ? '刷新状态' : 'Refresh status')}
      </button>
    </div>
    <p>{zh ? '浏览器事件连接' : 'Browser event connection'}: {browserConnected ? (zh ? '已连接' : 'Connected') : (zh ? '未连接' : 'Disconnected')}</p>
    {failed ? <p>{zh ? '无法获取监听状态' : 'Health unavailable'}</p> : health ? <>
      <p>{zh ? '后台状态' : 'Backend status'}: {health.state}</p>
      <p>{zh ? '最近成功轮询' : 'Last successful poll'}: {time(health.last_poll_success_at)}</p>
      {health.last_error_code && <p>{zh ? '最近错误' : 'Last error'}: {health.last_error_code}</p>}
      {health.lessons.map(item => <p key={item.lesson_id}>
        {zh ? '课堂' : 'Lesson'} {item.lesson_id}: {item.state} / {zh ? '最近连接' : 'Connected'}: {time(item.last_connected_at)} / {zh ? '最近消息' : 'Last frame'}: {time(item.last_frame_at)} / {zh ? '重连次数' : 'Reconnects'}: {item.reconnect_count}
      </p>)}
      <small>{zh ? '课堂无新消息不代表连接异常。浏览器连接状态与课堂连接状态独立。' : 'No new class messages does not imply a fault. Browser and class connections are separate.'}</small>
    </> : <p>{zh ? '正在读取…' : 'Loading…'}</p>}
  </section>
}
