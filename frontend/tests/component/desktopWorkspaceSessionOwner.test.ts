import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { flushSync } from 'svelte';
import { settle } from './helpers/settle';
import { keyboardFocus } from './helpers/keyboardFocus';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getSessionsForPreset: vi.fn(),
		getSessionById: vi.fn(),
		getSessionVersions: vi.fn(async () => ({ success: true, data: [] })),
		getPresetFormSchema: vi.fn(async () => ({ success: false, error: 'none' })),
		getFormulaDeclaration: vi.fn(async () => ({ success: true, data: null }))
	}
}));

vi.mock('$lib/stores/auth', () => ({
	authStore: { subscribe: (fn: (v: unknown) => void) => (fn({ user: { account_type: 'USER' } }), () => {}) }
}));
vi.mock('../../src/routes/generate/components/GenerationFormPane.svelte', async () => ({
	default: (await import('./stubs/BlankStub.svelte')).default
}));
vi.mock('../../src/routes/generate/components/GenerationWorkbenchPane.svelte', async () => ({
	default: (await import('./stubs/BlankStub.svelte')).default
}));
vi.mock('../../src/routes/generate/components/PromptSection.svelte', async () => ({
	default: (await import('./stubs/BlankStub.svelte')).default
}));

const { api } = await import('$lib/services/api/index');
const { tabsStore } = await import('$lib/stores/tabs');
const { tabSessionDirty } = await import('$lib/stores/tabSessionDirty');
const { queryTabDirty } = await import('$lib/stores/workspaceDirtyQuery');
const { keybindingsStore } = await import('$lib/stores/keybindings');
const { toasts } = await import('$lib/stores/toast');
const { collectTabSessionData } = await import('$lib/utils/sessionTabState');
const { default: DesktopWorkspaceFixture } = await import('./fixtures/DesktopWorkspaceFixture.svelte');
const { default: SessionScopeTabsFixture } = await import('./fixtures/SessionScopeTabsFixture.svelte');
const { createClassComponent } = await import('svelte/legacy');

const PRESET_ID = 'preset-owner';
const MODE = 'image';
const SESSION_ID = 'session-owner';
const ORIGINAL_SEGMENTS = [{ id: 'a', content: 'original segment' }];
const EDITED_SEGMENTS = [{ id: 'a', content: 'EDITED segment' }];

const session = {
	id: SESSION_ID,
	preset_id: PRESET_ID,
	name: 'Owner session',
	data: { [MODE]: { promptSegments: ORIGINAL_SEGMENTS } },
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-01T00:00:00Z'
};

let tabId: string;
let extraTabIds: string[] = [];
let mounted: { target: HTMLElement; component: { $set: (p: object) => void; $destroy: () => void } } | undefined;

function tab() {
	return generationTabs(get(tabsStore).tabs).find((t) => t.id === tabId)!;
}

function mountWorkspace() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: DesktopWorkspaceFixture as never,
		target,
		props: {
			tab: tab(),
			presets: [{ id: PRESET_ID, name: 'Owner preset', version: '1' }],
			availableModes: [{ id: MODE, label: 'Image' }]
		}
	}) as unknown as { $set: (p: object) => void; $destroy: () => void };
	flushSync();
	return { target, component };
}

function sessionSegment(target: HTMLElement) {
	return target.querySelector('[data-testid="context-bar"] [data-testid="session-trigger"]');
}

beforeEach(() => {
	const savedSessionSignature = JSON.stringify(
		collectTabSessionData({ promptSegments: ORIGINAL_SEGMENTS } as never, MODE, session.data)
	);
	tabId = tabsStore.addTabWithData('Owner tab', {
		selectedPreset: PRESET_ID,
		selectedMode: MODE,
		selectedSessionId: SESSION_ID,
		savedSessionSignature,
		promptSegments: EDITED_SEGMENTS,
		layoutMode: 'two'
	});
	vi.mocked(api.getSessionsForPreset).mockResolvedValue({ success: true, data: [session] } as never);
	vi.mocked(api.getSessionById).mockResolvedValue({ success: true, data: session } as never);
});

