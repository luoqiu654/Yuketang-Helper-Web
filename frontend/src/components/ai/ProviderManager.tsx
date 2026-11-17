import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import '../../styles/provider-manager.css'

type Provider = { id: string; name: string; base_url: string; enabled: boolean; proxy_mode: string; has_key: boolean }
type Model = { id: string; provider_id: string; model_id: string; display_name: string; supports_vision: string; image_transport: string; timeout_seconds: number; max_output_tokens: number; enabled: boolean }
type ProviderForm = Omit<Provider, 'id' | 'has_key'> & { api_key: string }
type ModelForm = Omit<Model, 'id' | 'provider_id'>
const emptyProvider = (): ProviderForm => ({ name: '', base_url: '', api_key: '', enabled: true, proxy_mode: 'system' })
const emptyModel = (): ModelForm => ({ model_id: '', display_name: '', supports_vision: 'unknown', image_transport: 'url', timeout_seconds: 20, max_output_tokens: 128, enabled: true })

async function request(url: string, options?: RequestInit) {
  const response = await fetch(url, options)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json()
}
const json = (method: string, body: object): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export default function ProviderManager({ base }: { base: string }) {
  const { i18n } = useTranslation()
  const zh = i18n.resolvedLanguage?.startsWith('zh') ?? true
  const tr = (cn: string, en: string) => zh ? cn : en
  const [providers, setProviders] = useState<Provider[]>([])
  const [defaultModel, setDefaultModel] = useState<string | null>(null)
  const [models, setModels] = useState<Record<string, Model[]>>({})
  const [providerForm, setProviderForm] = useState<ProviderForm>(emptyProvider)
  const [providerEdit, setProviderEdit] = useState<string | null>(null)
  const [modelForm, setModelForm] = useState<ModelForm>(emptyModel)
  const [modelProvider, setModelProvider] = useState<string | null>(null)
  const [modelEdit, setModelEdit] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState('')
  const [discovery, setDiscovery] = useState('')
  const [testing, setTesting] = useState<string | null>(null)

  const reload = useCallback(async () => {
    const list: Provider[] = await request(`${base}/ai/providers`)
    const selection = await request(`${base}/ai/default`)
    setDefaultModel(selection.model_id)
    const entries = await Promise.all(list.map(async p => [p.id, await request(`${base}/ai/providers/${encodeURIComponent(p.id)}/models`) as Model[]] as const))
    setProviders(list)
    setModels(Object.fromEntries(entries))
  }, [base])
  useEffect(() => { let active = true; setProviders([]); setModels({}); request(`${base}/ai/providers`).then(async (list: Provider[]) => {
    const selection = await request(`${base}/ai/default`)
    if (active) setDefaultModel(selection.model_id)
    const entries = await Promise.all(list.map(async p => [p.id, await request(`${base}/ai/providers/${encodeURIComponent(p.id)}/models`) as Model[]] as const))
    if (active) { setProviders(list); setModels(Object.fromEntries(entries)) }
  }).catch(() => { if (active) setError(tr('无法读取自定义供应商，请确认开发版后端已启动。', 'Could not load custom providers. Check the development backend.')) })
  return () => { active = false }
  // base changes when switching accounts; text is refreshed on next action.
  }, [base])

  const action = async (fn: () => Promise<void>) => {
    setBusy(true); setError('')
    try { await fn(); await reload() } catch (e) { setResult(''); setError(`${tr('操作失败', 'Operation failed')}: ${e instanceof Error ? e.message : ''}`) }
    finally { setBusy(false) }
  }
  const saveProvider = () => action(async () => {
    await request(`${base}/ai/providers${providerEdit ? `/${encodeURIComponent(providerEdit)}` : ''}`, json(providerEdit ? 'PATCH' : 'POST', providerForm))
    setProviderForm(emptyProvider()); setProviderEdit(null)
  })
  const editProvider = (p: Provider) => { setProviderEdit(p.id); setProviderForm({ name: p.name, base_url: p.base_url, api_key: '', enabled: !!p.enabled, proxy_mode: p.proxy_mode }) }
  const saveModel = () => action(async () => {
    if (!modelProvider) return
    const url = modelEdit ? `${base}/ai/models/${encodeURIComponent(modelEdit)}` : `${base}/ai/providers/${encodeURIComponent(modelProvider)}/models`
    await request(url, json(modelEdit ? 'PATCH' : 'POST', modelForm))
    setModelProvider(null); setModelEdit(null); setModelForm(emptyModel())
  })
  const editModel = (m: Model) => { setModelProvider(m.provider_id); setModelEdit(m.id); setModelForm({ model_id: m.model_id, display_name: m.display_name, supports_vision: m.supports_vision, image_transport: m.image_transport, timeout_seconds: m.timeout_seconds, max_output_tokens: m.max_output_tokens, enabled: !!m.enabled }) }
  const test = (m: Model, type: 'connection' | 'text' | 'vision') => {
    if (!window.confirm(tr('测试会调用模型服务，可能产生费用。继续？', 'This test calls the model provider and may incur charges. Continue?'))) return
    setResult(tr('正在测试，请稍候…', 'Testing, please wait…'))
    setTesting(m.id)
    void action(async () => {
      const value = await request(`${base}/ai/models/${encodeURIComponent(m.id)}/test`, json('POST', { test_type: type, timeout_seconds: m.timeout_seconds, request_id: globalThis.crypto.randomUUID() }))
      setResult(`${m.display_name} · ${type}: ${value.status === 'passed' ? tr('通过', 'Passed') : tr('失败', 'Failed')} · ${value.latency_ms} ms${value.error_code ? ` · ${value.error_code}` : ''}${type === 'vision' ? tr('（仅代表此测试图片的结果）', ' (result applies only to this test image)') : ''}`)
    }).finally(() => setTesting(null))
  }
  return <div className="card provider-manager">
    <h3 className="provider-manager__title">{tr('自定义 AI 供应商', 'Custom AI providers')}</h3>
    <p className="provider-manager__description">{tr('支持 OpenAI Chat Completions 兼容接口。模型可用于连接测试与课堂答案建议；提交需经过人工确认。', 'OpenAI Chat Completions compatible APIs. Models can be tested and used for classroom suggestions; submission requires explicit confirmation.')}</p>
    <p className="provider-manager__hint">{tr('基础地址需使用 HTTPS，通常以 /v1 结尾；请自行确认服务商要求。', 'The base URL must use HTTPS and commonly ends in /v1; check your provider documentation.')}</p>
    {error && <p className="provider-manager__alert" role="alert">{error}</p>}{result && <p className="provider-manager__notice" role="status">{result}</p>}{discovery && <p className="provider-manager__notice" role="status">{discovery}</p>}
    {providers.map(p => <div key={p.id} className="credential-item provider-manager__provider">
      <div className="provider-manager__identity"><strong>{p.name}</strong><span className="provider-manager__url">{p.base_url}</span><span className="provider-manager__status">{p.enabled ? tr('已启用', 'Enabled') : tr('已停用', 'Disabled')}</span></div>
      <div className="credential-actions">
        <button className="btn btn-sm btn-secondary" disabled={busy} onClick={() => editProvider(p)}>{tr('编辑', 'Edit')}</button>
        <button className="btn btn-sm btn-secondary" disabled={busy} onClick={() => void action(async () => { await request(`${base}/ai/providers/${encodeURIComponent(p.id)}`, json('PATCH', { name: p.name, base_url: p.base_url, api_key: '', enabled: !p.enabled, proxy_mode: p.proxy_mode })) })}>{p.enabled ? tr('停用', 'Disable') : tr('启用', 'Enable')}</button>
        <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => { if (window.confirm(tr('删除此供应商及其所有模型？', 'Delete this provider and all its models?'))) void action(async () => { await request(`${base}/ai/providers/${encodeURIComponent(p.id)}`, { method: 'DELETE' }) }) }}>{tr('删除', 'Delete')}</button>
        <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => { setModelProvider(p.id); setModelEdit(null); setModelForm(emptyModel()) }}>{tr('添加模型', 'Add model')}</button>
        <button className="btn btn-sm btn-secondary" disabled={busy || !p.enabled} onClick={() => void action(async () => { const data = await request(`${base}/ai/providers/${encodeURIComponent(p.id)}/models/discover`, { method: 'POST' }); setDiscovery(`${p.name}: ${(data.models as string[]).join(', ') || tr('未返回模型', 'No models returned')}`) })}>{tr('发现模型', 'Discover models')}</button>
      </div>
      {(models[p.id] ?? []).map(m => <div key={m.id} className="provider-manager__model"><div className="provider-manager__model-heading"><strong>{m.display_name}</strong><span className="provider-manager__model-id">{m.model_id}</span><span className="provider-manager__status">{m.enabled ? tr('已启用', 'Enabled') : tr('已停用', 'Disabled')}</span></div>
        {testing === m.id && <p role="status">{tr('正在测试，请稍候…', 'Testing, please wait…')}</p>}
        {result && !testing && result.startsWith(`${m.display_name} ·`) && <p role="status">{result}</p>}
        <div className="credential-actions"><button className="btn btn-sm btn-secondary" disabled={busy || !m.enabled || !p.enabled || defaultModel === m.id} onClick={() => void action(async () => { await request(`${base}/ai/default`, json('PUT', { model_id: m.id })) })}>{defaultModel === m.id ? tr('默认模型', 'Default model') : tr('设为默认', 'Set default')}</button><button className="btn btn-sm btn-secondary" disabled={busy} onClick={() => editModel(m)}>{tr('编辑', 'Edit')}</button>
          <button className="btn btn-sm btn-secondary" disabled={busy} onClick={() => void action(async () => { const { id, provider_id, ...body } = m; void id; void provider_id; await request(`${base}/ai/models/${encodeURIComponent(m.id)}`, json('PATCH', { ...body, enabled: !m.enabled })) })}>{m.enabled ? tr('停用', 'Disable') : tr('启用', 'Enable')}</button>
          <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => { if (window.confirm(tr('删除此模型？', 'Delete this model?'))) void action(async () => { await request(`${base}/ai/models/${encodeURIComponent(m.id)}`, { method: 'DELETE' }) }) }}>{tr('删除', 'Delete')}</button>
          {(['connection', 'text', 'vision'] as const).map(type => <button key={type} className="btn btn-sm btn-secondary" disabled={busy || !m.enabled || !p.enabled} onClick={() => test(m, type)}>{type === 'connection' ? tr('连接测试', 'Connection') : type === 'text' ? tr('文本测试', 'Text') : tr('视觉测试', 'Vision')}</button>)}</div>
      </div>)}
    </div>)}
    <h4 className="provider-manager__form-heading">{providerEdit ? tr('编辑供应商', 'Edit provider') : tr('添加供应商', 'Add provider')}</h4>
    <div className="credential-add-fields provider-manager__fields">
      <label>{tr('名称', 'Name')}<input className="form-input" value={providerForm.name} onChange={e => setProviderForm({ ...providerForm, name: e.target.value })} /></label>
      <label>Base URL<input className="form-input" type="url" placeholder="https://example.com/v1" value={providerForm.base_url} onChange={e => setProviderForm({ ...providerForm, base_url: e.target.value })} /></label>
      <label>API Key<input className="form-input" type="password" autoComplete="new-password" value={providerForm.api_key} placeholder={providerEdit ? tr('留空保留现有密钥', 'Leave blank to keep existing key') : ''} onChange={e => setProviderForm({ ...providerForm, api_key: e.target.value })} /></label>
      <label>{tr('代理', 'Proxy')}<select className="form-select" value={providerForm.proxy_mode} onChange={e => setProviderForm({ ...providerForm, proxy_mode: e.target.value })}><option value="system">{tr('系统设置', 'System')}</option><option value="direct">{tr('直连', 'Direct')}</option></select></label>
    </div>
    <div className="credential-actions"><button className="btn btn-primary" disabled={busy || !providerForm.name.trim() || !providerForm.base_url.trim() || (!providerEdit && !providerForm.api_key.trim())} onClick={() => void saveProvider()}>{tr('保存', 'Save')}</button>{providerEdit && <button className="btn btn-secondary" onClick={() => { setProviderEdit(null); setProviderForm(emptyProvider()) }}>{tr('取消', 'Cancel')}</button>}</div>
    {modelProvider && <><h4 className="provider-manager__form-heading">{modelEdit ? tr('编辑模型', 'Edit model') : tr('添加模型', 'Add model')}</h4><div className="credential-add-fields provider-manager__fields">
      <label>Model ID<input className="form-input" value={modelForm.model_id} onChange={e => setModelForm({ ...modelForm, model_id: e.target.value })} /></label>
      <label>{tr('显示名称', 'Display name')}<input className="form-input" value={modelForm.display_name} onChange={e => setModelForm({ ...modelForm, display_name: e.target.value })} /></label>
      <label>{tr('视觉输入', 'Vision')}<select className="form-select" value={modelForm.supports_vision} onChange={e => setModelForm({ ...modelForm, supports_vision: e.target.value })}><option value="unknown">{tr('未知', 'Unknown')}</option><option value="yes">{tr('支持', 'Yes')}</option><option value="no">{tr('不支持', 'No')}</option></select></label>
      <label>{tr('图片传输', 'Image transport')}<select className="form-select" value={modelForm.image_transport} onChange={e => setModelForm({ ...modelForm, image_transport: e.target.value })}><option value="url">HTTPS URL</option><option value="data_url">Data URL</option></select></label>
      <label>{tr('超时（秒）', 'Timeout (s)')}<input className="form-input" type="number" min="1" max="120" value={modelForm.timeout_seconds} onChange={e => setModelForm({ ...modelForm, timeout_seconds: Number(e.target.value) })} /></label>
      <label>{tr('最大输出 Token', 'Max output tokens')}<input className="form-input" type="number" min="1" max="8192" value={modelForm.max_output_tokens} onChange={e => setModelForm({ ...modelForm, max_output_tokens: Number(e.target.value) })} /></label>
    </div><div className="credential-actions"><button className="btn btn-primary" disabled={busy || !modelForm.model_id.trim() || !modelForm.display_name.trim()} onClick={() => void saveModel()}>{tr('保存模型', 'Save model')}</button><button className="btn btn-secondary" onClick={() => { setModelProvider(null); setModelEdit(null) }}>{tr('取消', 'Cancel')}</button></div></>}
  </div>
}
