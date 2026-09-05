// @vitest-environment jsdom
//
// The SessionCluster counterpart of this file explains the race; SessionPill
// carries the same save/save-as/restore/delete logic for the tabs-row mount
// and needs the same ownership rule, so the same window is driven here through
// SessionControl's compact pill (dot + name + Save) rather than the console
// bar's two-line cells.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getSessionsForPreset: vi.fn(),
		getSessionById: vi.fn(),
		updateSession: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { tabsStore } = await import('$lib/stores/tabs');
const { default: SessionPill } = await import('$lib/components/session/SessionPill.svelte');
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

function mountPill(tabId: string) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: SessionPill as never,
		target,
		props: {
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
		// The compact pill has no status line: the save control's own label is
		// the only place the in-flight flag surfaces, and it is rendered at all
		// only while the session is dirty.
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
			const row = Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((el) =>
				el.textContent?.includes(label)
			);
			row!.click();
		},
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function tabState(id: string) {
	return get(tabsStore).tabs.find((t) => t.id === id);
}

let mounted: ReturnType<typeof mountPill> | undefined;
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

	tabId = tabsStore.addTabWithData('Pill ownership tab', {
		selectedPreset: PRESET_ID,
		selectedMode: CURRENT_MODE,
		selectedSessionId: SESSION_A,
		savedSessionSignature,
		promptSegments: EDITED_SEGMENTS
	});
	otherTabId = tabsStore.addTabWithData('Pill sessionless tab', {
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
	// Return-value queues survive clearAllMocks and would leak into the next test.
	vi.resetAllMocks();
});

describe('SessionPill save ownership', () => {
	it('drops a save response for a session the user already switched away from', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountPill(tabId);
		await settle();
		expect(mounted.sessionName()).toBe('Session A');

		mounted.clickSave();
		await settle();

		mounted.openSessionMenu();
		await settle();
		mounted.clickMenuItem('Session B');
		await settle();

		expect(mounted.sessionName()).toBe('Session B');
		const baselineForB = tabState(tabId)?.savedSessionSignature;

		pending.resolve({
			success: true,
			data: { ...makeSession(SESSION_A, 'Session A (saved)') }
		});
		await settle();

		expect(mounted.sessionName()).toBe('Session B');
		expect(tabState(tabId)?.selectedSessionId).toBe(SESSION_B);
		expect(tabState(tabId)?.savedSessionSignature).toBe(baselineForB);

		mounted.openSessionMenu();
		await settle();
		expect(mounted.target.textContent).toContain('Session A (saved)');
	});

	it('drops a save response after the pill has been given another tab', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountPill(tabId);
		await settle();

		mounted.clickSave();
		await settle();

		mounted.component.$set({ tabId: otherTabId });
		await settle();
		expect(mounted.sessionName()).toBe('No saved session');

		pending.resolve({
			success: true,
			data: { ...makeSession(SESSION_A, 'Session A (saved)') }
		});
		await settle();

		expect(mounted.sessionName()).toBe('No saved session');
		expect(tabState(otherTabId)?.selectedSessionId).toBeFalsy();
		expect(tabState(otherTabId)?.savedSessionSignature).toBeFalsy();
	});

	it('keeps a draft edited while the save was in flight and reports it as unsaved', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountPill(tabId);
		await settle();

		mounted.clickSave();
		await settle();

		tabsStore.updateTab(tabId, { promptSegments: LATER_SEGMENTS });
		await settle();

		pending.resolve({ success: true, data: makeSession(SESSION_A, 'Session A') });
		await settle();

		expect(tabState(tabId)?.promptSegments).toEqual(LATER_SEGMENTS);
		expect(tabState(tabId)?.savedSessionSignature).toContain('EDITED segment');
		expect(tabState(tabId)?.savedSessionSignature).not.toContain('EDITED AGAIN');
		// Still dirty, so the save control is still offered.
		expect(mounted.saveButtonLabel()).toBe('Save session');
	});

	it('reports no error for a save that failed after the user switched session', async () => {
		// Unlike the console bar's cluster, the pill's Save control is disabled
		// while a save is running, so a second quick save cannot overlap the
		// first here — what a stale failure can still do is surface someone
		// else's error against the session now selected.
		const failing = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(failing.promise as never);

		mounted = mountPill(tabId);
		await settle();

		mounted.clickSave();
		await settle();

		mounted.openSessionMenu();
		await settle();
		mounted.clickMenuItem('Session B');
		await settle();

		tabsStore.updateTab(tabId, { promptSegments: LATER_SEGMENTS });
		await settle();
		const baselineForB = tabState(tabId)?.savedSessionSignature;

		failing.reject(new Error('network down'));
		await settle();

		expect(mounted.sessionName()).toBe('Session B');
		expect(tabState(tabId)?.savedSessionSignature).toBe(baselineForB);
		// The saving flag is released rather than left stuck on the pill.
		expect(mounted.saveButtonLabel()).toBe('Save session');

		mounted.openSessionMenu();
		await settle();
		mounted.clickMenuItem('Rename');
		await settle();
		expect(document.body.textContent).not.toContain('network down');
	});

	it('clears the dirty state when the saved session is still the selected one', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountPill(tabId);
		await settle();
		expect(mounted.saveButtonLabel()).toBe('Save session');

		mounted.clickSave();
		await settle();

		pending.resolve({ success: true, data: makeSession(SESSION_A, 'Session A') });
		await settle();

		expect(mounted.saveButtonLabel()).toBeNull();
		expect(tabState(tabId)?.savedSessionSignature).toContain('EDITED segment');
	});

	it('drops a save response that arrives after the pill was destroyed', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountPill(tabId);
		await settle();

		const baselineBeforeSave = tabState(tabId)?.savedSessionSignature;
		mounted.clickSave();
		await settle();

		mounted.destroy();
		mounted = undefined;

		pending.resolve({ success: true, data: makeSession(SESSION_A, 'Session A') });
		await settle();

		expect(tabState(tabId)?.savedSessionSignature).toBe(baselineBeforeSave);
	});
});
