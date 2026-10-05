// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';

const { authState, getPreset } = vi.hoisted(() => ({
	authState: { user: { account_type: 'USER' } as { account_type: string } | null },
	getPreset: vi.fn()
}));

vi.mock('$lib/stores/auth', () => ({
	authStore: { subscribe: (fn: (v: unknown) => void) => (fn(authState), () => {}) }
}));

vi.mock('$lib/services/api/index', async (orig) => {
	const real: any = await orig();
	return { ...real, api: { ...real.api, getPreset, getClient: () => ({}) } };
});

vi.mock('$lib/components/formulas/FormulasButton.svelte', async () => ({
	default: (await import('./stubs/FormulasButtonStub.svelte')).default
}));

const { default: PresetControls } = await import('../../src/routes/generate/components/PresetControls.svelte');
const { setModeLabels } = await import('../../src/lib/utils/modeLabel.svelte');
const { iconPaths } = await import('../../src/lib/utils/IconLibrary');

function hasGlyph(root: ParentNode, name: string) {
	const d = iconPaths[name];
	const first = Array.isArray(d) ? d[0] : d;
	return Array.from(root.querySelectorAll('svg path')).some((p) => p.getAttribute('d') === first);
}

const PRESETS = [
	{
		id: 'krea2',
		name: 'Krea-2',
		version: '1.1.0',
		engine: 'native',
		category: 'image',
		tags: [],
		description: 'A preset'
	}
];

const MODES = [
	{ id: 'txt2img', label: 'Text to Image', description: 'Generate an image from a text prompt.' },
	{ id: 'enhance', label: 'Enhance', description: 'Refine an already-upscaled image with a short tail of the sampler schedule.' },
	{ id: 'edit', label: 'Edit', sourcePlugin: 'krea2-edit', description: null },
	{ id: 'bare', label: 'Bare' }
];

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let panelWidth = 0;
const originalRO = (globalThis as any).ResizeObserver;
const widthDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth');

class FakeResizeObserver {
	cb: (entries: unknown[]) => void;
	constructor(cb: (entries: unknown[]) => void) {
		this.cb = cb;
	}
	observe(el: Element) {
		queueMicrotask(() => this.cb([{ target: el, contentRect: {}, borderBoxSize: [] }]));
	}
	unobserve() {}
	disconnect() {}
}

beforeEach(() => {
	panelWidth = 0;
	authState.user = { account_type: 'USER' };
	getPreset.mockResolvedValue({ success: true, data: { media: { gallery: [] } } });
	(globalThis as any).ResizeObserver = FakeResizeObserver;
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get() {
			return this.getAttribute('data-testid') === null && this.classList.contains('flex-col') ? panelWidth : 0;
		}
	});
});

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
	document.body.innerHTML = '';
	(globalThis as any).ResizeObserver = originalRO;
	if (widthDescriptor) Object.defineProperty(HTMLElement.prototype, 'clientWidth', widthDescriptor);
});

async function render(overrides: Record<string, unknown> = {}) {
	const handlers = {
		onPresetChange: vi.fn(),
		onModeChange: vi.fn(),
		onVariantChange: vi.fn(),
		onReload: vi.fn()
	};
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(PresetControls as never, {
		target,
		props: {
			tab: { id: 't1', selectedPreset: 'krea2', selectedMode: 'txt2img', selectedVariant: null },
			presets: PRESETS,
			availableModes: MODES,
			...handlers,
			...overrides
		}
	} as never);
	flushSync();
	await tick();
	flushSync();
	return handlers;
}

function trigger() {
	return target.querySelector('[data-testid="preset-header-mode"] button[aria-haspopup="listbox"]') as HTMLElement;
}

async function openMenu() {
	trigger().click();
	await tick();
	flushSync();
	return Array.from(document.body.querySelectorAll<HTMLElement>('[role="option"]'));
}

