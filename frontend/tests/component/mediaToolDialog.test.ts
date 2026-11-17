import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from './helpers/mount';
import { settle } from './helpers/settle';
import { toolDto } from './fixtures/mediaToolCatalog';

const startMediaToolRun = vi.fn();
const getMediaToolRun = vi.fn();
const cancelMediaToolRun = vi.fn();
const previewMediaTool = vi.fn();
const listGenerationMedia = vi.fn();
const listUploads = vi.fn();

vi.mock('$lib/services/api/mediaTools', () => ({
	startMediaToolRun: (...args: unknown[]) => startMediaToolRun(...args),
	getMediaToolRun: (...args: unknown[]) => getMediaToolRun(...args),
	cancelMediaToolRun: (...args: unknown[]) => cancelMediaToolRun(...args),
	previewMediaTool: (...args: unknown[]) => previewMediaTool(...args),
	listMediaTools: vi.fn()
}));

vi.mock('$lib/services/api/index', async (importOriginal) => {
	const overrides: Record<string, unknown> = {
		getPresetFormSchema: async () => {
			throw new Error('no preset');
		},
		listGenerationMedia: (...args: unknown[]) => listGenerationMedia(...args),
		listUploads: (...args: unknown[]) => listUploads(...args)
	};
	return {
		...(await importOriginal<Record<string, unknown>>()),
		api: new Proxy(overrides, { get: (target, key) => (key in target ? target[key as string] : () => undefined) })
	};
});

const { default: MediaToolDialog } = await import('$lib/media-tools/MediaToolDialog.svelte');
const { mediaToolRuns } = await import('$lib/media-tools/runs');
const { resetMediaToolCatalog, toolById } = await import('$lib/media-tools/catalog');

const clip = {
	id: 'row-1',
	kind: 'video' as const,
	url: '/api/media/uploads/clip.mp4',
	filename: 'clip.mp4',
	width: 1920,
	height: 1080,
	durationSeconds: 8,
	fps: 24
};

const still = {
	id: 'row-2',
	kind: 'image' as const,
	url: '/api/media/uploads/still.png',
	filename: 'still.png',
	width: 1254,
	height: 1254
};

const SPEED = toolDto({
	id: 'speed',
	label: 'Speed',
	icon: 'gauge',
	accepts: ['video', 'audio'],
	params: [
		{ name: 'speed', type: 'float', label: 'Speed', min: 0.25, max: 4, step: 0.05, default: 2, unit: '×' },
		{ name: 'keep_pitch', type: 'checkbox', label: 'Keep pitch', default: true }
	]
});

const COLOR_KEY = toolDto({
	id: 'color_key',
	label: 'Color key',
	icon: 'palette',
	preview: true,
	params: [
		{ name: 'key_mode', type: 'select', label: 'Key', options: ['auto', 'color'], default: 'auto' },
		{ name: 'key_color', type: 'color', label: 'Key color', default: '#00B140', when: { field: 'key_mode', equals: 'color' } },
		{ name: 'tolerance', type: 'float', label: 'Tolerance', min: 0, max: 100, step: 1, default: 25 },
		{ name: 'despill', type: 'checkbox', label: 'Despill', default: true }
	]
});

const FIT_CANVAS = toolDto({
	id: 'fit_canvas',
	label: 'Fit canvas',
	icon: 'expand',
	preview: true,
	params: [
		{ name: 'width', type: 'integer', label: 'Width', min: 1, default: 1024, unit: 'px' },
		{ name: 'height', type: 'integer', label: 'Height', min: 1, default: 1024, unit: 'px' },
		{ name: 'scale_percent', type: 'float', label: 'Scale', min: 1, max: 400, step: 1, default: 100, unit: '%' },
		{ name: 'anchor', type: 'anchor', label: 'Anchor', default: 'center' },
		{ name: 'fill', type: 'color', label: 'Fill', default: 'transparent', allow_transparent: true }
	]
});

const ROTATE = toolDto({
	id: 'rotate',
	label: 'Rotate',
	icon: 'refresh',
	accepts: ['image', 'video'],
	preview: true,
	params: [{ name: 'degrees', type: 'select', label: 'Degrees', options: [90, 180, 270], default: 90 }]
});

const CONCAT = toolDto({
	id: 'concat',
	label: 'Concat',
	icon: 'film',
	accepts: ['video'],
	cardinality: 'many_to_one',
	produces: 'video',
	selection: { min: 2, max: 8 },
	params: [
		{ name: 'overlap', type: 'select', label: 'Overlap', options: ['cut', 'crossfade'], default: 'cut' },
		{ name: 'overlap_frames', type: 'integer', label: 'Overlap frames', min: 0, max: 48, default: 0, when: { field: 'overlap', equals: 'crossfade' } },
		{ name: 'keep_audio', type: 'checkbox', label: 'Keep audio', default: true }
	]
});

const MANY_OPTIONS = toolDto({
	id: 'acme.many',
	label: 'Many',
	params: [{ name: 'mode', type: 'select', label: 'Mode', options: ['a', 'b', 'c', 'd', 'e'], default: 'a' }]
});

