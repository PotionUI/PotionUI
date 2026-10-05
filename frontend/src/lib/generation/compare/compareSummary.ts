import type { LimitRow } from '$lib/plans/meApi';
import { dailyShortfall, formatEstimate, shortfallMessage, type DailyShortfall } from './compareLimits';

export type CompareSummary = {
	count: number;
	overCap: boolean;
	needsConfirm: boolean;
	estimate: string;
	shortfall: DailyShortfall | null;
	disabledReason: string | null;
};

export function deriveCompareSummary(input: {
	count: number;
	perCellMs: number | null;
	hardCap: number;
	confirmAbove: number;
	limitRows: LimitRow[];
	serverShortfall: DailyShortfall | null;
	contactLine: string;
	blockedReason: string | null;
}): CompareSummary {
	const { count } = input;
	const overCap = count > input.hardCap;
	const shortfall = dailyShortfall(input.limitRows, count) ?? input.serverShortfall;
	let disabledReason: string | null = null;
	if (input.blockedReason) disabledReason = input.blockedReason;
	else if (count === 0) disabledReason = 'Pick at least one field and some values to compare.';
	else if (overCap) disabledReason = `A comparison can have at most ${input.hardCap} cells.`;
	else if (shortfall) disabledReason = shortfallMessage(shortfall, input.contactLine);
	return {
		count,
		overCap,
		needsConfirm: count > input.confirmAbove && !overCap,
		estimate: formatEstimate(count, input.perCellMs),
		shortfall,
		disabledReason
	};
}
