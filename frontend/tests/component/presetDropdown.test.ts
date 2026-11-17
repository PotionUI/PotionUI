import { describe, it, expect, afterEach, beforeEach, vi, type Mock } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { get } from 'svelte/store';
import type { PresetInfo } from '$lib/types/api';
import { settle } from './helpers/settle';
import { getIconPath } from '$lib/utils/IconLibrary';

const { authState, calls } = vi.hoisted(() => ({
	authState: { user: { account_type: 'USER' } as { account_type: string } | null },
	calls: {
		getPresetPickerState: vi.fn(),
		pinPreset: vi.fn(),
		unpinPreset: vi.fn(),
		pushRecentPreset: vi.fn(),
		getPreset: vi.fn(),
		getClient: () => ({ get: vi.fn() })
	}
}));

vi.mock('$lib/stores/auth', () => ({
	authStore: { subscribe: (fn: (v: unknown) => void) => (fn(authState), () => {}) }
}));

vi.mock('$lib/services/api/index', () => ({ api: calls }));

vi.mock('$lib/utils/presetHardware', async (orig) => ({
	...(await orig<typeof import('$lib/utils/presetHardware')>()),
	fetchDetectedVramGb: vi.fn().mockResolvedValue(16)
}));

vi.mock('$lib/stores/recipeCatalog', async (orig) => ({
	...(await orig<typeof import('$lib/stores/recipeCatalog')>()),
	loadRecipeCatalog: vi.fn()
}));

const { default: PresetPicker } = await import('$lib/components/preset/PresetPicker.svelte');
const { presetPickerState } = await import('$lib/stores/presetPickerState');
const { viewportWidth } = await import('$lib/stores/viewport');

const CATALOG = [
	{ id: 'sdxl', name: 'SDXL', engine: 'native', version: '1.4.0', category: 'image', tags: ['photo'], engine_available: true },
	{ id: 'flux1', name: 'FLUX.1 Dev', engine: 'comfyui', version: '3.2.0', category: 'image', tags: ['flux'], engine_available: false, requires: { min_vram_gb: 24 } },
	{ id: 'krea', name: 'Krea 2', engine: 'native', version: '2.1.0', category: 'image', tags: [], engine_available: true },
	{
		id: 'pony',
		name: 'Pony Diffusion',
		engine: 'native',
		version: '1.0.0',
		category: 'image',
		tags: [],
		engine_available: true,
		requirements_summary: { ok: 2, missing: 1, unknown: 0, optional_missing: 0 }
	},
	{ id: 'wan', name: 'Wan 2.2', engine: 'native', version: '2.2.4', category: 'video', tags: ['i2v'], engine_available: true, requires: { min_vram_gb: 24 } },
	{ id: 'seedvr2', name: 'SeedVR2 Upscale', engine: 'native', version: '1.4.0', category: 'utility', tags: ['upscale'], engine_available: true }
] as unknown as PresetInfo[];

let instance: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement | undefined;
let events: { select: Mock<(presetId: string) => void>; reload: Mock<() => void> };

const state = (pinned: string[], recent: string[]) => ({ success: true, data: { pinned, recent } });

beforeEach(() => {
	viewportWidth.set(1440);
	authState.user = { account_type: 'USER' };
	presetPickerState.reset();
	calls.getPresetPickerState.mockResolvedValue(state(['seedvr2'], ['krea', 'wan', 'sdxl']));
	calls.pushRecentPreset.mockImplementation(async (id: string) => state(['seedvr2'], [id, 'krea']));
	calls.pinPreset.mockImplementation(async (id: string) => state(['seedvr2', id], ['krea', 'wan']));
	calls.getPreset.mockResolvedValue({ success: true, data: { media: { gallery: [] } } });
});

afterEach(async () => {
	if (instance) unmount(instance);
	instance = undefined;
	target?.remove();
	target = undefined;
	await settle(2);
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

async function open(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	events = { select: vi.fn<(presetId: string) => void>(), reload: vi.fn<() => void>() };
	instance = mount(PresetPicker as never, {
		target,
		props: { presets: CATALOG, selectedPreset: 'sdxl', ...props },
		events: { select: (e: CustomEvent<string>) => events.select(e.detail), reload: () => events.reload() }
	} as never);
	flushSync();
	trigger().click();
	flushSync();
	await settle(6);
	flushSync();
}

function trigger() {
	return document.querySelector('[data-testid="preset-picker-trigger"]') as HTMLButtonElement;
}

function dialog() {
	return document.body.querySelector('[role="dialog"][aria-label="Choose preset"]') as HTMLElement | null;
}

function search() {
	return dialog()!.querySelector('input[aria-label="Search presets"]') as HTMLInputElement;
}

function groups() {
	return Array.from(dialog()!.querySelectorAll<HTMLElement>('[data-preset-group]')).map((g) => g.querySelector('[data-preset-group-label]')?.textContent?.trim());
}

function rowsIn(group: string) {
	return Array.from(dialog()!.querySelectorAll<HTMLElement>(`ul[aria-label="${group}"] [role="option"]`)).map((row) => row.dataset.presetRow);
}

function row(id: string, group = 'Image') {
	return dialog()!.querySelector(`ul[aria-label="${group}"] [data-preset-row="${id}"]`) as HTMLElement;
}

async function type(value: string) {
	const input = search();
	input.value = value;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	await tick();
}

function key(name: string, el: Element = search()) {
	el.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));
	flushSync();
}