afterEach(() => {
	mounted?.component.$destroy();
	mounted?.target.remove();
	mounted = undefined;
	tabsStore.removeTab(tabId);
	for (const id of extraTabIds) tabsStore.removeTab(id);
	extraTabIds = [];
	vi.restoreAllMocks();
	document.body.innerHTML = '';
});

describe('Desktop workspace session owner', () => {
	it('puts the session in the context bar above the panes and publishes the unsaved state', async () => {
		mounted = mountWorkspace();
		await settle(10);
		flushSync();
		expect(sessionSegment(mounted.target)?.textContent).toContain('Owner session');
		expect(mounted.target.querySelector('[data-testid="context-bar"] button[aria-label="Save session"]')).not.toBeNull();
		expect(mounted.target.querySelector('[data-testid="generation-panels-root"] [data-testid="session-trigger"]')).toBeNull();
		expect(get(tabSessionDirty)[tabId]).toBe(true);
	});

	it('keeps the session in the bar while the form is folded and forgets the tab on unmount', async () => {
		mounted = mountWorkspace();
		await settle(10);
		tabsStore.updateTab(tabId, { leftPanelCollapsed: true });
		mounted.component.$set({ tab: tab() });
		flushSync();
		await settle(10);
		expect(mounted.target.querySelector('button[aria-label="Expand generation settings"]')).not.toBeNull();
		expect(sessionSegment(mounted.target)?.textContent).toContain('Owner session');
		expect(mounted.target.querySelector('[data-testid="context-bar"] button[aria-label="Save session"]')).not.toBeNull();
		expect(get(tabSessionDirty)[tabId]).toBe(true);
		expect(await queryTabDirty(tabId, 200)).toBe(true);
		mounted.component.$destroy();
		mounted.target.remove();
		mounted = undefined;
		expect(tabId in get(tabSessionDirty)).toBe(false);
	});
});

