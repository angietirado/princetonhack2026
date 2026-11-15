import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowLeft, Bike, Calculator, Check, Coffee, LampDesk, Package, PackageOpen, Refrigerator, X, Zap, type LucideIcon } from 'lucide-react';
import { api, errorText, money, type Listing, type ListingKind } from './api';

export const locations = ['Campus Center', 'Redwood Hall', 'GITC', 'Maple Hall', 'Fenster Hall', 'Central King Building', 'Eberhardt Hall', 'Ying Wu College of Computing', 'Wellness and Events Center', 'Laurel Hall', 'Oak Hall', 'Cypress Hall', 'Greek Village', 'Parking Deck'];
export const kinds: Record<ListingKind, string> = { sale: 'For sale', rent: 'For Rent', free: 'Free', urgent: 'Urgent', wanted: 'Wanted' };
export const json = (value: unknown) => JSON.stringify(value);
export const go = (path: string) => { window.history.pushState({}, '', path); window.dispatchEvent(new PopStateEvent('popstate')); window.scrollTo(0, 0); };
export const initials = (name: string) => name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase();
export const price = (listing: Listing) => listing.kind === 'urgent' ? 'URGENT' : listing.kind === 'wanted' ? 'WANTED' : listing.kind === 'free' || listing.price_cents === 0 ? 'FREE' : `${money(listing.price_cents)}${listing.kind === 'rent' ? '/day' : ''}`;
export const appearance = (listing: Pick<Listing, 'title' | 'category' | 'kind'>): { icon: LucideIcon; color: string } => {
  const title = listing.title.toLowerCase();
  if (listing.kind === 'urgent') return { icon: Zap, color: 'peach' };
  if (title.includes('fridge')) return { icon: Refrigerator, color: 'sage' };
  if (title.includes('calculator')) return { icon: Calculator, color: 'blue' };
  if (title.includes('lamp')) return { icon: LampDesk, color: 'sand' };
  if (title.includes('bike')) return { icon: Bike, color: 'blue' };
  if (title.includes('coffee')) return { icon: Coffee, color: 'aqua' };
  return { icon: Package, color: listing.kind === 'free' ? 'peach' : listing.category === 'electronics' ? 'blue' : 'sage' };
};
export function useResource<T>(path: string | null, revision = 0, polling = 0) {
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<{ key: string; data?: T; error: string; loading: boolean }>({ key: '', error: '', loading: true });
  const key = `${path}|${revision}`;
  useEffect(() => {
    if (!path) return;
    let active = true; const controller = new AbortController();
    setState(old => ({ key, data: old.key === key ? old.data : undefined, error: '', loading: true }));
    api<T>(path, { signal: controller.signal }).then(data => { if (active) setState({ key, data, error: '', loading: false }); }).catch(error => { if (active) setState(old => ({ key, data: old.key === key ? old.data : undefined, error: errorText(error), loading: false })); });
    return () => { active = false; controller.abort(); };
  }, [path, key, tick]);
  useEffect(() => { if (!polling || !path) return; const timer = window.setInterval(() => setTick(value => value + 1), polling); return () => window.clearInterval(timer); }, [polling, path]);
  return { data: state.key === key ? state.data : undefined, error: state.key === key ? state.error : '', loading: !!path && (state.key !== key || state.loading), refresh: () => setTick(value => value + 1) };
}
export function useMounted() {
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  return mounted;
}
export function Button({ children, className = '', onClick, type = 'button', disabled = false }: { children: ReactNode; className?: string; onClick?: () => void; type?: 'button' | 'submit'; disabled?: boolean }) { return <button type={type} className={`button ${className}`} onClick={onClick} disabled={disabled}>{children}</button>; }
export function Logo({ light = true }: { light?: boolean }) { return <button aria-label="2ndLife home" className={`logo ${light ? 'logo-light' : ''}`} onClick={() => go('/home')}><span>2</span><b>LIFE</b></button>; }
export function ErrorNotice({ message, retry }: { message: string; retry?: () => void }) { return message ? <div className="error-notice" role="alert">{message}{retry && <button onClick={retry}>Try again</button>}</div> : null; }
export function Empty({ title, children }: { title: string; children?: ReactNode }) { return <div className="empty-state"><PackageOpen size={30} /><h3>{title}</h3>{children && <p>{children}</p>}</div>; }
export function Loading() { return <p className="loading-note" role="status">Loading…</p>; }
export function PageTop({ title, back = '/home' }: { title: string; back?: string }) { return <div className="page-top"><button className="icon-btn" aria-label="Back" onClick={() => go(back)}><ArrowLeft size={20} /></button><h1>{title}</h1><span /></div>; }
export function ModalShell({ children, onClose, className = '', title = 'Details' }: { children: ReactNode; onClose: () => void; className?: string; title?: string }) {
  const ref = useRef<HTMLDivElement>(null); const close = useRef(onClose); close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null; const oldOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; ref.current?.focus();
    const handler = (event: KeyboardEvent) => { if (event.key === 'Escape') close.current(); if (event.key === 'Tab') { const nodes = Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]') || []).filter(node => node.offsetParent !== null); const first = nodes[0], last = nodes[nodes.length - 1]; if (event.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); } } };
    document.addEventListener('keydown', handler); return () => { document.removeEventListener('keydown', handler); document.body.style.overflow = oldOverflow; previous?.focus(); };
  }, []);
  return <div className="overlay" onMouseDown={event => event.target === event.currentTarget && onClose()}><div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} className={`modal-sheet ${className}`}><button className="modal-close" aria-label="Close dialog" onClick={onClose}><X size={19} /></button>{children}</div></div>;
}
