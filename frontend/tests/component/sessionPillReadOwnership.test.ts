// @vitest-environment jsdom
//
// Picking a session starts an `api.getSessionById` the user can outrun: the
// preset can change (a new preset picked in the header, or the tab's own
// preset swapped) while that read is still in flight. Without read ownership
// the late payload is applied anyway, and the pill ends up showing — and the
// tab ends up linked to — a session that belongs to a preset no longer on
// screen. This mounts the real SessionPill and drives the pick through
// SessionControl's menu, so the stale completion is exercised through the
// presenter the user actually sees.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';

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

const PRESET_ID = 'preset-read-ownership';
const OTHER_PRESET_ID = 'preset-read-ownership-other';
const CURRENT_MODE = 'image';
const PICKED = 'session-picked';

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

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((res) => {
		resolve = res;
	});
	return { promise, resolve };
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
			target.querySelector<HTMLButtonElement>('button[aria-label="Session"]')!.textContent?.trim(),
		openMenu: () => {
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
	tabId = tabsStore.addTabWithData('Read ownership tab', {
		selectedPreset: PRESET_ID,
		selectedMode: CURRENT_MODE
	});

	vi.mocked(api.getSessionsForPreset).mockImplementation((async (presetId: string) => ({
		success: true,
		data: presetId === PRESET_ID ? [makeSession(PICKED)] : []
	})) as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	tabsStore.removeTab(tabId);
	vi.clearAllMocks();
});

describe('SessionPill session-read ownership', () => {
	it('does not adopt a session whose load lands after the preset changed', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.getSessionById).mockReturnValue(pending.promise as never);

		mounted = mountPill(tabId);
		await settle();

		mounted.openMenu();
		await settle();
		mounted.clickSession(PICKED);
		await settle();

		mounted.component.$set({ presetId: OTHER_PRESET_ID });
		await settle();

		// The backend finally answers the pick, with a session belonging to the
		// preset the user has already left.
		pending.resolve({ success: true, data: makeSession(PICKED) });
		await settle();

		expect(mounted.sessionName()).not.toContain(`Session ${PICKED}`);
		expect(mounted.sessionName()).toContain('No saved session');
		const tab = get(tabsStore).tabs.find((entry) => entry.id === tabId);
		expect(tab?.selectedSessionId).toBeFalsy();
	});
});
