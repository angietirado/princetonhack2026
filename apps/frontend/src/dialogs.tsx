import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { api, categories, conditions, errorText, money, type Category, type Condition, type Conversation, type Listing, type User } from './api'
import { Avatar, Icon, ListingImage } from './visuals'

export function Modal({ title, subtitle, children, onClose, wide = false }: { title: string; subtitle?: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const panel = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const oldOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const target = panel.current
    target?.querySelector<HTMLElement>('input, button, select, textarea')?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close.current()
      if (event.key !== 'Tab' || !target) return
      const nodes = [...target.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]')]
      const first = nodes[0], last = nodes[nodes.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', keydown)
    return () => { document.body.style.overflow = oldOverflow; document.removeEventListener('keydown', keydown); previous?.focus() }
  }, [])
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <div ref={panel} className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby="dialog-title">
      <div className="modal-heading"><div><h2 id="dialog-title">{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button className="icon-button" aria-label="Close dialog" onClick={onClose}><Icon name="close" /></button></div>
      {children}
    </div>
  </div>
}

export function ErrorBanner({ message }: { message: string }) { return message ? <div className="error-banner" role="alert">{message}</div> : null }

export function StudentCheckDialog({ onClose, onSuccess }: { onClose: () => void; onSuccess: (user: User) => void }) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (name.trim() && name.trim().length < 2) { setError('Use at least two characters for your name, or leave it blank.'); return }
    setBusy(true); setError('')
    try {
      const user = await api<User>('/student/verify', { method: 'POST', body: JSON.stringify({ email: email.trim().toLowerCase(), ...(name.trim() ? { display_name: name.trim() } : {}) }) })
      onSuccess(user)
    } catch (error) { setError(errorText(error)) } finally { setBusy(false) }
  }
  return <Modal title="Add your student email" subtitle="Use a .edu address to buy and sell." onClose={onClose}>
    <form onSubmit={submit} className="stack-form">
      <ErrorBanner message={error} />
      <label>Student email<input type="email" autoComplete="email" placeholder="you@university.edu" value={email} onChange={event => setEmail(event.target.value)} required maxLength={254} pattern="[^\s@]+@[^\s@]+\.[eE][dD][uU]" /></label>
      <label>What should we call you? <span className="optional">Optional</span><input autoComplete="nickname" placeholder="Your first name" minLength={2} maxLength={60} value={name} onChange={event => setName(event.target.value)} /></label>
      <button className="button primary full-width" disabled={busy}>{busy ? 'Checking…' : 'Continue'}{!busy && <Icon name="arrow" size={18} />}</button>
      <p className="fine-print">We check the .edu ending only; no email is sent. Your profile stays in this browser.</p>
    </form>
  </Modal>
}

