import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { get } from 'svelte/store';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetModes: vi.fn(), listRecipes: vi.fn(), planRecipe: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { default: PickerSelect } = await import('../../src/lib/components/preset/PickerSelect.svelte');
const { default: PresetModesList } = await import('../../src/routes/admin/components/presets/PresetModesList.svelte');
const { presetModeOptions, MODE_NOT_SET_UP_NOTICE } = await import('../../src/lib/utils/presetModeOptions');
const { modeInstallRequest, closeModeInstall } = await import('../../src/lib/stores/modeInstall');
const { announceModelInstalled } = await import('../../src/lib/stores/modelInstalls');

const MODES = [
	{ id: 'video', label: 'Video', description: 'Text or image to video', install: { recipe_id: 'modes-ltx', ready: true, missing_bytes: 0 } },
	{
		id: 'cine',
		label: 'Cinemagraph',
		description: 'Make a still image loop',
		requirementsMissing: ['effect_lora'],
		install: { recipe_id: 'modes-ltx', ready: false, missing_bytes: 211_288_064 }
	},
	{
		id: 'layout',
		label: 'Layout to render',
		description: 'Turn a layout into a shot',
		requirementsMissing: ['control'],
		install: { recipe_id: 'modes-ltx', ready: false, missing_bytes: 1_500_000_000 }
	}
];

let instance: ReturnType<typeof mount> | null = null;

function openPicker(admin: boolean) {
	const target = document.body.appendChild(document.createElement('div'));
	instance = mount(PickerSelect, {
		target,
		props: { label: 'Mode', value: 'video', options: presetModeOptions(MODES, { admin }), onchange: vi.fn() }
	});
	flushSync();
	target.querySelector('button')!.click();
	flushSync();
}

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	closeModeInstall();
	document.body.innerHTML = '';
});

describe('Generate mode picker doors', () => {
	it('gives an admin an inline Install button with the size on every not-ready mode', async () => {
		openPicker(true);
		await settle();
		const buttons = Array.from(document.body.querySelectorAll('[data-option-action]'));
		expect(buttons.map((b) => b.textContent?.trim())).toEqual(['Install · 202 MB', 'Install · 1.4 GB']);
		expect(document.body.querySelector('[data-option-notice]')).toBeNull();
	});

	it('opens the install sheet request with that mode preselected', async () => {
		openPicker(true);
		await settle();
		(document.body.querySelectorAll('[data-option-action]')[1] as HTMLButtonElement).click();
		flushSync();
		const request = get(modeInstallRequest);
		expect(request?.recipeId).toBe('modes-ltx');
		expect(request?.modes).toEqual(['layout']);
	});

	it('stacks the Install button after the description, outside the option button', () => {
		openPicker(true);
		const row = Array.from(document.body.querySelectorAll('[role="option"]')).find((b) => b.textContent?.includes('Cinemagraph'))!;
		const action = document.body.querySelector('[data-option-action]') as HTMLElement;
		expect(row.contains(action)).toBe(false);
		expect(row.parentElement).toBe(action.closest('.picker-item'));
		expect(row.textContent).toContain('Make a still image loop');
		expect(row.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('stacks the lock note under the description inside the same text column', () => {
		openPicker(false);
		const notice = document.body.querySelector('[data-option-notice]') as HTMLElement;
		const description = Array.from(notice.parentElement!.children).find((child) => child.textContent === 'Make a still image loop')!;
		expect(description).toBeTruthy();
		expect(description.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('does not select the mode when its Install button is used', async () => {
		const onchange = vi.fn();
		const target = document.body.appendChild(document.createElement('div'));
		instance = mount(PickerSelect, {
			target,
			props: { label: 'Mode', value: 'video', options: presetModeOptions(MODES, { admin: true }), onchange }
		});
		flushSync();
		target.querySelector('button')!.click();
		flushSync();
		(document.body.querySelector('[data-option-action]') as HTMLButtonElement).click();
		expect(onchange).not.toHaveBeenCalled();
	});

	it('shows a regular user a lock note, keeps the mode visible and offers no Install button', async () => {
		openPicker(false);
		await settle();
		expect(document.body.querySelector('[data-option-action]')).toBeNull();
		const notices = Array.from(document.body.querySelectorAll('[data-option-notice]'));
		expect(notices).toHaveLength(2);
		expect(notices[0].textContent).toContain(MODE_NOT_SET_UP_NOTICE);
		expect(document.body.textContent).toContain('Cinemagraph');
		expect(document.body.textContent).toContain('Layout to render');
	});

	it('leaves ready modes plain for both audiences', () => {
		const admin = presetModeOptions(MODES, { admin: true });
		const user = presetModeOptions(MODES, { admin: false });
		for (const options of [admin, user]) {
			const video = options.find((option) => option.value === 'video')!;
			expect(video.action).toBeUndefined();
			expect(video.notice).toBeUndefined();
			expect(video.issue).toBeUndefined();
		}
	});

	it('falls back to the missing-file line for an admin when no recipe covers the mode', () => {
		const [option] = presetModeOptions([{ id: 'x', label: 'X', requirementsMissing: ['a'] }], { admin: true });
		expect(option.issue).toBe('A required file is missing.');
		expect(option.action).toBeUndefined();
	});
});

describe('Preset overview modes list', () => {
	it('lists modes with status and an Install button that opens the sheet for that mode', async () => {
		vi.mocked(api.getPresetModes).mockResolvedValue({
			success: true,
			data: {
				preset_id: 'p1',
				default_mode: 'video',
				modes: [
					...MODES.map((m) => ({ name: m.id, label: m.label, short_description: m.description, variants: [], install: m.install })),
					{ name: 'plain', label: 'Plain mode', variants: [] }
				]
			}
		} as never);
		const target = document.body.appendChild(document.createElement('div'));
		instance = mount(PresetModesList, { target, props: { presetId: 'p1' } });
		await settle();
		flushSync();
		expect(target.querySelector('[data-preset-modes-count]')!.textContent).toContain('1 of 3 ready');
		expect(target.querySelector('[data-preset-mode="plain"]')).toBeNull();
		expect(target.querySelector('[data-preset-mode="video"]')!.textContent).toContain('Ready');
		const cine = target.querySelector('[data-preset-mode="cine"]')!;
		expect(cine.textContent).toContain('Not installed');
		(cine.querySelector('button') as HTMLButtonElement).click();
		expect(get(modeInstallRequest)?.modes).toEqual(['cine']);
	});

	it('reloads after an install finishes', async () => {
		vi.mocked(api.getPresetModes).mockClear();
		vi.mocked(api.getPresetModes).mockResolvedValue({ success: true, data: { preset_id: 'p1', default_mode: '', modes: [] } } as never);
		const target = document.body.appendChild(document.createElement('div'));
		instance = mount(PresetModesList, { target, props: { presetId: 'p1' } });
		await settle();
		const before = vi.mocked(api.getPresetModes).mock.calls.length;
		announceModelInstalled();
		await settle();
		expect(vi.mocked(api.getPresetModes).mock.calls.length).toBe(before + 1);
	});

	it('renders nothing when no mode has an install recipe', async () => {
		vi.mocked(api.getPresetModes).mockResolvedValue({
			success: true,
			data: { preset_id: 'p1', default_mode: '', modes: [{ name: 'a', label: 'A', variants: [] }] }
		} as never);
		const target = document.body.appendChild(document.createElement('div'));
		instance = mount(PresetModesList, { target, props: { presetId: 'p1' } });
		await settle();
		expect(target.querySelector('[data-preset-modes]')).toBeNull();
	});
});
