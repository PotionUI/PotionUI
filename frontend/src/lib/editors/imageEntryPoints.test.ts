import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/utils/logger', () => ({ logger: { error: vi.fn(), warn: vi.fn(), debug: vi.fn() } }));
vi.mock('$lib/services/api/mediaTools', () => ({
	listMediaTools: async () => [],
	uploadMediaToolArtifact: async () => ({ id: 'a', url: '/a.png' }),
	previewFrame: async () => ({ blob: new Blob(['x']), cached: false })
}));
vi.mock('$lib/paint/loadPicked', () => ({
	canvasFromUrl: async () => fakeCanvas(8, 6),
	canvasFromBlob: async () => fakeCanvas(30, 20)
}));
vi.mock('$lib/paint/flatten', () => ({ canvasToPngFile: async () => new File(['png'], 'look.png') }));
vi.mock('$lib/plans/uploadLimit', () => ({ blockedUploadMessage: () => null }));

interface FakeCanvas {
	width: number;
	height: number;
	marks: string[];
	getContext: () => { drawImage: () => void; imageSmoothingQuality: string };
}

function fakeCanvas(width = 8, height = 6): FakeCanvas {
	return { width, height, marks: [], getContext: () => ({ drawImage: () => undefined, imageSmoothingQuality: 'low' }) };
}

