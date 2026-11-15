import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft, ArrowRight, Bell, Bike, Check, ChevronRight, CircleHelp, Clock3, Coffee,
  HandHeart, MapPin, Menu, MessageCircle, PackageOpen, Plus, Search, ShieldCheck,
  Sparkles, Store, Tag, Ticket, Timer, UserRound, Wifi, Wrench, X, Zap
} from "lucide-react";
import { categories, listings, orders, repairers, type Listing, type ListingCategory } from "./data";

type Modal = "urgent" | "item" | "profile" | "repairer" | "application" | "chat" | "map" | "location" | null;
type Tab = "Buying" | "Selling" | "Borrowing";

const go = (path: string) => {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
};

const normalizePath = (path: string) => {
  const trimmed = path.replace(/\/+$/, "");
  return trimmed || "/";
};

function Logo({ light = true }: { light?: boolean }) {
  return <div className={`logo ${light ? "logo-light" : ""}`} onClick={() => go("/home")}><span>2</span><b>LIFE</b></div>;
}

function Button({ children, className = "", onClick, type = "button" }: { children: React.ReactNode; className?: string; onClick?: () => void; type?: "button" | "submit" }) {
  return <button type={type} className={`button ${className}`} onClick={onClick}>{children}</button>;
}

function App() {
  const [path, setPath] = useState(normalizePath(window.location.pathname));
  const [modal, setModal] = useState<Modal>(null);
  const [selectedListing, setSelectedListing] = useState<Listing | null>(null);
  const [selectedRepairer, setSelectedRepairer] = useState<typeof repairers[number] | null>(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [homeResetKey, setHomeResetKey] = useState(0);
  const [iouTickets, setIouTickets] = useState(1);

  useEffect(() => {
    const handler = () => setPath(normalizePath(window.location.pathname));
    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);

  const notify = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 2400);
  };
  const openListing = (listing: Listing) => { setSelectedListing(listing); setModal("item"); };
  const knownPath = path === "/" || path === "/login" || path === "/home" || path === "/repair" || path === "/post" || path === "/orders" || path.startsWith("/orders/");
  const isAuth = knownPath && path !== "/" && path !== "/login";

  return (
    <div className="app-frame">
      {!knownPath ? <LandingPage /> : !isAuth ? <PublicPage path={path} onNotify={notify} /> : (
        <div className="mobile-app">
          <div className="app-content">
            {path === "/home" && <HomePage key={homeResetKey} iouTickets={iouTickets} onOpenListing={openListing} onUrgent={() => setModal("urgent")} onProfile={() => setProfileOpen(true)} onLocation={() => setModal("location")} />}
            {path === "/repair" && <RepairPage onApply={() => setModal("application")} onRepairer={(repairer) => { setSelectedRepairer(repairer); setModal("repairer"); }} />}
            {path === "/post" && <PostPage notify={notify} onFreePost={() => setIouTickets((value) => value + 1)} />}
            {path === "/orders" && <OrdersPage onOrder={(id) => go(`/orders/${id}`)} onNotify={notify} />}
            {path.startsWith("/orders/") && <OrderDetailPage orderId={path.split("/")[2]} onModal={setModal} />}
          </div>
          {!path.startsWith("/orders/") && <BottomNav path={path} onHome={() => { setHomeResetKey((value) => value + 1); go("/home"); }} />}
        </div>
      )}
      {modal === "urgent" && <UrgentModal onClose={() => setModal(null)} notify={notify} />}
      {modal === "item" && selectedListing && <ItemModal listing={selectedListing} onClose={() => setModal(null)} notify={notify} />}
      {modal === "profile" && <ProfileDrawer onClose={() => setModal(null)} notify={notify} onSignOut={() => { setModal(null); go("/login"); notify("Signed out"); }} />}
      {profileOpen && <ProfileDrawer onClose={() => setProfileOpen(false)} notify={notify} onSignOut={() => { setProfileOpen(false); go("/login"); notify("Signed out"); }} />}
      {modal === "repairer" && selectedRepairer && <RepairBooking repairer={selectedRepairer} onClose={() => setModal(null)} notify={notify} />}
      {modal === "application" && <ApplicationDrawer onClose={() => setModal(null)} notify={notify} />}
      {modal === "chat" && <ChatDrawer onClose={() => setModal(null)} />}
      {modal === "map" && <MapModal onClose={() => setModal(null)} />}
      {modal === "location" && <LocationModal onClose={() => setModal(null)} notify={notify} />}
      {toast && <div className="toast"><Check size={16} />{toast}</div>}
    </div>
  );
}

