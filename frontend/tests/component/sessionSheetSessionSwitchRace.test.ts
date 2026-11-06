// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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
		openSessionMenu: () => {
			target.querySelector<HTMLButtonElement>('button[aria-label="Session"]')!.click();
		},
		clickSession: (sessionId: string) => {
			const row = Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-session-row]')).find(
				(el) => el.textContent?.includes(`Session ${sessionId}`)
			);
			row!.click();
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

let mounted: ReturnType<typeof mountSessionControl> | undefined;
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

describe('Session sheet session-switch race', () => {
	it('never re-fetches the session the user just switched away from', async () => {
		mounted = mountSessionControl(tabId);
		await settle(10);

		mounted.openSessionMenu();
		await settle(10);
		mounted.clickSession(NEW_SESSION_ID);
		await settle(10);
		mounted.confirmDiscard();

		await Promise.resolve();
		await Promise.resolve();
		await settle(10);

		const fetchedIds = vi.mocked(api.getSessionById).mock.calls.map((call) => call[0]);
		expect(fetchedIds).not.toContain(OLD_SESSION_ID);
		expect(fetchedIds).toContain(NEW_SESSION_ID);
	});
});