export function ListingEditor({ listing, onClose, onSaved }: { listing?: Listing; onClose: () => void; onSaved: (listing: Listing) => void }) {
  const [title, setTitle] = useState(listing?.title || '')
  const [description, setDescription] = useState(listing?.description || '')
  const [price, setPrice] = useState(listing ? (listing.price_cents / 100).toString() : '')
  const [category, setCategory] = useState<Category>(listing?.category || 'books')
  const [condition, setCondition] = useState<Condition>(listing?.condition || 'good')
  const [pickup, setPickup] = useState(listing?.pickup_location || '')
  const [image, setImage] = useState(listing?.image_url || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const result = await api<Listing>(listing ? `/listings/${listing.id}` : '/listings', {
        method: listing ? 'PATCH' : 'POST',
        body: JSON.stringify({ title: title.trim(), description: description.trim(), price_cents: Math.round(Number(price) * 100), category, condition, pickup_location: pickup.trim(), image_url: image.trim() || null }),
      })
      onSaved(result)
    } catch (error) { setError(errorText(error)) } finally { setBusy(false) }
  }
  return <Modal title={listing ? 'Edit listing' : 'Sell an item'} subtitle="Add the details buyers need. Payment is arranged directly." onClose={onClose} wide>
    <form className="stack-form" onSubmit={submit}>
      <ErrorBanner message={error} />
      <label>What are you selling?<input placeholder="e.g. Desk lamp" value={title} onChange={e => setTitle(e.target.value)} required minLength={3} maxLength={100} /></label>
      <div className="form-row"><label>Price (USD)<div className="price-input"><span>$</span><input type="number" min="0" max="100000" step="0.01" placeholder="0.00" value={price} onChange={e => setPrice(e.target.value)} required /></div><span className="field-help">Enter 0 to give it away.</span></label><label>Category<select value={category} onChange={e => setCategory(e.target.value as Category)}>{categories.map(category => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label></div>
      <div className="form-row"><label>Condition<select value={condition} onChange={e => setCondition(e.target.value as Condition)}>{Object.entries(conditions).map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label><label>Pickup location<input placeholder="e.g. Campus center" value={pickup} onChange={e => setPickup(e.target.value)} required minLength={2} maxLength={160} /></label></div>
      <label>The details<textarea rows={4} placeholder="Tell us about it: size, age, anything a buyer should know…" value={description} onChange={e => setDescription(e.target.value)} required minLength={10} maxLength={4000} /></label>
      <label>Photo URL <span className="optional">Optional</span><input type="url" placeholder="https://…" pattern="https://.*" value={image} onChange={e => setImage(e.target.value)} maxLength={2048} /><span className="field-help">Add a public HTTPS image link, or we’ll use category artwork.</span></label>
      <div className="form-actions"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button className="button primary" disabled={busy}>{busy ? 'Saving…' : listing ? 'Save changes' : 'Publish listing'}<Icon name="arrow" size={17} /></button></div>
    </form>
  </Modal>
}

export function DetailDialog({ listing, user, onClose, onCheckStudent, onEdit, onConversation }: { listing: Listing; user: User | null; onClose: () => void; onCheckStudent: () => void; onEdit: () => void; onConversation: (conversation: Conversation) => void }) {
  const [message, setMessage] = useState(`Hi! Is your ${listing.title} still available?`)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const owner = listing.seller.id === user?.id
  const contact = async (event: FormEvent) => {
    event.preventDefault()
    if (!user) { onCheckStudent(); return }
    setBusy(true); setError('')
    try { onConversation(await api<Conversation>(`/listings/${listing.id}/conversations`, { method: 'POST', body: JSON.stringify({ message: message.trim() }) })) }
    catch (error) { setError(errorText(error)) } finally { setBusy(false) }
  }
  return <Modal title="Listing details" onClose={onClose} wide>
    <div className="detail-image"><ListingImage listing={listing} /><span className={`status-badge status-${listing.status}`}>{listing.status}</span>{listing.is_demo && <span className="demo-label">Demo listing</span>}</div>
    <div className="detail-title"><div><p className="eyebrow">{categories.find(c => c.id === listing.category)?.name} · {conditions[listing.condition]}</p><h2>{listing.title}</h2></div><strong>{money(listing.price_cents)}</strong></div>
    <p className="detail-description">{listing.description}</p>
    <div className="pickup"><Icon name="pin" size={18} /><span>{listing.pickup_location}</span></div>
    <div className="seller-line"><Avatar name={listing.seller.display_name} /><div><strong>{listing.seller.display_name}</strong><span>{listing.seller.university}</span></div><span className="verified"><Icon name="books" size={15} /> .edu address</span></div>
    <ErrorBanner message={error} />
    {owner ? <button className="button primary full-width" onClick={onEdit}><Icon name="edit" size={18} />Edit your listing</button>
      : listing.is_demo ? <div className="info-note">This is a display-only sample listing.</div>
      : listing.status !== 'available' ? <div className="info-note">This item is {listing.status}.</div>
      : <form className="contact-form" onSubmit={contact}><label>Say hello to the seller<textarea rows={2} value={message} onChange={e => setMessage(e.target.value)} required minLength={1} maxLength={2000} /></label><button className="button primary full-width" disabled={busy}>{busy ? 'Starting conversation…' : user ? 'Message seller' : 'Check .edu email to message'}<Icon name="message" size={18} /></button><p className="fine-print">Arrange pickup and payment directly with the seller. Campus Exchange does not process payments.</p></form>}
  </Modal>
}

export function ProfileDialog({ user, onClose, onSaved }: { user: User; onClose: () => void; onSaved: (user: User) => void }) {
  const [name, setName] = useState(user.display_name)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <Modal title="Your student profile" subtitle={user.email} onClose={onClose}><form className="stack-form" onSubmit={async event => { event.preventDefault(); setBusy(true); setError(''); try { onSaved(await api<User>('/me', { method: 'PATCH', body: JSON.stringify({ display_name: name.trim() }) })) } catch (error) { setError(errorText(error)) } finally { setBusy(false) } }}><ErrorBanner message={error} /><label>Display name<input value={name} onChange={event => setName(event.target.value)} minLength={2} maxLength={60} required /></label><p className="fine-print">This name appears on your listings and messages. Your email stays private.</p><button className="button primary" disabled={busy}>{busy ? 'Saving…' : 'Save profile'}</button></form></Modal>
}
