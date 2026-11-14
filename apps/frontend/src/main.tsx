import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { getSummary, type Summary } from './api';
import './styles.css';

const money = (cents: number) => new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD',
}).format(cents / 100);

function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  async function refresh() {
    setLoading(true);
    setError('');
    try {
      setSummary(await getSummary());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load spending.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  return <main>
    <header>
      <p className="eyebrow">Spending companion</p>
      <h1>Your spending, at a glance.</h1>
      <p>A starting point for understanding where your money goes.</p>
    </header>
    <section aria-labelledby="summary-title" aria-busy={loading}>
      <div className="row">
        <h2 id="summary-title">Spending overview</h2>
        <button onClick={() => void refresh()} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>
      <div aria-live="polite">
        {error && <p role="alert">{error}</p>}
        {!error && summary && <>
          <p className="label">{summary.source === 'fixture' ? 'Synthetic demo data' : 'Nessie sandbox data'}</p>
          <p className="amount">{money(summary.net_spending_cents)}</p>
          <p>Completed expenses after refunds · {summary.start ?? 'No data'} — {summary.end ?? 'No data'}</p>
          <ul>{summary.categories.map(category => <li key={category.category}>
            <span>{category.category}</span><strong>{money(category.amount_cents)}</strong>
          </li>)}</ul>
          <p className="label">{summary.included_count} expenses and refunds included. Transfers,
            deposits, cash withdrawals, and unfinished charges are excluded.</p>
        </>}
      </div>
    </section>
    <footer>Phone conversations and AI insights are planned for the next milestone.</footer>
  </main>;
}

createRoot(document.getElementById('root')!).render(<App />);
