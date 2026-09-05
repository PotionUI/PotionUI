import { describe, it, expect, vi } from 'vitest';
import { ensureSubscribed, isSubscriptionRequested, releaseSubscription, clearSubscriptionOwner } from './subscriptions';

describe('ensureSubscribed', () => {
	it('calls subscribe on the first request for a (owner, id) pair', () => {
		const owner = {};
		const subscribe = vi.fn();
		ensureSubscribed(owner, 'gen-1', subscribe);
		expect(subscribe).toHaveBeenCalledTimes(1);
		expect(isSubscriptionRequested(owner, 'gen-1')).toBe(true);
	});

	it('is a no-op on a second request for the SAME owner and id', () => {
		const owner = {};
		const subscribe = vi.fn();
		ensureSubscribed(owner, 'gen-1', subscribe);
		ensureSubscribed(owner, 'gen-1', subscribe);
		expect(subscribe).toHaveBeenCalledTimes(1);
	});

	it('subscribes independently per owner -- a different owner has no memory of its own', () => {
		const ownerA = {};
		const ownerB = {};
		const subscribeA = vi.fn();
		const subscribeB = vi.fn();
		ensureSubscribed(ownerA, 'gen-1', subscribeA);
		ensureSubscribed(ownerB, 'gen-1', subscribeB);
		expect(subscribeA).toHaveBeenCalledTimes(1);
		expect(subscribeB).toHaveBeenCalledTimes(1);
	});

	it('always subscribes when owner is undefined (dedup opted out)', () => {
		const subscribe = vi.fn();
		ensureSubscribed(undefined, 'gen-1', subscribe);
		ensureSubscribed(undefined, 'gen-1', subscribe);
		expect(subscribe).toHaveBeenCalledTimes(2);
	});
});

describe('releaseSubscription', () => {
	it('forgets one id so a later request for it resubscribes, without touching a different id', () => {
		const owner = {};
		const subscribe = vi.fn();
		ensureSubscribed(owner, 'gen-1', subscribe);
		ensureSubscribed(owner, 'gen-2', subscribe);

		releaseSubscription(owner, 'gen-1');

		ensureSubscribed(owner, 'gen-1', subscribe);
		ensureSubscribed(owner, 'gen-2', subscribe);

		expect(subscribe).toHaveBeenCalledTimes(3);
		expect(isSubscriptionRequested(owner, 'gen-1')).toBe(true);
	});
});

describe('clearSubscriptionOwner', () => {
	it('drops every id for that owner, without touching another owner', () => {
		const ownerA = {};
		const ownerB = {};
		ensureSubscribed(ownerA, 'gen-1', () => {});
		ensureSubscribed(ownerA, 'gen-2', () => {});
		ensureSubscribed(ownerB, 'gen-1', () => {});

		clearSubscriptionOwner(ownerA);

		expect(isSubscriptionRequested(ownerA, 'gen-1')).toBe(false);
		expect(isSubscriptionRequested(ownerA, 'gen-2')).toBe(false);
		expect(isSubscriptionRequested(ownerB, 'gen-1')).toBe(true);
	});
});
