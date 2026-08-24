import { writable } from 'svelte/store';
import { api } from '$lib/services/api';
import { logger } from '$lib/utils/logger';
import type {
	AppNotification,
	NotificationPreferences,
	NotificationTypePref,
	UpdateNotificationPreferencesInput
} from '$lib/services/api/notifications';

export const NOTIFICATION_CAP = 200;

export interface NotificationsState {
	items: AppNotification[];
	unreadCount: number;
	loaded: boolean;
	panelOpen: boolean;
	prefTypes: NotificationTypePref[];
	sound: boolean;
	prefsLoaded: boolean;
}

export function initialState(): NotificationsState {
	return {
		items: [],
		unreadCount: 0,
		loaded: false,
		panelOpen: false,
		prefTypes: [],
		sound: false,
		prefsLoaded: false
	};
}

/** Merge a preferences payload into store state (pure). */
export function applyPrefs(
	state: NotificationsState,
	prefs: NotificationPreferences
): NotificationsState {
	return { ...state, prefTypes: prefs.types, sound: prefs.sound, prefsLoaded: true };
}

/** Optimistically flip one type's `enabled` flag (pure). */
export function setTypeEnabledInState(
	state: NotificationsState,
	key: string,
	enabled: boolean
): NotificationsState {
	return {
		...state,
		prefTypes: state.prefTypes.map((t) => (t.key === key ? { ...t, enabled } : t))
	};
}

/**
 * Prepend a notification, dedupe by id, and cap the list length. Pure helper
 * shared by `add` and the `applyWsEvent` reducer.
 */
function insert(items: AppNotification[], n: AppNotification): AppNotification[] {
	const deduped = items.filter((i) => i.id !== n.id);
	return [n, ...deduped].slice(0, NOTIFICATION_CAP);
}

/**
 * Server WS messages that touch notification store state. `notification` /
 * `toast` side effects (showing a toast) are handled by the WS service; this
 * reducer only computes the next store state, so it is testable without a socket.
 */
export type NotificationWsEvent =
	| { type: 'connection_established'; unread_count?: number }
	| { type: 'notification'; notification: AppNotification; show_toast?: boolean }
	| { type: 'notification_read'; id: string }
	| { type: 'all_read' }
	| { type: 'notification_deleted'; id: string }
	| { type: 'notifications_cleared' }
	| { type: string; [key: string]: unknown };

/** Pure reducer: given the current state and a WS event, return the next state. */
export function applyWsEvent(
	state: NotificationsState,
	event: NotificationWsEvent
): NotificationsState {
	switch (event.type) {
		case 'connection_established': {
			const unread = (event as { unread_count?: number }).unread_count;
			return typeof unread === 'number' ? { ...state, unreadCount: unread } : state;
		}
		case 'notification': {
			const n = (event as { notification: AppNotification }).notification;
			if (!n || state.items.some((i) => i.id === n.id)) return state;
			return {
				...state,
				items: insert(state.items, n),
				unreadCount: n.read ? state.unreadCount : state.unreadCount + 1
			};
		}
		case 'notification_read': {
			const id = (event as { id: string }).id;
			const target = state.items.find((i) => i.id === id);
			if (!target || target.read) return state;
			return {
				...state,
				items: state.items.map((i) => (i.id === id ? { ...i, read: true } : i)),
				unreadCount: Math.max(0, state.unreadCount - 1)
			};
		}
		case 'all_read':
			return {
				...state,
				items: state.items.map((i) => (i.read ? i : { ...i, read: true })),
				unreadCount: 0
			};
		case 'notification_deleted': {
			const id = (event as { id: string }).id;
			const target = state.items.find((i) => i.id === id);
			if (!target) return state;
			return {
				...state,
				items: state.items.filter((i) => i.id !== id),
				unreadCount: target.read ? state.unreadCount : Math.max(0, state.unreadCount - 1)
			};
		}
		case 'notifications_cleared':
			return { ...state, items: [], unreadCount: 0 };
		default:
			return state;
	}
}

