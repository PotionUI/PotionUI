export type CostSource = 'provider' | 'estimate' | 'mixed' | 'unknown';

export interface CostSummary {
	amount_usd: string | null;
	source: CostSource | null;
	entries: number;
	unpriced: number;
}

export interface CostItem {
	id: string;
	backend_id: string | null;
	model_id: string | null;
	amount_usd: string | null;
	source: CostSource;
	detail: Record<string, unknown>;
	created_at: string;
}

export interface GenerationCost extends CostSummary {
	items: CostItem[];
}

export interface SpendBucket {
	amount_usd: string | null;
	source: CostSource | null;
	entries: number;
	unpriced: number;
}

export interface SpendByBackend extends SpendBucket {
	backend_id: string | null;
	backend_name: string | null;
}

export interface SpendByModel extends SpendBucket {
	model_id: string | null;
	model: string | null;
}

export interface CloudSpend {
	from: string | null;
	to: string | null;
	total_usd: string;
	entries: number;
	unpriced: number;
	by_backend: SpendByBackend[];
	by_model: SpendByModel[];
}

export interface CostCell {
	kind: 'none' | 'unpriced' | 'amount';
	text: string;
	estimate: boolean;
	tooltip: string | null;
}

const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const USD_EXACT = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4 });

export function parseAmount(value: string | number | null | undefined): number | null {
	if (value === null || value === undefined || value === '') return null;
	const amount = typeof value === 'number' ? value : Number(value);
	return Number.isFinite(amount) ? amount : null;
}

export function formatUsd(value: string | number | null | undefined): string {
	const amount = parseAmount(value);
	if (amount === null) return '—';
	if (amount > 0 && amount < 0.005) return '<$0.01';
	return USD.format(amount);
}

export function formatUsdExact(value: string | number | null | undefined): string {
	const amount = parseAmount(value);
	return amount === null ? '—' : USD_EXACT.format(amount);
}

export function costSourceLabel(source: string | null | undefined): string {
	switch (source) {
		case 'provider':
			return 'Reported';
		case 'estimate':
			return 'Estimate';
		case 'mixed':
			return 'Mixed';
		default:
			return 'Unknown';
	}
}

export function costSourceSentence(source: string | null | undefined): string {
	switch (source) {
		case 'provider':
			return 'Reported by the provider.';
		case 'estimate':
			return "Estimated from the model's listed prices.";
		case 'mixed':
			return 'Partly reported by the provider, partly estimated.';
		default:
			return 'The source of this amount is unknown.';
	}
}

function jobs(count: number): string {
	return `${count} ${count === 1 ? 'job' : 'jobs'}`;
}

export function costCell(cost: CostSummary | null | undefined): CostCell {
	if (!cost) return { kind: 'none', text: '—', estimate: false, tooltip: null };
	if (parseAmount(cost.amount_usd) === null) {
		return {
			kind: 'unpriced',
			text: 'unpriced',
			estimate: false,
			tooltip: `No price is known for ${jobs(Math.max(cost.unpriced, cost.entries, 1))}.`
		};
	}
	const parts = [formatUsdExact(cost.amount_usd), costSourceSentence(cost.source)];
	if (cost.unpriced > 0) parts.push(`${jobs(cost.unpriced)} with no known price not included.`);
	return {
		kind: 'amount',
		text: formatUsd(cost.amount_usd),
		estimate: cost.source === 'estimate' || cost.source === 'mixed',
		tooltip: parts.join(' ')
	};
}

export function bucketAmountValue(bucket: { amount_usd: string | null }): number {
	return parseAmount(bucket.amount_usd) ?? -1;
}

export function sortByAmountDesc<T extends { amount_usd: string | null }>(rows: readonly T[]): T[] {
	return [...rows].sort((a, b) => bucketAmountValue(b) - bucketAmountValue(a));
}

export function spendIsEmpty(spend: CloudSpend | null | undefined): boolean {
	return !spend || spend.entries === 0;
}

export function spendNote(spend: CloudSpend): string {
	const parts: string[] = [];
	if (spend.unpriced > 0) parts.push(`${jobs(spend.unpriced)} with no known price are counted but add nothing to the total.`);
	parts.push('Rows marked est. include estimated amounts.');
	return parts.join(' ');
}
