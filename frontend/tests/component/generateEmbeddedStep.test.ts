import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

vi.mock('$lib/services/websocket', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	return {
		...real,
		createGenerationSocket: () => ({
			onConnectionChange() {},
			connect() {},
			disconnect() {},
			on() {},
			off() {},
			send() {},
			subscribe() {}
		})
	};
});

vi.mock('$lib/tool-tabs/ToolTabHost.svelte', async () => ({
	default: (await import('./stubs/BlankStub.svelte')).default
}));

const MARKER = 'EMBEDDEDSTEPFORMMARKER';
const SCHEMA = { properties: { marker: { type: 'string', name: 'marker', title: MARKER } } };

vi.mock('$lib/services/api', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	const api = new Proxy(
		{},
		{
			get: (_target, key: string) => async () => {
				if (key === 'getPresetFormSchema') return { success: true, data: { form_schema: SCHEMA } };
				if (key === 'getPresetModes') {
					return { success: true, data: { modes: [{ name: 'image', label: 'Image', variants: [] }], default_mode: 'image' } };
				}
				if (key === 'getPreset') return { success: true, data: { vars: {}, styles: [] } };
				if (key === 'listPresets') {
					return { success: true, data: [{ id: 'p1', name: 'Preset One', version: '1', engine: 'native', modes: ['image'] }] };
				}
				if (key === 'getReadiness') return { checks: [], ready: true, status: 'ready' };
				return { success: false, error: 'unused', data: null };
			}
		}
	);
	return { ...real, api };
});

Object.defineProperty(window, 'innerWidth', { value: 1280, configurable: true });

const { mount, unmount, flushSync } = await import('svelte');
const { get } = await import('svelte/store');
const { tabsStore } = await import('$lib/stores/tabs');
const { publishStepBar } = await import('$lib/elixirs/stepBar');
const { registerBuiltinFieldComponents } = await import('$lib/fields/builtin');
const { default: Page } = await import('../../src/routes/generate/+page.svelte');

registerBuiltinFieldComponents();

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(check: () => boolean, label: string) {
	for (let i = 0; i < 150; i++) {
		if (check()) return;
		await wait(10);
	}
	throw new Error(`timed out waiting for ${label}`);
}

function barModel(over: Record<string, unknown> = {}) {
	return {
		elixirName: 'Potion study',
		stepLabel: 'Orbit left',
		follow: false,
		sessionName: null,
		steps: [{ id: 'n1', label: 'Orbit left', detail: 'Preset One · Image', follow: false }],
		index: 0,
		edits: 0,
		saving: false,
		opening: false,
		confirm: null,
		conflict: null,
		back: vi.fn(),
		go: vi.fn(),
		save: vi.fn(),
		answerConfirm: vi.fn(),
		resolveConflict: vi.fn(),
		...over
	};
}

let teardown: (() => void) | undefined;
let owner = '';

function mountPage() {
	const target = document.body.appendChild(document.createElement('div'));
	const app = mount(Page as never, { target, props: {} });
	teardown = () => {
		unmount(app);
		target.remove();
	};
	return target;
}

beforeEach(() => {
	tabsStore.reset();
	owner = tabsStore.openToolTab('elixir', null, { key: 'e1', state: { elixirId: 'e1' }, name: 'Potion study' });
	tabsStore.openEmbeddedTab(owner, { nodeId: 'n1', persistence: 'local', selectedPreset: 'p1', selectedMode: 'image', name: 'Orbit left' });
});

afterEach(() => {
	publishStepBar(owner, null);
	teardown?.();
	teardown = undefined;
});

describe('Generate page while an Elixir step is open in preset mode', () => {
	it('shows the step form under the edit bar and hides the lane without unmounting it', async () => {
		publishStepBar(owner, barModel());
		const target = mountPage();
		await until(() => target.textContent?.includes(MARKER) ?? false, 'the step form');

		expect(target.querySelector('[data-testid="step-edit-bar"]')).not.toBeNull();
		const lane = target.querySelector<HTMLElement>('[data-testid="tool-tab-area"]');
		expect(lane).not.toBeNull();
		expect(lane!.className).toContain('invisible');
		expect(lane!.getAttribute('aria-hidden')).toBe('true');
	});

	it('has one header: the context bar with the step controls on the left and the tools kept on the right', async () => {
		publishStepBar(owner, barModel());
		const target = mountPage();
		await until(() => target.textContent?.includes(MARKER) ?? false, 'the step form');
		expect(target.querySelectorAll('[data-testid="context-bar"]')).toHaveLength(1);
		const bar = target.querySelector('[data-testid="context-bar"]')!;
		expect(bar.querySelector('[data-testid="context-leading"] [data-testid="step-edit-bar"]')).not.toBeNull();
		expect(bar.querySelector('[data-testid="preset-picker-trigger"]')).toBeNull();
		expect(bar.querySelector('[data-testid="context-tools"]')).not.toBeNull();
		expect(bar.querySelector('[data-testid="context-trailing"] [data-testid="step-edit-actions"]')).not.toBeNull();
		const kids = [...bar.children].map((child) => child.getAttribute('data-testid') ?? '');
		expect(kids.indexOf('context-tools')).toBeLessThan(kids.indexOf('context-trailing'));
	});

	it('has no Save or Discard for a followed step', async () => {
		publishStepBar(owner, barModel({ follow: true, sessionName: 'Bottle orbit' }));
		tabsStore.openEmbeddedTab(owner, { nodeId: 'n1', persistence: 'session', selectedPreset: 'p1', selectedMode: 'image', name: 'Orbit left' });
		const target = mountPage();
		await until(() => target.textContent?.includes(MARKER) ?? false, 'the step form');
		const bar = target.querySelector('[data-testid="context-bar"]')!;
		expect(bar.textContent).toContain('Saved automatically');
		expect(bar.querySelector('[data-testid="step-edit-actions"]')).toBeNull();
		expect(bar.querySelector('[data-testid="context-tools"]')).not.toBeNull();
	});

	it('shows the lane and not the form until the Elixir tab has taken the step over', async () => {
		const target = mountPage();
		await wait(150);
		expect(target.querySelector('[data-testid="step-edit-bar"]')).toBeNull();
		expect(target.textContent).not.toContain(MARKER);
		const lane = target.querySelector<HTMLElement>('[data-testid="tool-tab-area"]');
		expect(lane!.className).not.toContain('invisible');
	});

	it('goes back to the lane when the bar is withdrawn', async () => {
		publishStepBar(owner, barModel());
		const target = mountPage();
		await until(() => target.textContent?.includes(MARKER) ?? false, 'the step form');
		publishStepBar(owner, null);
		flushSync();
		await until(() => !target.textContent?.includes(MARKER), 'the form to go');
		expect(target.querySelector('[data-testid="step-edit-bar"]')).toBeNull();
		expect(target.querySelector<HTMLElement>('[data-testid="tool-tab-area"]')!.className).not.toContain('invisible');
	});

	it('keeps the embedded tab out of the tab strip', async () => {
		publishStepBar(owner, barModel());
		const target = mountPage();
		await until(() => target.textContent?.includes(MARKER) ?? false, 'the step form');
		expect(get(tabsStore).tabs).toHaveLength(3);
		expect(target.textContent).not.toContain('Orbit left Generation');
	});
});
