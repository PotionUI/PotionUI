import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';

// Built without importing `svelte/store` (vi.hoisted factories run before
// this file's own imports are evaluated, so `writable` isn't available yet).
const mockAuthStore = vi.hoisted(() => {
	type AuthState = { user: { account_type: 'USER' | 'ADMIN' } | null };
	let state: AuthState = { user: null };
	const subscribers = new Set<(s: AuthState) => void>();
	return {
		subscribe(run: (s: AuthState) => void) {
			subscribers.add(run);
			run(state);
			return () => subscribers.delete(run);
		},
		set(next: AuthState) {
			state = next;
			subscribers.forEach((run) => run(state));
		}
	};
});

vi.mock('$lib/stores/auth', () => ({
	authStore: mockAuthStore
}));

import { setContributions, contributionsForSlot, type SlotContribution } from './extensionSlots';

const admin: SlotContribution = {
	plugin_id: 'example-extensions',
	slot: 'admin.tabs',
	component: 'AdminTab.svelte',
	label: 'Admin Only Tab',
	order: 10,
	require_role: 'ADMIN'
};

const userVisible: SlotContribution = {
	plugin_id: 'example-extensions',
	slot: 'admin.tabs',
	component: 'PublicTab.svelte',
	label: 'Public Tab',
	order: 5
};

const otherSlot: SlotContribution = {
	plugin_id: 'other-plugin',
	slot: 'nav.primary',
	component: 'NavItem.svelte',
	order: 1
};

describe('extensionSlots', () => {
	beforeEach(() => {
		mockAuthStore.set({ user: null });
		setContributions([]);
	});

	it('filters contributions by slot', () => {
		setContributions([admin, userVisible, otherSlot]);
		mockAuthStore.set({ user: { account_type: 'ADMIN' } });

		const result = get(contributionsForSlot('admin.tabs'));
		expect(result).toHaveLength(2);
		expect(result.every((c) => c.slot === 'admin.tabs')).toBe(true);
	});

	it('sorts contributions by order ascending', () => {
		setContributions([admin, userVisible]);
		mockAuthStore.set({ user: { account_type: 'ADMIN' } });

		const result = get(contributionsForSlot('admin.tabs'));
		expect(result.map((c) => c.order)).toEqual([5, 10]);
	});

	it('filters out contributions requiring a role the current user lacks', () => {
		setContributions([admin, userVisible]);
		mockAuthStore.set({ user: { account_type: 'USER' } });

		const result = get(contributionsForSlot('admin.tabs'));
		expect(result).toHaveLength(1);
		expect(result[0].component).toBe('PublicTab.svelte');
	});

	it('excludes role-gated contributions when there is no authenticated user', () => {
		setContributions([admin, userVisible]);
		mockAuthStore.set({ user: null });

		const result = get(contributionsForSlot('admin.tabs'));
		expect(result).toHaveLength(1);
		expect(result[0].component).toBe('PublicTab.svelte');
	});

	it('returns an empty list for a slot with no contributions', () => {
		setContributions([admin]);
		const result = get(contributionsForSlot('generation.panel.modes'));
		expect(result).toEqual([]);
	});
});
