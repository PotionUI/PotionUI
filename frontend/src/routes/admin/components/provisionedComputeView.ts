import type { ProvisionProgressEntry } from '$lib/services/admin-api';

const STAGE_LABELS: Record<string, string> = {
	preparing: 'Preparing',
	creating: 'Creating',
	starting: 'Starting',
	waiting_worker: 'Waiting for worker',
	ready: 'Ready'
};

const STATUS_VARIANTS: Record<string, 'success' | 'warning' | 'danger' | 'neutral' | 'signal'> = {
	provisioning: 'signal',
	running: 'success',
	stopped: 'neutral',
	missing: 'danger',
	unreachable: 'danger',
	failed: 'danger',
	unknown: 'warning'
};

export function statusVariant(status: string): 'success' | 'warning' | 'danger' | 'neutral' | 'signal' {
	return STATUS_VARIANTS[status] ?? 'neutral';
}

/** `snake_case` -> "Snake case" for any stage the conventional list doesn't name. */
export function stageLabel(stage: string): string {
	const known = STAGE_LABELS[stage];
	if (known) return known;
	const words = stage.split('_').filter(Boolean);
	if (words.length === 0) return stage;
	return words
		.map((word, i) => (i === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word))
		.join(' ');
}

function pad2(n: number): string {
	return n < 10 ? `0${n}` : String(n);
}

/** Local `HH:MM:SS`, 24h, zero-padded — '' for an unparsable timestamp. */
export function formatClockTime(iso: string): string {
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return '';
	return `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

/** null for a null/unparsable `iso` (never checked yet); otherwise "checked ... ago". */
export function checkedAgo(iso: string | null, now: number = Date.now()): string | null {
	if (!iso) return null;
	const then = new Date(iso).getTime();
	if (Number.isNaN(then)) return null;
	const deltaSeconds = Math.max(0, Math.floor((now - then) / 1000));
	if (deltaSeconds < 5) return 'checked just now';
	if (deltaSeconds < 60) return `checked ${deltaSeconds}s ago`;
	if (deltaSeconds < 3600) return `checked ${Math.floor(deltaSeconds / 60)}m ago`;
	return `checked ${Math.floor(deltaSeconds / 3600)}h ago`;
}

/** Last non-null `percent` in the timeline, clamped to 0-100 — null when the
 * timeline is empty or every entry so far is indeterminate. */
export function latestPercent(progress: ProvisionProgressEntry[]): number | null {
	for (let i = progress.length - 1; i >= 0; i--) {
		const percent = progress[i].percent;
		if (percent !== null && percent !== undefined) {
			return Math.min(100, Math.max(0, percent));
		}
	}
	return null;
}