import { logger } from '$lib/utils/logger';
import { mediaToolCatalogRevision } from '$lib/media-tools/catalog';
import { apply, appliedSteps, beginEdit, createStack, deserialize, serialize, toggleHidden, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { emptyImageState, readImageState, stackSignature, writeImageState } from '$lib/tool-tabs/image/state';
import { applyClientStep, isClientStep, stepsKey, ImageRenderer, type Rendered } from '$lib/tool-tabs/image/render';
import { inputSizes, outputSize, runSteps, sizeAfter } from '$lib/tool-tabs/image/payload';
import { imageRowInfo, isStepUnavailable } from '$lib/tool-tabs/image/stepKinds';
import { cropDraft, imageRail, isRailTool, resizeDraft, toolMeta } from '$lib/tool-tabs/image/steps';
import { EMPTY_IMAGE_PLAN } from '$lib/media/imageEditPlan';
import { markEditorExtensionsReady, resetEditorExtensionsReady } from './ready';
import { pluginOwner } from './registry';
import { resetFailures } from './guard';
import { railGroups, railTools } from './railTools';
import { stepKinds, type StepKind } from './stepKinds';

const SOURCE = { width: 1000, height: 800 };
const OWNER = pluginOwner('acme');

function halftone(overrides: Partial<StepKind<{ factor: number }>> = {}): StepKind<{ factor: number }> {
	return {
		id: 'acme.halftone',
		editor: 'image',
		label: 'Halftone',
		icon: 'extension',
		lane: 'paint',
		summary: (params) => `Factor ${params.factor}`,
		outputSize: (params, ctx) => (ctx.input ? { width: ctx.input.width / params.factor, height: ctx.input.height / params.factor } : null),
		payload: (params) => [{ tool: 'acme_halftone', params: { factor: params.factor } }],
		preview: async (frame, params) => {
			(frame.canvas as unknown as FakeCanvas).marks.push(`halftone ${params.factor}`);
			return frame;
		},
		...overrides
	};
}

function pluginDraft(tool = 'acme.halftone', params: unknown = { factor: 2 }) {
	return { tool, icon: 'extension', label: 'Halftone', summary: 'Factor 2', params, lane: 'paint' };
}

function push(state: EditStackState<unknown>, draft: Parameters<typeof beginEdit>[1]) {
	return apply(beginEdit(state, draft));
}

function rendered(): Rendered {
	return { canvas: fakeCanvas() as unknown as HTMLCanvasElement, size: { width: 8, height: 6 } };
}

const disposers: Array<() => void> = [];

beforeEach(() => {
	markEditorExtensionsReady();
	mediaToolCatalogRevision.set(1);
	resetFailures();
	vi.mocked(logger.error).mockClear();
});

afterEach(() => {
	disposers.splice(0).forEach((dispose) => dispose());
	vi.unstubAllGlobals();
});

describe('a registered step kind drives the Image editor entry points', () => {
	it('is used for the size walk, the payload, the preview decision and the step key', () => {
		disposers.push(stepKinds.register(halftone(), OWNER));
		const stack = push(push(createStack(), resizeDraft({ width: 800, height: 600, lock: false })), pluginDraft());
		const steps = appliedSteps(stack);
		expect(sizeAfter(steps[1], { width: 800, height: 600 })).toEqual({ width: 400, height: 300 });
		expect(outputSize(steps, SOURCE)).toEqual({ width: 400, height: 300 });
		expect(inputSizes(steps, SOURCE)).toEqual([SOURCE, { width: 800, height: 600 }]);
		expect(runSteps(steps, { source: SOURCE })).toEqual([
			{ tool: 'resize', params: { width: 800, height: 600 } },
			{ tool: 'acme_halftone', params: { factor: 2 } }
		]);
		expect(isClientStep(steps[1])).toBe(true);
		expect(stepsKey(steps)).toContain('acme.halftone');
	});

	it('sends a hidden step of a registered kind as hidden', () => {
		disposers.push(stepKinds.register(halftone(), OWNER));
		const stack = toggleHidden(push(createStack(), pluginDraft()), 's1');
		expect(runSteps(appliedSteps(stack), { source: SOURCE })).toEqual([{ tool: 'acme_halftone', params: { factor: 2 }, hidden: true }]);
	});

	it('draws a preview step through its kind and leaves a kind without preview to the server', async () => {
		disposers.push(stepKinds.register(halftone({ preview: undefined, id: 'acme.server' }), OWNER));
		disposers.push(stepKinds.register(halftone(), OWNER));
		const steps = appliedSteps(push(push(createStack(), pluginDraft('acme.server')), pluginDraft()));
		expect(isClientStep(steps[0])).toBe(false);
		const frame = rendered();
		const out = await applyClientStep(frame, steps[1], 1600);
		expect((out.canvas as unknown as FakeCanvas).marks).toEqual(['halftone 2']);
	});

	it('keeps rendering the other steps when a plugin preview throws, and logs once', async () => {
		disposers.push(
			stepKinds.register(
				halftone({
					preview: async () => {
						throw new Error('boom');
					}
				}),
				OWNER
			)
		);
		const steps = appliedSteps(push(createStack(), pluginDraft()));
		const frame = rendered();
		expect(await applyClientStep(frame, steps[0], 1600)).toBe(frame);
		expect(await applyClientStep(frame, steps[0], 1600)).toBe(frame);
		expect(logger.error).toHaveBeenCalledTimes(1);
	});

	it('survives a throwing outputSize, payload and summary from a plugin', () => {
		disposers.push(
			stepKinds.register(
				halftone({
					outputSize: () => {
						throw new Error('size');
					},
					payload: () => {
						throw new Error('payload');
					}
				}),
				OWNER
			)
		);
		const steps = appliedSteps(push(createStack(), pluginDraft()));
		expect(sizeAfter(steps[0], SOURCE)).toEqual(SOURCE);
		expect(runSteps(steps, { source: SOURCE })).toEqual([{ tool: 'acme.halftone', params: {}, hidden: true }]);
		expect(logger.error).toHaveBeenCalledTimes(1);
	});

	it('renders the other steps of a picture whose plugin preview throws', async () => {
		vi.stubGlobal('document', { createElement: () => fakeCanvas() });
		disposers.push(
			stepKinds.register(
				halftone({
					preview: async () => {
						throw new Error('boom');
					}
				}),
				OWNER
			)
		);
		const stack = push(push(createStack(), cropDraft({ plan: EMPTY_IMAGE_PLAN, aspect: 'free' }, 'Free', { width: 8, height: 6 })), pluginDraft());
		const renderer = new ImageRenderer({ url: '/s.png', size: { width: 8, height: 6 }, ref: { item_id: 'i' } });
		const result = await renderer.render(appliedSteps(stack));
		expect(result.size).toEqual({ width: 8, height: 6 });
	});

	it('shows a plugin rail tool and group in the rail data, after the core ones', () => {
		disposers.push(
			railGroups.register({ id: 'acme.stylize', editor: 'image', label: 'Stylize', order: 450 }, OWNER),
			railTools.register({ id: 'acme.halftone', editor: 'image', group: 'acme.stylize', label: 'Halftone', icon: 'extension' }, OWNER),
			railTools.register({ id: 'acme.extra', editor: 'image', group: 'tools', label: 'Extra', icon: 'extension' }, OWNER)
		);
		const rail = imageRail();
		expect(rail.map((group) => group.id)).toEqual(['generate', 'shape', 'paint', 'select', 'look', 'fx', 'looks', 'tools', 'acme.stylize']);
		expect(rail[7].tools.map((tool) => tool.id)).toEqual(['colorKey', 'subject', 'acme.extra']);
		expect(rail[8].tools).toEqual([{ id: 'acme.halftone', label: 'Halftone', icon: 'extension' }]);
		expect(isRailTool('acme.halftone')).toBe(true);
		expect(toolMeta('acme.halftone')).toMatchObject({ label: 'Halftone', icon: 'extension' });
	});

	it('every paint entry contributes an in-progress group to the Edits list and no other core entry does', () => {
		imageRail();
		const withGroup = railTools.list('image').filter((tool) => tool.children).map((tool) => tool.id).sort();
		expect(withGroup).toEqual(['paint.brush', 'paint.eraser', 'paint.fill', 'paint.lasso', 'paint.move', 'paint.pick', 'paint.rect']);
	});

	it('lets a plugin entry contribute its own in-progress group', () => {
		const component = (() => null) as never;
		disposers.push(railTools.register({ id: 'acme.sketch', editor: 'image', group: 'tools', label: 'Sketch', icon: 'extension', children: { component } }, OWNER));
		expect(railTools.get('image', 'acme.sketch')?.children).toEqual({ component });
	});

	it('keeps the core rail exactly as it was with nothing registered', () => {
		const ids = (group: string) => imageRail().find((entry) => entry.id === group)?.tools.map((tool) => tool.id);
		expect(imageRail().map((group) => [group.id, group.label, group.fold])).toEqual([
			['generate', 'Generate', 7],
			['shape', 'Transform', 6],
			['paint', 'Paint', 5],
			['select', 'Select and move', 3],
			['look', 'Light and colour', 4],
			['fx', 'Effects', 1],
			['looks', 'Looks', undefined],
			['tools', 'Cut out', 2]
		]);
		expect(ids('shape')).toEqual(['crop', 'resize', 'canvas', 'slice']);
		expect(ids('paint')).toEqual(['paint.brush', 'paint.eraser', 'paint.fill', 'paint.pick']);
		expect(ids('select')).toEqual(['paint.rect', 'paint.lasso', 'paint.move']);
		expect(ids('generate')).toEqual(['edit_region', 'remove_object', 'replace_background', 'expand']);
		expect(ids('look')).toEqual(['light', 'colour', 'split_tone', 'curves', 'lut']);
		expect(ids('fx')).toEqual(['film_grain', 'vignette', 'grayscale', 'invert']);
		expect(ids('looks')).toEqual(['effects']);
		expect(ids('tools')).toEqual(['colorKey', 'subject']);
	});
});

describe('a step whose kind is not registered', () => {
	const gone = () => push(push(createStack(), resizeDraft({ width: 800, height: 600, lock: false })), pluginDraft('acme.gone', { v: 1, secret: [1, 2, 3] }));

	it('survives serialize and deserialize, and the saved tab state, byte for byte', () => {
		const stack = gone();
		const before = JSON.stringify(serialize(stack));
		expect(JSON.stringify(serialize(deserialize(JSON.parse(before))))).toBe(before);
		const raw = writeImageState({ ...emptyImageState('crop'), stack });
		const wire = JSON.stringify(raw);
		expect(JSON.stringify(writeImageState(readImageState(JSON.parse(wire))))).toBe(wire);
		expect(stackSignature(readImageState(JSON.parse(wire)).stack)).toBe(stackSignature(stack));
		expect(appliedSteps(readImageState(JSON.parse(wire)).stack).map((step) => step.tool)).toEqual(['resize', 'acme.gone']);
	});

	it('is sent to the server as hidden, with the steps around it intact', () => {
		const steps = appliedSteps(gone());
		expect(runSteps(steps, { source: SOURCE })).toEqual([
			{ tool: 'resize', params: { width: 800, height: 600 } },
			{ tool: 'acme.gone', params: {}, hidden: true }
		]);
		expect(outputSize(steps, SOURCE)).toEqual({ width: 800, height: 600 });
	});

	it('is left out of the preview', () => {
		const steps = appliedSteps(gone());
		expect(stepsKey(steps)).toBe(stepsKey(steps.slice(0, 1)));
		expect(isClientStep(steps[1])).toBe(false);
	});

	it('shows as not available in the stack and cannot be reopened', () => {
		const step = appliedSteps(gone())[1];
		expect(isStepUnavailable(step)).toBe(true);
		expect(imageRowInfo(step)).toEqual({ state: { kind: 'skipped', tone: 'muted', label: 'Not available' }, locked: true });
	});

	it('shows as loading, and keeps its legacy payload, before the first plugin snapshot', () => {
		resetEditorExtensionsReady();
		const step = appliedSteps(gone())[1];
		expect(imageRowInfo(step)).toEqual({ state: { kind: 'busy', label: 'Loading' }, locked: true });
		expect(runSteps([step], { source: SOURCE })).toEqual([{ tool: 'acme.gone', params: {} }]);
	});

	it('becomes a normal step again when its kind registers later', () => {
		const stack = push(createStack(), pluginDraft());
		const step = appliedSteps(stack)[0];
		expect(runSteps([step], { source: SOURCE })).toEqual([{ tool: 'acme.halftone', params: {}, hidden: true }]);
		const dispose = stepKinds.register(halftone(), OWNER);
		disposers.push(dispose);
		expect(runSteps([step], { source: SOURCE })).toEqual([{ tool: 'acme_halftone', params: { factor: 2 } }]);
		expect(imageRowInfo(step)).toBeNull();
		dispose();
		expect(isStepUnavailable(step)).toBe(true);
	});

	it('is unavailable when its stored version is newer than the kind knows', () => {
		disposers.push(stepKinds.register(halftone({ version: 1 }), OWNER));
		const step = appliedSteps(push(createStack(), pluginDraft('acme.halftone', { v: 3, factor: 2 })))[0];
		expect(imageRowInfo(step)).toEqual({ state: { kind: 'skipped', tone: 'muted', label: 'Made with a newer version' }, locked: true });
	});

	it('migrates older params when read, without rewriting the stored step', () => {
		disposers.push(stepKinds.register(halftone({ version: 2, migrate: (_params, from) => ({ factor: from * 10 }) }), OWNER));
		const stack = push(createStack(), pluginDraft('acme.halftone', { v: 1, old: true }));
		const step = appliedSteps(stack)[0];
		expect(runSteps([step], { source: SOURCE })).toEqual([{ tool: 'acme_halftone', params: { factor: 10 } }]);
		expect(step.params).toEqual({ v: 1, old: true });
	});
});

describe('step state and row', () => {
	it('reports the state a kind gives and turns a failing plugin kind into a danger row', () => {
		disposers.push(stepKinds.register(halftone({ state: () => ({ kind: 'busy', label: 'Waiting' }) }), OWNER));
		const step = appliedSteps(push(createStack(), pluginDraft()))[0];
		expect(imageRowInfo(step)).toMatchObject({ state: { kind: 'busy', label: 'Waiting' }, locked: false });
		stepKinds.register(
			halftone({
				state: () => {
					throw new Error('boom');
				}
			}),
			OWNER
		);
		expect(imageRowInfo(step)).toBeNull();
	});
});

describe('runtime registrations follow their plugin', () => {
	it('end when the plugin leaves the enabled set, show the step as not available, and return with it', async () => {
		const { createHostEditorsApi, reconcileRuntimeEditorEntries } = await import('./pluginEntries');
		const editors = createHostEditorsApi();
		const off = editors.registerStepKind(halftone(), { pluginId: 'acme' });
		disposers.push(off);
		const step = appliedSteps(push(createStack(), pluginDraft()))[0];
		expect(imageRowInfo(step)).toBeNull();
		reconcileRuntimeEditorEntries(new Set());
		expect(stepKinds.get('image', 'acme.halftone')).toBeUndefined();
		expect(imageRowInfo(step)).toEqual({ state: { kind: 'skipped', tone: 'muted', label: 'Not available' }, locked: true });
		reconcileRuntimeEditorEntries(new Set(['acme']));
		expect(imageRowInfo(step)).toBeNull();
		off();
		reconcileRuntimeEditorEntries(new Set(['acme']));
		expect(stepKinds.get('image', 'acme.halftone')).toBeUndefined();
	});
});
