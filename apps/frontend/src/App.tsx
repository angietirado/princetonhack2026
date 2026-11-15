import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { api, ApiError, categories, conditions, errorText, money, type Category, type Config, type Conversation, type DevStudent, type Listing, type ListingStatus, type Page, type User } from './api'
import { StudentCheckDialog, DetailDialog, ErrorBanner, ListingEditor, Modal, ProfileDialog } from './dialogs'
import Inbox from './Inbox'
import { Avatar, Icon, ListingImage } from './visuals'

type Tab = 'browse' | 'selling' | 'inbox'
type Dialog = 'student-check' | 'create' | 'edit' | 'detail' | 'profile' | 'delete' | 'clear-profile' | null

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [config, setConfig] = useState<Config | null>(null)
  const [devStudents, setDevStudents] = useState<DevStudent[]>([])
  const [switchingDev, setSwitchingDev] = useState(false)
  const [devError, setDevError] = useState('')
  const [sessionLoading, setSessionLoading] = useState(true)
  const [tab, setTab] = useState<Tab>('browse')
  const [dialog, setDialog] = useState<Dialog>(null)
  const [studentIntent, setStudentIntent] = useState<'sell' | 'inbox' | 'selling' | 'contact' | null>(null)
  const [selected, setSelected] = useState<Listing | null>(null)
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [listings, setListings] = useState<Listing[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState<Category | ''>('')
  const [sort, setSort] = useState('newest')
  const [maxPrice, setMaxPrice] = useState('')
  const [priceInput, setPriceInput] = useState('')
  const [showFilters, setShowFilters] = useState(false)
  const [page, setPage] = useState(1)
  const [revision, setRevision] = useState(0)
  const [busyId, setBusyId] = useState('')
  const [clearingProfile, setClearingProfile] = useState(false)
  const identityVersion = useRef(0)
  const switchPending = useRef(false)
  const refresh = () => setRevision(value => value + 1)

  useEffect(() => {
    let active = true
    const initialize = async () => {
      const [profileResult, configResult] = await Promise.allSettled([api<User>('/me'), api<Config>('/config')])
      if (!active) return
      if (profileResult.status === 'fulfilled') setUser(profileResult.value)
      else if (!(profileResult.reason instanceof ApiError && profileResult.reason.status === 401)) setError('The server is unavailable. Please try again.')
      setSessionLoading(false)
      if (configResult.status === 'fulfilled') {
        setConfig(configResult.value)
        if (configResult.value.devmode) {
          try {
            const result = await api<{ items: DevStudent[] }>('/dev/students')
            if (active) setDevStudents(result.items)
          } catch { if (active) setDevStudents([]) }
        }
      }
    }
    void initialize()
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(''), 4500)
    return () => clearTimeout(timer)
  }, [toast])

  const fetchListings = useCallback(async () => {
    if (tab === 'inbox' || switchingDev || (tab === 'selling' && !user)) return
    const params = new URLSearchParams({ page: String(page), page_size: '12' })
    if (tab === 'browse') {
      params.set('sort', sort)
      if (search) params.set('q', search)
      if (category) params.set('category', category)
      if (maxPrice !== '') params.set('max_price', String(Math.round(Number(maxPrice) * 100)))
    }
    return api<Page<Listing>>(`${tab === 'selling' ? '/me/listings' : '/listings'}?${params}`)
  }, [tab, user, switchingDev, page, sort, search, category, maxPrice])

  useEffect(() => {
    if (tab === 'inbox' || switchingDev) return
    let active = true
    const identity = identityVersion.current
    setLoading(true); setError('')
    fetchListings().then(result => {
      if (!active || identity !== identityVersion.current || !result) return
      const lastPage = Math.max(1, Math.ceil(result.total / 12))
      if (page > lastPage) { setPage(lastPage); return }
      setListings(result.items); setTotal(result.total)
    }).catch(error => {
      if (active && identity === identityVersion.current) setError(errorText(error))
    }).finally(() => {
      if (active && identity === identityVersion.current) setLoading(false)
    })
    return () => { active = false }
  }, [fetchListings, revision, tab, switchingDev, page])

  const navigate = (next: Tab) => {
    if (switchingDev) return
    if (next !== 'browse' && !user) { setStudentIntent(next); setDialog('student-check'); return }
    setTab(next); setPage(1); setError('')
  }
  const sell = () => {
    if (!user) { setStudentIntent('sell'); setDialog('student-check') }
    else { setSelected(null); setDialog('create') }
  }
  const onStudentChecked = (studentProfile: User) => {
    identityVersion.current++
    setUser(studentProfile); setToast('Your profile is ready.')
    if (studentIntent === 'sell') { setSelected(null); setDialog('create') }
    else if (studentIntent === 'contact') setDialog('detail')
    else { setDialog(null); if (studentIntent) { setTab(studentIntent); setPage(1) } }
    setStudentIntent(null)
  }
  const switchDevStudent = async (username: string) => {
    if (switchPending.current || sessionLoading || user?.dev_username === username) return
    switchPending.current = true
    identityVersion.current++
    setSwitchingDev(true); setLoading(true); setError(''); setDevError(''); setToast('')
    setDialog(null); setStudentIntent(null); setSelected(null); setConversation(null)
    setListings([]); setTotal(0); setPage(1); setBusyId('')
    try {
      const profile = await api<User>('/dev/session', { method: 'POST', body: JSON.stringify({ username }) })
      setUser(profile); setToast(`Using ${username}.`)
    } catch (error) {
      setDevError(errorText(error))
    } finally { switchPending.current = false; setSwitchingDev(false); refresh() }
  }
  const clearProfile = async () => {
    setClearingProfile(true); setError('')
    try {
      await api('/student/session', { method: 'DELETE' })
      identityVersion.current++
      setUser(null); setTab('browse'); setPage(1); setConversation(null); setSelected(null); setDialog(null)
      setListings([]); setToast('Profile cleared from this browser.')
    } catch (error) { setError(errorText(error)) } finally { setClearingProfile(false) }
  }
  const changeStatus = async (listing: Listing, status: ListingStatus) => {
    setBusyId(listing.id); setError('')
    try { await api(`/listings/${listing.id}`, { method: 'PATCH', body: JSON.stringify({ status }) }); refresh(); setToast(`Listing marked ${status}.`) }
    catch (error) { setError(errorText(error)) } finally { setBusyId('') }
  }
  const removeListing = async () => {
    if (!selected) return
    setBusyId(selected.id); setError('')
    try { await api(`/listings/${selected.id}`, { method: 'DELETE' }); setDialog(null); refresh(); setToast('Listing removed.') }
    catch (error) { setError(errorText(error)) } finally { setBusyId('') }
  }
  const doSearch = (event: FormEvent) => { event.preventDefault(); setSearch(query.trim()); setPage(1) }
  const clearFilters = () => { setSearch(''); setQuery(''); setCategory(''); setMaxPrice(''); setPriceInput(''); setSort('newest'); setPage(1) }
  const filtered = tab === 'browse' && Boolean(search || category || maxPrice)

  return <>
    <header className="site-header">
      <div className="header-inner">
        <button className="brand" onClick={() => navigate('browse')} aria-label="Campus Exchange home"><span className="brand-symbol"><Icon name="exchange" size={23} /></span><span>Campus Exchange</span></button>
        <nav aria-label="Main navigation">
          <button className={tab === 'browse' ? 'active' : ''} onClick={() => navigate('browse')}>Browse</button>
          <button className={tab === 'selling' ? 'active' : ''} onClick={() => navigate('selling')}>My listings</button>
          <button className={tab === 'inbox' ? 'active' : ''} onClick={() => navigate('inbox')}>Messages</button>
        </nav>
        <div className="header-actions">
          {user ? <><button className="profile-button" disabled={switchingDev} onClick={() => setDialog('profile')} aria-label={`Edit ${user.display_name}'s profile`}><Avatar name={user.display_name} small /><span>{user.display_name}</span></button><button className="icon-button" disabled={switchingDev} onClick={() => { setError(''); setDialog('clear-profile') }} aria-label="Clear profile" title="Clear profile"><Icon name="logout" size={18} /></button></> : <button className="student-button" onClick={() => { setStudentIntent(null); setDialog('student-check') }} disabled={sessionLoading || switchingDev}>{sessionLoading ? 'Connecting…' : 'Check .edu email'}</button>}
          <button className="button primary sell-button" onClick={sell} disabled={switchingDev}><Icon name="plus" size={18} /><span>Sell an item</span></button>
        </div>
      </div>
    </header>

    <main className="main-container">
      {config?.devmode && devStudents.length > 0 && <section className="dev-switcher" aria-label="Development student profiles">
        <span className="dev-label">Dev mode</span>
        <div className="dev-students">{devStudents.map(student => <button key={student.username} aria-pressed={user?.dev_username === student.username} disabled={switchingDev || sessionLoading} onClick={() => switchDevStudent(student.username)}>{student.username}</button>)}</div>
        <span className="dev-hint">{switchingDev ? 'Switching profile…' : 'Choose a test student'}</span>
      </section>}
      <ErrorBanner message={devError} />

      {tab === 'browse' && <section className="marketplace" id="marketplace">
        <div className="page-heading"><h1>Student marketplace</h1><p>Buy and sell with other students. Arrange payment and pickup directly.</p></div>
        <form className="market-search" onSubmit={doSearch}><Icon name="search" size={20} /><label className="sr-only" htmlFor="search">Search listings</label><input id="search" placeholder="Search books, furniture, electronics…" value={query} onChange={event => setQuery(event.target.value)} maxLength={100} /><button className="button primary" aria-label="Search listings">Search</button></form>
        <div className="category-row" role="group" aria-label="Listing categories"><button className={`category-button ${category === '' ? 'selected' : ''}`} onClick={() => { setCategory(''); setPage(1) }}>All items</button>{categories.map(item => <button key={item.id} className={`category-button ${category === item.id ? 'selected' : ''}`} onClick={() => { setCategory(item.id); setPage(1) }}>{item.name}</button>)}</div>
        <div className="results-toolbar"><div className="result-count">{loading ? 'Loading listings…' : <><strong>{total}</strong> {total === 1 ? 'listing' : 'listings'}{search && <> for “{search}”</>}</>}{filtered && <button className="text-button" onClick={clearFilters}>Clear filters</button>}</div><div className="sort-actions"><button className={`filter-button ${showFilters || maxPrice !== '' ? 'on' : ''}`} onClick={() => setShowFilters(!showFilters)} aria-expanded={showFilters}><Icon name="filter" size={16} />Price</button><select aria-label="Sort listings" value={sort} onChange={event => { setSort(event.target.value); setPage(1) }}><option value="newest">Newest first</option><option value="price_asc">Price: low to high</option><option value="price_desc">Price: high to low</option></select></div></div>
        {showFilters && <form className="price-filter" onSubmit={event => { event.preventDefault(); setMaxPrice(priceInput); setPage(1) }}><label htmlFor="max-price">Maximum price (USD)</label><input id="max-price" type="number" min="0" max="100000" step="0.01" placeholder="Any price" value={priceInput} onChange={event => setPriceInput(event.target.value)} /><button className="button primary small">Apply</button>{maxPrice !== '' && <button type="button" className="text-button" onClick={() => { setMaxPrice(''); setPriceInput(''); setPage(1) }}>Reset</button>}</form>}
      </section>}

      {tab === 'selling' && <div className="page-heading"><h1>My listings</h1><p>Edit your items and update their availability.</p></div>}

      {switchingDev ? <div className="empty-state" role="status"><p>Switching profile…</p></div> : tab !== 'inbox' && <>
        <ErrorBanner message={error} />
        {error ? <div className="retry-row"><button className="button secondary" onClick={refresh}>Try again</button></div> : loading ? <div className="listing-grid" aria-label="Loading listings">{Array.from({ length: 4 }, (_, i) => <div className="skeleton-card" key={i}><div /><span /><span /></div>)}</div> : listings.length ? <div className="listing-grid">{listings.map(listing => <article className="listing-card" key={listing.id}>
          <button className="listing-open" onClick={() => { setSelected(listing); setDialog('detail') }} aria-label={`View ${listing.title}, ${money(listing.price_cents)}`}>
            <div className="listing-image-wrap"><ListingImage listing={listing} /><span className="condition-tag">{conditions[listing.condition]}</span>{listing.status !== 'available' && <span className={`status-badge status-${listing.status}`}>{listing.status}</span>}{listing.is_demo && <span className="demo-label">Demo</span>}</div>
            <div className="listing-content"><div className="listing-price">{money(listing.price_cents)}</div><h3>{listing.title}</h3><div className="listing-location"><Icon name="pin" size={14} /><span>{listing.pickup_location}</span></div><div className="listing-seller"><span>{listing.seller.display_name}</span><time dateTime={listing.created_at}>{new Date(listing.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</time></div></div>
          </button>
          {tab === 'selling' && <div className="owner-controls"><label className="sr-only" htmlFor={`status-${listing.id}`}>Status for {listing.title}</label><select id={`status-${listing.id}`} value={listing.status} onChange={event => changeStatus(listing, event.target.value as ListingStatus)} disabled={busyId === listing.id}><option value="available">Available</option><option value="reserved">Reserved</option><option value="sold">Sold</option></select><button className="icon-button" aria-label={`Edit ${listing.title}`} onClick={() => { setSelected(listing); setDialog('edit') }}><Icon name="edit" size={17} /></button><button className="icon-button" aria-label={`Delete ${listing.title}`} onClick={() => { setSelected(listing); setDialog('delete') }}><Icon name="trash" size={17} /></button></div>}
        </article>)}</div> : <div className="empty-state"><Icon name={tab === 'selling' ? 'bag' : 'search'} size={30} /><h2>{tab === 'selling' ? 'No listings yet' : filtered ? 'No matching listings' : 'No items listed yet'}</h2><p>{tab === 'selling' ? 'Post an item you want to sell.' : filtered ? 'Try another search or clear the filters.' : 'Be the first to list something.'}</p><button className="button primary" onClick={filtered ? clearFilters : sell}>{filtered ? 'Clear filters' : 'Sell an item'}</button></div>}
        {!loading && !error && total > 12 && <div className="pagination"><button className="button secondary small" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page} of {Math.ceil(total / 12)}</span><button className="button secondary small" disabled={page * 12 >= total} onClick={() => setPage(page + 1)}>Next</button></div>}
      </>}
      {!switchingDev && tab === 'inbox' && user && <Inbox key={user.id} user={user} initialConversation={conversation} />}
    </main>

    {toast && <div className="toast" role="status"><Icon name="check" size={19} /><span>{toast}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setToast('')}><Icon name="close" size={16} /></button></div>}
    {dialog === 'student-check' && <StudentCheckDialog onClose={() => { setDialog(null); setStudentIntent(null) }} onSuccess={onStudentChecked} />}
    {(dialog === 'create' || dialog === 'edit') && user && <ListingEditor listing={dialog === 'edit' ? selected || undefined : undefined} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); setTab('selling'); setPage(1); refresh(); setToast(dialog === 'edit' ? 'Listing updated.' : 'Listing published.') }} />}
    {dialog === 'detail' && selected && <DetailDialog listing={selected} user={user} onClose={() => setDialog(null)} onCheckStudent={() => { setStudentIntent('contact'); setDialog('student-check') }} onEdit={() => setDialog('edit')} onConversation={conversation => { setConversation(conversation); setTab('inbox'); setDialog(null) }} />}
    {dialog === 'profile' && user && <ProfileDialog user={user} onClose={() => setDialog(null)} onSaved={user => { setUser(user); setDialog(null); refresh(); setToast('Profile updated.') }} />}
    {dialog === 'clear-profile' && <Modal title="Clear this profile?" subtitle={user?.dev_username ? 'You can select this test student again from the dev switcher. Its listings and messages will stay saved.' : 'This browser will lose access to your listings and messages. Entering the same email later creates a new profile.'} onClose={() => { if (!clearingProfile) setDialog(null) }}><ErrorBanner message={error} /><div className="form-actions"><button className="button secondary" disabled={clearingProfile} onClick={() => setDialog(null)}>Keep profile</button><button className="button danger" disabled={clearingProfile} onClick={clearProfile}>{clearingProfile ? 'Clearing…' : 'Clear profile'}</button></div></Modal>}
    {dialog === 'delete' && selected && <Modal title="Remove listing?" subtitle={`“${selected.title}” will be removed. Existing messages will remain available.`} onClose={() => setDialog(null)}><ErrorBanner message={error} /><div className="form-actions"><button className="button secondary" onClick={() => setDialog(null)}>Keep listing</button><button className="button danger" disabled={busyId === selected.id} onClick={removeListing}>{busyId === selected.id ? 'Removing…' : 'Remove listing'}</button></div></Modal>}
  </>
}
