import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';

// Mock the api module before importing the store (the store binds `api` at import).
vi.mock('$lib/services/api', () => ({
	api: {
		listNotifications: vi.fn(),
		markNotificationRead: vi.fn().mockResolvedValue({ success: true }),
		markAllNotificationsRead: vi.fn().mockResolvedValue({ success: true, data: { updated: 0 } }),
		deleteNotification: vi.fn().mockResolvedValue({ success: true }),
		clearNotifications: vi.fn().mockResolvedValue({ success: true, data: { deleted: 0 } }),
		getNotificationTypes: vi.fn(),
		updateNotificationPreferences: vi.fn()
	}
}));

import { api } from '$lib/services/api';
import {
	notifications,
	applyWsEvent,
	applyPrefs,
	setTypeEnabledInState,
	initialState,
	NOTIFICATION_CAP,
	type NotificationsState
} from './notifications';
import type {
	AppNotification,
	NotificationPreferences,
	NotificationTypePref
} from '$lib/services/api/notifications';

function makeNotification(overrides: Partial<AppNotification> = {}): AppNotification {
	return {
		id: overrides.id ?? crypto.randomUUID(),
		user_id: 'u1',
		category: 'system',
		type: '',
		level: 'info',
		title: 'Title',
		message: 'Message',
		metadata: null,
		source: 'core',
		read: false,
		created_at: new Date().toISOString(),
		...overrides
	};
}

function makePref(overrides: Partial<NotificationTypePref> = {}): NotificationTypePref {
	return {
		key: overrides.key ?? 'generation.completed',
		label: 'Generation completed',
		description: 'When a generation finishes',
		category: 'Generation',
		default_enabled: true,
		enabled: true,
		...overrides
	};
}

function makePrefs(overrides: Partial<NotificationPreferences> = {}): NotificationPreferences {
	return { types: [makePref()], sound: false, ...overrides };
}

const mockedApi = api as unknown as {
	listNotifications: ReturnType<typeof vi.fn>;
	markNotificationRead: ReturnType<typeof vi.fn>;
	markAllNotificationsRead: ReturnType<typeof vi.fn>;
	deleteNotification: ReturnType<typeof vi.fn>;
	clearNotifications: ReturnType<typeof vi.fn>;
	getNotificationTypes: ReturnType<typeof vi.fn>;
	updateNotificationPreferences: ReturnType<typeof vi.fn>;
};

