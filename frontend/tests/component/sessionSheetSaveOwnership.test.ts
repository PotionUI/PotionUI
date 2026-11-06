// @vitest-environment jsdom
import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { flushSync } from 'svelte';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getSessionsForPreset: vi.fn(),
		getSessionById: vi.fn(),
		getSessionVersions: vi.fn(async () => ({ success: true, data: [] })),
		updateSession: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { tabsStore } = await import('$lib/stores/tabs');
const { default: SessionScopeFixture } = await import('./fixtures/SessionScopeFixture.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { collectTabSessionData } = await import('$lib/utils/sessionTabState');

const PRESET_ID = 'preset-pill-ownership';
const CURRENT_MODE = 'image';
const SESSION_A = 'session-a';
const SESSION_B = 'session-b';
const ORIGINAL_SEGMENTS = [{ id: 'a', content: 'original segment' }];
const EDITED_SEGMENTS = [{ id: 'a', content: 'EDITED segment' }];
const LATER_SEGMENTS = [{ id: 'a', content: 'EDITED AGAIN segment' }];

function makeSession(id: string, name: string) {
	return {
		id,
		preset_id: PRESET_ID,
		name,
		data: { [CURRENT_MODE]: { promptSegments: ORIGINAL_SEGMENTS } },
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z'
	};
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

function mountSessionControl(tabId: string) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: SessionScopeFixture as never,
		target,
		props: { layout: 'sheet',
			presetId: PRESET_ID,
			currentMode: CURRENT_MODE,
			tabId,
			availableModes: []
		}
	});
	return {
		target,
		component,
		sessionName: () =>
			target.querySelector('button[aria-label="Session"] span.truncate')?.textContent,
		saveButtonLabel: () =>
			target
				.querySelector('button[aria-label="Save session"], button[aria-label="Saving session"]')
				?.getAttribute('aria-label') ?? null,
		clickSave: () => {
			target
				.querySelector<HTMLButtonElement>(
					'button[aria-label="Save session"], button[aria-label="Saving session"]'
				)!
				.click();
		},
		openSessionMenu: () =>
			target.querySelector<HTMLButtonElement>('button[aria-label="Session"]')!.click(),
		clickMenuItem: (label: string) => {
			const row = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find(
				(el) => el.textContent?.includes(label) || el.getAttribute('aria-label')?.includes(label)
			);
			row!.click();
		},
		openRename: (name: string) => {
			document.body.querySelector<HTMLButtonElement>(`button[aria-label="More actions for ${name}"]`)!.click();
			flushSync();
			Array.from(document.body.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'))
				.find((el) => el.textContent?.includes('Rename'))!
				.click();
		},
		confirmDiscard: () => {
			Array.from(document.body.querySelectorAll<HTMLButtonElement>('[role="alertdialog"] button'))
				.find((el) => el.textContent?.includes('Confirm'))
				?.click();
		},
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

function tabState(id: string) {
	return generationTabs(get(tabsStore).tabs).find((t) => t.id === id);
}

let mounted: ReturnType<typeof mountSessionControl> | undefined;
let tabId: string;
let otherTabId: string;

beforeEach(() => {
	const sessionA = makeSession(SESSION_A, 'Session A');
	const sessionB = makeSession(SESSION_B, 'Session B');

	const savedSessionSignature = JSON.stringify(
		collectTabSessionData(
			{ promptSegments: ORIGINAL_SEGMENTS } as never,
			CURRENT_MODE,
			sessionA.data
		)
	);

	tabId = tabsStore.addTabWithData('Sheet ownership tab', {
		selectedPreset: PRESET_ID,
		selectedMode: CURRENT_MODE,
		selectedSessionId: SESSION_A,
		savedSessionSignature,
		promptSegments: EDITED_SEGMENTS
	});
	otherTabId = tabsStore.addTabWithData('Sheet sessionless tab', {
		selectedPreset: PRESET_ID,
		selectedMode: CURRENT_MODE
	});

	vi.mocked(api.getSessionsForPreset).mockResolvedValue({
		success: true,
		data: [sessionA, sessionB]
	} as never);
	vi.mocked(api.getSessionById).mockImplementation(
		async (sessionId: string) =>
			({
				success: true,
				data: sessionId === SESSION_A ? sessionA : sessionB
			}) as never
	);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	tabsStore.removeTab(tabId);
	tabsStore.removeTab(otherTabId);
	vi.resetAllMocks();
});

describe('Session sheet save ownership', () => {
	it('drops a save response for a session the user already switched away from', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountSessionControl(tabId);
		await settle(10);
		expect(mounted.sessionName()).toBe('Session A');

		mounted.clickSave();
		await settle(10);

		mounted.openSessionMenu();
		await settle(10);
		mounted.clickMenuItem('Session B');
		await settle(10);
		mounted.confirmDiscard();
		await settle(10);

		expect(mounted.sessionName()).toBe('Session B');
		const baselineForB = tabState(tabId)?.savedSessionSignature;

		pending.resolve({
			success: true,
			data: { ...makeSession(SESSION_A, 'Session A (saved)') }
		});
		await settle(10);

		expect(mounted.sessionName()).toBe('Session B');
		expect(tabState(tabId)?.selectedSessionId).toBe(SESSION_B);
		expect(tabState(tabId)?.savedSessionSignature).toBe(baselineForB);

		mounted.openSessionMenu();
		await settle(10);
		expect(document.body.textContent).toContain('Session A (saved)');
	});

	it('drops a save response after the pill has been given another tab', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountSessionControl(tabId);
		await settle(10);

		mounted.clickSave();
		await settle(10);

		mounted.component.$set({ tabId: otherTabId });
		await settle(10);
		expect(mounted.sessionName()).toBe('No saved session');

		pending.resolve({
			success: true,
			data: { ...makeSession(SESSION_A, 'Session A (saved)') }
		});
		await settle(10);

		expect(mounted.sessionName()).toBe('No saved session');
		expect(tabState(otherTabId)?.selectedSessionId).toBeFalsy();
		expect(tabState(otherTabId)?.savedSessionSignature).toBeFalsy();
	});

	it('keeps a draft edited while the save was in flight and reports it as unsaved', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountSessionControl(tabId);
		await settle(10);

		mounted.clickSave();
		await settle(10);

		tabsStore.updateTab(tabId, { promptSegments: LATER_SEGMENTS });
		await settle(10);

		pending.resolve({ success: true, data: makeSession(SESSION_A, 'Session A') });
		await settle(10);

		expect(tabState(tabId)?.promptSegments).toEqual(LATER_SEGMENTS);
		expect(tabState(tabId)?.savedSessionSignature).toContain('EDITED segment');
		expect(tabState(tabId)?.savedSessionSignature).not.toContain('EDITED AGAIN');
		expect(mounted.saveButtonLabel()).toBe('Save session');
	});

	it('reports no error for a save that failed after the user switched session', async () => {
		const failing = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(failing.promise as never);

		mounted = mountSessionControl(tabId);
		await settle(10);

		mounted.clickSave();
		await settle(10);

		mounted.openSessionMenu();
		await settle(10);
		mounted.clickMenuItem('Session B');
		await settle(10);
		mounted.confirmDiscard();
		await settle(10);

		tabsStore.updateTab(tabId, { promptSegments: LATER_SEGMENTS });
		await settle(10);
		const baselineForB = tabState(tabId)?.savedSessionSignature;

		failing.reject(new Error('network down'));
		await settle(10);

		expect(mounted.sessionName()).toBe('Session B');
		expect(tabState(tabId)?.savedSessionSignature).toBe(baselineForB);
		expect(mounted.saveButtonLabel()).toBe('Save session');

		mounted.openSessionMenu();
		await settle(10);
		mounted.openRename('Session B');
		await settle(10);
		expect(document.body.textContent).not.toContain('network down');
	});

	it('clears the dirty state when the saved session is still the selected one', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountSessionControl(tabId);
		await settle(10);
		expect(mounted.saveButtonLabel()).toBe('Save session');

		mounted.clickSave();
		await settle(10);

		pending.resolve({ success: true, data: makeSession(SESSION_A, 'Session A') });
		await settle(10);

		expect(mounted.saveButtonLabel()).toBeNull();
		expect(tabState(tabId)?.savedSessionSignature).toContain('EDITED segment');
	});

	it('drops a save response that arrives after the pill was destroyed', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountSessionControl(tabId);
		await settle(10);

		const baselineBeforeSave = tabState(tabId)?.savedSessionSignature;
		mounted.clickSave();
		await settle(10);

		mounted.destroy();
		mounted = undefined;

		pending.resolve({ success: true, data: makeSession(SESSION_A, 'Session A') });
		await settle(10);

		expect(tabState(tabId)?.savedSessionSignature).toBe(baselineBeforeSave);
	});
});