describe('Reload from last snapshot', () => {
	function reloadButton(target: HTMLElement) {
		return target.querySelector<HTMLButtonElement>('[data-testid="context-bar"] [data-testid="session-reload"]');
	}

	function confirmButton(label: RegExp) {
		return Array.from(document.querySelectorAll<HTMLButtonElement>('[role="alertdialog"] button')).find((el) => label.test(el.textContent?.trim() ?? ''));
	}

	async function mountDirty() {
		mounted = mountWorkspace();
		await settle(10);
		flushSync();
		expect(get(tabSessionDirty)[tabId]).toBe(true);
		return mounted.target;
	}

	it('offers the reload in the context bar while the session has unsaved changes', async () => {
		const target = await mountDirty();
		expect(reloadButton(target)?.getAttribute('aria-label')).toBe('Reload from last snapshot');
		expect(reloadButton(target)?.getAttribute('aria-disabled')).toBeNull();
		expect(tab().promptSegments).toEqual(EDITED_SEGMENTS);
	});

	it('offers no reload when the tab has no saved session', async () => {
		tabsStore.updateTab(tabId, { selectedSessionId: null, savedSessionSignature: undefined });
		vi.mocked(api.getSessionsForPreset).mockResolvedValue({ success: true, data: [] } as never);
		mounted = mountWorkspace();
		await settle(10);
		flushSync();
		expect(reloadButton(mounted.target)).toBeNull();
	});

	it('asks first and keeps the edits when the prompt is cancelled', async () => {
		const target = await mountDirty();
		reloadButton(target)!.click();
		flushSync();
		await settle(4);
		const dialog = document.querySelector('[role="alertdialog"]')!;
		expect(dialog.textContent).toContain('Discard your changes and reload the last snapshot of "Owner session"?');
		confirmButton(/^Cancel/)!.click();
		flushSync();
		await settle(10);
		expect(document.querySelector('[role="alertdialog"]')).toBeNull();
		expect(tab().promptSegments).toEqual(EDITED_SEGMENTS);
		expect(get(tabSessionDirty)[tabId]).toBe(true);
		expect(reloadButton(target)).not.toBeNull();
	});

	it('restores the saved snapshot through the load path, clears the unsaved state and leaves the button disabled', async () => {
		const target = await mountDirty();
		vi.mocked(api.getSessionById).mockClear();
		reloadButton(target)!.click();
		flushSync();
		await settle(4);
		confirmButton(/^Reload/)!.click();
		flushSync();
		await settle(12);
		flushSync();
		expect(api.getSessionById).toHaveBeenCalledWith(SESSION_ID);
		expect(tab().promptSegments).toEqual(ORIGINAL_SEGMENTS);
		expect(tab().selectedSessionId).toBe(SESSION_ID);
		expect(get(tabSessionDirty)[tabId]).toBe(false);
		expect(reloadButton(target)?.getAttribute('aria-disabled')).toBe('true');
		expect(target.querySelector('[data-testid="context-bar"] button[aria-label="Session saved"]')).not.toBeNull();
		keyboardFocus(reloadButton(target)!);
		await Promise.resolve();
		expect(document.body.querySelector('[role="tooltip"]')?.textContent?.trim()).toBe('No unsaved changes to discard');
		reloadButton(target)!.click();
		flushSync();
		await settle(4);
		expect(document.querySelector('[role="alertdialog"]')).toBeNull();
	});

	it('does the same from the row in the session dropdown', async () => {
		const target = await mountDirty();
		vi.mocked(api.getSessionById).mockClear();
		sessionSegment(target)!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();
		await settle(4);
		const row = document.body.querySelector<HTMLElement>('[data-testid="session-reload-row"]')!;
		expect(row).not.toBeNull();
		row.querySelector('button')!.click();
		flushSync();
		await settle(4);
		expect(document.body.querySelector('[role="dialog"][aria-label="Sessions"]')).toBeNull();
		confirmButton(/^Reload/)!.click();
		flushSync();
		await settle(12);
		flushSync();
		expect(tab().promptSegments).toEqual(ORIGINAL_SEGMENTS);
		expect(get(tabSessionDirty)[tabId]).toBe(false);
	});
});

