import { writable, derived, type Readable } from 'svelte/store';
import { authStore } from '$lib/stores/auth';

/** One plugin `contributions:` entry, as returned by `/api/plugins/frontend-extensions`. */
export interface SlotContribution {
	plugin_id: string;
	slot: string;
	component: string;
	label?: string | null;
	icon?: string | null;
	route?: string | null;
	order: number;
	require_role?: string | null;
}

const allContributions = writable<SlotContribution[]>([]);

/** Replaces the full contribution set (called once by `stores/extensions.ts` on init). */
export function setContributions(contributions: SlotContribution[]): void {
	allContributions.set(contributions);
}

/**
 * Contributions for `slot`, sorted by `order` ascending and filtered by
 * `require_role` against the current user's `account_type` - mirrors
 * `Sidebar.svelte`'s existing plugin-nav-item role filter.
 */
export function contributionsForSlot(slot: string): Readable<SlotContribution[]> {
	return derived([allContributions, authStore], ([$contributions, $auth]) =>
		$contributions
			.filter((c) => c.slot === slot)
			.filter((c) => !c.require_role || c.require_role === $auth.user?.account_type)
			.sort((a, b) => a.order - b.order)
	);
}
