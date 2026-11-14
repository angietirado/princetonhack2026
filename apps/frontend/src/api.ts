export interface Summary {
  source: string;
  currency: string;
  start: string | null;
  end: string | null;
  net_spending_cents: number;
  included_count: number;
  excluded_count: number;
  categories: { category: string; amount_cents: number }[];
}

export async function getSummary(): Promise<Summary> {
  const response = await fetch('/api/v1/spending/summary');
  if (!response.ok) {
    throw new Error('Spending data is unavailable. Please try again.');
  }
  return response.json();
}