function run(overrides: Record<string, unknown> = {}) {
	return { run_id: 'run-1', status: 'completed', progress: 1, label: '', items: [], replaced: false, error: null, ...overrides };
}

const RESULT_ITEM = { id: 'new-1', filename: 'out.mp4', original_filename: 'clip-speed.mp4', media_type: 'video', url: '/out.mp4' };

type Props = Record<string, unknown>;

function open(props: Props) {
	const onClose = vi.fn();
	const onResult = vi.fn();
	const view = mountComponent(MediaToolDialog, { items: [clip], scope: 'library', onClose, onResult, ...props });
	return { ...view, onClose, onResult };
}

function dialog(): HTMLElement {
	return document.body;
}

function buttonByText(text: string): HTMLButtonElement | undefined {
	return Array.from(document.body.querySelectorAll('button')).find((b) => (b.textContent || '').trim() === text);
}

function param(name: string): HTMLElement {
	return document.body.querySelector(`[data-field-name="${name}"]`) as HTMLElement;
}

async function applyNow() {
	await settle();
	buttonByText('Apply')!.click();
	await settle();
}

beforeEach(() => {
	document.body.innerHTML = '';
	resetMediaToolCatalog();
	startMediaToolRun.mockReset().mockResolvedValue({ run_id: 'run-1' });
	getMediaToolRun.mockReset().mockResolvedValue(run({ items: [RESULT_ITEM] }));
	cancelMediaToolRun.mockReset().mockResolvedValue(run({ status: 'cancelled', progress: 0.1 }));
	previewMediaTool.mockReset().mockResolvedValue(new Blob(['x'], { type: 'image/webp' }));
	listGenerationMedia.mockReset().mockResolvedValue({ success: false });
	listUploads.mockReset().mockResolvedValue({ success: true, data: { uploads: [], total: 0, limit: 100, offset: 0 } });
	vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:preview'), revokeObjectURL: vi.fn() }));
});

afterEach(() => {
	mediaToolRuns.destroy();
	vi.useRealTimers();
});

