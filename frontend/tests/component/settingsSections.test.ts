// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { writable } from 'svelte/store';

const hoisted = vi.hoisted(() => ({
	loadPrefs: vi.fn(),
	getReadiness: vi.fn()
}));

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: () => ({ get: vi.fn().mockResolvedValue({ data: { data: {} } }), put: vi.fn() }),
		listApiKeys: vi.fn().mockResolvedValue({
			success: true,
			data: {
				keys: [],
				limits: { keys_enabled: true, mcp_enabled: true, scopes_allowed: ['read', 'generate', 'content', 'mcp'], max_lifetime_days: null }
			}
		}),
		getMcpTools: vi.fn().mockResolvedValue({
			success: true,
			data: {
				level: 'default',
				key_id: null,
				policy: 'read_only_on',
				ceiling_policy: 'all_on',
				summary: { exposed: 0, total: 0, writes_exposed: 0, unavailable: 0, locked: 0, unreviewed: 0 },
				tools: []
			}
		}),
		getReadiness: (...args: unknown[]) => hoisted.getReadiness(...args)
	}
}));

vi.mock('$lib/plans/meApi', async (importOriginal) => ({
	...(await importOriginal<typeof import('../../src/lib/plans/meApi')>()),
	getMyStorageBreakdown: vi.fn().mockResolvedValue([])
}));

const authState = writable<Record<string, unknown>>({
	isAuthenticated: true,
	loading: false,
	error: null,
	token: 't',
	user: { id: 'u1', username: 'alice', account_type: 'USER', avatar_url: null, has_local_password: true }
});

vi.mock('$lib/stores/auth', () => ({
	authStore: { subscribe: authState.subscribe, logout: vi.fn(), refreshUser: vi.fn() }
}));

const notificationsState = writable({
	items: [],
	unreadCount: 0,
	loaded: true,
	panelOpen: false,
	lastSeenAt: null,
	prefTypes: [] as { key: string; label: string; description: string; category: string; enabled: boolean }[],
	sound: false,
	chat_sound: false,
	prefsLoaded: true
});

vi.mock('$lib/stores/notifications', () => ({
	notifications: {
		subscribe: notificationsState.subscribe,
		loadPrefs: hoisted.loadPrefs,
		setSound: vi.fn(),
		setChatSound: vi.fn(),
		setTypeEnabled: vi.fn()
	}
}));

const { page } = await import('../../tests/component/stubs/appStores');
const { showResumeSetup } = await import('$lib/stores/resumeSetup');
const { nsfwFilterStore } = await import('$lib/stores/nsfwFilter');
const { default: SettingsPage } = await import('../../src/routes/settings/+page.svelte');

const TYPE = { key: 'gen_done', label: 'Generation finished', description: '', category: 'Generation', enabled: true };

let target: HTMLDivElement;
let instance: ReturnType<typeof mount> | null = null;

async function settle() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

async function open(href = '/settings') {
	page.update((current) => ({ ...current, url: new URL(href, 'http://localhost') }));
	target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(SettingsPage, { target });
	await settle();
}

function title(): string {
	return target.querySelector('h1')?.textContent?.trim() ?? '';
}

function rows(): HTMLElement[] {
	return Array.from(target.querySelectorAll('[aria-label="Settings sections"] [role="option"]'));
}

function labels(): string[] {
	return rows().map((row) => row.textContent?.replace(/\s+/g, ' ').trim() ?? '');
}

beforeEach(() => {
	localStorage.clear();
	hoisted.getReadiness.mockResolvedValue({ overall: 'ready' });
	notificationsState.update((state) => ({ ...state, prefTypes: [TYPE], prefsLoaded: true }));
	showResumeSetup.set(false);
	nsfwFilterStore.reset();
	authState.update((state) => ({
		...state,
		user: { id: 'u1', username: 'alice', account_type: 'USER', avatar_url: null, has_local_password: true }
	}));
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target?.remove();
});