function PublicPage({ path, onNotify }: { path: string; onNotify: (message: string) => void }) {
  if (path === "/login") return <LoginPage onNotify={onNotify} />;
  return <LandingPage />;
}

function LandingPage() {
  return <div className="landing">
    <div className="landing-hero">
      <Logo />
      <div className="hero-art"><div className="hero-circle"><PackageOpen size={72} strokeWidth={1.2} /></div><span className="hero-spark">✦</span></div>
      <p className="eyebrow light">NJIT student exchange</p>
      <h1>Give campus life<br /><em>a second life.</em></h1>
      <p className="landing-copy">Buy, sell, repair, and share what you need with students nearby.</p>
    </div>
    <div className="landing-body">
      <div className="value-props"><Value icon={Store} title="Buy & sell as-is" /><Value icon={Wrench} title="Campus repairs" /><Value icon={Zap} title="Urgent requests" /><Value icon={HandHeart} title="Share locally" /></div>
      <Button className="primary full" onClick={() => go("/login")}>Sign up with .edu <ArrowRight size={17} /></Button>
      <button className="text-link" onClick={() => go("/login")}>Already have an account? <b>Log in</b></button>
    </div>
  </div>;
}

function Value({ icon: Icon, title }: { icon: typeof Store; title: string }) {
  return <div><div className="value-icon"><Icon size={18} /></div><span>{title}</span></div>;
}

function LoginPage({ onNotify }: { onNotify: (message: string) => void }) {
  const [email, setEmail] = useState("alex@njit.edu");
  const [password, setPassword] = useState("");
  const [signUp, setSignUp] = useState(false);
  const submit = (event: React.FormEvent) => { event.preventDefault(); if (!email.endsWith(".edu")) { onNotify("Use your .edu campus email"); return; } go("/home"); };
  return <div className="auth-page">
    <div className="auth-top"><button className="icon-btn" onClick={() => go("/")}><ArrowLeft size={20} /></button><Logo light={false} /><span /></div>
    <div className="auth-inner">
      <div className="auth-welcome"><div className="small-logo"><Sparkles size={16} /></div><p className="eyebrow">NJIT student access</p><h1>{signUp ? "Join your campus marketplace." : "Welcome back to 2ndLife."}</h1><p>Verified .edu students only. Your campus, your community, less waste.</p></div>
      <form className="form-stack" onSubmit={submit}>
        {signUp && <label>Full name<input placeholder="Alex Johnson" required /></label>}
        <label>Campus email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@university.edu" required /></label>
        <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" required={signUp} /></label>
        {signUp && <div className="toggle-row"><span><ShieldCheck size={17} /> Enable student verification</span><input type="checkbox" defaultChecked /></div>}
        <Button type="submit" className="primary full">{signUp ? "Create student account" : "Log in"} <ArrowRight size={17} /></Button>
      </form>
      {!signUp && <Button className="demo-button" onClick={() => { setEmail("alex@njit.edu"); setPassword("demo"); go("/home"); }}><Sparkles size={16} /> Quick demo login (Alex @ NJIT)</Button>}
      <p className="switch-auth">{signUp ? "Already verified?" : "New to 2ndLife?"} <button onClick={() => setSignUp(!signUp)}>{signUp ? "Log in" : "Sign up with .edu"}</button></p>
    </div>
    <p className="auth-foot"><ShieldCheck size={14} /> Your student status is verified securely.</p>
  </div>;
}

function AppHeader({ onProfile, onSearch, onLocation }: { onProfile?: () => void; onSearch?: (value: string) => void; onLocation?: () => void }) {
  return <header className="app-header">
    <div className="header-row"><Logo /><button className="location-pill" onClick={onLocation}><MapPin size={13} /> NJIT · Campus Center <ChevronRight size={13} /></button><button className="avatar-btn" onClick={onProfile}><UserRound size={17} /></button></div>
    <div className="search-field"><Search size={17} /><input placeholder="Search mini fridge, calculator, tape..." onChange={(event) => onSearch?.(event.target.value)} /><Bell size={17} /></div>
  </header>;
}

