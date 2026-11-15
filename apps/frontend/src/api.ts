export type Category = 'books' | 'electronics' | 'furniture' | 'clothing' | 'sports' | 'other'
export type ListingStatus = 'available' | 'reserved' | 'sold'
export type Condition = 'new' | 'like_new' | 'good' | 'fair'
export interface PublicUser { id: string; display_name: string; university: string; edu_eligible: boolean; email_verified: false }
export interface User extends PublicUser { email: string; created_at: string; dev_username: string | null; campus_location: string; iou_tickets: number }
export interface DevStudent { username: string; id: string; display_name: string; email: string }
export interface Listing {
  id: string; title: string; description: string; price_cents: number; category: Category;
  condition: Condition; pickup_location: string; image_url: string | null; status: ListingStatus;
  seller: PublicUser; created_at: string; updated_at: string; is_demo: boolean;
  kind: ListingKind; needs_repair: boolean; rental_period: 'day' | null;
}
export type ListingKind = 'sale' | 'rent' | 'free' | 'urgent' | 'wanted'
export type OrderStatus = 'requested' | 'accepted' | 'ready' | 'completed' | 'returned' | 'cancelled'
export interface Order { id: string; listing: Listing; buyer: PublicUser; seller: PublicUser; status: OrderStatus; pickup_location: string; conversation_id: string; created_at: string; updated_at: string }
export interface Repairer { id: string; user: PublicUser; specialties: string[]; about: string; rate_cents: number; portfolio_image_url: string | null; verification: 'self_reported'; created_at: string; updated_at: string }
export interface RepairRequest { id: string; repairer: Repairer; requester: PublicUser; description: string; location: string; status: 'requested' | 'accepted' | 'completed' | 'cancelled'; conversation_id: string; created_at: string; updated_at: string }
export interface Conversation {
  id: string; listing: Pick<Listing, 'id' | 'title' | 'price_cents' | 'image_url'> & { status: ListingStatus | 'removed' };
  buyer: PublicUser; seller: PublicUser; last_message: string; updated_at: string; created_at: string;
}
export interface Message { id: string; conversation_id: string; sender_id: string; body: string; created_at: string }
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Config { email_policy: string; verification_mode: 'domain_only'; demo_data: boolean; devmode: boolean }
export class ApiError extends Error { constructor(message: string, public status: number) { super(message) } }

export async function api<T>(path: string, options: RequestInit = {}, onStatus?: (status: number) => void): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...options.headers },
    })
  } catch {
    throw new ApiError('We couldn’t connect. Check your connection and try again.', 0)
  }
  if (!response.ok) {
    let message = 'Something went wrong. Please try again.'
    try {
      const data = await response.json()
      if (typeof data.detail === 'string') message = data.detail
      else if (Array.isArray(data.detail)) message = data.detail.map((e: { msg: string }) => e.msg.replace(/^Value error, /, '')).join('. ')
    } catch { /* The server may not return JSON on a gateway failure. */ }
    throw new ApiError(message, response.status)
  }
  onStatus?.(response.status)
  return response.status === 204 ? undefined as T : response.json()
}

export const money = (cents: number) => cents === 0 ? 'Free' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: cents % 100 === 0 ? 0 : 2 }).format(cents / 100)
export const conditions: Record<Condition, string> = { new: 'New', like_new: 'Like new', good: 'Good', fair: 'Fair' }
export const categories: { id: Category; name: string }[] = [
  { id: 'books', name: 'Books & notes' }, { id: 'electronics', name: 'Electronics' },
  { id: 'furniture', name: 'Dorm essentials' }, { id: 'clothing', name: 'Clothing' },
  { id: 'sports', name: 'Sports & outdoors' }, { id: 'other', name: 'Everything else' },
]
export const errorText = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.'
