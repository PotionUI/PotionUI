import { afterEach, describe, expect, it } from 'vitest';
import type { EditorRun } from '$lib/services/api/editorTasks';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import { ensureCoreImageEditor } from '../coreEditor';
import { geometryKey } from '../mask';
import { inputSizes, outputSize, runSteps, sizeAfter } from '../payload';
import { imageRowInfo, stepOutOfDate } from '../stepKinds';
import { busyLabel, generateDraft, generateStateOf, generateStepKind, readGenerateParams, summaryOf, withRun, type GenerateParams } from './generateStep';
import { runViews } from './runEngine';
import { pictureKey } from './upscale';

ensureCoreImageEditor();

const SOURCE = { width: 1024, height: 768 };

function params(over: Partial<GenerateParams> = {}): GenerateParams {
	return {
		v: 2,
		task: 'upscale',
		prompt: '',
		setup: { preset_id: 'up', mode: 'editor:upscale', form_data: {}, source: { kind: 'defaults', id: null, name: null }, label: '' },
		area: { region: null, size: null },
		candidates: 1,
		results: [{ patch: { item_id: 'patch-1' }, url: '/patch-1.png', seed: 1, size: { width: 4096, height: 3072 } }],
		chosen: 0,
		seed: null,
		rect: null,
		soft: false,
		run: { id: 'er_up', status: 'done', error: null },
		basis: { geometry: pictureKey([]), size: SOURCE },
		options: { scale: 4 },
		...over
	};
}

function stepOf(p: GenerateParams, id = 's1', hidden = false): EditStep {
	return { ...generateDraft(p, 'Upscale', 'mode-upscale'), id, hidden };
}

afterEach(() => runViews.set({}));

describe('the upscale step', () => {
	it('sits in the geometry lane from the moment it starts', () => {
		expect(generateDraft(params({ results: [], run: { id: 'x', status: 'queued', error: null } }), 'Upscale', 'i').lane).toBe('geometry');
		expect(generateDraft(params({ task: 'edit_region', results: [] }), 'Edit', 'i').lane).toBe('paint');
	});

	it('replaces the picture with the patch at its own size', () => {
		const step = stepOf(params());
		expect(generateStepKind.payload(step.params as GenerateParams, {} as never)).toEqual([{ tool: 'place_patch', params: { patch: { item_id: 'patch-1' }, replace: true } }]);
		expect(sizeAfter(step, SOURCE)).toEqual({ width: 4096, height: 3072 });
		expect(outputSize([step], SOURCE)).toEqual({ width: 4096, height: 3072 });
		expect(runSteps([step], { source: SOURCE })).toEqual([{ tool: 'place_patch', params: { patch: { item_id: 'patch-1' }, replace: true } }]);
	});

	it('hands the next steps the new size as their input', () => {
		const step = stepOf(params());
		const crop = { id: 's2', tool: 'resize', icon: 'x', label: 'Resize', summary: '', params: { width: 100, height: 100, lock: true }, hidden: false };
		expect(inputSizes([step, crop], SOURCE)[1]).toEqual({ width: 4096, height: 3072 });
	});

	it('changes nothing while it runs, when it failed, or when hidden', () => {
		const running = stepOf(params({ results: [], run: { id: 'er_up', status: 'running', error: null } }));
		expect(sizeAfter(running, SOURCE)).toEqual(SOURCE);
		expect(runSteps([running], { source: SOURCE })).toEqual([{ tool: 'place_patch', params: {}, hidden: true }]);
		const hidden = stepOf(params(), 's1', true);
		expect(outputSize([hidden], SOURCE)).toEqual(SOURCE);
		expect(runSteps([hidden], { source: SOURCE })[0]).toMatchObject({ tool: 'place_patch', hidden: true });
	});

	it('is out of date when anything beneath it changes, and then gives the size back', () => {
		const beneath: EditStep[] = [{ id: 's0', tool: 'resize', icon: 'x', label: 'Resize', summary: '', params: { width: 1024, height: 768, lock: true }, hidden: false }];
		const step = stepOf(params({ basis: { geometry: pictureKey(beneath), size: SOURCE } }));
		expect(stepOutOfDate(step, beneath)).toBe(false);
		expect(sizeAfter(step, SOURCE, beneath)).toEqual({ width: 4096, height: 3072 });
		const edited: EditStep[] = [{ ...beneath[0], params: { width: 800, height: 600, lock: true } }];
		expect(stepOutOfDate(step, edited)).toBe(true);
		expect(sizeAfter(step, SOURCE, edited)).toEqual(SOURCE);
		const painted: EditStep[] = [...beneath, { id: 's9', tool: 'paint', icon: 'x', label: 'Paint', summary: '', params: { layer: 'l', url: '/l.png', layers: 1 }, hidden: false }];
		expect(stepOutOfDate(step, painted)).toBe(true);
		expect(imageRowInfo(step, painted)?.state).toMatchObject({ kind: 'skipped', tone: 'warning' });
	});

	it('counts in the geometry only once it has landed', () => {
		const landed = stepOf(params());
		const running = stepOf(params({ results: [], run: { id: 'x', status: 'running', error: null } }));
		expect(geometryKey([running])).toBe('[]');
		expect(geometryKey([landed])).toContain('4096');
		expect(geometryKey([landed])).not.toContain('"run"');
	});

	it('describes itself on the row', () => {
		expect(summaryOf(params())).toBe('4× · 4096×3072');
		expect(summaryOf(params({ results: [], run: { id: 'x', status: 'running', error: null } }))).toBe('4×');
		expect(summaryOf(params({ options: { target: { width: 3000, height: 2000 } } }))).toBe('Custom size · 4096×3072');
	});

	it('keeps the size from the run on each result', () => {
		const run = { id: 'er_up', task: 'upscale', status: 'done', candidates: [{ patch: { item_id: 'p' }, url: '/p.png', seed: 2, size: { width: 800, height: 600 } }] } as unknown as EditorRun;
		const next = withRun(params({ results: [] }), run);
		expect(next.results[0].size).toEqual({ width: 800, height: 600 });
		expect(readGenerateParams(next)?.results[0].size).toEqual({ width: 800, height: 600 });
	});
});

describe('progress by parts', () => {
	it('names the part being worked on only when there is more than one', () => {
		expect(busyLabel('upscale', null)).toBe('Upscaling');
		expect(busyLabel('upscale', { done: 0, total: 1 })).toBe('Upscaling');
		expect(busyLabel('upscale', { done: 2, total: 6 })).toBe('Upscaling 3 of 6');
		expect(busyLabel('upscale', { done: 6, total: 6 })).toBe('Upscaling 6 of 6');
		expect(busyLabel('edit_region', { done: 0, total: 2 })).toBe('Generating 1 of 2');
		expect(busyLabel('edit_region', null)).toBe('Generating');
	});

	it('shows the step as busy with a bounded percentage', () => {
		const p = params({ results: [], run: { id: 'er_up', status: 'running', error: null } });
		runViews.set({ er_up: { id: 'er_up', stepId: 's1', status: 'running', progress: 0.5, preview: null, error: null, run: { parts: { done: 1, total: 4 } } as EditorRun } });
		expect(generateStateOf(p)).toEqual({ kind: 'busy', label: 'Upscaling 2 of 4', progress: 50 });
	});
});
