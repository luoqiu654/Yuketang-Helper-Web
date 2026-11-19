import { useEffect, useState } from 'react'

type Quiz = { id: string; lesson: string; problem_id: string; problem_type: number; cover: string; options: string[]; status: string; revision: number; deadline_at: number | null; suggested_answer?: string | string[] | null; model_id?: string; latency_ms?: number; error_code?: string }

export default function QuizReview({ accountId }: { accountId: string }) {
  const [items, setItems] = useState<Quiz[]>([])
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const r = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/quizzes/pending`)
        if (!r.ok) throw new Error('题目加载失败')
        const data = await r.json() as Quiz[]
        if (active) { setItems(data); setError('') }
      } catch { if (active) setError('题目加载失败') }
    }
    void load()
    const timer = setInterval(load, 5000)
    return () => { active = false; clearInterval(timer) }
  }, [accountId, refresh])
  async function dismiss(q: Quiz) {
    const r = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/quizzes/${q.id}/dismiss`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_revision: q.revision })
    })
    if (r.ok) setItems(prev => prev.filter(item => item.id !== q.id))
    else setError('操作失败，题目状态可能已变化')
  }
  return <section className="card"><h2 className="card-title">待处理题目</h2>
    {error && <p role="alert">{error}</p>}
    {items.length === 0 && <p>暂无待处理题目</p>}
    {items.map(q => <article key={q.id}>
      <p>{q.lesson} · 题目 {q.problem_id} · 类型 {q.problem_type} · {q.status}</p>
      <p>{q.deadline_at ? `截止时间：${new Date(q.deadline_at * 1000).toLocaleString()}` : '截止时间未知'}</p>
      {q.cover && <img src={q.cover} alt="题目图片" style={{maxWidth: '100%'}} referrerPolicy="no-referrer" />}
      {q.options.length > 0 && <p>选项：{q.options.join('、')}</p>}
      {q.status === 'SUGGESTING' && <p>正在生成 AI 建议…</p>}
      {q.suggested_answer && <p>AI 建议：{Array.isArray(q.suggested_answer) ? q.suggested_answer.join('、') : q.suggested_answer} · {q.model_id} · {q.latency_ms} ms</p>}
      {q.status === 'FAILED'
        ? <p role="alert">自动提交的结果不明确，请先到雨课堂核实，助手不会自动重复提交。{q.error_code ? `（${q.error_code}）` : ''}</p>
        : q.error_code && <p role="alert">AI 答题未完成，请先到雨课堂核实：{q.error_code}</p>}
      <label>答案草稿（选择题使用逗号分隔）<input className="form-input" value={answers[q.id] ?? (Array.isArray(q.suggested_answer) ? q.suggested_answer.join(',') : q.suggested_answer ?? '')} onChange={e => setAnswers({...answers, [q.id]: e.target.value})} /></label>
      <p>此处的答案草稿不会提交；如自动答题失败或结果不明确，请在雨课堂核实并手动作答。</p>
      <button className="btn btn-secondary" onClick={() => void dismiss(q)}>忽略</button>
      <button className="btn btn-secondary" onClick={() => setRefresh(refresh + 1)}>刷新</button>
    </article>)}
  </section>
}