function HomePage({ iouTickets, onOpenListing, onUrgent, onProfile, onLocation }: { iouTickets: number; onOpenListing: (listing: Listing) => void; onUrgent: () => void; onProfile: () => void; onLocation: () => void }) {
  const [filter, setFilter] = useState<ListingCategory | "All">("All");
  const [query, setQuery] = useState("");
  const [priceFilter, setPriceFilter] = useState("Any price");
  const shown = useMemo(() => {
    const marketplaceItems = listings.filter((item) => (filter === "Urgent" ? item.category === "Urgent" : ["For sale", "For Rent", "Free"].includes(item.category)) && (filter === "All" || item.category === filter) && `${item.title} ${item.subtitle}`.toLowerCase().includes(query.toLowerCase()));
    const inRange = marketplaceItems.filter((item) => {
      const price = item.price === "FREE" ? 0 : Number(item.price.replace(/[^0-9.]/g, "")) || 0;
      return priceFilter === "Any price" || priceFilter === "Lowest first" || priceFilter === "Highest first" || priceFilter === "Under $10" && price < 10 || priceFilter === "$10-$30" && price >= 10 && price <= 30 || priceFilter === "$30+" && price > 30;
    });
    if (priceFilter === "Lowest first") return [...inRange].sort((a, b) => (a.price === "FREE" ? 0 : Number(a.price.replace(/[^0-9.]/g, "")) || 0) - (b.price === "FREE" ? 0 : Number(b.price.replace(/[^0-9.]/g, "")) || 0));
    if (priceFilter === "Highest first") return [...inRange].sort((a, b) => (b.price === "FREE" ? 0 : Number(b.price.replace(/[^0-9.]/g, "")) || 0) - (a.price === "FREE" ? 0 : Number(a.price.replace(/[^0-9.]/g, "")) || 0));
    return inRange;
  }, [filter, priceFilter, query]);
  return <><AppHeader onProfile={onProfile} onSearch={setQuery} onLocation={onLocation} /><main className="page home-page">
    <div className="filter-bar">{categories.map(({ label, icon: Icon }) => <button key={label} className={`filter-button ${label === filter ? "active" : ""} ${label.toLowerCase().replace(" ", "-")}`} onClick={() => setFilter(filter === label ? "All" : label)}><span><Icon size={17} /></span><b>{label}</b></button>)}</div>
    {filter === "Free" && <div className="iou-card"><div className="iou-icon"><Ticket size={21} /></div><div><strong>{iouTickets} IOU {iouTickets === 1 ? "ticket" : "tickets"}</strong><small>Post an item for free to earn +1 ticket.</small></div><span className="iou-count">+1</span></div>}
    <section className="urgent-banner"><div className="urgent-copy"><span className="badge urgent-badge">URGENT</span><strong>Need packing tape for a poster</strong><small>Campus Center · 2 min walk · just now</small></div><Button className="terracotta" onClick={onUrgent}>I can help</Button></section>
    <div className="section-heading"><h2>Near you</h2><div className="section-actions"><label className="price-filter compact"><select aria-label="Filter by price" value={priceFilter} onChange={(event) => setPriceFilter(event.target.value)}><option>Any price</option><option>Lowest first</option><option>Highest first</option><option>Under $10</option><option>$10-$30</option><option>$30+</option></select></label><button onClick={() => { setFilter("All"); setPriceFilter("Any price"); }}>See all</button></div></div>
    <div className="listing-grid">{shown.map((item) => <ListingCard key={item.id} listing={item} onClick={() => onOpenListing(item)} />)}</div>
    {shown.length === 0 && <div className="empty-state"><Search size={28} /><p>No listings match that search.</p></div>}
  </main></>;
}

function ListingCard({ listing, onClick }: { listing: Listing; onClick: () => void }) {
  const Icon = listing.icon;
  return <button className="listing-card" onClick={onClick}><div className={`listing-art ${listing.color}`}><span className={`badge ${listing.category === "Urgent" ? "urgent-badge" : listing.category === "For Rent" ? "wanted-badge" : "as-is-badge"}`}>{listing.price === "FREE" ? "FREE" : listing.category === "Urgent" ? "URGENT" : listing.category === "For Rent" ? "FOR RENT" : "As-is"}</span><Icon size={30} strokeWidth={1.35} /></div><div className="listing-info"><strong>{listing.title}</strong><small>{listing.subtitle}</small><b className={listing.category === "For Rent" ? "blue-text" : listing.category === "Urgent" ? "terracotta-text" : "green-text"}>{listing.price}</b></div></button>;
}

