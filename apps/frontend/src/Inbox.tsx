import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { api, errorText, money, type Conversation, type Message, type Page, type User } from './api'
import { ErrorBanner } from './dialogs'
import { Avatar, Icon } from './visuals'

export default function Inbox({ user, initialConversation }: { user: User; initialConversation: Conversation | null }) {
  const [conversations, setConversations] = useState<Conversation[]>(initialConversation ? [initialConversation] : [])
  const [selected, setSelected] = useState<Conversation | null>(initialConversation)
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(true)
  const [messageLoading, setMessageLoading] = useState(false)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [messagePage, setMessagePage] = useState(1)
  const [messageTotal, setMessageTotal] = useState(0)
  const end = useRef<HTMLDivElement>(null)
  const selectedId = selected?.id
  const activeId = useRef(selectedId)
  activeId.current = selectedId
  const messageRequest = useRef(0)
  const listRequest = useRef(0)
  const sendRequest = useRef(0)
  const loadConversations = useCallback(async () => {
    const request = ++listRequest.current
    const result = await api<Page<Conversation>>(`/conversations?page=${page}&page_size=50`)
    if (request !== listRequest.current) return
    setConversations(result.items); setTotal(result.total)
    setSelected(current => current ? result.items.find(item => item.id === current.id) || current : null)
  }, [page])
  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    loadConversations().catch(error => { if (active) setError(errorText(error)) }).finally(() => { if (active) setLoading(false) })
    const interval = setInterval(() => loadConversations().catch(() => {}), 12000)
    return () => { active = false; clearInterval(interval) }
  }, [loadConversations])
  useEffect(() => {
    if (!selectedId) return
    let active = true
    const request = ++messageRequest.current
    ++sendRequest.current
    setMessageLoading(true); setMessages([]); setError(''); setDraft(''); setSending(false)
    const load = async () => {
      const first = await api<Page<Message>>(`/conversations/${selectedId}/messages?page=1&page_size=100`)
      const lastPage = Math.max(1, Math.ceil(first.total / 100))
      const result = lastPage === 1 ? first : await api<Page<Message>>(`/conversations/${selectedId}/messages?page=${lastPage}&page_size=100`)
      if (active && request === messageRequest.current && activeId.current === selectedId) { setMessages(result.items); setMessagePage(lastPage); setMessageTotal(result.total) }
    }
    load().catch(error => { if (active && request === messageRequest.current) setError(errorText(error)) }).finally(() => { if (active && request === messageRequest.current) setMessageLoading(false) })
    return () => { active = false }
  }, [selectedId])
  useEffect(() => {
    if (!selectedId || messageLoading || sending) return
    const interval = setInterval(async () => {
      const request = ++messageRequest.current
      try {
        const result = await api<Page<Message>>(`/conversations/${selectedId}/messages?page=${messagePage}&page_size=100`)
        if (activeId.current === selectedId && request === messageRequest.current) { setMessages(result.items); setMessageTotal(result.total); setMessageLoading(false) }
      } catch { /* Surface actionable errors on direct interaction. */ }
    }, 8000)
    return () => clearInterval(interval)
  }, [selectedId, messagePage, messageLoading, sending])
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [messages.length, selectedId])
  const changeMessagePage = async (next: number) => {
    if (!selectedId) return
    const request = ++messageRequest.current
    setMessageLoading(true); setError('')
    try { const result = await api<Page<Message>>(`/conversations/${selectedId}/messages?page=${next}&page_size=100`); if (activeId.current === selectedId && request === messageRequest.current) { setMessages(result.items); setMessagePage(next); setMessageTotal(result.total) } }
    catch (error) { if (activeId.current === selectedId && request === messageRequest.current) setError(errorText(error)) } finally { if (activeId.current === selectedId && request === messageRequest.current) setMessageLoading(false) }
  }
  const send = async (event: FormEvent) => {
    event.preventDefault()
    if (!selected || !draft.trim()) return
    const conversationId = selected.id
    const request = ++sendRequest.current
    setSending(true); setError('')
    try {
      await api<Message>(`/conversations/${selected.id}/messages`, { method: 'POST', body: JSON.stringify({ body: draft.trim() }) })
      if (activeId.current !== conversationId || request !== sendRequest.current) return
      setDraft('')
      const messagesRequest = ++messageRequest.current
      const first = await api<Page<Message>>(`/conversations/${selected.id}/messages?page=1&page_size=100`)
      const latestPage = Math.max(1, Math.ceil(first.total / 100))
      const latest = latestPage === 1 ? first : await api<Page<Message>>(`/conversations/${selected.id}/messages?page=${latestPage}&page_size=100`)
      if (activeId.current === conversationId && messagesRequest === messageRequest.current) { setMessages(latest.items); setMessageTotal(latest.total); setMessagePage(latestPage) }
      await loadConversations()
    } catch (error) { if (activeId.current === conversationId && request === sendRequest.current) setError(errorText(error)) } finally { if (activeId.current === conversationId && request === sendRequest.current) setSending(false) }
  }
  const other = selected ? selected.buyer.id === user.id ? selected.seller : selected.buyer : null
  return <section className="inbox-section"><div className="section-heading"><div><h1>Messages</h1><p>Arrange payment and pickup with buyers and sellers.</p></div></div>
    <ErrorBanner message={error} />
    <div className={`inbox-layout ${selected ? 'has-selected' : ''}`}>
      <aside className="conversation-sidebar"><div className="inbox-label">Conversations <span>{total}</span></div>{loading && !conversations.length ? <p className="muted padded">Loading messages…</p> : !conversations.length ? <div className="inbox-empty"><Icon name="message" size={32} /><h3>No conversations yet</h3><p>Contact a seller from a listing to get started.</p></div> : conversations.map(conversation => {
        const person = conversation.buyer.id === user.id ? conversation.seller : conversation.buyer
        return <button key={conversation.id} className={`conversation-preview ${selected?.id === conversation.id ? 'selected' : ''}`} onClick={() => setSelected(conversation)}><Avatar name={person.display_name} small /><div><div className="conversation-name"><strong>{person.display_name}</strong><span>{new Date(conversation.updated_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span></div><b>{conversation.listing.title}</b><p>{conversation.last_message}</p></div></button>
      })}{total > 50 && <div className="pagination compact"><button className="text-button" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</button><span>{page}</span><button className="text-button" disabled={page * 50 >= total} onClick={() => setPage(page + 1)}>Next</button></div>}</aside>
      <div className="conversation-main">{selected && other ? <><div className="chat-header"><button className="icon-button mobile-back" aria-label="Back to conversations" onClick={() => setSelected(null)}><Icon name="arrow" /></button><Avatar name={other.display_name} small /><div><strong>{other.display_name}</strong><span>{other.university} · .edu address</span></div></div><div className="chat-listing"><Icon name="bag" size={20} /><div><strong>{selected.listing.title}</strong><span>{money(selected.listing.price_cents)} · {selected.listing.status === 'removed' ? 'Item removed' : selected.listing.status}</span></div></div>
        <div className="messages" aria-live="polite">{messageTotal > 100 && <div className="pagination compact"><button className="text-button" onClick={() => changeMessagePage(messagePage - 1)} disabled={messagePage === 1 || messageLoading}>Earlier messages</button><span>{messagePage} / {Math.ceil(messageTotal / 100)}</span><button className="text-button" onClick={() => changeMessagePage(messagePage + 1)} disabled={messagePage * 100 >= messageTotal || messageLoading}>Newer messages</button></div>}{messageLoading ? <p className="muted">Loading messages…</p> : messages.map(message => <div key={message.id} className={`message ${message.sender_id === user.id ? 'own' : ''}`}><div>{message.body}</div><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</time></div>)}<div ref={end} /></div>
        <form className="message-compose" onSubmit={send}><label className="sr-only" htmlFor="message-draft">Your message</label><textarea id="message-draft" rows={2} placeholder="Write a message…" maxLength={2000} disabled={sending} value={draft} onChange={event => setDraft(event.target.value)} required /><button className="button primary" disabled={sending || !draft.trim()} aria-label="Send message"><Icon name="send" size={19} /><span>{sending ? 'Sending…' : 'Send'}</span></button></form><p className="chat-note">Arrange payment and pickup directly. Check the item before paying.</p>
      </> : <div className="chat-placeholder"><div><Icon name="message" size={36} /><h2>Select a conversation</h2><p>Your messages will appear here.</p></div></div>}</div>
    </div>
  </section>
}