function createNotificationsStore() {
	const { subscribe, update, set } = writable<NotificationsState>(initialState());

	async function load(): Promise<void> {
		try {
			const res = await api.listNotifications({ limit: 50 });
			if (res.success && res.data) {
				update((s) => ({
					...s,
					items: res.data!.notifications,
					unreadCount: res.data!.unread_count,
					loaded: true
				}));
			}
		} catch (err) {
			logger.error('Failed to load notifications:', err);
		}
	}

	async function loadMore(): Promise<void> {
		let before: string | undefined;
		update((s) => {
			before = s.items[s.items.length - 1]?.id;
			return s;
		});
		if (!before) return;
		try {
			const res = await api.listNotifications({ limit: 50, before });
			if (res.success && res.data) {
				const older = res.data.notifications;
				update((s) => {
					const existing = new Set(s.items.map((i) => i.id));
					const merged = [...s.items, ...older.filter((n) => !existing.has(n.id))];
					return { ...s, items: merged.slice(0, NOTIFICATION_CAP), unreadCount: res.data!.unread_count };
				});
			}
		} catch (err) {
			logger.error('Failed to load more notifications:', err);
		}
	}

	/** Apply a WS event to store state (used by the WS service for sync events). */
	function dispatch(event: NotificationWsEvent): void {
		update((s) => applyWsEvent(s, event));
	}

	/** Add a pushed notification (dedupe, prepend, cap). */
	function add(n: AppNotification): void {
		update((s) => applyWsEvent(s, { type: 'notification', notification: n }));
	}

	async function markRead(id: string): Promise<void> {
		let changed = false;
		update((s) => {
			const target = s.items.find((i) => i.id === id);
			if (!target || target.read) return s;
			changed = true;
			return applyWsEvent(s, { type: 'notification_read', id });
		});
		if (!changed) return;
		try {
			await api.markNotificationRead(id);
		} catch (err) {
			logger.error('Failed to mark notification read:', err);
		}
	}

	async function markAllRead(): Promise<void> {
		update((s) => applyWsEvent(s, { type: 'all_read' }));
		try {
			await api.markAllNotificationsRead();
		} catch (err) {
			logger.error('Failed to mark all notifications read:', err);
		}
	}

	async function remove(id: string): Promise<void> {
		update((s) => applyWsEvent(s, { type: 'notification_deleted', id }));
		try {
			await api.deleteNotification(id);
		} catch (err) {
			logger.error('Failed to delete notification:', err);
		}
	}

	async function clear(): Promise<void> {
		update((s) => applyWsEvent(s, { type: 'notifications_cleared' }));
		try {
			await api.clearNotifications();
		} catch (err) {
			logger.error('Failed to clear notifications:', err);
		}
	}

	async function loadPrefs(): Promise<void> {
		try {
			const res = await api.getNotificationTypes();
			if (res.success && res.data) {
				update((s) => applyPrefs(s, res.data!));
			}
		} catch (err) {
			logger.error('Failed to load notification preferences:', err);
		}
	}

	/** Optimistically apply a prefs change, PUT the partial payload, revert on failure. */
	async function persistPrefs(
		optimistic: (s: NotificationsState) => NotificationsState,
		payload: UpdateNotificationPreferencesInput
	): Promise<void> {
		let previous: NotificationsState | null = null;
		update((s) => {
			previous = s;
			return optimistic(s);
		});
		try {
			const res = await api.updateNotificationPreferences(payload);
			if (res.success && res.data) {
				update((s) => applyPrefs(s, res.data!));
			} else if (previous) {
				const revert = previous;
				update(() => revert);
			}
		} catch (err) {
			logger.error('Failed to update notification preferences:', err);
			if (previous) {
				const revert = previous;
				update(() => revert);
			}
		}
	}

	async function setTypeEnabled(key: string, enabled: boolean): Promise<void> {
		await persistPrefs((s) => setTypeEnabledInState(s, key, enabled), { types: { [key]: enabled } });
	}

	async function setSound(enabled: boolean): Promise<void> {
		await persistPrefs((s) => ({ ...s, sound: enabled }), { sound: enabled });
	}

	function openPanel(): void {
		update((s) => ({ ...s, panelOpen: true }));
	}

	function closePanel(): void {
		update((s) => ({ ...s, panelOpen: false }));
	}

	function togglePanel(): void {
		update((s) => ({ ...s, panelOpen: !s.panelOpen }));
	}

	function reset(): void {
		set(initialState());
	}

	return {
		subscribe,
		load,
		loadMore,
		dispatch,
		add,
		markRead,
		markAllRead,
		remove,
		clear,
		loadPrefs,
		setTypeEnabled,
		setSound,
		openPanel,
		closePanel,
		togglePanel,
		reset
	};
}

export const notifications = createNotificationsStore();
