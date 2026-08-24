// @vitest-environment jsdom
//
// handleSessionSelect assigns `selectedSessionId` optimistically BEFORE
// awaiting api.getSessionById. Svelte flushes at that await point with
// local=NEW but the tab store still holding OLD, and the store-mirror
// reactive block used to read that mismatch as "the store changed" and
// re-fetch OLD over the user's own pick — two fetches race, and whichever
// resolves last wins (the user has to click a session two or three times
// before it sticks). This mounts the real SessionPill against the real
// tabsStore and drives a session switch through SessionControl's onSelect,
// so the race is exercised through the actual reactivity graph rather than
// through shouldHydrateSessionSelection in isolation.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getSessionsForPreset: vi.fn(),
		getSessionById: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { tabsStore } = await import('$lib/stores/tabs');
const { default: SessionPill } = await import('$lib/components/session/SessionPill.svelte');
const { createClassComponent } = await import('svelte/legacy');

const PRESET_ID = 'preset-race';
const CURRENT_MODE = 'image';
const OLD_SESSION_ID = 'session-old';
const NEW_SESSION_ID = 'session-new';

function makeSession(id: string) {
	return {
		id,
		preset_id: PRESET_ID,
		name: `Session ${id}`,
		data: { [CURRENT_MODE]: {} },
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z'
	};
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
		openSessionMenu: () => {
			target.querySelector<HTMLButtonElement>('button[aria-label="Session"]')!.click();
		},
		clickSession: (sessionId: string) => {
			const row = Array.from(target.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')).find(
				(el) => el.textContent?.includes(`Session ${sessionId}`)
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

let mounted: ReturnType<typeof mountPill> | undefined;
let tabId: string;

beforeEach(() => {
	tabId = tabsStore.addTabWithData('Race tab', {
		selectedPreset: PRESET_ID,
		selectedMode: CURRENT_MODE,
		selectedSessionId: OLD_SESSION_ID
	});

	vi.mocked(api.getSessionsForPreset).mockResolvedValue({
		success: true,
		data: [makeSession(OLD_SESSION_ID), makeSession(NEW_SESSION_ID)]
	} as never);
	vi.mocked(api.getSessionById).mockImplementation(async (sessionId: string) => ({
		success: true,
		data: makeSession(sessionId)
	}) as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	tabsStore.removeTab(tabId);
	vi.clearAllMocks();
});

describe('SessionPill session-switch race', () => {
	it('never re-fetches the session the user just switched away from', async () => {
		mounted = mountPill(tabId);
		await settle();

		mounted.openSessionMenu();
		await settle();
		mounted.clickSession(NEW_SESSION_ID);

		// Flush the microtask the optimistic assignment + in-flight fetch land
		// on without waiting for getSessionById(NEW) to fully resolve — this is
		// exactly the window where the unguarded mirror block used to read
		// local=NEW / store=OLD as a store-side change and fire off OLD.
		await Promise.resolve();
		await Promise.resolve();
		await settle();

		const fetchedIds = vi.mocked(api.getSessionById).mock.calls.map((call) => call[0]);
		expect(fetchedIds).not.toContain(OLD_SESSION_ID);
		expect(fetchedIds).toContain(NEW_SESSION_ID);
	});
});