describe('applyWsEvent reducer', () => {
	let state: NotificationsState;

	beforeEach(() => {
		state = initialState();
	});

	it('connection_established seeds unreadCount', () => {
		const next = applyWsEvent(state, { type: 'connection_established', unread_count: 5 });
		expect(next.unreadCount).toBe(5);
	});

	it('connection_established without a count leaves state unchanged', () => {
		state.unreadCount = 3;
		const next = applyWsEvent(state, { type: 'connection_established' });
		expect(next.unreadCount).toBe(3);
	});

	it('notification prepends and bumps unread for unread items', () => {
		const n = makeNotification({ id: 'a' });
		const next = applyWsEvent(state, { type: 'notification', notification: n });
		expect(next.items[0].id).toBe('a');
		expect(next.unreadCount).toBe(1);
	});

	it('notification does not bump unread for an already-read item', () => {
		const n = makeNotification({ id: 'a', read: true });
		const next = applyWsEvent(state, { type: 'notification', notification: n });
		expect(next.items).toHaveLength(1);
		expect(next.unreadCount).toBe(0);
	});

	it('notification dedupes by id', () => {
		const n = makeNotification({ id: 'a' });
		let next = applyWsEvent(state, { type: 'notification', notification: n });
		next = applyWsEvent(next, { type: 'notification', notification: n });
		expect(next.items).toHaveLength(1);
		expect(next.unreadCount).toBe(1);
	});

	it('notification caps the list at NOTIFICATION_CAP', () => {
		let next = state;
		for (let i = 0; i < NOTIFICATION_CAP + 25; i++) {
			next = applyWsEvent(next, {
				type: 'notification',
				notification: makeNotification({ id: `n${i}` })
			});
		}
		expect(next.items).toHaveLength(NOTIFICATION_CAP);
		// Newest is prepended, so the most recent id survives.
		expect(next.items[0].id).toBe(`n${NOTIFICATION_CAP + 24}`);
	});

	it('notification_read marks read and decrements unread once', () => {
		let next = applyWsEvent(state, {
			type: 'notification',
			notification: makeNotification({ id: 'a' })
		});
		next = applyWsEvent(next, { type: 'notification_read', id: 'a' });
		expect(next.items[0].read).toBe(true);
		expect(next.unreadCount).toBe(0);
		// Idempotent — reading again doesn't push unread negative.
		next = applyWsEvent(next, { type: 'notification_read', id: 'a' });
		expect(next.unreadCount).toBe(0);
	});

	it('all_read clears unread and marks every item', () => {
		let next = applyWsEvent(state, {
			type: 'notification',
			notification: makeNotification({ id: 'a' })
		});
		next = applyWsEvent(next, {
			type: 'notification',
			notification: makeNotification({ id: 'b' })
		});
		next = applyWsEvent(next, { type: 'all_read' });
		expect(next.unreadCount).toBe(0);
		expect(next.items.every((i) => i.read)).toBe(true);
	});

	it('notification_deleted removes and decrements unread for an unread item', () => {
		let next = applyWsEvent(state, {
			type: 'notification',
			notification: makeNotification({ id: 'a' })
		});
		next = applyWsEvent(next, { type: 'notification_deleted', id: 'a' });
		expect(next.items).toHaveLength(0);
		expect(next.unreadCount).toBe(0);
	});

	it('notification_deleted does not change unread for a read item', () => {
		let next = applyWsEvent(state, {
			type: 'notification',
			notification: makeNotification({ id: 'a', read: true })
		});
		next.unreadCount = 2;
		next = applyWsEvent(next, { type: 'notification_deleted', id: 'a' });
		expect(next.items).toHaveLength(0);
		expect(next.unreadCount).toBe(2);
	});

	it('notifications_cleared empties the list', () => {
		let next = applyWsEvent(state, {
			type: 'notification',
			notification: makeNotification({ id: 'a' })
		});
		next = applyWsEvent(next, { type: 'notifications_cleared' });
		expect(next.items).toHaveLength(0);
		expect(next.unreadCount).toBe(0);
	});

	it('ignores unknown event types', () => {
		const next = applyWsEvent(state, { type: 'something_else' });
		expect(next).toBe(state);
	});
});

describe('notifications store', () => {
	beforeEach(() => {
		notifications.reset();
		vi.clearAllMocks();
	});

	it('load() populates items and unreadCount from the api', async () => {
		mockedApi.listNotifications.mockResolvedValueOnce({
			success: true,
			data: { notifications: [makeNotification({ id: 'a' })], unread_count: 1 }
		});
		await notifications.load();
		const s = get(notifications);
		expect(s.items).toHaveLength(1);
		expect(s.unreadCount).toBe(1);
		expect(s.loaded).toBe(true);
	});

	it('add() dedupes and caps', () => {
		notifications.add(makeNotification({ id: 'a' }));
		notifications.add(makeNotification({ id: 'a' }));
		expect(get(notifications).items).toHaveLength(1);
	});

	it('markRead() optimistically updates and calls the api', async () => {
		notifications.add(makeNotification({ id: 'a' }));
		await notifications.markRead('a');
		const s = get(notifications);
		expect(s.items[0].read).toBe(true);
		expect(s.unreadCount).toBe(0);
		expect(mockedApi.markNotificationRead).toHaveBeenCalledWith('a');
	});

	it('markRead() on an already-read item skips the api call', async () => {
		notifications.add(makeNotification({ id: 'a', read: true }));
		await notifications.markRead('a');
		expect(mockedApi.markNotificationRead).not.toHaveBeenCalled();
	});

	it('markAllRead() zeroes unread and calls the api', async () => {
		notifications.add(makeNotification({ id: 'a' }));
		notifications.add(makeNotification({ id: 'b' }));
		await notifications.markAllRead();
		expect(get(notifications).unreadCount).toBe(0);
		expect(mockedApi.markAllNotificationsRead).toHaveBeenCalled();
	});

	it('remove() drops the item and calls the api', async () => {
		notifications.add(makeNotification({ id: 'a' }));
		await notifications.remove('a');
		expect(get(notifications).items).toHaveLength(0);
		expect(mockedApi.deleteNotification).toHaveBeenCalledWith('a');
	});

	it('clear() empties the list and calls the api', async () => {
		notifications.add(makeNotification({ id: 'a' }));
		await notifications.clear();
		expect(get(notifications).items).toHaveLength(0);
		expect(mockedApi.clearNotifications).toHaveBeenCalled();
	});

	it('panel open/close/toggle track state', () => {
		expect(get(notifications).panelOpen).toBe(false);
		notifications.openPanel();
		expect(get(notifications).panelOpen).toBe(true);
		notifications.closePanel();
		expect(get(notifications).panelOpen).toBe(false);
		notifications.togglePanel();
		expect(get(notifications).panelOpen).toBe(true);
	});
});

