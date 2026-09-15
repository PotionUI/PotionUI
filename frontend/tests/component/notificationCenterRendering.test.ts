// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { writable } from 'svelte/store';
import { flushSync } from 'svelte';

const notificationTypes = [
	{
		key: 'generation.completed',
		label: 'Generation completed',
		description: '',
		category: 'generation',
		default_enabled: true,
		enabled: true
	},
	{
		key: 'system.plugins',
		label: 'Plugin lifecycle events',
		description: '',
		category: 'system',
		default_enabled: true,
		enabled: true
	},
	{
		key: 'inspiration.comment',
		label: 'Inspiration comments',
		description: '',
		category: 'inspirations',
		default_enabled: true,
		enabled: true
	}
];

const notificationsApiMock = {
	listNotifications: vi.fn().mockResolvedValue({ success: true, data: { notifications: [], unread_count: 0 } }),
	markNotificationRead: vi.fn().mockResolvedValue({ success: true }),
	markAllNotificationsRead: vi.fn().mockResolvedValue({ success: true, data: { updated: 0 } }),
	deleteNotification: vi.fn().mockResolvedValue({ success: true }),
	clearNotifications: vi.fn().mockResolvedValue({ success: true, data: { deleted: 0 } }),
	getNotificationTypes: vi
		.fn()
		.mockResolvedValue({ success: true, data: { types: notificationTypes, sound: false, chat_sound: false } }),
	updateNotificationPreferences: vi.fn()
};

vi.mock('$lib/services/api', () => ({ api: notificationsApiMock }));

vi.mock('$lib/services/api/index', () => ({
	api: {
		...notificationsApiMock,
		getClient: () => ({ get: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }) }),
		getBaseURL: () => '',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		getReadiness: vi.fn().mockResolvedValue({ overall: 'ready' }),
		getKeybindings: vi.fn().mockResolvedValue({ success: true, data: { keybindings: [] } })
	}
}));

vi.mock('$lib/plugin-api/extensionRefresh', () => ({
	refreshPluginExtensions: vi.fn(() => Promise.resolve())
}));

vi.mock('$lib/stores/auth', () => ({
	authStore: writable({ isAuthenticated: true, token: null, user: null, loading: false, error: null })
}));

vi.mock('$lib/stores/keybindings', () => ({
	keybindingsStore: {
		subscribe: writable({ bindings: [], helpPanelOpen: false, loaded: false }).subscribe,
		registerHandler: vi.fn(),
		unregisterHandler: vi.fn(),
		openHelp: vi.fn(),
		toggleHelp: vi.fn()
	},
	shortcutLabels: writable<Record<string, string | undefined>>({})
}));

vi.mock('$lib/services/admin-api', () => ({
	getBackends: vi.fn().mockResolvedValue({ success: true, data: [] }),
	invokeBackendQuickAction: vi.fn()
}));

const { notifications } = await import('$lib/stores/notifications');
const { default: NotificationPanel } = await import('$lib/components/notifications/NotificationPanel.svelte');
const { default: Sidebar } = await import('$lib/components/Sidebar.svelte');
const { createClassComponent } = await import('svelte/legacy');

type AppNotification = Parameters<typeof notifications.add>[0];

function makeNotification(overrides: Partial<AppNotification> = {}): AppNotification {
	return {
		id: overrides.id ?? crypto.randomUUID(),
		user_id: 'u1',
		category: 'generation',
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

let mounted: { target: HTMLDivElement; component: ReturnType<typeof createClassComponent> } | null = null;

function mountComponent(component: unknown, props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({ component: component as never, target, props });
	mounted = { target, component: instance };
	return mounted;
}

async function settle() {
	await new Promise((r) => setTimeout(r, 0));
	flushSync();
}

afterEach(() => {
	mounted?.component.$destroy();
	mounted?.target.remove();
	mounted = null;
	notifications.reset();
	vi.clearAllMocks();
});

describe('NotificationPanel', () => {
	it('groups items into Today/Yesterday/Earlier and renders a filter chip per category', async () => {
		const startOfToday = new Date();
		startOfToday.setHours(0, 0, 0, 0);

		notifications.add(
			makeNotification({
				id: 'today',
				category: 'generation',
				created_at: new Date(startOfToday.getTime() + 60_000).toISOString()
			})
		);
		notifications.add(
			makeNotification({
				id: 'yesterday',
				category: 'system',
				created_at: new Date(startOfToday.getTime() - 60_000).toISOString()
			})
		);
		notifications.add(
			makeNotification({
				id: 'earlier',
				category: 'inspirations',
				created_at: new Date(startOfToday.getTime() - 10 * 86_400_000).toISOString()
			})
		);
		notifications.openPanel();

		const { target } = mountComponent(NotificationPanel);
		flushSync();
		await settle();
		await settle();

		const dayLabels = Array.from(target.querySelectorAll('div.font-mono.text-2xs.uppercase')).map((el) =>
			el.textContent?.trim()
		);
		expect(dayLabels).toEqual(['Today', 'Yesterday', 'Earlier']);

		const chips = target.querySelectorAll('[role="group"][aria-label="Filter by kind"] button');
		expect(Array.from(chips).map((c) => c.textContent?.trim())).toEqual([
			'All',
			'Generation',
			'System',
			'Inspirations'
		]);
	});

	it('shows the dot-grid empty state and no filter row when there are no items', async () => {
		notifications.openPanel();

		const { target } = mountComponent(NotificationPanel);
		flushSync();
		await settle();

		expect(target.textContent).toContain("You're all caught up");
		expect(target.querySelector('[role="group"][aria-label="Filter by kind"]')).toBeNull();
	});
});

describe('Sidebar bell trigger', () => {
	it('shows no badge when idle', async () => {
		const { target } = mountComponent(Sidebar);
		flushSync();
		await settle();

		const bell = target.querySelector('[aria-label="Notifications"]');
		expect(bell).not.toBeNull();
		expect(bell?.querySelector('span[aria-hidden="true"]')).toBeNull();
	});

	it('shows the numeral count once unreadCount is known', async () => {
		notifications.add(makeNotification({ id: 'unread-1' }));

		const { target } = mountComponent(Sidebar);
		flushSync();
		await settle();

		const bell = target.querySelector('[aria-label="Notifications, unread"]');
		expect(bell).not.toBeNull();
		expect(bell?.querySelector('span[aria-hidden="true"]')?.textContent?.trim()).toBe('1');
	});
});
