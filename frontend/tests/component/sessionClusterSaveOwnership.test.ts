// @vitest-environment jsdom
//
// A session save is an async command whose completion writes tab-scoped state
// (currentSession, the saved baseline via tabsStore, the dirty/saving flags).
// The user can select another session — or switch tab entirely — while it is
// still in flight, and the response then lands on a controller that represents
// something else: the active session flips back to the one that was saved and
// the new selection's dirty state is cleared under it. These mount the real
// SessionCluster against the real tabsStore and hold `api.updateSession` open
// with a deferred promise so that window is exercised through the real
// reactivity graph rather than simulated.
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
const { default: SessionCluster } = await import(
	'$lib/components/generation-panel/SessionCluster.svelte'
);
const { createClassComponent } = await import('svelte/legacy');
const { collectTabSessionData } = await import('$lib/utils/sessionTabState');

const PRESET_ID = 'preset-ownership';
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

function mountCluster(tabId: string) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: SessionCluster as never,
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
		sessionName: () => target.querySelector('.session-name')?.textContent,
		saveStatus: () => target.querySelector('.session-state')?.textContent,
		clickSave: () => target.querySelector<HTMLButtonElement>('.session-save-button')!.click(),
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

let mounted: ReturnType<typeof mountCluster> | undefined;
let tabId: string;
let otherTabId: string;

beforeEach(() => {
	const sessionA = makeSession(SESSION_A, 'Session A');
	const sessionB = makeSession(SESSION_B, 'Session B');

	// A baseline recorded when session A was cleanly loaded; the tab's live
	// draft (EDITED_SEGMENTS) differs from it, so the cluster starts dirty and
	// the save button is a real save.
	const savedSessionSignature = JSON.stringify(
		collectTabSessionData(
			{ promptSegments: ORIGINAL_SEGMENTS } as never,
			CURRENT_MODE,
			sessionA.data
		)
	);

	tabId = tabsStore.addTabWithData('Ownership tab', {
		selectedPreset: PRESET_ID,
		selectedMode: CURRENT_MODE,
		selectedSessionId: SESSION_A,
		savedSessionSignature,
		promptSegments: EDITED_SEGMENTS
	});
	otherTabId = tabsStore.addTabWithData('Sessionless tab', {
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

describe('SessionCluster save ownership', () => {
	it('drops a save response for a session the user already switched away from', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountCluster(tabId);
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

		// The active session is still B, and B's baseline was not replaced by
		// the snapshot A's save was built from.
		expect(mounted.sessionName()).toBe('Session B');
		expect(tabState(tabId)?.selectedSessionId).toBe(SESSION_B);
		expect(tabState(tabId)?.savedSessionSignature).toBe(baselineForB);

		// The saved session's own list entry still gets its refreshed record.
		mounted.openSessionMenu();
		await settle();
		expect(mounted.target.textContent).toContain('Session A (saved)');
	});

	it('drops a save response after the cluster has been given another tab', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountCluster(tabId);
		await settle();

		mounted.clickSave();
		await settle();

		mounted.component.$set({ tabId: otherTabId });
		await settle();
		expect(mounted.sessionName()).toBe('None');

		pending.resolve({
			success: true,
			data: { ...makeSession(SESSION_A, 'Session A (saved)') }
		});
		await settle();

		expect(mounted.sessionName()).toBe('None');
		expect(tabState(otherTabId)?.selectedSessionId).toBeFalsy();
		expect(tabState(otherTabId)?.savedSessionSignature).toBeFalsy();
	});

	it('keeps a draft edited while the save was in flight and reports it as unsaved', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountCluster(tabId);
		await settle();

		mounted.clickSave();
		await settle();

		tabsStore.updateTab(tabId, { promptSegments: LATER_SEGMENTS });
		await settle();

		pending.resolve({ success: true, data: makeSession(SESSION_A, 'Session A') });
		await settle();

		expect(tabState(tabId)?.promptSegments).toEqual(LATER_SEGMENTS);
		// The baseline is the snapshot that was actually sent, so the newer edit
		// stays dirty instead of being silently absorbed as saved.
		expect(tabState(tabId)?.savedSessionSignature).toContain('EDITED segment');
		expect(tabState(tabId)?.savedSessionSignature).not.toContain('EDITED AGAIN');
		expect(mounted.saveStatus()).toContain('Unsaved changes');
	});

	it('lets an obsolete failure touch neither the error nor a newer save in flight', async () => {
		const failing = deferred<unknown>();
		const succeeding = deferred<unknown>();
		vi.mocked(api.updateSession)
			.mockReturnValueOnce(failing.promise as never)
			.mockReturnValueOnce(succeeding.promise as never);

		mounted = mountCluster(tabId);
		await settle();

		mounted.clickSave();
		await settle();

		mounted.openSessionMenu();
		await settle();
		mounted.clickMenuItem('Session B');
		await settle();

		mounted.clickSave();
		await settle();
		expect(mounted.saveStatus()).toContain('Saving');

		failing.reject(new Error('network down'));
		await settle();

		// B's save is still running: A's rejection must not clear its flag.
		expect(mounted.saveStatus()).toContain('Saving');

		succeeding.resolve({ success: true, data: makeSession(SESSION_B, 'Session B') });
		await settle();
		expect(mounted.saveStatus()).not.toContain('Saving');

		mounted.openSessionMenu();
		await settle();
		mounted.clickMenuItem('Rename');
		await settle();
		expect(document.body.textContent).not.toContain('network down');
	});

	it('clears the dirty state when the saved session is still the selected one', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountCluster(tabId);
		await settle();
		expect(mounted.saveStatus()).toContain('Unsaved changes');

		mounted.clickSave();
		await settle();

		pending.resolve({ success: true, data: makeSession(SESSION_A, 'Session A') });
		await settle();

		expect(mounted.saveStatus()).toContain('Saved');
		expect(tabState(tabId)?.savedSessionSignature).toContain('EDITED segment');
	});

	it('drops a save response that arrives after the cluster was destroyed', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.updateSession).mockReturnValue(pending.promise as never);

		mounted = mountCluster(tabId);
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
