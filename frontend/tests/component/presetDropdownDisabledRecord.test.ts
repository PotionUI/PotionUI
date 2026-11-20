import { describe, it, expect, afterEach, beforeEach, vi, type Mock } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import type { PresetInfo } from '$lib/types/api';
import { settle } from './helpers/settle';

const { calls } = vi.hoisted(() => ({
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
	authStore: { subscribe: (fn: (v: unknown) => void) => (fn({ user: { account_type: 'USER' } }), () => {}) }
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
	{ id: 'sdxl', name: 'SDXL', engine: 'native', version: '1.4.0', category: 'image', tags: [], engine_available: true },
	{ id: 'krea', name: 'Krea 2', engine: 'native', version: '2.1.0', category: 'image', tags: [], engine_available: true }
] as unknown as PresetInfo[];

let instance: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement | undefined;
let select: Mock<(presetId: string) => void>;

beforeEach(() => {
	viewportWidth.set(1440);
	presetPickerState.reset();
	calls.getPresetPickerState.mockResolvedValue({ success: true, data: { pinned: [], recent: [] } });
	calls.pushRecentPreset.mockResolvedValue({ success: true, data: { pinned: [], recent: ['krea'] } });
	calls.getPreset.mockResolvedValue({ success: true, data: { media: { gallery: [] } } });
});

afterEach(async () => {
	if (instance) unmount(instance);
	instance = undefined;
	target?.remove();
	await settle(2);
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

async function open(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	select = vi.fn<(presetId: string) => void>();
	instance = mount(PresetPicker as never, {
		target,
		props: { presets: CATALOG, selectedPreset: 'sdxl', ...props },
		events: { select: (event: CustomEvent<string>) => select(event.detail) }
	} as never);
	flushSync();
	(document.querySelector('[data-testid="preset-picker-trigger"]') as HTMLButtonElement).click();
	flushSync();
	await settle(6);
	flushSync();
}

const row = (id: string) => document.body.querySelector<HTMLElement>(`[data-preset-row="${id}"]`)!;
const main = (id: string) => row(id).querySelector<HTMLButtonElement>('button.row-main')!;

describe('a preset the picker is told is unavailable', () => {
	it('stays listed, cannot be chosen, and shows its reason in place of the engine line', async () => {
		await open({ disabledReasons: { krea: 'Models not installed' } });
		expect(row('krea').getAttribute('aria-disabled')).toBe('true');
		expect(row('krea').querySelector('[data-preset-unavailable]')?.textContent?.trim()).toBe('Models not installed');
		expect(row('krea').textContent).not.toContain('native');
		expect(main('krea').disabled).toBe(true);
		main('krea').click();
		await tick();
		expect(select).not.toHaveBeenCalled();
		expect(document.body.querySelector('[role="dialog"]')).not.toBeNull();
	});

	it('leaves the other presets chosen as before', async () => {
		await open({ selectedPreset: '', disabledReasons: { krea: 'Models not installed' } });
		expect(row('sdxl').getAttribute('aria-disabled')).toBeNull();
		expect(main('sdxl').disabled).toBe(false);
		main('sdxl').click();
		await tick();
		expect(select).toHaveBeenCalledWith('sdxl');
	});

	it('cannot be chosen from the search box either', async () => {
		await open({ selectedPreset: '', disabledReasons: { krea: 'Models not installed' } });
		const input = document.body.querySelector<HTMLInputElement>('input[aria-label="Search presets"]')!;
		input.value = 'krea';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await tick();
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
		flushSync();
		expect(select).not.toHaveBeenCalled();
	});

	it('changes nothing for the Generate page, which passes no reasons', async () => {
		await open({ selectedPreset: '' });
		expect(document.body.querySelectorAll('[data-preset-unavailable]')).toHaveLength(0);
		expect(document.body.querySelectorAll('[aria-disabled="true"][data-preset-row]')).toHaveLength(0);
	});
});

describe('recording the choice as recent', () => {
	it('records it by default, as the Generate page does', async () => {
		await open({ selectedPreset: '' });
		main('krea').click();
		await tick();
		expect(calls.pushRecentPreset).toHaveBeenCalledWith('krea');
	});

	it('records nothing when the caller turns it off, and still tells the caller', async () => {
		await open({ selectedPreset: '', recordRecent: false });
		main('krea').click();
		await tick();
		expect(calls.pushRecentPreset).not.toHaveBeenCalled();
		expect(select).toHaveBeenCalledWith('krea');
	});
});

describe('opening the picker from outside', () => {
	it('opens at the given element once and never twice', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		const anchor = document.createElement('button');
		document.body.appendChild(anchor);
		instance = mount(PresetPicker as never, { target, props: { presets: CATALOG, selectedPreset: '' } } as never);
		flushSync();
		const picker = instance as unknown as { openAt(element: HTMLElement): void };
		picker.openAt(anchor);
		flushSync();
		await settle(4);
		expect(document.body.querySelectorAll('[role="dialog"][aria-label="Choose preset"]')).toHaveLength(1);
		picker.openAt(anchor);
		flushSync();
		expect(document.body.querySelectorAll('[role="dialog"][aria-label="Choose preset"]')).toHaveLength(1);
	});
});
