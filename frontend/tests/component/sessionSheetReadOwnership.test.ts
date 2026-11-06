// @vitest-environment jsdom
import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
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
			target.querySelector<HTMLButtonElement>('button[aria-label="Session"]')!.textContent?.trim(),
		openMenu: () => {
			target.querySelector<HTMLButtonElement>('button[aria-label="Session"]')!.click();
		},
		clickSession: (sessionId: string) => {
			const row = Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-session-row]')).find(
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

let mounted: ReturnType<typeof mountSessionControl> | undefined;
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

describe('Session sheet session-read ownership', () => {
	it('does not adopt a session whose load lands after the preset changed', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.getSessionById).mockReturnValue(pending.promise as never);

		mounted = mountSessionControl(tabId);
		await settle(10);

		mounted.openMenu();
		await settle(10);
		mounted.clickSession(PICKED);
		await settle(10);

		mounted.component.$set({ presetId: OTHER_PRESET_ID });
		await settle(10);

		pending.resolve({ success: true, data: makeSession(PICKED) });
		await settle(10);

		expect(mounted.sessionName()).not.toContain(`Session ${PICKED}`);
		expect(mounted.sessionName()).toContain('No saved session');
		const tab = generationTabs(get(tabsStore).tabs).find((entry) => entry.id === tabId);
		expect(tab?.selectedSessionId).toBeFalsy();
	});
});