async function rowMenu(id: string, group = 'Image') {
	(row(id, group).querySelector('button[aria-label^="More actions for"]') as HTMLButtonElement).click();
	flushSync();
	await tick();
	return Array.from(document.body.querySelectorAll<HTMLButtonElement>(`[role="menu"] [role="menuitem"]`));
}

describe('PresetDropdown', () => {
	it('opens a 480px "Choose preset" dialog with the searchable listbox, Recent, Pinned and the families', async () => {
		await open();
		expect(dialog()).not.toBeNull();
		expect(dialog()!.className).not.toContain('is-fullscreen');
		expect(dialog()!.getAttribute('style')).toContain('width: 480px');
		expect(dialog()!.querySelector('[role="listbox"][aria-label="Presets"]')).not.toBeNull();
		expect(search().getAttribute('role')).toBe('combobox');
		expect(groups()).toEqual(['Recent', 'Pinned', 'Image', 'Video', 'Tools']);
		expect(rowsIn('Recent')).toEqual(['krea', 'wan']);
		expect(rowsIn('Pinned')).toEqual(['seedvr2']);
		expect(dialog()!.textContent).not.toContain('Utility');
		expect(trigger().getAttribute('aria-expanded')).toBe('true');
		expect(row('sdxl').getAttribute('aria-selected')).toBe('true');
	});

	it('marks each section with its icon: clock for Recent, pin for Pinned and the family icon for each family', async () => {
		const extra = [
			{ id: 'stable-audio', name: 'Stable Audio', engine: 'native', version: '1.0.0', category: 'audio', tags: [], engine_available: true },
			{ id: 'trellis', name: 'Trellis 2', engine: 'native', version: '2.0.0', category: '3d', tags: [], engine_available: true }
		] as unknown as PresetInfo[];
		await open({ presets: [...CATALOG, ...extra] });
		const expected: Record<string, string> = { Recent: 'clock', Pinned: 'pin', Image: 'photo', Video: 'film', Audio: 'audio', '3D': 'cube', Tools: 'wand' };
		const headers = Array.from(dialog()!.querySelectorAll<HTMLElement>('[data-preset-group]'));
		expect(headers.map((g) => g.querySelector('[data-preset-group-label]')!.textContent)).toEqual(Object.keys(expected));
		for (const header of headers) {
			const label = header.querySelector('[data-preset-group-label]')!.textContent!;
			const path = getIconPath(expected[label])!;
			const svg = header.firstElementChild as SVGElement;
			expect(svg.tagName.toLowerCase(), label).toBe('svg');
			expect(svg.getAttribute('class'), label).toContain('h-3.5 w-3.5');
			expect(svg.querySelector('path')!.getAttribute('d'), label).toBe(Array.isArray(path) ? path[0] : path);
		}
	});

	it('names each option by its preset alone and finds it exactly once among the families', async () => {
		await open();
		expect(dialog()!.querySelectorAll('[data-preset-row="krea"]')).toHaveLength(2);
		const family = dialog()!.querySelectorAll<HTMLElement>('[data-preset-family] [data-preset-row="krea"]');
		expect(family).toHaveLength(1);
		expect(family[0].closest('[data-preset-family]')!.getAttribute('data-preset-family')).toBe('image');
		expect(dialog()!.querySelector('ul[aria-label="Recent"]')!.hasAttribute('data-preset-family')).toBe(false);
		expect(document.getElementById(family[0].getAttribute('aria-labelledby')!)!.textContent!.trim()).toBe('Krea 2');
		expect(document.getElementById(family[0].getAttribute('aria-describedby')!)!.textContent).toContain('native · v2.1.0');
	});

	it('turns the trigger chevron while the dropdown is open', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(PresetPicker as never, { target, props: { presets: CATALOG, selectedPreset: 'sdxl' } } as never);
		flushSync();
		const chevron = () => trigger().lastElementChild!.getAttribute('class')!;
		expect(chevron()).toContain('transition-transform');
		expect(chevron()).not.toContain('rotate-180');
		trigger().click();
		flushSync();
		await settle(4);
		expect(chevron()).toContain('rotate-180');
		key('Escape');
		await settle(2);
		expect(dialog()).toBeNull();
		expect(chevron()).not.toContain('rotate-180');
	});

	it('shows a 40px cover, the name and one secondary line per row', async () => {
		await open();
		const sdxl = row('sdxl');
		expect(sdxl.querySelector('button')!.className).toContain('h-[52px]');
		expect(sdxl.querySelector('.w-10.h-10')).not.toBeNull();
		expect(sdxl.textContent).toContain('native · v1.4.0');
	});

	it('chooses a preset with one click on its name, records it as recent and closes', async () => {
		await open();
		const name = Array.from(row('krea').querySelectorAll('span')).find((el) => el.textContent === 'Krea 2') as HTMLElement;
		name.click();
		flushSync();
		await settle(4);
		expect(events.select).toHaveBeenCalledWith('krea');
		expect(calls.pushRecentPreset).toHaveBeenCalledWith('krea');
		expect(dialog()).toBeNull();
		expect(get(presetPickerState).recent[0]).toBe('krea');
	});

	it('searches by family, counts the matches and drops Recent and Pinned', async () => {
		await open();
		await type('tools');
		expect(groups()).toEqual(['Tools']);
		expect(dialog()!.textContent).toContain('1 of 6');
		await type('zzz');
		expect(dialog()!.textContent).toContain('No presets match');
		(Array.from(dialog()!.querySelectorAll('button')).find((b) => b.textContent?.includes('Clear search')) as HTMLButtonElement).click();
		flushSync();
		expect(search().value).toBe('');
	});

	it('moves the active row with the arrow keys and chooses it with Enter', async () => {
		await open();
		await type('i');
		expect(search().getAttribute('aria-activedescendant')).toMatch(/-opt-0$/);
		key('ArrowDown');
		const activeId = search().getAttribute('aria-activedescendant')!;
		expect(activeId).toMatch(/-opt-1$/);
		const activeRow = document.getElementById(activeId)!;
		expect(activeRow.classList.contains('is-active')).toBe(true);
		key('Enter');
		await settle(4);
		expect(events.select).toHaveBeenCalledWith(activeRow.dataset.presetRow);
		expect(dialog()).toBeNull();
	});

	it('clears the search on the first Escape and closes on the second', async () => {
		await open();
		await type('wan');
		key('Escape');
		expect(dialog()).not.toBeNull();
		expect(search().value).toBe('');
		key('Escape');
		await settle(2);
		expect(dialog()).toBeNull();
	});

	it('keeps presets that cannot run selectable, at the end of their family, with the reason', async () => {
		await open();
		expect(rowsIn('Image')).toEqual(['sdxl', 'krea', 'flux1', 'pony']);
		expect(row('flux1').querySelector('[data-preset-issue="backend"]')!.textContent).toContain('No ComfyUI backend');
		expect(row('pony').querySelector('[data-preset-issue="missing"]')!.textContent).toContain('1 missing');
		expect(row('wan', 'Video').querySelector('[data-preset-issue="vram"]')!.textContent).toContain('needs 24 GB · this GPU 16 GB');
		(row('flux1').querySelector('button') as HTMLButtonElement).click();
		await settle(4);
		expect(events.select).toHaveBeenCalledWith('flux1');
	});

	it('shows the reason in Details, with the fix for admins and a note for everyone else', async () => {
		authState.user = { account_type: 'ADMIN' };
		await open();
		(await rowMenu('flux1')).find((item) => item.textContent?.includes('Details'))!.click();
		await settle(4);
		const issue = dialog()!.querySelector('[data-testid="preset-issue"]') as HTMLElement;
		expect(issue.textContent).toContain('No ComfyUI backend');
		expect(issue.querySelector('a')!.getAttribute('href')).toBe('/admin?tab=backends');
		const vramChip = Array.from(dialog()!.querySelectorAll('[data-testid="preset-details"] span')).find(
			(el) => el.textContent === 'needs 24 GB · this GPU 16 GB'
		) as HTMLElement;
		expect(vramChip.className).toContain('text-warning');
		key('Escape', dialog()!.querySelector('[data-testid="preset-details"]')!);
		await settle(2);
		expect(dialog()!.querySelector('[data-testid="preset-details"]')).toBeNull();
		unmount(instance!);
		instance = undefined;
		authState.user = { account_type: 'USER' };
		await open();
		(await rowMenu('pony')).find((item) => item.textContent?.includes('Details'))!.click();
		await settle(4);
		const userIssue = dialog()!.querySelector('[data-testid="preset-issue"]') as HTMLElement;
		expect(userIssue.textContent).toContain('Ask your administrator');
		expect(userIssue.querySelector('a')).toBeNull();
	});

	it('offers Details and Pin on every row, and Reload from disk on the current one for admins only', async () => {
		await open();
		expect((await rowMenu('sdxl')).map((item) => item.textContent?.trim())).toEqual(['Details', 'Pin']);
		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		unmount(instance!);
		instance = undefined;
		authState.user = { account_type: 'ADMIN' };
		await open();
		const items = await rowMenu('sdxl');
		expect(items.map((item) => item.textContent?.trim())).toEqual(['Details', 'Pin', 'Reload from disk']);
		items[2].click();
		flushSync();
		expect(events.reload).toHaveBeenCalledTimes(1);
	});

	it('pins a preset from the row menu on the server', async () => {
		await open();
		(await rowMenu('krea')).find((item) => item.textContent?.includes('Pin'))!.click();
		await settle(4);
		expect(calls.pinPreset).toHaveBeenCalledWith('krea');
		expect(rowsIn('Pinned')).toEqual(['seedvr2', 'krea']);
	});

	it('shows the description, examples and Use this preset in Details', async () => {
		await open({ presets: [{ ...CATALOG.find((p) => p.id === 'krea')!, description: 'Hello **bold** world' }, CATALOG[0]] });
		(await rowMenu('krea')).find((item) => item.textContent?.includes('Details'))!.click();
		await settle(6);
		expect(dialog()!.querySelector('strong')?.textContent).toBe('bold');
		expect(dialog()!.querySelector('[data-testid="preset-examples"]')).not.toBeNull();
		expect(calls.getPreset).toHaveBeenCalledWith('krea');
		const use = Array.from(dialog()!.querySelectorAll('button')).find((b) => b.textContent?.includes('Use this preset')) as HTMLButtonElement;
		use.click();
		await settle(4);
		expect(events.select).toHaveBeenCalledWith('krea');
	});

	it('gives admins a Manage presets link in the footer', async () => {
		authState.user = { account_type: 'ADMIN' };
		await open();
		const manage = Array.from(dialog()!.querySelectorAll('a')).find((a) => a.textContent?.includes('Manage presets')) as HTMLAnchorElement;
		expect(manage.getAttribute('href')).toBe('/admin?tab=presets');
		unmount(instance!);
		instance = undefined;
		authState.user = { account_type: 'USER' };
		await open();
		expect(Array.from(dialog()!.querySelectorAll('a')).some((a) => a.textContent?.includes('Manage presets'))).toBe(false);
	});

	it('explains an empty catalogue and offers setup to admins', async () => {
		authState.user = { account_type: 'ADMIN' };
		await open({ presets: [], selectedPreset: '' });
		expect(trigger().textContent).toContain('Choose a preset');
		expect(dialog()!.textContent).toContain('Nothing to generate with yet');
		expect(dialog()!.querySelector('[role="listbox"]')).toBeNull();
		expect(Array.from(dialog()!.querySelectorAll('a')).some((a) => /Setup|Install/.test(a.textContent ?? ''))).toBe(true);
	});

	it('fills the screen on a phone with a Choose preset header, 56px rows and 44px covers', async () => {
		viewportWidth.set(390);
		await open();
		expect(dialog()!.className).toContain('is-fullscreen');
		expect(dialog()!.querySelector('header h2')?.textContent).toBe('Choose preset');
		const sdxl = row('sdxl');
		expect(sdxl.querySelector('button')!.className).toContain('h-14');
		expect(sdxl.querySelector('.w-11.h-11')).not.toBeNull();
	});
});