describe('settings page sections', () => {
	it('lists the six sections in order with no counts and opens on Account', async () => {
		await open();
		expect(labels()).toEqual(['Account', 'Plan & usage', 'Appearance', 'Content', 'Notifications', 'API keys']);
		expect(title()).toBe('Account');
		expect(target.textContent).toContain('Change avatar');
		expect(target.textContent).toContain('Change password');
		expect(target.querySelector('#plan')).toBeNull();
	});

	it('switches section on click and puts it in the URL without navigating away', async () => {
		await open();
		rows()[2].click();
		await settle();
		let url!: URL;
		page.subscribe((value) => (url = value.url))();
		expect(url.pathname).toBe('/settings');
		expect(url.searchParams.get('section')).toBe('appearance');
		expect(title()).toBe('Appearance');
		expect(target.querySelector('[role="radiogroup"][aria-label="Theme"]')).not.toBeNull();
		expect(target.textContent).not.toContain('Change password');
	});

	it('opens the API keys section for ?section=keys', async () => {
		await open('/settings?section=keys');
		expect(title()).toBe('API keys');
		expect(target.textContent).toContain('A key lets a script or an MCP client act as you');
		expect(target.textContent).toContain('MCP tools');
		expect(target.textContent).not.toContain('Change avatar');
	});

	it('no longer knows the old mcp section id and falls back to Account', async () => {
		await open('/settings?section=mcp');
		expect(title()).toBe('Account');
	});

	it('maps the legacy #plan hash to Plan & usage', async () => {
		await open('/settings#plan');
		expect(title()).toBe('Plan & usage');
		expect(target.querySelector('#plan')).not.toBeNull();
	});

	it('maps #notifications to Notifications while the instance has notification types', async () => {
		await open('/settings#notifications');
		expect(title()).toBe('Notifications');
		expect(target.querySelector('#notifications')).not.toBeNull();
		expect(target.textContent).toContain('Generation finished');
	});

	it('hides Notifications and falls back to Account for #notifications when there are no types', async () => {
		notificationsState.update((state) => ({ ...state, prefTypes: [], prefsLoaded: true }));
		await open('/settings#notifications');
		expect(labels()).not.toContain('Notifications');
		expect(title()).toBe('Account');
	});

	it('hides Content for restricted accounts and falls back to Account', async () => {
		nsfwFilterStore.setRestricted(true);
		await open('/settings?section=content');
		expect(labels()).not.toContain('Content');
		expect(title()).toBe('Account');
	});

	it('falls back to Account for an unknown section', async () => {
		await open('/settings?section=nope');
		expect(title()).toBe('Account');
	});

	it('lets the legacy hash win over ?section', async () => {
		await open('/settings?section=appearance#plan');
		expect(title()).toBe('Plan & usage');
		expect(target.querySelector('#plan')).not.toBeNull();
	});

	it('clears the legacy hash when a section is clicked', async () => {
		await open('/settings#plan');
		rows()[4].click();
		await settle();
		let url!: URL;
		page.subscribe((value) => (url = value.url))();
		expect(url.hash).toBe('');
		expect(url.searchParams.get('section')).toBe('notifications');
		expect(title()).toBe('Notifications');
		expect(target.querySelector('#plan')).toBeNull();
	});

	it('keeps Notifications listed and shows a spinner while the preferences are still loading', async () => {
		notificationsState.update((state) => ({ ...state, prefTypes: [], prefsLoaded: false }));
		await open('/settings?section=notifications');
		expect(labels()).toContain('Notifications');
		expect(title()).toBe('Notifications');
		expect(target.textContent).not.toContain('No configurable notification types.');
	});

	it('shows the Sensitive media control under Content for an unrestricted account', async () => {
		await open('/settings?section=content');
		expect(title()).toBe('Content');
		expect(target.querySelector('[role="radiogroup"][aria-label="Sensitive media"]')).not.toBeNull();
		expect(target.textContent).not.toContain('Change password');
	});

	it('has no Logout card on any section', async () => {
		await open();
		for (let i = 0; i < rows().length; i++) {
			rows()[i].click();
			await settle();
			expect(target.textContent).not.toMatch(/\bLogout\b/);
		}
	});

	it('keeps the Resume setup banner above a non-Account section for an admin on an unready instance', async () => {
		authState.update((state) => ({
			...state,
			user: { id: 'a1', username: 'root', account_type: 'ADMIN', avatar_url: null, has_local_password: true }
		}));
		hoisted.getReadiness.mockResolvedValue({ overall: 'needs_setup' });
		await open('/settings?section=appearance');
		expect(title()).toBe('Appearance');
		expect(target.textContent).toContain('Resume setup');
		expect(target.querySelector('a[href="/setup"]')).not.toBeNull();
	});

	it('shows no Resume setup banner to a non-admin', async () => {
		showResumeSetup.set(true);
		await open();
		expect(target.querySelector('a[href="/setup"]')).toBeNull();
	});
});