function RepairPage({ onApply, onRepairer }: { onApply: () => void; onRepairer: (repairer: typeof repairers[number]) => void }) {
  const [category, setCategory] = useState("All");
  const filtered = repairers.filter((r) => category === "All" || r.specialties.toLowerCase().includes(category.toLowerCase().slice(0, -1)));
  return <main className="page repair-page"><PageTop title="Campus Repair" /><section className="recruiter-card"><p className="eyebrow light">Good with your hands?</p><h1>Become a verified repairer and earn on campus</h1><ol><li>Pick what you can fix</li><li>Upload a photo of a past repair</li><li>Get your verified badge</li></ol><Button className="white-button" onClick={onApply}>Apply now</Button></section><div className="chip-row">{["All", "Appliances", "Laptops", "Bikes"].map((item) => <button key={item} className={item === category ? "selected" : ""} onClick={() => setCategory(item)}>{item}</button>)}</div><div className="section-heading compact"><h2>Repairers near you</h2></div><div className="repairer-list">{filtered.map((repairer) => <RepairerCard key={repairer.id} repairer={repairer} onClick={() => onRepairer(repairer)} />)}</div><p className="safety-note"><ShieldCheck size={14} /> Safety first: items with swollen batteries or gas leaks can't be repaired through 2ndLife.</p></main>;
}

function PageTop({ title }: { title: string }) {
  return <div className="page-top"><button className="icon-btn" onClick={() => go("/home")}><ArrowLeft size={20} /></button><h1>{title}</h1><span /></div>;
}

function RepairerCard({ repairer, onClick }: { repairer: typeof repairers[number]; onClick: () => void }) {
  return <div className="repairer-card"><div className={`initials ${repairer.color}`}>{repairer.initials}</div><div className="repairer-copy"><strong>{repairer.name} <span className="verified"><ShieldCheck size={11} /> Verified</span></strong><small>{repairer.major} · {repairer.specialties}</small><small>From {repairer.rate} · {repairer.reply}</small></div><Button className="small-primary" onClick={onClick}>Request</Button></div>;
}