describe('the form built from the params', () => {
	it('names the tool and the file in the header', async () => {
		open({ tool: SPEED });
		await settle();
		expect(dialog().textContent).toContain('Speed');
		expect(dialog().querySelector('[data-tool-subject]')?.textContent?.trim()).toBe('clip.mp4');
	});

	it('draws a slider from a float with min and max and a switch from a checkbox', async () => {
		open({ tool: SPEED });
		await settle();

		expect(param('speed').getAttribute('data-field-type')).toBe('slider');
		expect(param('keep_pitch').getAttribute('data-field-type')).toBe('checkbox');
		expect(param('speed').textContent).toContain('2×');
		expect(param('speed').textContent).toContain('0.25×');
		expect(param('speed').textContent).toContain('4×');
		expect((param('keep_pitch').querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(true);
	});

	it('draws a select of four options or fewer as a segmented control and more as a dropdown', async () => {
		open({ tool: ROTATE, items: [still] });
		await settle();
		expect(param('degrees').getAttribute('data-field-type')).toBe('segmented');
		expect(Array.from(param('degrees').querySelectorAll('button')).map((b) => b.textContent?.trim())).toEqual([
			'90',
			'180',
			'270'
		]);

		document.body.innerHTML = '';
		open({ tool: MANY_OPTIONS, items: [still] });
		await settle();
		expect(param('mode').getAttribute('data-field-type')).toBe('select');
	});

	it('draws number boxes with a unit, the anchor grid, and a colour with a transparent choice', async () => {
		open({ tool: FIT_CANVAS, items: [still] });
		await settle();

		expect(param('width').getAttribute('data-field-type')).toBe('number_box');
		expect(param('width').textContent).toContain('px');
		expect(param('scale_percent').getAttribute('data-field-type')).toBe('slider');
		expect(param('anchor').getAttribute('data-field-type')).toBe('anchor');
		expect(param('anchor').querySelectorAll('button[data-anchor]')).toHaveLength(9);
		expect(param('fill').getAttribute('data-field-type')).toBe('color');
		expect(param('fill').textContent).toContain('Transparent');
	});

	it('puts the two number boxes of a pair side by side', async () => {
		open({ tool: FIT_CANVAS, items: [still] });
		await settle();
		const grid = param('width').closest('.row-grid') as HTMLElement;
		expect(grid.style.gridTemplateColumns.trim().split(/\s+/).length).toBeGreaterThanOrEqual(2);
		expect(grid.contains(param('height'))).toBe(true);
	});

	it('hides a field until its when condition holds, and shows it when the choice changes', async () => {
		open({ tool: COLOR_KEY, items: [still] });
		await settle();
		expect(param('key_color')).toBeNull();

		(Array.from(param('key_mode').querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Color') as HTMLButtonElement).click();
		await settle();
		expect(param('key_color')).not.toBeNull();

		(Array.from(param('key_mode').querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Auto') as HTMLButtonElement).click();
		await settle();
		expect(param('key_color')).toBeNull();
	});

	it('opens on the values the user last used, and falls back to the default for one that no longer fits', async () => {
		const remembered = { ...SPEED, last_params: { speed: 3.5, keep_pitch: false } };
		open({ tool: remembered });
		await settle();
		expect(param('speed').textContent).toContain('3.5×');
		expect((param('keep_pitch').querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(false);

		document.body.innerHTML = '';
		open({ tool: { ...SPEED, last_params: { speed: 99 } } });
		await settle();
		expect(param('speed').textContent).toContain('2×');
	});

	it('shows and sends the minimum for a ranged number that declares no default', async () => {
		const loop = toolDto({
			id: 'acme.loop',
			label: 'Loop',
			accepts: ['video'],
			params: [{ name: 'repeats', type: 'integer', label: 'Repeats', min: 2, max: 8 }]
		});
		open({ tool: loop, detach: true });
		await settle();

		expect(param('repeats').getAttribute('data-field-type')).toBe('slider');
		expect(param('repeats').textContent).toContain('2');
		expect((param('repeats').querySelector('input[type="range"]') as HTMLInputElement).value).toBe('2');
		buttonByText('Apply')!.click();
		await settle();

		expect(startMediaToolRun.mock.calls[0][0].steps).toEqual([{ tool: 'acme.loop', params: { repeats: 2 } }]);
	});

	it('draws nothing but the source for a tool with no params', async () => {
		open({ tool: toolDto({ id: 'plain', label: 'Plain', accepts: ['video'] }) });
		await settle();
		expect(dialog().querySelector('[data-param]')).toBeNull();
		expect(dialog().querySelector('[data-tool-source]')?.textContent).toContain('clip.mp4');
		expect(dialog().querySelector('[data-tool-source]')?.textContent).toContain('1920×1080 · 00:08.0 · 24 fps');
	});

	it('shows a number that breaks its bound and refuses to apply it', async () => {
		open({ tool: FIT_CANVAS, items: [still] });
		await settle();

		const width = param('width').querySelector('input') as HTMLInputElement;
		width.value = '0';
		width.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		expect(param('width').textContent).toContain('At least 1');
		expect(buttonByText('Apply')!.disabled).toBe(true);
		expect(dialog().querySelector('[data-tool-blocked]')?.textContent).toBe('Fix the highlighted setting to continue.');
		expect(dialog().textContent!.split('At least 1')).toHaveLength(2);
	});
});

describe('the readout', () => {
	it('works out the result from the values as they change', async () => {
		open({ tool: SPEED });
		await settle();
		expect(dialog().querySelector('[data-tool-readout]')?.textContent).toContain('1920×1080 · 00:04.0');

		const slider = param('speed').querySelector('input[type="range"]') as HTMLInputElement;
		slider.value = '4';
		slider.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		expect(dialog().querySelector('[data-tool-readout]')?.textContent).toContain('1920×1080 · 00:02.0');
	});

	it('is left out for a tool that cannot tell', async () => {
		open({ tool: toolDto({ id: 'acme.loop', label: 'Loop', accepts: ['video'] }) });
		await settle();
		expect(dialog().querySelector('[data-tool-readout]')).toBeNull();
	});
});

describe('applying', () => {
	it('starts one run with the values on the form, as a new item, and closes when detached', async () => {
		const { onClose } = open({ tool: SPEED, detach: true });

		await applyNow();

		expect(startMediaToolRun).toHaveBeenCalledTimes(1);
		expect(startMediaToolRun).toHaveBeenCalledWith({
			steps: [{ tool: 'speed', params: { speed: 2, keep_pitch: true } }],
			inputs: [{ item_id: 'row-1' }],
			output: { mode: 'new' }
		});
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('closes at once while the run is still going when detached', async () => {
		getMediaToolRun.mockResolvedValue(run({ status: 'running', progress: 0.3, items: [] }));
		const { onClose, onResult } = open({ tool: SPEED, detach: true });

		await applyNow();

		expect(onClose).toHaveBeenCalledTimes(1);
		expect(onResult).not.toHaveBeenCalled();
	});

	it('hands the items over with their thumbnails and whether the original was replaced', async () => {
		const item = { ...RESULT_ITEM, thumbnail_small: '/t/s.webp', thumbnail_medium: '/t/m.webp', thumbnail_large: '/t/l.webp' };
		getMediaToolRun.mockResolvedValue(run({ items: [item], replaced: true }));
		const { onResult } = open({ tool: SPEED, detach: true });

		await settle();
		buttonByText('Replace original')!.click();
		await settle();
		buttonByText('Apply')!.click();
		await settle();

		expect(onResult).toHaveBeenCalledWith({ items: [item], replaced: true });
	});

	it('hands the items to the host when a detached run completes', async () => {
		const { onResult } = open({ tool: SPEED, detach: true });
		await applyNow();
		await settle();
		expect(onResult).toHaveBeenCalledWith({ items: [RESULT_ITEM], replaced: false });
	});

	it('sends the changed value, and leaves hidden params out', async () => {
		open({ tool: COLOR_KEY, items: [still] });
		await settle();

		const slider = param('tolerance').querySelector('input[type="range"]') as HTMLInputElement;
		slider.value = '40';
		slider.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		buttonByText('Apply')!.click();
		await settle();

		const { steps } = startMediaToolRun.mock.calls[0][0];
		expect(steps[0].params).toEqual({ key_mode: 'auto', tolerance: 40, despill: true });
	});

	it('offers Save as new and Replace original on the library and sends the one that is picked', async () => {
		open({ tool: SPEED });
		await settle();

		expect(buttonByText('Save as new')!.getAttribute('aria-pressed')).toBe('true');
		buttonByText('Replace original')!.click();
		await settle();
		expect(buttonByText('Replace original')!.getAttribute('aria-pressed')).toBe('true');
		buttonByText('Apply')!.click();
		await settle();

		expect(startMediaToolRun.mock.calls[0][0].output).toEqual({ mode: 'replace' });
	});

	it('offers no replace for several items, and says each result is saved as a new item', async () => {
		open({ tool: SPEED, items: [clip, { ...clip, id: 'row-9', filename: 'second.mp4' }] });
		await settle();

		expect(buttonByText('Replace original')).toBeUndefined();
		expect(dialog().querySelector('[data-tool-note]')?.textContent).toBe('Each result is saved as a new item.');
		buttonByText('Apply')!.click();
		await settle();

		expect(startMediaToolRun.mock.calls[0][0].inputs).toEqual([{ item_id: 'row-1' }, { item_id: 'row-9' }]);
		expect(startMediaToolRun.mock.calls[0][0].output).toEqual({ mode: 'new' });
	});

	it('runs a history file from storage, never offers a replace and says where the result goes', async () => {
		listGenerationMedia.mockResolvedValue({ success: true, data: { media: [{ id: 'file-5', filename: 'clip.mp4' }] } });
		open({
			tool: SPEED,
			scope: 'history',
			items: [{ ...clip, id: 'gen-1:0', generationId: 'gen-1', url: '/api/media/generations/gen-1/clip.mp4' }]
		});
		await settle();

		expect(buttonByText('Replace original')).toBeUndefined();
		expect(dialog().querySelector('[data-tool-note]')?.textContent).toBe(
			'Saves a copy to your Library. The generation is not changed.'
		);
		buttonByText('Apply')!.click();
		await settle();

		expect(listGenerationMedia).toHaveBeenCalledWith('gen-1');
		expect(startMediaToolRun.mock.calls[0][0].inputs).toEqual([{ generation_file_id: 'file-5' }]);
		expect(startMediaToolRun.mock.calls[0][0].output).toEqual({ mode: 'new' });
	});

	it('says the result replaces the value in a field', async () => {
		open({ tool: SPEED, scope: 'field', items: [{ ...clip, id: 'field', itemId: 'row-1' }] });
		await settle();
		expect(dialog().querySelector('[data-tool-note]')?.textContent).toBe(
			'The result replaces this video in the field. The library copy stays as it is.'
		);
	});

	it('names the join and the video it saves for a many-to-one tool', async () => {
		open({ tool: CONCAT, items: [clip, { ...clip, id: 'row-3', filename: 'b.mp4' }] });
		await settle();
		expect(buttonByText('Join')).toBeDefined();
		expect(dialog().querySelector('[data-tool-note]')?.textContent).toBe('Saved as a new video.');
	});

	it('does not start a run when closed', async () => {
		const { onClose } = open({ tool: SPEED });
		await settle();
		buttonByText('Cancel')!.click();
		await settle();
		expect(onClose).toHaveBeenCalled();
		expect(startMediaToolRun).not.toHaveBeenCalled();
	});

	it('explains why it cannot apply a file that is not in the library', async () => {
		open({ tool: SPEED, items: [{ ...clip, id: 'field', storedPath: 'somewhere/else.mp4' }], scope: 'field' });
		await settle();
		expect(buttonByText('Apply')!.disabled).toBe(true);
		expect(dialog().querySelector('[data-tool-blocked]')?.textContent).toContain('not in your library');
	});

	describe('when the server names the param it refused', () => {
		const refusal = (field: string | undefined) => ({
			message: 'Request failed with status code 400',
			response: {
				data: {
					detail: { error: 'invalid_params', message: 'key_color must be a hex colour', ...(field ? { field } : {}), step_index: 0 }
				}
			}
		});

		async function openKeyed() {
			const view = open({ tool: { ...COLOR_KEY, last_params: { key_mode: 'color' } }, items: [still] });
			await settle();
			return view;
		}

		it('marks that control invalid with the message beside it, and keeps the footer message too', async () => {
			startMediaToolRun.mockRejectedValue(refusal('key_color'));
			await openKeyed();

			buttonByText('Apply')!.click();
			await settle();

			const control = param('key_color');
			expect(control.getAttribute('data-invalid')).toBe('true');
			expect(control.querySelector('[data-field-error]')?.textContent?.trim()).toBe('key_color must be a hex colour');
			expect(control.querySelector('[data-field-error]')?.getAttribute('role')).toBe('alert');
			const input = control.querySelector('input[type="text"]') as HTMLInputElement;
			expect(input.getAttribute('aria-invalid')).toBe('true');
			expect(input.getAttribute('aria-describedby')).toBe('key_color-field-error');
			expect(dialog().querySelector('[data-tool-failure]')?.textContent).toBe('key_color must be a hex colour');
			expect(param('tolerance').getAttribute('data-invalid')).toBeNull();
		});

		it('clears the mark when the value is changed, and keeps the footer message until the next try', async () => {
			startMediaToolRun.mockRejectedValue(refusal('key_color'));
			await openKeyed();
			buttonByText('Apply')!.click();
			await settle();

			const input = param('key_color').querySelector('input[type="text"]') as HTMLInputElement;
			input.focus();
			input.value = '#112233';
			input.dispatchEvent(new Event('input', { bubbles: true }));
			input.blur();
			await settle();

			expect(param('key_color').getAttribute('data-invalid')).toBeNull();
			expect(param('key_color').querySelector('[data-field-error]')).toBeNull();
			expect(param('key_color').querySelector('input[type="text"]')?.getAttribute('aria-invalid')).not.toBe('true');
		});

		it('leaves other controls alone when another one is changed', async () => {
			startMediaToolRun.mockRejectedValue(refusal('key_color'));
			await openKeyed();
			buttonByText('Apply')!.click();
			await settle();

			const slider = param('tolerance').querySelector('input[type="range"]') as HTMLInputElement;
			slider.value = '50';
			slider.dispatchEvent(new Event('input', { bubbles: true }));
			await settle();

			expect(param('key_color').getAttribute('data-invalid')).toBe('true');
		});

		it('clears the mark when the edit is tried again', async () => {
			startMediaToolRun.mockRejectedValueOnce(refusal('key_color')).mockResolvedValue({ run_id: 'run-1' });
			await openKeyed();
			buttonByText('Apply')!.click();
			await settle();
			expect(param('key_color').getAttribute('data-invalid')).toBe('true');

			buttonByText('Apply')!.click();
			await settle();

			expect(param('key_color')?.getAttribute('data-invalid') ?? null).toBeNull();
		});

		it('marks nothing for a field the form does not have, and still shows the footer message', async () => {
			startMediaToolRun.mockRejectedValue(refusal('mystery'));
			await openKeyed();
			buttonByText('Apply')!.click();
			await settle();

			expect(dialog().querySelector('[data-invalid]')).toBeNull();
			expect(dialog().querySelector('[data-tool-failure]')?.textContent).toBe('key_color must be a hex colour');
		});

		it('marks nothing when the server names no field', async () => {
			startMediaToolRun.mockRejectedValue(refusal(undefined));
			await openKeyed();
			buttonByText('Apply')!.click();
			await settle();
			expect(dialog().querySelector('[data-invalid]')).toBeNull();
			expect(dialog().querySelector('[data-tool-failure]')).not.toBeNull();
		});

		it('shows the footer message for a refused param that is hidden by a condition', async () => {
			startMediaToolRun.mockRejectedValue(refusal('key_color'));
			open({ tool: COLOR_KEY, items: [still] });
			await settle();
			buttonByText('Apply')!.click();
			await settle();
			expect(param('key_color')).toBeNull();
			expect(dialog().querySelector('[data-tool-failure]')).not.toBeNull();
		});
	});

	it('shows the refusal of a request the server turned down and stays open', async () => {
		startMediaToolRun.mockRejectedValue({
			message: 'Request failed with status code 400',
			response: { data: { detail: { error: 'invalid_params', message: 'speed must be at most 4', field: 'speed' } } }
		});
		const { onClose } = open({ tool: SPEED });

		await applyNow();

		expect(dialog().querySelector('[data-tool-failure]')?.textContent).toBe('speed must be at most 4');
		expect(onClose).not.toHaveBeenCalled();
		expect(buttonByText('Apply')!.disabled).toBe(false);
	});
});

describe('a run that is waited for', () => {
	it('shows progress and a Cancel edit button, then hands the result over and closes', async () => {
		let finish: (value: unknown) => void = () => undefined;
		getMediaToolRun
			.mockResolvedValueOnce(run({ status: 'running', progress: 0.42, label: 'Encoding', items: [] }))
			.mockImplementation(() => new Promise((resolve) => (finish = resolve)));
		const { onClose, onResult } = open({ tool: SPEED, scope: 'field', items: [{ ...clip, id: 'field', itemId: 'row-1' }] });

		await applyNow();

		const progress = dialog().querySelector('[data-tool-progress]')!;
		expect(progress.textContent).toContain('Encoding · 42%');
		expect(progress.parentElement!.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('42');
		expect(buttonByText('Cancel edit')).toBeDefined();
		expect(buttonByText('Apply')).toBeUndefined();
		expect(onClose).not.toHaveBeenCalled();

		await new Promise((resolve) => setTimeout(resolve, 450));
		finish(run({ items: [RESULT_ITEM] }));
		await settle();

		expect(onResult).toHaveBeenCalledWith({ items: [RESULT_ITEM], replaced: false });
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('cancels the run and goes back to the form', async () => {
		getMediaToolRun.mockResolvedValue(run({ status: 'running', progress: 0.1, items: [] }));
		const { onClose, onResult } = open({ tool: SPEED, scope: 'field', items: [{ ...clip, id: 'field', itemId: 'row-1' }] });
		await applyNow();

		buttonByText('Cancel edit')!.click();
		await settle();

		expect(cancelMediaToolRun).toHaveBeenCalledWith('run-1');
		expect(onResult).not.toHaveBeenCalled();
		expect(onClose).not.toHaveBeenCalled();
		expect(buttonByText('Apply')).toBeDefined();
		expect(dialog().querySelector('[data-tool-progress]')).toBeNull();
	});

	it('hands the result over when the cancel is answered with a run that had already completed', async () => {
		let finish: (value: unknown) => void = () => undefined;
		getMediaToolRun
			.mockResolvedValueOnce(run({ status: 'running', progress: 0.95, label: 'Saving', items: [] }))
			.mockImplementation(() => new Promise((resolve) => (finish = resolve)));
		cancelMediaToolRun.mockResolvedValue(run({ status: 'completed', progress: 1, items: [RESULT_ITEM], replaced: true }));
		const { onClose, onResult } = open({ tool: SPEED, scope: 'field', items: [{ ...clip, id: 'field', itemId: 'row-1' }] });
		await applyNow();

		buttonByText('Cancel edit')!.click();
		await settle();

		expect(onResult).toHaveBeenCalledWith({ items: [RESULT_ITEM], replaced: true });
		expect(onClose).toHaveBeenCalledTimes(1);
		expect(dialog().querySelector('[data-tool-failure]')).toBeNull();
		void finish;
	});

	it('stays in the progress state while a cancel is not final and then follows the poll to the end', async () => {
		getMediaToolRun.mockResolvedValue(run({ status: 'running', progress: 0.9, items: [] }));
		cancelMediaToolRun.mockResolvedValue(run({ status: 'running', progress: 0.9 }));
		const { onClose, onResult } = open({ tool: SPEED, scope: 'field', items: [{ ...clip, id: 'field', itemId: 'row-1' }] });
		await applyNow();

		buttonByText('Cancelling')?.click();
		buttonByText('Cancel edit')!.click();
		await settle();

		expect(buttonByText('Cancelling')?.disabled).toBe(true);
		expect(onResult).not.toHaveBeenCalled();
		expect(onClose).not.toHaveBeenCalled();

		getMediaToolRun.mockResolvedValue(run({ status: 'completed', progress: 1, items: [RESULT_ITEM], replaced: false }));
		await new Promise((resolve) => setTimeout(resolve, 450));
		await settle();
		expect(onResult).toHaveBeenCalledWith({ items: [RESULT_ITEM], replaced: false });
	});

	it('shows why a run failed and offers the form again', async () => {
		getMediaToolRun.mockResolvedValue(run({ status: 'failed', error: { code: 'ffmpeg_failed', message: 'The encoder stopped' } }));
		const { onClose } = open({ tool: SPEED, scope: 'field', items: [{ ...clip, id: 'field', itemId: 'row-1' }] });

		await applyNow();

		expect(dialog().querySelector('[data-tool-failure]')?.textContent).toBe('The encoder stopped');
		expect(onClose).not.toHaveBeenCalled();
		expect(buttonByText('Apply')).toBeDefined();
	});

	it('cannot be closed while it runs', async () => {
		getMediaToolRun.mockResolvedValue(run({ status: 'running', progress: 0.1, items: [] }));
		const { onClose } = open({ tool: SPEED, scope: 'field', items: [{ ...clip, id: 'field', itemId: 'row-1' }] });
		await applyNow();

		expect(buttonByText('Cancel')).toBeUndefined();
		expect(document.body.querySelector('button[aria-label="Close modal"]')).toBeNull();
		expect(onClose).not.toHaveBeenCalled();
	});

	it('leaves the run to finish in the background when the dialog goes away', async () => {
		getMediaToolRun.mockResolvedValue(run({ status: 'running', progress: 0.1, items: [] }));
		const view = open({ tool: SPEED, scope: 'field', items: [{ ...clip, id: 'field', itemId: 'row-1' }] });
		await applyNow();

		view.destroy();
		await settle();

		expect(cancelMediaToolRun).not.toHaveBeenCalled();
		expect(view.onResult).not.toHaveBeenCalled();
	});
});

describe('the preview', () => {
	beforeEach(() => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
	});

	function afterImage(): HTMLImageElement | null {
		return dialog().querySelector('[data-before-after] img[alt="After"]');
	}

	it('asks for a preview of the first state straight away and shows the image it gets', async () => {
		open({ tool: COLOR_KEY, items: [still] });
		await settle();

		expect(previewMediaTool).toHaveBeenCalledTimes(1);
		expect(previewMediaTool.mock.calls[0][0]).toEqual({
			tool: 'color_key',
			params: { key_mode: 'auto', tolerance: 25, despill: true },
			input: { item_id: 'row-2' }
		});
		expect(afterImage()?.getAttribute('src')).toBe('blob:preview');
		expect(dialog().querySelector('[data-before-after] img[alt="Before"]')?.getAttribute('src')).toBe(still.url);
	});

	it('waits 300 ms after the last change before asking again', async () => {
		open({ tool: COLOR_KEY, items: [still] });
		await settle();
		previewMediaTool.mockClear();

		const slider = param('tolerance').querySelector('input[type="range"]') as HTMLInputElement;
		slider.value = '60';
		slider.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await vi.advanceTimersByTimeAsync(299);
		expect(previewMediaTool).not.toHaveBeenCalled();
		expect(dialog().querySelector('[data-preview-loading]')).not.toBeNull();

		await vi.advanceTimersByTimeAsync(1);
		expect(previewMediaTool).toHaveBeenCalledTimes(1);
		expect(previewMediaTool.mock.calls[0][0].params.tolerance).toBe(60);
	});

	it('does not ask again for a change to a param that is hidden', async () => {
		open({ tool: COLOR_KEY, items: [still] });
		await settle();
		previewMediaTool.mockClear();
		await vi.advanceTimersByTimeAsync(1000);
		expect(previewMediaTool).not.toHaveBeenCalled();
	});

	it('says so when the preview cannot be rendered, and still lets the edit be applied', async () => {
		previewMediaTool.mockRejectedValue(new Error('boom'));
		open({ tool: COLOR_KEY, items: [still] });
		await settle();

		expect(dialog().textContent).toContain('The preview could not be rendered');
		expect(buttonByText('Apply')!.disabled).toBe(false);
	});

	it('gives the preview a split control and labels both sides', async () => {
		open({ tool: COLOR_KEY, items: [still] });
		await settle();
		expect(dialog().querySelector('input[aria-label="Before and after split"]')).not.toBeNull();
		expect(dialog().querySelector('[data-before-after]')?.textContent).toContain('Before');
		expect(dialog().querySelector('[data-before-after]')?.textContent).toContain('After');
	});

	it('draws no preview pane and asks for no preview for a tool that declares none', async () => {
		open({ tool: SPEED });
		await settle();
		expect(dialog().querySelector('[data-preview]')).toBeNull();
		expect(previewMediaTool).not.toHaveBeenCalled();
	});

	it('asks for no preview while an input is invalid', async () => {
		open({ tool: FIT_CANVAS, items: [still] });
		await settle();
		previewMediaTool.mockClear();

		const width = param('width').querySelector('input') as HTMLInputElement;
		width.value = '0';
		width.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await vi.advanceTimersByTimeAsync(1000);

		expect(previewMediaTool).not.toHaveBeenCalled();
	});

	it('drops the request waiting out its delay when the params turn invalid, and keeps the image shown', async () => {
		open({ tool: FIT_CANVAS, items: [still] });
		await settle();
		expect(afterImage()?.getAttribute('src')).toBe('blob:preview');
		previewMediaTool.mockClear();

		const width = param('width').querySelector('input') as HTMLInputElement;
		width.value = '800';
		width.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await vi.advanceTimersByTimeAsync(100);
		expect(dialog().querySelector('[data-preview-loading]')).not.toBeNull();
		width.value = '0';
		width.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await vi.advanceTimersByTimeAsync(1000);

		expect(previewMediaTool).not.toHaveBeenCalled();
		expect(dialog().querySelector('[data-preview-loading]')).toBeNull();
		expect(afterImage()?.getAttribute('src')).toBe('blob:preview');
		expect(URL.revokeObjectURL).not.toHaveBeenCalled();
	});

	it('aborts the request in flight when the params turn invalid, and shows nothing stale', async () => {
		const signals: AbortSignal[] = [];
		previewMediaTool.mockImplementation((_body: unknown, signal: AbortSignal) => {
			signals.push(signal);
			return new Promise(() => undefined);
		});
		open({ tool: FIT_CANVAS, items: [still] });
		await settle();
		expect(signals).toHaveLength(1);
		expect(signals[0].aborted).toBe(false);

		const width = param('width').querySelector('input') as HTMLInputElement;
		width.value = '0';
		width.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await settle();

		expect(signals[0].aborted).toBe(true);
		expect(dialog().querySelector('[data-preview-loading]')).toBeNull();
	});

	it('asks again once the params are valid again', async () => {
		open({ tool: FIT_CANVAS, items: [still] });
		await settle();
		const width = param('width').querySelector('input') as HTMLInputElement;
		width.value = '0';
		width.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await settle();
		previewMediaTool.mockClear();

		width.value = '640';
		width.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await vi.advanceTimersByTimeAsync(300);

		expect(previewMediaTool).toHaveBeenCalledTimes(1);
		expect(previewMediaTool.mock.calls[0][0].params.width).toBe(640);
	});

	it('frees the preview image and stops its timers when the dialog goes away', async () => {
		const view = open({ tool: COLOR_KEY, items: [still] });
		await settle();
		const slider = param('tolerance').querySelector('input[type="range"]') as HTMLInputElement;
		slider.value = '70';
		slider.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(vi.getTimerCount()).toBeGreaterThan(0);

		view.destroy();

		expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview');
		await vi.advanceTimersByTimeAsync(2000);
		expect(previewMediaTool).toHaveBeenCalledTimes(1);
	});

	it('draws the preview of a video from its first frame', async () => {
		open({ tool: ROTATE, items: [clip] });
		await settle();
		expect(dialog().querySelector('[data-before-after] video')).not.toBeNull();
		expect(previewMediaTool.mock.calls[0][0].input).toEqual({ item_id: 'row-1' });
	});
});

describe('joining clips', () => {
	const second = { ...clip, id: 'row-3', filename: 'b.mp4' };
	const third = { ...clip, id: 'row-4', filename: 'c.mp4' };

	function clipNames(): string[] {
		return Array.from(dialog().querySelectorAll('[data-clip] .truncate.text-fg')).map((el) => el.textContent!.trim());
	}

	it('lists the clips in order with their count', async () => {
		open({ tool: CONCAT, items: [clip, second, third] });
		await settle();
		expect(clipNames()).toEqual(['clip.mp4', 'b.mp4', 'c.mp4']);
		expect(dialog().querySelector('[data-clip-count]')?.textContent).toBe('3');
		expect(dialog().textContent).toContain('Joins 2 to 8 clips.');
		expect(dialog().querySelector('[data-tool-subject]')?.textContent?.trim()).toBe('3 clips');
	});

	it('moves a clip with Alt and the arrow keys and sends the inputs in that order', async () => {
		open({ tool: CONCAT, items: [clip, second, third] });
		await settle();

		const grip = dialog().querySelector('[data-clip="0"] button') as HTMLButtonElement;
		grip.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', altKey: true, bubbles: true }));
		await settle();
		expect(clipNames()).toEqual(['b.mp4', 'clip.mp4', 'c.mp4']);

		buttonByText('Join')!.click();
		await settle();
		expect(startMediaToolRun.mock.calls[0][0].inputs).toEqual([{ item_id: 'row-3' }, { item_id: 'row-1' }, { item_id: 'row-4' }]);
		expect(startMediaToolRun.mock.calls[0][0].steps).toEqual([
			{ tool: 'concat', params: { overlap: 'cut', keep_audio: true } }
		]);
	});

	it('removes a clip, and will not go below the minimum', async () => {
		open({ tool: CONCAT, items: [clip, second, third] });
		await settle();

		(dialog().querySelector('[aria-label="Remove b.mp4"]') as HTMLButtonElement).click();
		await settle();
		expect(clipNames()).toEqual(['clip.mp4', 'c.mp4']);

		for (const button of Array.from(dialog().querySelectorAll('[data-clip] button[aria-label^="Remove"]'))) {
			expect((button as HTMLButtonElement).disabled).toBe(true);
		}
	});

	it('works out the joined length and takes the crossfade off it', async () => {
		open({ tool: CONCAT, items: [clip, second] });
		await settle();
		expect(dialog().querySelector('[data-tool-readout]')?.textContent).toContain('1 video from 2 clips · 00:16.0');

		(Array.from(param('overlap').querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Crossfade') as HTMLButtonElement).click();
		await settle();
		const frames = param('overlap_frames').querySelector('input[type="range"]') as HTMLInputElement;
		frames.value = '24';
		frames.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(dialog().querySelector('[data-tool-readout]')?.textContent).toContain('00:15.0');
	});

	it('will not join fewer clips than the tool needs', async () => {
		open({ tool: CONCAT, items: [clip] });
		await settle();
		expect(buttonByText('Join')!.disabled).toBe(true);
		expect(dialog().querySelector('[data-tool-blocked]')?.textContent).toBe('Select 2 to 8 items');
	});
});

describe('keeping to the design rules', () => {
	it('uses no text below 12px and no native title attributes', async () => {
		open({ tool: COLOR_KEY, items: [still] });
		await settle();
		const html = document.body.innerHTML;
		expect(html).not.toMatch(/text-(2xs|xs)\b/);
		expect(html).not.toMatch(/<[a-z]+[^>]*\stitle=/);
		expect(html).not.toMatch(/font-(bold|extrabold|black)\b/);
	});

	it('has no blue button and no pill button', async () => {
		open({ tool: SPEED });
		await settle();
		const buttons = Array.from(document.body.querySelectorAll('button'));
		for (const button of buttons) {
			expect(button.className).not.toMatch(/rounded-full/);
			expect(button.className).not.toMatch(/bg-signal(?!\/)/);
		}
	});
});

describe('the catalog the dialog was opened from', () => {
	it('does not need the catalog to be loaded to open', async () => {
		open({ tool: SPEED });
		await settle();
		expect(toolById('speed')).toBeNull();
		expect(dialog().querySelector('[data-tool-dialog="speed"]')).not.toBeNull();
	});
});
