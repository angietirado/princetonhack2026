import { useEffect, useState, type CSSProperties } from 'react'
import type { Category, Listing } from './api'

export type IconName = 'arrow' | 'search' | 'plus' | 'close' | 'check' | 'shield' | 'message' | 'pin' | 'bag' | 'logout' | 'edit' | 'trash' | 'send' | 'filter' | 'chevron' | 'books' | 'electronics' | 'furniture' | 'clothing' | 'sports' | 'other' | 'exchange'
const paths: Record<IconName, React.ReactNode> = {
  arrow: <><path d="M5 12h14m-6-6 6 6-6 6" /></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  check: <path d="m5 12 4 4L19 6" />,
  shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z" /><path d="m8 12 3 3 5-6" /></>,
  message: <path d="M20 11a8 8 0 0 1-8 8H4l-2 3V11a9 9 0 0 1 18 0Z" />,
  pin: <><path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 0 1 14 0Z" /><circle cx="12" cy="10" r="2.3" /></>,
  bag: <><path d="M5 7h14l1 14H4L5 7Z" /><path d="M8 8V6a4 4 0 0 1 8 0v2" /></>,
  logout: <><path d="M10 4H4v16h6m5-13 5 5-5 5m-7-5h12" /></>,
  edit: <><path d="m15 5 4 4M4 20l5-1L21 7l-4-4L5 15l-1 5Z" /></>,
  trash: <><path d="M3 6h18M8 6V3h8v3M6 6l1 15h10l1-15M10 10v7m4-7v7" /></>,
  send: <><path d="m21 3-6 18-4-8-8-4 18-6ZM11 13 21 3" /></>,
  filter: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="2" fill="currentColor" /><circle cx="15" cy="17" r="2" fill="currentColor" /></>,
  chevron: <path d="m9 5 7 7-7 7" />,
  books: <><path d="M3 4h7v16H3zM14 5l6-1 3 15-6 1zM3 8h7m-7 8h7m6-7 5-1" /></>,
  electronics: <><rect x="3" y="4" width="18" height="13" rx="2" /><path d="M8 21h8m-4-4v4" /></>,
  furniture: <><path d="M5 13V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v8M3 12v7h18v-7M6 19v3m12-3v3M3 15h18" /></>,
  clothing: <path d="m8 3-6 4 3 5 3-2v11h8V10l3 2 3-5-6-4a4 4 0 0 1-8 0Z" />,
  sports: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c-5 4-5 14 0 18m0-18c5 4 5 14 0 18" /></>,
  other: <><path d="m12 3 9 5v9l-9 5-9-5V8l9-5ZM3 8l9 5 9-5m-9 5v9m-5-17 10 6" /></>,
  exchange: <><path d="M4 7h16l-4-4M20 17H4l4 4" /></>,
}
export function Icon({ name, size = 20, className = '' }: { name: IconName; size?: number; className?: string }) {
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={className}>{paths[name]}</svg>
}

export function ItemArt({ category = 'other', className = '' }: { category?: Category; className?: string }) {
  return <div className={`item-art art-${category} ${className}`} aria-hidden="true"><div className="art-orbit" /><div className="art-object"><Icon name={category} size={86} /></div><span className="art-dot" /></div>
}

export function ListingImage({ listing, className = '' }: { listing: Pick<Listing, 'image_url' | 'title' | 'category'>; className?: string }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [listing.image_url])
  return listing.image_url && !failed
    ? <img className={`listing-photo ${className}`} src={listing.image_url} alt={listing.title} onError={() => setFailed(true)} loading="lazy" referrerPolicy="no-referrer" />
    : <ItemArt category={listing.category} className={className} />
}

export function HeroArt() {
  return <div className="hero-art" aria-hidden="true">
    <div className="hero-orbit orbit-one" /><div className="hero-orbit orbit-two" />
    <div className="floating-note"><span className="note-star">✳</span> A little less new.<br /><strong>A lot more you.</strong></div>
    <div className="hero-book book-back" /><div className="hero-book book-front"><span>THE NEXT<br />CHAPTER</span><div className="book-flower">✳</div><small>PASS IT ON.</small></div>
    <div className="desk-lamp"><div className="lamp-head" /><div className="lamp-arm" /><div className="lamp-base" /></div>
    <div className="hero-badge"><Icon name="exchange" size={18} /><span>Good things,<br /><b>going around.</b></span></div>
    <span className="hero-spark spark-one">✧</span><span className="hero-spark spark-two">✳</span>
  </div>
}

export function Avatar({ name, small = false }: { name: string; small?: boolean }) {
  const colors = ['#e3ebd8', '#f5e4d4', '#e0e9ec', '#eee0e5']
  const color = colors[(name.charCodeAt(0) || 0) % colors.length]
  return <span className={`avatar ${small ? 'avatar-small' : ''}`} style={{ backgroundColor: color } as CSSProperties}>{name.slice(0, 1).toUpperCase() || 'S'}</span>
}