describe('Reload from last snapshot, while it runs and when it fails', () => {
	function reloadButton(target: HTMLElement) {
		return target.querySelector<HTMLButtonElement>('[data-testid="context-bar"] [data-testid="session-reload"]');
	}

	function confirmButton(label: RegExp) {
		return Array.from(document.querySelectorAll<HTMLButtonElement>('[role="alertdialog"] button')).find((el) => label.test(el.textContent?.trim() ?? ''));
	}

	async function openConfirm(target: HTMLElement) {
		reloadButton(target)!.focus();
		reloadButton(target)!.click();
		flushSync();
		await settle(4);
	}

	async function mountDirty() {
		mounted = mountWorkspace();
		await settle(10);
		flushSync();
		return mounted.target;
	}

	afterEach(() => {
		for (const toast of get(toasts)) toasts.remove(toast.id);
	});

	it('keeps the confirm open and its buttons disabled while the snapshot loads, then closes it', async () => {
		const target = await mountDirty();
		let finish!: (value: unknown) => void;
		vi.mocked(api.getSessionById).mockReturnValueOnce(new Promise((resolve) => (finish = resolve)) as never);
		await openConfirm(target);
		confirmButton(/^Reload/)!.click();
		flushSync();
		await settle(6);

		const dialog = document.querySelector('[role="alertdialog"]')!;
		expect(dialog).not.toBeNull();
		expect(Array.from(dialog.querySelectorAll('button')).every((button) => button.disabled)).toBe(true);
		expect(tab().promptSegments).toEqual(EDITED_SEGMENTS);

		finish({ success: true, data: session });
		await settle(14);
		flushSync();
		expect(document.querySelector('[role="alertdialog"]')).toBeNull();
		expect(tab().promptSegments).toEqual(ORIGINAL_SEGMENTS);
	});

	it('closes the confirm, keeps the edits and reports it when the snapshot cannot be loaded', async () => {
		const target = await mountDirty();
		vi.mocked(api.getSessionById).mockRejectedValueOnce(new Error('offline'));
		await openConfirm(target);
		confirmButton(/^Reload/)!.click();
		flushSync();
		await settle(14);
		flushSync();

		expect(document.querySelector('[role="alertdialog"]')).toBeNull();
		expect(tab().promptSegments).toEqual(EDITED_SEGMENTS);
		expect(get(tabSessionDirty)[tabId]).toBe(true);
		expect(get(toasts).map((toast) => toast.message)).toContain('Could not reload the last snapshot.');
		expect(reloadButton(target)?.getAttribute('aria-disabled')).toBeNull();
	});

	it('returns focus to the Reload button after Confirm and after Cancel', async () => {
		const target = await mountDirty();
		await openConfirm(target);
		confirmButton(/^Cancel/)!.click();
		flushSync();
		await settle(14);
		expect(document.activeElement).toBe(reloadButton(target));

		await openConfirm(target);
		confirmButton(/^Reload/)!.click();
		flushSync();
		await settle(14);
		flushSync();
		expect(document.activeElement).toBe(reloadButton(target));
	});

	it('returns focus to the session trigger after Confirm and after Cancel when started from the dropdown row', async () => {
		const target = await mountDirty();
		const trigger = () => target.querySelector<HTMLButtonElement>('[data-testid="context-bar"] [data-testid="session-trigger"]')!;

		async function startFromRow() {
			trigger().focus();
			trigger().click();
			flushSync();
			await settle(4);
			document.body.querySelector<HTMLElement>('[data-testid="session-reload-row"] button')!.click();
			flushSync();
			await settle(6);
		}

		await startFromRow();
		confirmButton(/^Cancel/)!.click();
		flushSync();
		await settle(14);
		expect(document.activeElement).toBe(trigger());

		await startFromRow();
		confirmButton(/^Reload/)!.click();
		flushSync();
		await settle(14);
		flushSync();
		expect(document.activeElement).toBe(trigger());
	});
});

describe('Ctrl+S across a tab switch', () => {
	it('stays bound when the newly active tab sits before the old one', async () => {
		const first = tabsStore.addTabWithData('First tab', { selectedPreset: PRESET_ID, selectedMode: MODE });
		const second = tabsStore.addTabWithData('Second tab', { selectedPreset: PRESET_ID, selectedMode: MODE });
		extraTabIds = [first, second];
		const calls: string[] = [];
		const register = keybindingsStore.registerHandler.bind(keybindingsStore);
		const unregister = keybindingsStore.unregisterHandler.bind(keybindingsStore);
		vi.spyOn(keybindingsStore, 'registerHandler').mockImplementation((id, handler) => {
			if (id === 'save_session') calls.push('register');
			register(id, handler);
		});
		vi.spyOn(keybindingsStore, 'unregisterHandler').mockImplementation((id) => {
			if (id === 'save_session') calls.push('unregister');
			unregister(id);
		});
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({
			component: SessionScopeTabsFixture as never,
			target,
			props: { tabIds: [first, second], activeTabId: second, presetId: PRESET_ID, currentMode: MODE }
		}) as unknown as { $set: (p: object) => void; $destroy: () => void };
		mounted = { target, component };
		flushSync();
		await settle(5);
		expect(calls).toEqual(['register']);
		component.$set({ activeTabId: first });
		flushSync();
		await settle(5);
		expect(calls.at(-1)).toBe('register');
		expect(calls).toEqual(['register', 'unregister', 'register']);
	});
});