function PostPage({ notify, onFreePost }: { notify: (message: string) => void; onFreePost: () => void }) {
  const [mode, setMode] = useState("Sell");
  const [scanned, setScanned] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const modes = [{ label: "Sell", icon: Tag }, { label: "Give free", icon: HandHeart }, { label: "Urgent", icon: Zap }, { label: "Wanted", icon: Search }];
  const submit = (event: React.FormEvent) => { event.preventDefault(); setSubmitted(true); if (mode === "Give free") onFreePost(); notify(mode === "Give free" ? "Free post is live · +1 IOU ticket" : `${mode} post is live for NJIT students`); };
  return <main className="page post-page"><PageTop title="Create a post" /><p className="page-intro">Share something useful, or ask your campus community for help.</p><div className="mode-tabs">{modes.map(({ label, icon: Icon }) => <button key={label} className={mode === label ? "active" : ""} onClick={() => { setMode(label); setSubmitted(false); }}><Icon size={16} />{label}</button>)}</div>{submitted ? <div className="success-card"><div className="success-icon"><Check /></div><h2>Your post is live!</h2><p>Nearby students at NJIT can see your {mode.toLowerCase()} post now.</p><Button className="primary" onClick={() => setSubmitted(false)}>Create another post</Button></div> : <form className="post-form" onSubmit={submit}>{mode === "Give free" && <div className="notice aqua"><Ticket size={16} /><span><b>Earn 1 IOU ticket</b><small>Every free item you post adds one ticket to your balance.</small></span></div>}<label>{mode === "Urgent" ? "What do you need right now?" : mode === "Wanted" ? "What are you looking for?" : "What are you posting?"}<input required placeholder={mode === "Urgent" ? "e.g. Packing tape for a poster" : "e.g. Mini fridge, TI-84 calculator"} /></label><label>Tell your campus community<textarea placeholder="Add a few details..." rows={3} /></label>{mode !== "Urgent" && mode !== "Wanted" && <div className="toggle-row"><span><Wrench size={17} /> As-is (needs repair)</span><input type="checkbox" /></div>}{mode === "Sell" && <label>Price<input placeholder="$ 0.00" /></label>}{(mode === "Urgent" || mode === "Wanted") && <div className="notice aqua"><Wifi size={16} /><span><b>Nearby broadcast</b><small>We'll notify students within a 10-minute walk.</small></span></div>}<div className="scan-card"><div><div className="scan-icon"><Sparkles size={17} /></div><strong>AI-assisted item check</strong><p>{scanned ? "Scan complete · condition tags and price suggested" : "Scan a photo to suggest condition and campus pricing."}</p></div><Button className="outline-button" onClick={() => { setScanned(true); notify("AI scan complete"); }}><Sparkles size={15} /> {scanned ? "Scanned" : "Run scan"}</Button></div><Button type="submit" className="primary full">{mode === "Urgent" ? "Broadcast request" : "Publish post"} <ArrowRight size={17} /></Button></form>}</main>;
}

function OrdersPage({ onOrder, onNotify }: { onOrder: (id: string) => void; onNotify: (message: string) => void }) {
  const [tab, setTab] = useState<Tab>("Buying");
  return <main className="page orders-page"><div className="orders-title"><h1>My Orders</h1><button className="icon-btn" onClick={() => onNotify("You're all caught up")}><Bell size={19} /></button></div><div className="order-tabs">{(["Buying", "Selling", "Borrowing"] as Tab[]).map((item) => <button key={item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{item}</button>)}</div>{tab === "Buying" ? <div className="order-list">{orders.map((order) => { const Icon = order.icon; return <button className={`order-card ${order.active ? "active" : ""}`} key={order.id} onClick={() => onOrder(order.id)}><div className={`order-art ${order.color}`}><Icon size={25} strokeWidth={1.4} /></div><div className="order-copy"><strong>{order.title}</strong><b className={order.active ? "green-text" : order.status.startsWith("Waiting") ? "sand-text" : ""}>{order.status}</b><small>{order.detail}</small></div><ChevronRight size={18} /></button>; })}</div> : <div className="empty-state"><PackageOpen size={30} /><h3>No {tab.toLowerCase()} orders yet</h3><p>Your activity will show up here.</p></div>}</main>;
}

function OrderDetailPage({ orderId, onModal }: { orderId?: string; onModal: (modal: Modal) => void }) {
  const order = orders.find((item) => item.id === orderId) ?? orders[0];
  const Icon = order.icon;
  const isWaiting = order.status.startsWith("Waiting");
  const isCompleted = order.status.startsWith("Completed");
  const progressIndex = order.active ? 2 : isWaiting ? 1 : 3;
  return <main className="page order-detail"><PageTop title="Order details" /><section className="order-summary"><div className={`order-art ${order.color}`}><Icon size={24} /></div><div><strong>{order.title}{order.price && !order.title.includes("·") ? ` · ${order.price}` : ""}</strong><small>{order.detail}</small></div></section><div className="progress-card">{["Paid", "Dropped off", "Ready", "Picked up"].map((step, index) => <div key={step} className={index < progressIndex || isCompleted ? "complete" : index === progressIndex ? "current" : ""}><span>{index < progressIndex || isCompleted ? <Check size={13} /> : index + 1}</span><small>{step}</small></div>)}</div><section className="pickup-card"><p>{isCompleted ? "Order completed" : isWaiting ? "Waiting for seller drop-off" : "Your item is ready for pickup"}</p><h2>{order.detail.split(" · ")[0]}</h2><span>{isCompleted ? "Thanks for keeping campus circular." : isWaiting ? "We'll notify you when the seller is on the way." : "Level 1 · next to the elevators"}</span><div className="pickup-note"><MapPin size={17} /><span><b>{isCompleted ? "Picked up successfully" : isWaiting ? "Seller action needed" : "Meet the seller here"}</b><small>{order.detail}</small></span></div></section><div className="dual-actions"><Button className="outline-button" onClick={() => onModal("chat")}><MessageCircle size={16} />Message seller</Button><Button className="outline-button" onClick={() => onModal("map")}><MapPin size={16} />Get directions</Button></div></main>;
}

function ModalShell({ children, onClose, className = "" }: { children: React.ReactNode; onClose: () => void; className?: string }) {
  return <div className="overlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><div className={`modal-sheet ${className}`}><button className="modal-close" onClick={onClose}><X size={19} /></button>{children}</div></div>;
}

function UrgentModal({ onClose, notify }: { onClose: () => void; notify: (message: string) => void }) {
  return <ModalShell onClose={onClose}><div className="modal-icon peach"><Zap size={21} /></div><p className="eyebrow">Quick response</p><h2>Help with packing tape?</h2><p className="modal-copy">Maya is 2 minutes away at Campus Center. Let them know you can help.</p><label>Message (optional)<textarea rows={3} placeholder="I have a roll in my room..." /></label><Button className="terracotta full" onClick={() => { onClose(); notify("Maya knows you can help!"); }}>I can help <HandHeart size={17} /></Button></ModalShell>;
}

function ItemModal({ listing, onClose, notify }: { listing: Listing; onClose: () => void; notify: (message: string) => void }) {
  const Icon = listing.icon;
  return <ModalShell onClose={onClose}><div className={`detail-art ${listing.color}`}><Icon size={48} strokeWidth={1.2} /></div><div className="detail-title"><div><h2>{listing.title}</h2><p>{listing.price} · {listing.location}</p></div><span className={`badge ${listing.category === "For Rent" ? "wanted-badge" : "as-is-badge"}`}>{listing.category === "For Rent" ? "FOR RENT" : listing.category === "Free" ? "FREE" : "AS-IS"}</span></div><div className="ai-summary"><Sparkles size={16} /><span><b>AI-assisted item check</b><small>Looks good for campus exchange · {listing.condition}</small></span></div>{listing.verification && <div className="verification"><ShieldCheck size={18} /><span><b>Student verification passed</b><small>Seller profile is verified for campus exchange</small></span><Check size={17} /></div>}<div className="seller-row"><div className="initials mint">{listing.seller?.slice(0, 2).toUpperCase()}</div><span><b>Listed by {listing.seller}</b><small>NJIT student · 4.9 rating</small></span><ChevronRight size={17} /></div><Button className="primary full" onClick={() => { onClose(); notify(listing.category === "Free" ? "Free item claimed" : "Pickup request sent"); }}>{listing.category === "Free" ? "Claim free item" : listing.category === "For Rent" || listing.title.includes("calculator") ? "Rent" : "Buy & arrange pickup"} <ArrowRight size={17} /></Button></ModalShell>;
}

function RepairBooking({ repairer, onClose, notify }: { repairer: typeof repairers[number]; onClose: () => void; notify: (message: string) => void }) {
  return <ModalShell onClose={onClose}><div className="modal-icon aqua"><Wrench size={21} /></div><p className="eyebrow">Request a repair</p><h2>Book {repairer.name}</h2><p className="modal-copy">{repairer.specialties} · from {repairer.rate}</p><label>What needs fixing?<textarea rows={3} placeholder="Tell them what happened..." /></label><label>Meet at<select defaultValue="Campus Center"><option>Campus Center maker space</option><option>GITC lobby</option><option>Redwood Hall lounge</option></select></label><Button className="primary full" onClick={() => { onClose(); notify(`Request sent to ${repairer.name}`); }}>Send request <ArrowRight size={17} /></Button></ModalShell>;
}

function ApplicationDrawer({ onClose, notify }: { onClose: () => void; notify: (message: string) => void }) {
  return <ModalShell onClose={onClose} className="drawer"><p className="eyebrow">Verified repairer</p><h2>Put your skills to work.</h2><p className="modal-copy">Build trust on campus, earn flexibly, and keep useful items out of landfills.</p><div className="application-steps"><div><span>1</span><b>Choose specialties</b><small>Appliances, laptops, bikes, and more.</small></div><div><span>2</span><b>Show your work</b><small>Upload one photo of a past repair.</small></div><div><span>3</span><b>Get verified</b><small>We'll review your .edu profile.</small></div></div><Button className="primary full" onClick={() => { onClose(); notify("Application started"); }}>Start application <ArrowRight size={17} /></Button></ModalShell>;
}

function ProfileDrawer({ onClose, notify, onSignOut }: { onClose: () => void; notify: (message: string) => void; onSignOut: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("Alex Johnson");
  const [email, setEmail] = useState("alex@njit.edu");
  const save = (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || !email.endsWith(".edu")) {
      notify("Enter a name and valid .edu email");
      return;
    }
    setEditing(false);
    notify("Profile updated");
  };
  return <ModalShell onClose={onClose} className="drawer">{editing ? <form className="profile-edit-form" onSubmit={save}><p className="eyebrow">Your profile</p><h2>Edit profile</h2><label>Full name<input value={name} onChange={(event) => setName(event.target.value)} /></label><label>Campus email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label><div className="profile-actions"><Button className="outline-button" onClick={() => setEditing(false)}>Cancel</Button><Button type="submit" className="primary">Save changes</Button></div></form> : <><div className="profile-large"><div className="profile-avatar-large">{name.split(" ").map((part) => part[0]).join("").slice(0, 2)}</div><div><h2>{name}</h2><p>{email} · <span className="verified"><ShieldCheck size={11} /> Verified student</span></p></div><Button className="profile-edit-button" onClick={() => setEditing(true)}>Edit</Button></div><div className="impact-card"><p className="eyebrow">Campus impact</p><div><strong>$185</strong><span>saved this semester</span><strong>42 lbs</strong><span>e-waste diverted</span></div></div><h3>Your badges</h3><div className="badge-row"><span><ShieldCheck size={17} /> NJIT verified</span><span><HandHeart size={17} /> Campus sharer</span></div><h3>Active listings</h3><div className="mini-listing"><Bike size={21} /><span><b>Bike for commuting</b><small>For rent · posted 2 days ago</small></span><ChevronRight size={16} /></div><Button className="sign-out-button full" onClick={onSignOut}>Sign out</Button></>}</ModalShell>;
}

function ChatDrawer({ onClose }: { onClose: () => void }) { return <ModalShell onClose={onClose} className="drawer"><p className="eyebrow">In-app chat</p><h2>Message Sam T.</h2><div className="chat-bubble">Hey! The fridge is in great working order. The seal just needs a little love.</div><label><input placeholder="Write a message..." /></label><Button className="primary full" onClick={onClose}>Send message <ArrowRight size={17} /></Button></ModalShell>; }
function MapModal({ onClose }: { onClose: () => void }) { return <ModalShell onClose={onClose}><p className="eyebrow">Campus directions</p><h2>Campus Center · Level 1</h2><div className="map-placeholder"><MapPin size={32} /><span>Campus Center desk</span><small>Next to the elevators</small></div><Button className="primary full" onClick={onClose}>Got it</Button></ModalShell>; }
function LocationModal({ onClose, notify }: { onClose: () => void; notify: (message: string) => void }) {
  const locations = [
    "Campus Center",
    "Redwood Hall",
    "GITC",
    "Maple Hall",
    "Fenster Hall",
    "Central King Building",
    "Eberhardt Hall",
    "Ying Wu College of Computing",
    "Wellness and Events Center",
    "Laurel Hall",
    "Oak Hall",
    "Cypress Hall",
    "Greek Village",
    "Parking Deck",
  ];
  return <ModalShell onClose={onClose}><p className="eyebrow">Campus location</p><h2>Where are you on campus?</h2><p className="modal-copy">Choose a location to prioritize nearby listings and requests.</p><div className="location-options">{locations.map((location) => <button key={location} className={location === "Campus Center" ? "selected" : ""} onClick={() => { onClose(); notify(`Location set to ${location}`); }}><MapPin size={16} /><span>{location}</span>{location === "Campus Center" && <Check size={16} />}</button>)}</div></ModalShell>;
}

function BottomNav({ path, onHome }: { path: string; onHome: () => void }) {
  const items = [{ label: "Home", icon: Store, path: "/home" }, { label: "Post", icon: Plus, path: "/post" }, { label: "Orders", icon: PackageOpen, path: "/orders" }];
  return <nav className="bottom-nav">{items.map(({ label, icon: Icon, path: itemPath }) => <button key={label} className={path === itemPath ? "active" : ""} onClick={itemPath === "/home" ? onHome : () => go(itemPath)}><span className={label === "Post" ? "post-icon" : ""}><Icon size={17} /></span>{label}</button>)}</nav>;
}

export default App;