describe('prefs pure helpers', () => {
	it('applyPrefs merges types + sound and sets prefsLoaded', () => {
		const next = applyPrefs(initialState(), makePrefs({ sound: true }));
		expect(next.prefTypes).toHaveLength(1);
		expect(next.sound).toBe(true);
		expect(next.prefsLoaded).toBe(true);
	});

	it('setTypeEnabledInState flips only the matching key', () => {
		const state = applyPrefs(initialState(), {
			types: [makePref({ key: 'a', enabled: true }), makePref({ key: 'b', enabled: true })],
			sound: false
		});
		const next = setTypeEnabledInState(state, 'a', false);
		expect(next.prefTypes.find((t) => t.key === 'a')?.enabled).toBe(false);
		expect(next.prefTypes.find((t) => t.key === 'b')?.enabled).toBe(true);
	});
});

describe('notifications store — preferences', () => {
	beforeEach(() => {
		notifications.reset();
		vi.clearAllMocks();
	});

	// Seed the store's prefs via the load path (mocked API) so tests start from a
	// known preferences state without a test-only store method.
	async function seedPrefs(prefs: NotificationPreferences): Promise<void> {
		mockedApi.getNotificationTypes.mockResolvedValueOnce({ success: true, data: prefs });
		await notifications.loadPrefs();
	}

	it('loadPrefs maps the API payload into state', async () => {
		mockedApi.getNotificationTypes.mockResolvedValueOnce({
			success: true,
			data: makePrefs({ sound: true })
		});
		await notifications.loadPrefs();
		const s = get(notifications);
		expect(s.prefsLoaded).toBe(true);
		expect(s.sound).toBe(true);
		expect(s.prefTypes).toHaveLength(1);
	});

	it('setTypeEnabled optimistically updates, PUTs the partial, and syncs from the response', async () => {
		await seedPrefs(makePrefs({ types: [makePref({ key: 'a', enabled: true })] }));
		mockedApi.updateNotificationPreferences.mockResolvedValueOnce({
			success: true,
			data: makePrefs({ types: [makePref({ key: 'a', enabled: false })] })
		});
		await notifications.setTypeEnabled('a', false);
		expect(mockedApi.updateNotificationPreferences).toHaveBeenCalledWith({ types: { a: false } });
		expect(get(notifications).prefTypes.find((t) => t.key === 'a')?.enabled).toBe(false);
	});

	it('setTypeEnabled reverts optimistic change when the API fails', async () => {
		await seedPrefs(makePrefs({ types: [makePref({ key: 'a', enabled: true })] }));
		mockedApi.updateNotificationPreferences.mockRejectedValueOnce(new Error('boom'));
		await notifications.setTypeEnabled('a', false);
		// Reverted back to the pre-toggle value.
		expect(get(notifications).prefTypes.find((t) => t.key === 'a')?.enabled).toBe(true);
	});

	it('setSound optimistically updates and PUTs the partial', async () => {
		await seedPrefs(makePrefs({ sound: false }));
		mockedApi.updateNotificationPreferences.mockResolvedValueOnce({
			success: true,
			data: makePrefs({ sound: true })
		});
		await notifications.setSound(true);
		expect(mockedApi.updateNotificationPreferences).toHaveBeenCalledWith({ sound: true });
		expect(get(notifications).sound).toBe(true);
	});

	it('setSound reverts when the API responds unsuccessfully', async () => {
		await seedPrefs(makePrefs({ sound: false }));
		mockedApi.updateNotificationPreferences.mockResolvedValueOnce({ success: false });
		await notifications.setSound(true);
		expect(get(notifications).sound).toBe(false);
	});
});
