import type { LucideIcon } from "lucide-react";
import { Bike, Box, Calculator, Coffee, LampDesk, Package, Refrigerator, Wrench, Zap } from "lucide-react";

export type ListingCategory = "For sale" | "For Rent" | "Urgent" | "Repair" | "Free";
export type Listing = {
  id: string;
  title: string;
  subtitle: string;
  price: string;
  category: ListingCategory;
  location: string;
  icon: LucideIcon;
  color: string;
  condition?: string;
  seller?: string;
  verification?: boolean;
};

export const listings: Listing[] = [
  { id: "fridge", title: "Mini fridge", subtitle: "Door seal loose · Redwood Hall", price: "$40", category: "For sale", location: "Redwood Hall", icon: Refrigerator, color: "sage", condition: "As-is, door seal loose", seller: "Sam T.", verification: true },
  { id: "calculator", title: "TI-84 calculator", subtitle: "Works perfectly · GITC", price: "$25 or rent $2/day", category: "For sale", location: "GITC", icon: Calculator, color: "blue", condition: "Excellent condition", seller: "Maya L.", verification: true },
  { id: "lamp", title: "Desk lamp", subtitle: "Warm light · Campus Center", price: "$8", category: "For sale", location: "Campus Center", icon: LampDesk, color: "sand", condition: "Good condition", seller: "Jordan M." },
  { id: "bike", title: "Commuter bike", subtitle: "Step-through bike · Maple Hall", price: "$5/day", category: "For Rent", location: "Maple Hall", icon: Bike, color: "blue", condition: "Good condition", seller: "Priya R." },
  { id: "tape", title: "Packing tape", subtitle: "Free roll · Campus Center", price: "FREE", category: "Free", location: "Campus Center", icon: Package, color: "peach", condition: "Opened, plenty left", seller: "Maya L." },
  { id: "coffee", title: "Coffee maker", subtitle: "Needs a new carafe · Maple Hall", price: "$5", category: "Repair", location: "Maple Hall", icon: Coffee, color: "aqua", condition: "Needs repair", seller: "Alex K." },
  { id: "packing-tape-request", title: "Packing tape request", subtitle: "Needed now · Campus Center", price: "URGENT", category: "Urgent", location: "Campus Center", icon: Zap, color: "peach", condition: "Needed for a poster", seller: "Maya L." },
];

export const repairers = [
  { id: "alex", initials: "AK", name: "Alex K.", major: "Mechanical Eng.", specialties: "fridges, fans, lamps", rate: "$15", reply: "usually replies in 1 hr", color: "mint" },
  { id: "priya", initials: "PR", name: "Priya R.", major: "Computer Eng.", specialties: "laptops, screens, batteries", rate: "$20", reply: "usually replies in 2 hr", color: "blue" },
  { id: "jordan", initials: "JM", name: "Jordan M.", major: "Cycling club", specialties: "bikes, scooters", rate: "$10", reply: "usually replies today", color: "sand" },
];

export const orders = [
  { id: "mini-fridge", title: "Mini fridge", price: "$40", status: "Ready for pickup · Campus Center", detail: "Campus Center, Level 1", color: "sage", icon: Refrigerator, active: true },
  { id: "desk-lamp", title: "Desk lamp", price: "$8", status: "Waiting for seller drop-off", detail: "Seller drops off by Friday", color: "sand", icon: LampDesk },
  { id: "calculator-rental", title: "TI-84 calculator · rented", price: "$2/day", status: "Return by Nov 6 · GITC", detail: "Deposit held by 2ndLife", color: "blue", icon: Calculator },
  { id: "packing-tape", title: "Packing tape · free", price: "Free", status: "Completed · picked up in 9 min", detail: "Thanks sent to Maya L.", color: "peach", icon: Package },
];

export const categories: { label: ListingCategory; icon: LucideIcon }[] = [
  { label: "Urgent", icon: Zap },
  { label: "For sale", icon: Box },
  { label: "For Rent", icon: Calculator },
  { label: "Free", icon: Box },
];
