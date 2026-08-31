// @vitest-environment jsdom
//
// `routes/generate/+page.svelte` wraps `<GenerationPanel>` (which nests
// SessionCluster) in `{#key currentTab.id}` — switching the active tab
// destroys the old tab's SessionCluster instance and creates a fresh one for
// the tab being switched to. Switching back therefore mounts a *brand new*
// SessionCluster for a tab that already has a live, unsaved draft (edited
// promptSegments never written back to the session). This mounts the real
// SessionCluster against the real tabsStore and simulates that destroy +
// recreate cycle, asserting the dirty draft survives it and the component
// never re-fetches the session it's already linked to.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import type { Segment } from '$lib/types/segments';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getSessionsForPreset: vi.fn(),
		getSessionById: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { tabsStore } = await import('$lib/stores/tabs');
const { default: SessionCluster } = await import(
	'$lib/components/generation-panel/SessionCluster.svelte'
);
const { createClassComponent } = await import('svelte/legacy');
const { collectTabSessionData } = await import('$lib/utils/sessionTabState');

const PRESET_ID = 'preset-roundtrip';
const CURRENT_MODE = 'image';
const SESSION_ID = 'session-roundtrip';
const ORIGINAL_SEGMENTS = [{ id: 'a', content: 'original segment' }];
const EDITED_SEGMENTS = [{ id: 'a', content: 'EDITED segment' }];

function makeSession(promptSegments: Segment[]) {
	return {
		id: SESSION_ID,
		preset_id: PRESET_ID,
		name: 'Session 1',
		data: { [CURRENT_MODE]: { promptSegments } },
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z'
	};
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
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let tabId: string;
let mounted: ReturnType<typeof mountCluster> | undefined;

beforeEach(() => {
	const originalSession = makeSession(ORIGINAL_SEGMENTS);
	// The baseline recorded when the session was originally (cleanly) loaded —
	// built from ORIGINAL_SEGMENTS, exactly like SessionCluster itself would
	// record via recordSavedBaseline/applySessionModeData.
	const savedSessionSignature = JSON.stringify(
		collectTabSessionData(
			{ promptSegments: ORIGINAL_SEGMENTS } as never,
			CURRENT_MODE,
			originalSession.data
		)
	);

	tabId = tabsStore.addTabWithData('Round-trip tab', {
		selectedPreset: PRESET_ID,
		selectedMode: CURRENT_MODE,
		selectedSessionId: SESSION_ID,
		savedSessionSignature,
		// The user's live, never-saved edit — this is what must survive the
		// tab round-trip untouched.
		promptSegments: EDITED_SEGMENTS
	});

	vi.mocked(api.getSessionsForPreset).mockResolvedValue({
		success: true,
		data: [originalSession]
	} as never);
	vi.mocked(api.getSessionById).mockResolvedValue({
		success: true,
		data: originalSession
	} as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	tabsStore.removeTab(tabId);
	vi.clearAllMocks();
});

describe('SessionCluster survives a tab-switch destroy/recreate round trip', () => {
	it('keeps the dirty draft and never re-fetches the still-linked session', async () => {
		mounted = mountCluster(tabId);
		await settle();

		// Switch away: {#key currentTab.id} destroys this tab's instance.
		mounted.destroy();
		// Switch back: {#key currentTab.id} creates a brand new instance for
		// the same tabId, exactly as the generate page does today.
		mounted = mountCluster(tabId);
		await settle();

		expect(api.getSessionById).not.toHaveBeenCalled();

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId);
		expect(tab?.promptSegments).toEqual(EDITED_SEGMENTS);

		// The dirty indicator ("save" readout cell) must reflect the surviving
		// draft, not silently go quiet as if the session were freshly (cleanly)
		// loaded — sessionIsDirty compares the live signature against the
		// baseline recorded at load time, never a server refetch.
		const saveCell = mounted.target.querySelector('[aria-label="Save session"], [aria-label="Session save unavailable"], [aria-label="Save as a new session"], [aria-label="Session saved"]');
		expect(saveCell?.textContent).toContain('Unsaved changes');
	});
});

describe('SessionCluster survives a tab-switch round trip after loading (not saving) a session', () => {
	// The bug report's tab had an EXISTING session applied through the picker
	// (`handleSessionSelect` -> `applySessionModeData`), not a fresh
	// save-as-new (`confirmSaveSession`). The two paths write different tab
	// fields: applySessionModeData sets `sessionBaselineAwaitingFormNormalization:
	// true` (DynamicForm's schema-default merge is expected to consume this
	// once and normalize the baseline - see normalizeSessionBaselineFormData's
	// doc comment); confirmSaveSession's save-as-new sets it to `false`
	// straight away via recordSavedBaseline's default param. This drives the
	// real picker click (not a hand-seeded store) so that divergence is
	// exercised for real, then edits and round-trips exactly like the test
	// above.
	beforeEach(() => {
		tabsStore.updateTab(tabId, {
			selectedSessionId: null,
			savedSessionSignature: undefined,
			promptSegments: [],
			sessionBaselineAwaitingFormNormalization: false
		} as never);
	});

	it('keeps the post-load edit and never re-fetches after a destroy/recreate round trip', async () => {
		mounted = mountCluster(tabId);
		await settle();

		// Open the session picker and load the existing session - the real
		// handleSessionSelect -> applySessionModeData path, not a seeded store.
		mounted.target.querySelector<HTMLButtonElement>('button[aria-label="Session"]')!.click();
		await settle();
		const sessionRow = Array.from(mounted.target.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')).find(
			(el) => el.textContent?.includes('Session 1')
		);
		sessionRow!.click();
		await settle();

		expect(vi.mocked(api.getSessionById).mock.calls.map((c) => c[0])).toContain(SESSION_ID);

		let tab = get(tabsStore).tabs.find((t) => t.id === tabId);
		expect(tab?.promptSegments).toEqual(ORIGINAL_SEGMENTS);
		expect(tab?.selectedSessionId).toBe(SESSION_ID);
		// applySessionModeData's markSaved branch - this is the field that
		// diverges from a save-as-new.
		expect(tab?.sessionBaselineAwaitingFormNormalization).toBe(true);

		vi.mocked(api.getSessionById).mockClear();

		// Edit after the load, same as the maintainer's repro - dirty against
		// the just-loaded baseline, never re-saved.
		tabsStore.updateTab(tabId, { promptSegments: EDITED_SEGMENTS });

		// Switch away and back, exactly like the save-as-new test above.
		mounted.destroy();
		mounted = mountCluster(tabId);
		await settle();

		expect(api.getSessionById).not.toHaveBeenCalled();

		tab = get(tabsStore).tabs.find((t) => t.id === tabId);
		expect(tab?.promptSegments).toEqual(EDITED_SEGMENTS);

		const saveCell = mounted.target.querySelector('[aria-label="Save session"], [aria-label="Session save unavailable"], [aria-label="Save as a new session"], [aria-label="Session saved"]');
		expect(saveCell?.textContent).toContain('Unsaved changes');
	});
});