describe('Generate header: one-line preset and mode select', () => {
	it('shows mode icons on the closed trigger and in the menu options', async () => {
		await render({
			availableModes: [
				{ id: 'txt2img', label: 'Text to Image', icon: 'image' },
				{ id: 'img2video', label: 'Image to Video', icon: 'video' },
				{ id: 'face_swap', label: 'Face Swap', icon: 'not-an-icon' },
				{ id: 'bare', label: 'Bare' }
			]
		});
		expect(hasGlyph(trigger(), 'image')).toBe(true);
		expect(trigger().querySelector('svg.w-3\\.5.h-3\\.5.text-fg-muted')).not.toBeNull();
		expect(trigger().textContent).toContain('Text to Image');
		const [txt2img, img2video, faceSwap, bare] = await openMenu();
		expect(hasGlyph(txt2img, 'image')).toBe(true);
		expect(hasGlyph(img2video, 'video')).toBe(true);
		expect(faceSwap.querySelector('svg')).toBeNull();
		expect(faceSwap.textContent?.trim()).toBe('Face Swap');
		expect(bare.querySelector('svg')).toBeNull();
	});

	it('never shows a raw mode key when a mode arrives without a served label', async () => {
		setModeLabels({ txt2img: 'Text to Image', img2video: 'Image to Video' });
		await render({
			availableModes: [
				{ id: 'txt2img', label: '' },
				{ id: 'img2video', label: '' },
				{ id: 'face_swap', label: '' }
			]
		});
		expect(trigger().textContent).toContain('Text to Image');
		const options = await openMenu();
		expect(options.map((o) => o.textContent?.replace(/\s+/g, ' ').trim())).toEqual(['Text to Image', 'Image to Video', 'Face Swap']);
		expect(document.body.textContent).not.toMatch(/txt2img|img2video|face_swap/);
	});

	it('renders the preset picker, the mode select and the joined icon group in one row', async () => {
		await render();
		const row = target.querySelector('[data-testid="preset-header-row"]') as HTMLElement;
		expect(row.contains(target.querySelector('[data-testid="preset-header-picker"]'))).toBe(true);
		expect(row.contains(target.querySelector('[data-testid="preset-header-mode"]'))).toBe(true);
		const group = row.querySelector('[data-testid="preset-header-actions"]') as HTMLElement;
		expect(group.getAttribute('role')).toBe('group');
		expect(group.querySelector('[data-testid="formulas-stub"]')).not.toBeNull();
		expect(group.querySelectorAll('button').length).toBe(1);
		expect(row.textContent).toContain('Krea-2');
		expect(trigger().textContent).toContain('Text to Image');
	});

	it('has no info or reload button in the header row', async () => {
		await render();
		expect(target.querySelector('[aria-label="View preset description and examples"]')).toBeNull();
		expect(target.querySelector('[aria-label="Reload preset from disk"]')).toBeNull();
	});

	it('shows the preset name on the picker with no engine or version line', async () => {
		await render();
		const picker = target.querySelector('[data-testid="preset-header-picker"]') as HTMLElement;
		expect(picker.textContent?.trim()).toBe('Krea-2');
		expect(picker.textContent).not.toContain('native');
	});

	it('highlights the picker until a preset is chosen', async () => {
		await render({ tab: { id: 't1', selectedPreset: null, selectedMode: null, selectedVariant: null } });
		const trigger = target.querySelector('[data-testid="preset-picker-trigger"]') as HTMLElement;
		expect(trigger.textContent).toContain('Choose a preset');
		expect(trigger.classList.contains('preset-picker-cta')).toBe(true);
	});

	it('drops the highlight once a preset is chosen', async () => {
		await render();
		const trigger = target.querySelector('[data-testid="preset-picker-trigger"]') as HTMLElement;
		expect(trigger.classList.contains('preset-picker-cta')).toBe(false);
	});

	it('does not render the old segmented control', async () => {
		await render();
		expect(target.querySelectorAll('button[title]').length).toBe(0);
		expect(target.querySelector('[style*="grid-template-columns"]')).toBeNull();
	});

	it('lists every mode with its description and leaves description-less modes label-only', async () => {
		await render();
		const options = await openMenu();
		expect(options.map((o) => o.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'Text to Image Generate an image from a text prompt.',
			'Enhance Refine an already-upscaled image with a short tail of the sampler schedule.',
			'Edit \u2022 contributed by krea2-edit',
			'Bare'
		]);
	});

	it('clamps menu descriptions to two lines and widens the menu', async () => {
		await render();
		await openMenu();
		const desc = document.body.querySelector('[role="option"] span.text-fg-subtle.block') as HTMLElement;
		expect(desc.className).toContain('line-clamp-2');
		const menu = document.body.querySelector('[role="listbox"]') as HTMLElement;
		expect(menu.style.width).toBe('276px');
	});

	it('marks the selected option and shows the plugin dot on option and trigger', async () => {
		await render({
			tab: { id: 't1', selectedPreset: 'krea2', selectedMode: 'edit', selectedVariant: null }
		});
		expect(trigger().textContent).toContain('•');
		const options = await openMenu();
		expect(options[2].getAttribute('aria-selected')).toBe('true');
		expect(options[2].querySelector('svg')).not.toBeNull();
		expect(options[2].textContent).toContain('•');
		expect(options[0].textContent).not.toContain('•');
		expect(options[1].querySelector('svg')).toBeNull();
	});

	it('selecting a mode calls onModeChange with the mode id', async () => {
		const handlers = await render();
		const options = await openMenu();
		options[1].click();
		flushSync();
		expect(handlers.onModeChange).toHaveBeenCalledTimes(1);
		expect(handlers.onModeChange).toHaveBeenCalledWith('enhance');
		expect(document.body.querySelector('[role="listbox"]')).toBeNull();
	});

	it('keyboard: arrows move and Enter selects', async () => {
		const handlers = await render();
		trigger().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
		await tick();
		trigger().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
		await tick();
		trigger().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
		await tick();
		expect(handlers.onModeChange).toHaveBeenCalledWith('enhance');
	});

	it('hides the mode select for a single-mode preset but keeps the rest of the row', async () => {
		await render({ availableModes: [MODES[0]] });
		expect(target.querySelector('[data-testid="preset-header-mode"]')).toBeNull();
		expect(target.querySelector('[data-testid="preset-header-picker"]')).not.toBeNull();
		expect(target.querySelector('[data-testid="preset-header-actions"]')).not.toBeNull();
	});

	async function openPickerDialog(overrides: Record<string, unknown> = {}) {
		const handlers = await render(overrides);
		(target.querySelector('[data-testid="preset-header-picker"] button') as HTMLElement).click();
		await tick();
		flushSync();
		await tick();
		flushSync();
		return handlers;
	}

	function reloadButton() {
		return Array.from(document.body.querySelectorAll('button')).find((b) =>
			/Reload preset from disk/.test(b.textContent ?? '')
		);
	}

	it('picker dialog shows the reload action to admins only, on the selected preset', async () => {
		authState.user = { account_type: 'USER' };
		await openPickerDialog();
		expect(document.body.textContent).toContain('Keep selected');
		expect(reloadButton()).toBeUndefined();
	});

	it('picker dialog reload runs onReload for an admin', async () => {
		authState.user = { account_type: 'ADMIN' };
		const handlers = await openPickerDialog();
		const btn = reloadButton();
		expect(btn).toBeDefined();
		btn!.click();
		flushSync();
		expect(handlers.onReload).toHaveBeenCalledTimes(1);
	});

	it('picker dialog carries the markdown description and the examples section', async () => {
		authState.user = { account_type: 'USER' };
		await openPickerDialog({ presets: [{ ...PRESETS[0], description: 'Hello **bold** world' }] });
		expect(document.body.querySelector('strong')?.textContent).toBe('bold');
		expect(document.body.querySelector('[data-testid="preset-examples"]')).not.toBeNull();
		expect(getPreset).toHaveBeenCalledWith('krea2');
	});

	it('wide panel: trigger shows the label only', async () => {
		panelWidth = 395;
		await render();
		await tick();
		flushSync();
		const mode = target.querySelector('[data-testid="preset-header-mode"]') as HTMLElement;
		expect(mode.className).toContain('w-[148px]');
		expect(mode.className).toContain('flex-shrink-0');
		expect(trigger().textContent).not.toContain('Generate an image');
	});

	it('wide panel: the icon and the full mode name fit while the preset picker takes the rest and truncates', async () => {
		panelWidth = 395;
		await render({ availableModes: [{ ...MODES[0], icon: 'image' }, ...MODES.slice(1)] });
		await tick();
		flushSync();
		const mode = target.querySelector('[data-testid="preset-header-mode"]') as HTMLElement;
		const picker = target.querySelector('[data-testid="preset-header-picker"]') as HTMLElement;
		expect(mode.className).not.toContain('basis-full');
		expect(mode.className).toContain('w-[148px]');
		expect(picker.className).toContain('flex-1');
		expect(picker.className).toContain('min-w-0');
		expect(hasGlyph(trigger(), 'image')).toBe(true);
		expect(trigger().querySelector('span.truncate')?.textContent).toBe('Text to Image');
	});

	it('narrow panel: the mode select wraps to a full-width row with the description', async () => {
		panelWidth = 320;
		await render();
		await tick();
		flushSync();
		const mode = target.querySelector('[data-testid="preset-header-mode"]') as HTMLElement;
		expect(mode.className).toContain('basis-full');
		expect(trigger().textContent).toContain('Generate an image from a text prompt.');
	});

	it('narrow panel: the wrapped full-width trigger shows the mode icon too', async () => {
		panelWidth = 320;
		await render({ availableModes: [{ ...MODES[0], icon: 'image' }, ...MODES.slice(1)] });
		await tick();
		flushSync();
		expect((target.querySelector('[data-testid="preset-header-mode"]') as HTMLElement).className).toContain('basis-full');
		expect(hasGlyph(trigger(), 'image')).toBe(true);
	});
});
