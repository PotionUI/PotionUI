// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

vi.mock('$lib/services/api', () => ({
	api: { getWorkspaces: vi.fn().mockResolvedValue({ success: true, data: [] }) }
}));

const { tabsStore } = await import('$lib/stores/tabs');
const { formDataPublicationPatch } = await import('$lib/utils/sessionTabState');
const { requestCloseTab, cancelCloseTab, confirmCloseTab } = await import('$lib/tabs/closeConfirm');
const { default: TabBar } = await import('../../src/routes/generate/components/TabBar.svelte');
const { createClassComponent } = await import('svelte/legacy');

let mounted: { destroy: () => void } | undefined;

function mountBar() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: TabBar as never, target });
	mounted = {
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

function tabIds(): string[] {
	let ids: string[] = [];
	const unsub = tabsStore.subscribe((s) => (ids = s.tabs.map((t) => t.id)));
	unsub();
	return ids;
}

function dialog(): HTMLElement | null {
	return document.body.querySelector('[role="alertdialog"]');
}

async function settle() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function press(key: string) {
	window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
}

function clickButton(label: RegExp) {
	const button = [...dialog()!.querySelectorAll('button')].find((b) => label.test(b.textContent ?? ''));
	button!.click();
}

beforeEach(() => {
	tabsStore.reset();
	tabsStore.addTab();
	tabsStore.addTab();
});

afterEach(async () => {
	cancelCloseTab();
	await settle();
	mounted?.destroy();
	mounted = undefined;
	tabsStore.reset();
});

describe('guarded tab close', () => {
	it('asks first when the tab close button is pressed and closes only on confirm', async () => {
		mountBar();
		const before = tabIds();
		const closeButtons = document.body.querySelectorAll<HTMLElement>('.close-button');
		expect(closeButtons.length).toBeGreaterThan(0);
		closeButtons[0].click();
		await settle();
		expect(dialog()).not.toBeNull();
		expect(dialog()!.textContent).toContain('Close tab?');
		expect(tabIds()).toEqual(before);
		press('Enter');
		await settle();
		expect(tabIds()).toHaveLength(before.length - 1);
		expect(dialog()).toBeNull();
	});

	it('keeps the tab when cancelled with Esc or the Cancel button', async () => {
		mountBar();
		const before = tabIds();
		requestCloseTab(before[1]);
		await settle();
		press('Escape');
		await settle();
		expect(tabIds()).toEqual(before);
		expect(dialog()).toBeNull();
		requestCloseTab(before[1]);
		await settle();
		clickButton(/Cancel/);
		await settle();
		expect(tabIds()).toEqual(before);
	});

	it('confirms with the Confirm button', async () => {
		mountBar();
		const before = tabIds();
		requestCloseTab(before[1]);
		await settle();
		clickButton(/Confirm/);
		await settle();
		expect(tabIds()).toEqual([before[0], before[2]]);
	});

	it('drops a pending request whose tab was removed before confirming', async () => {
		mountBar();
		const before = tabIds();
		requestCloseTab(before[1]);
		await settle();
		tabsStore.removeTab(before[1]);
		await settle();
		expect(dialog()).toBeNull();
		confirmCloseTab();
		expect(tabIds()).toEqual([before[0], before[2]]);
	});

	it('drops a pending request when the bar is destroyed', async () => {
		mountBar();
		const before = tabIds();
		requestCloseTab(before[1]);
		await settle();
		mounted!.destroy();
		mounted = undefined;
		confirmCloseTab();
		expect(tabIds()).toEqual(before);
	});

	it('warns about a running generation and unsaved changes', async () => {
		mountBar();
		const before = tabIds();
		tabsStore.updateTab(before[1], {
			prompt: 'a fox',
			generation: { ...tabsStoreGeneration(before[1]), isGenerating: true } as never
		});
		requestCloseTab(before[1]);
		await settle();
		const text = dialog()!.textContent ?? '';
		expect(text).toContain('generation running or queued');
		expect(text).toContain('unsaved changes');
	});

	it('warns for a queued-only generation', async () => {
		mountBar();
		const before = tabIds();
		tabsStore.updateTab(before[1], {
			generation: {
				...tabsStoreGeneration(before[1]),
				queue: [{ generation_id: 'g1', queue_position: 1, status: 'pending' }]
			} as never
		});
		requestCloseTab(before[1]);
		await settle();
		expect(dialog()!.textContent ?? '').toContain('generation running or queued');
	});

	it('does not warn for a preset tab with published default form data', async () => {
		mountBar();
		const before = tabIds();
		publishForm(before[2], { steps: 30 });
		requestCloseTab(before[2]);
		await settle();
		expect(dialog()!.textContent ?? '').not.toContain('unsaved');
	});

	it('warns when a setting was changed after the form published its defaults', async () => {
		mountBar();
		const before = tabIds();
		publishForm(before[2], { steps: 30 });
		publishForm(before[2], { steps: 45 });
		requestCloseTab(before[2]);
		await settle();
		expect(dialog()!.textContent ?? '').toContain('unsaved changes');
	});

	it('warns for a session tab with a dirty baseline but not a genuinely clean one', async () => {
		mountBar();
		const before = tabIds();
		tabsStore.updateTab(before[1], { selectedSessionId: 's1', savedSessionSignature: null });
		tabsStore.updateTab(before[2], {
			selectedSessionId: 's2',
			selectedMode: 'txt2img',
			prompt: 'saved',
			savedSessionSignature: JSON.stringify({ txt2img: { prompt: 'saved' } })
		});
		requestCloseTab(before[1]);
		await settle();
		expect(dialog()!.textContent ?? '').toContain('unsaved changes');
		cancelCloseTab();
		await settle();
		requestCloseTab(before[2]);
		await settle();
		expect(dialog()!.textContent ?? '').not.toContain('unsaved');
	});

	it('warns for a session tab edited after its last save', async () => {
		mountBar();
		const before = tabIds();
		tabsStore.updateTab(before[1], {
			selectedSessionId: 's1',
			selectedMode: 'txt2img',
			prompt: 'edited',
			savedSessionSignature: JSON.stringify({ txt2img: { prompt: 'saved' } })
		});
		requestCloseTab(before[1]);
		await settle();
		expect(dialog()!.textContent ?? '').toContain('unsaved changes');
	});

	it('shows no warnings for a clean idle tab', async () => {
		mountBar();
		const before = tabIds();
		requestCloseTab(before[2]);
		await settle();
		const text = dialog()!.textContent ?? '';
		expect(text).not.toContain('running');
		expect(text).not.toContain('unsaved');
	});

	it('does not prompt for a programmatic removal', async () => {
		mountBar();
		const before = tabIds();
		tabsStore.removeTab(before[1]);
		await settle();
		expect(dialog()).toBeNull();
		expect(tabIds()).toEqual([before[0], before[2]]);
	});

	it('never prompts to close the only tab', async () => {
		mountBar();
		const before = tabIds();
		tabsStore.removeTab(before[1]);
		tabsStore.removeTab(before[2]);
		requestCloseTab(before[0]);
		await settle();
		expect(dialog()).toBeNull();
	});
});

function publishForm(id: string, formData: Record<string, unknown>) {
	let tab: never = undefined as never;
	const unsub = tabsStore.subscribe((s) => (tab = s.tabs.find((t) => t.id === id) as never));
	unsub();
	tabsStore.updateTab(id, { selectedPreset: 'some-preset', ...formDataPublicationPatch(tab, formData) });
}

function tabsStoreGeneration(id: string) {
	let generation: Record<string, unknown> = {};
	const unsub = tabsStore.subscribe((s) => (generation = { ...s.tabs.find((t) => t.id === id)!.generation }));
	unsub();
	return generation;
}