describe('a short list', () => {
	it('sizes the dropdown to its rows and shows no empty Recent or Pinned header', async () => {
		calls.getPresetPickerState.mockResolvedValue(state([], []));
		await open({ presets: CATALOG.slice(0, 3) });
		expect(groups()).toEqual(['Image']);
		expect(dialog()!.querySelector('[data-preset-group="recent"]')).toBeNull();
		expect(dialog()!.querySelector('[data-preset-group="pinned"]')).toBeNull();
		const style = dialog()!.getAttribute('style') ?? '';
		expect(style).toContain('max-height');
		expect(style.replace(/max-height:[^;]*;?/g, '')).not.toMatch(/(^|;|\s)height:/);
	});

	it('still fills the space for the details view', async () => {
		calls.getPresetPickerState.mockResolvedValue(state([], []));
		await open({ presets: CATALOG.slice(0, 3) });
		await rowMenu('sdxl');
		(Array.from(document.body.querySelectorAll('[role="menuitem"]')).find((item) => item.textContent?.includes('Details')) as HTMLElement).click();
		flushSync();
		await settle(6);
		expect(dialog()!.querySelector('[data-testid="preset-details"]')).not.toBeNull();
		expect(dialog()!.getAttribute('style') ?? '').toMatch(/(^|;|\s)height:/);
	});
});
