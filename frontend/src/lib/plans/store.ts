import { derived, get, readable, writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import { getMyLimits, type LimitRefusal, type LimitRow, type MyPlanMeta } from './meApi';
import { msUntil } from './countdown';
import {
	blocksSubmit,
	isPerItemRefusal,
	gateFromRefusal,
	gateFromRow,
	parseLimitRefusal,
	type LimitGate
} from './refusal';
import { closestLimit } from './limitView';

export const limits = writable<LimitRow[]>([]);
export const limitsLoaded = writable(false);
export const limitsMeta = writable<MyPlanMeta | null>(null);
export const storageUsed = writable<number | null>(null);
export const serverRefusal = writable<LimitRefusal | null>(null);
export const refusalDismissed = writable(false);

export const nowTick = readable(Date.now(), (set) => {
	set(Date.now());
	const id = setInterval(() => set(Date.now()), 1000);
	return () => clearInterval(id);
});

export const closest = derived(limits, ($limits) => closestLimit($limits));

export const generateGate = derived(
	[limits, serverRefusal, nowTick, limitsMeta],
	([$limits, $refusal, $now, $meta]): LimitGate | null => {
		if ($refusal) {
			const gate = gateFromRefusal($refusal, $now);
			if (gate) return gate;
		}
		for (const row of $limits) {
			if (!blocksSubmit(row)) continue;
			const gate = gateFromRow(row, $now, $meta?.contactLine ?? undefined);
			if (gate) return gate;
		}
		return null;
	}
);

let resetTimer: ReturnType<typeof setTimeout> | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;

function armResetTimer(): void {
	if (resetTimer) clearTimeout(resetTimer);
	resetTimer = null;
	const now = Date.now();
	const candidates: number[] = [];
	for (const row of get(limits)) {
		const ms = msUntil(row.resets_at, now);
		if (ms !== null && ms > 0 && row.state !== 'ok') candidates.push(ms);
	}
	const refusal = get(serverRefusal);
	const refusalMs = refusal ? msUntil(refusal.resets_at, now) : null;
	if (refusalMs !== null && refusalMs > 0) candidates.push(refusalMs);
	if (candidates.length === 0) return;
	const wait = Math.min(Math.min(...candidates) + 500, 2 ** 31 - 1);
	resetTimer = setTimeout(() => {
		serverRefusal.set(null);
		void refreshLimits();
	}, wait);
}

export async function refreshLimits(): Promise<void> {
	try {
		const mine = await getMyLimits();
		const rows = mine.rows.filter((row) => row.enforced);
		limitsMeta.set(mine.meta);
		storageUsed.set(mine.storageBytes ?? null);
		limits.set(rows);
		limitsLoaded.set(true);
		const refusal = get(serverRefusal);
		if (refusal) {
			const row = rows.find((r) => r.kind === refusal.kind);
			if (!row || row.state !== 'full') serverRefusal.set(null);
		}
		armResetTimer();
	} catch {
		limitsLoaded.set(true);
	}
}

export function refreshLimitsSoon(delayMs = 400): void {
	if (refreshTimer) clearTimeout(refreshTimer);
	refreshTimer = setTimeout(() => {
		refreshTimer = null;
		void refreshLimits();
	}, delayMs);
}

export function reportLimitRefusal(refusal: LimitRefusal): void {
	if (isPerItemRefusal(refusal)) return;
	serverRefusal.set(refusal);
	refusalDismissed.set(false);
	armResetTimer();
	void refreshLimits();
}

export function resetLimitsState(): void {
	if (resetTimer) clearTimeout(resetTimer);
	if (refreshTimer) clearTimeout(refreshTimer);
	limits.set([]);
	limitsMeta.set(null);
	storageUsed.set(null);
	limitsLoaded.set(false);
	serverRefusal.set(null);
	refusalDismissed.set(false);
}

let installed = false;

export function installLimitsWatcher(): void {
	if (installed) return;
	installed = true;
	api.getClient().interceptors.response.use(
		(response) => {
			const method = (response.config?.method ?? '').toLowerCase();
			const url = response.config?.url ?? '';
			if (method === 'delete' || (method === 'post' && /\/api\/(media|generations)\/upload/.test(url))) {
				refreshLimitsSoon();
			}
			return response;
		},
		(error) => {
			const refusal = parseLimitRefusal(error?.response?.data);
			if (refusal && !isPerItemRefusal(refusal)) reportLimitRefusal(refusal);
			return Promise.reject(error);
		}
	);
}
