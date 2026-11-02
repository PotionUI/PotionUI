import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import type { Segment } from '$lib/types/segments';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getSessionsForPreset: vi.fn(),
		getSessionById: vi.fn(),
		getSessionVersions: vi.fn(async () => ({ success: true, data: [] }))
	}
}));

const { api } = await import('$lib/services/api/index');
const { tabsStore } = await import('$lib/stores/tabs');
const { default: SessionScopeFixture } = await import('./fixtures/SessionScopeFixture.svelte');
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

function mountScope(tabId: string) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: SessionScopeFixture as never,
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

let tabId: string;
let mounted: ReturnType<typeof mountScope> | undefined;

beforeEach(() => {
	const originalSession = makeSession(ORIGINAL_SEGMENTS);
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

describe('SessionScope survives a tab-switch destroy/recreate round trip', () => {
	it('keeps the dirty draft and never re-fetches the still-linked session', async () => {
		mounted = mountScope(tabId);
		await settle(10);

		mounted.destroy();
		mounted = mountScope(tabId);
		await settle(10);

		expect(api.getSessionById).not.toHaveBeenCalled();

		const tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === tabId);
		expect(tab?.promptSegments).toEqual(EDITED_SEGMENTS);

		expect(mounted.target.querySelector('button[aria-label="Save session"]')).not.toBeNull();
	});
});

describe('SessionScope survives a tab-switch round trip after loading (not saving) a session', () => {
	beforeEach(() => {
		tabsStore.updateTab(tabId, {
			selectedSessionId: null,
			savedSessionSignature: undefined,
			promptSegments: [],
			sessionBaselineAwaitingFormNormalization: false
		} as never);
	});

	it('keeps the post-load edit and never re-fetches after a destroy/recreate round trip', async () => {
		mounted = mountScope(tabId);
		await settle(10);

		mounted.target.querySelector<HTMLButtonElement>('button[aria-label="Session"]')!.click();
		await settle(10);
		const sessionRow = Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-session-row]')).find(
			(el) => el.textContent?.includes('Session 1')
		);
		sessionRow!.click();
		await settle(10);

		expect(vi.mocked(api.getSessionById).mock.calls.map((c) => c[0])).toContain(SESSION_ID);

		let tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === tabId);
		expect(tab?.promptSegments).toEqual(ORIGINAL_SEGMENTS);
		expect(tab?.selectedSessionId).toBe(SESSION_ID);
		expect(tab?.sessionBaselineAwaitingFormNormalization).toBe(true);

		vi.mocked(api.getSessionById).mockClear();

		tabsStore.updateTab(tabId, { promptSegments: EDITED_SEGMENTS });

		mounted.destroy();
		mounted = mountScope(tabId);
		await settle(10);

		expect(api.getSessionById).not.toHaveBeenCalled();

		tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === tabId);
		expect(tab?.promptSegments).toEqual(EDITED_SEGMENTS);

		expect(mounted.target.querySelector('button[aria-label="Save session"]')).not.toBeNull();
	});
});
