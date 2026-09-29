// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getSessionsForPreset: vi.fn(),
		getSessionById: vi.fn(),
		getSessionVersions: vi.fn(async () => ({ success: true, data: [] })),
		saveSession: vi.fn(),
		updateSession: vi.fn(),
		deleteSession: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { tabsStore } = await import('$lib/stores/tabs');
const { default: SessionPill } = await import('$lib/components/session/SessionPill.svelte');
const { createClassComponent } = await import('svelte/legacy');

const PRESET_ID = 'preset-new';
const MODE = 'image';

function makeSession(id: string) {
	return {
		id,
		preset_id: PRESET_ID,
		name: `Session ${id}`,
		data: { [MODE]: { prompt: 'saved prompt' } },
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z'
	};
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let tabId: string;
let target: HTMLDivElement;
let component: { $destroy: () => void };

function clickButton(label: RegExp | string) {
	const match = (text: string) => (typeof label === 'string' ? text.includes(label) : label.test(text));
	const found = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find(
		(el) => match(el.getAttribute('aria-label') ?? '') || match((el.textContent ?? '').trim().replace(/\s+/g, ' '))
	);
	found!.click();
}

function currentTab() {
	return get(tabsStore).tabs.find((tab) => tab.id === tabId)!;
}

beforeEach(async () => {
	tabId = tabsStore.addTabWithData('New session tab', {
		selectedPreset: PRESET_ID,
		selectedMode: MODE,
		selectedSessionId: 'a',
		prompt: 'a half written idea',
		promptSegments: [{ id: 's', content: 'a half written idea' }]
	} as never);
	vi.mocked(api.getSessionsForPreset).mockResolvedValue({
		success: true,
		data: [makeSession('a'), makeSession('b')]
	} as never);
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: SessionPill as never,
		target,
		props: { presetId: PRESET_ID, currentMode: MODE, tabId, availableModes: [] }
	});
	await settle();
});

afterEach(() => {
	component.$destroy();
	target.remove();
	tabsStore.removeTab(tabId);
	vi.clearAllMocks();
	document.body.innerHTML = '';
});

describe('SessionPill New', () => {
	it('unbinds the session and empties the draft without saving anything', async () => {
		clickButton('Session');
		await settle();
		clickButton(/^New$/);
		await settle();
		clickButton(/^Confirm/);
		await settle();

		expect(currentTab().selectedSessionId).toBeNull();
		expect(currentTab().promptSegments).toEqual([]);
		expect(currentTab().prompt).toBe('');
		expect(api.saveSession).not.toHaveBeenCalled();
		expect(api.updateSession).not.toHaveBeenCalled();
		expect(api.deleteSession).not.toHaveBeenCalled();
		expect(target.textContent).toContain('No saved session');
		expect(document.body.querySelector('[role="dialog"][aria-label="Sessions"]')).toBeNull();
	});

	it('leaves the draft alone when the discard prompt is refused', async () => {
		clickButton('Session');
		await settle();
		clickButton(/^New$/);
		await settle();
		const dialog = document.body.querySelector('[role="alertdialog"]')!;
		dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		await settle();

		expect(currentTab().selectedSessionId).toBe('a');
		expect(currentTab().prompt).toBe('a half written idea');
	});
});
