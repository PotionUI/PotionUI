import { describe, expect, it } from 'vitest';
import { EMPTY_IMAGE_PLAN } from '$lib/media/editors/imageEditPlan';
import { apply, beginEdit, createStack, move, canMove, toggleHidden, appliedSteps, type EditStackState, type EditStep } from '$lib/tool-tabs/frame/editStack';
import {
	PayloadError,
	inputRef,
	sizeAfter,
	inputSizes,
	outputSize,
	payloadGaps,
	replaceOption,
	runBody,
	runSteps,
	sourceOrigin
} from './payload';
import {
	IMAGE_PINNED_LANES,
	adjustDraft,
	catalogDraft,
	cropDraft,
	editorCatalogTools,
	imageRail,
	lookDraft,
	paintDraft,
	resizeDraft,
	toolEffectDraft
} from './steps';
import { imageEffects } from './shaderEffect';
import { EFFECT_CATALOG, LOOK_TOOL } from '../../../../tests/component/fixtures/effectCatalog';
import type { ToolDTO } from '$lib/media-tools/types';

const SOURCE = { width: 1024, height: 1024 };

function push(state: EditStackState<unknown>, draft: Parameters<typeof beginEdit>[1]): EditStackState<unknown> {
	return apply(beginEdit(state, draft));
}

function crop43(): Parameters<typeof beginEdit>[1] {
	return cropDraft(
		{ plan: { ...EMPTY_IMAGE_PLAN, rect: { x: 0.0625, y: 0.171875, width: 0.875, height: 0.65625 } }, aspect: '4:3' },
		'4:3',
		{ width: 896, height: 672 }
	);
}

function tool(id: string, overrides: Partial<ToolDTO> = {}): ToolDTO {
	return {
		id,
		label: id,
		icon: 'extension',
		accepts: ['image'],
		produces: 'same',
		cardinality: 'one_to_one',
		params: [],
		requires: {},
		selection: null,
		scopes: ['editor'],
		editor: null,
		preview: false,
		component: null,
		runnable: true,
		source: 'plugin',
		last_params: null,
		...overrides
	};
}

describe('stack to run steps', () => {
	it('maps a crop, a resize, a paint layer and a baked look in stack order', () => {
		let stack = createStack();
		stack = push(stack, crop43());
		stack = push(stack, resizeDraft({ width: 1536, height: 1152, lock: true }));
		stack = push(stack, paintDraft({ layer: 'art-paint', url: '/a/paint.png', layers: 2 }, { width: 1536, height: 1152 }));
		stack = push(stack, lookDraft({ kind: 'look', filter: 'reel', name: 'Reel', intensity: 60 }));
		const steps = appliedSteps(stack);
		const look = steps[3];

		expect(runSteps(steps, { source: SOURCE, baked: { [look.id]: 'art-look' } })).toEqual([
			{ tool: 'crop', params: { x: 64, y: 176, width: 896, height: 672 } },
			{ tool: 'resize', params: { width: 1536 } },
			{ tool: 'paint_layer', params: { layer: { item_id: 'art-paint' } } },
			{ tool: 'paint_layer', params: { layer: { item_id: 'art-look' } } }
		]);
	});

	it('expands rotate and flip inside one crop step before the crop itself', () => {
		const stack = push(
			createStack(),
			cropDraft(
				{ plan: { ...EMPTY_IMAGE_PLAN, rotation: 90, flipHorizontal: true, rect: { x: 0, y: 0, width: 0.5, height: 1 } }, aspect: 'free' },
				'Free',
				{ width: 512, height: 1024 }
			)
		);
		expect(runSteps(appliedSteps(stack), { source: { width: 1024, height: 512 } })).toEqual([
			{ tool: 'rotate', params: { degrees: 90 } },
			{ tool: 'flip', params: { axis: 'horizontal' } },
			{ tool: 'crop', params: { x: 0, y: 0, width: 256, height: 1024 } }
		]);
	});

	it('keeps hidden steps with hidden set and leaves them out of the size chain', () => {
		let stack = push(createStack(), resizeDraft({ width: 512, height: 512, lock: false }));
		stack = push(stack, crop43());
		stack = toggleHidden(stack, stack.steps[0].id);
		const steps = appliedSteps(stack);

		expect(inputSizes(steps, SOURCE)).toEqual([SOURCE, SOURCE]);
		expect(runSteps(steps, { source: SOURCE })).toEqual([
			{ tool: 'resize', params: { width: 512, height: 512 }, hidden: true },
			{ tool: 'crop', params: { x: 64, y: 176, width: 896, height: 672 } }
		]);
		expect(outputSize(steps, SOURCE)).toEqual({ width: 896, height: 672 });
	});

	it('sends a hidden look as a hidden step that needs no render', () => {
		let stack = push(createStack(), lookDraft({ kind: 'look', filter: 'noir', name: 'Noir', intensity: 100 }));
		stack = toggleHidden(stack, stack.steps[0].id);
		expect(payloadGaps(appliedSteps(stack), { source: SOURCE })).toEqual([]);
		expect(runSteps(appliedSteps(stack), { source: SOURCE })).toEqual([{ tool: 'paint_layer', params: {}, hidden: true }]);
	});

	it('sends a hidden crop after an unknown size as a hidden placeholder without throwing', () => {
		let stack = push(createStack(), catalogDraft('subject', { margin: 0 }));
		stack = push(stack, crop43());
		stack = toggleHidden(stack, stack.steps[1].id);
		expect(runSteps(appliedSteps(stack), { source: SOURCE })).toEqual([
			{ tool: 'crop_subject', params: { margin: 0 } },
			{ tool: 'crop', params: {}, hidden: true }
		]);
	});

	it('takes the canvas size from its width and height, and keeps the input size when they are missing', () => {
		const canvas = (values: Record<string, unknown>) => apply(beginEdit(createStack(), catalogDraft('canvas', values))).steps[0];
		expect(sizeAfter(canvas({ width: 1200, height: 800 }), SOURCE)).toEqual({ width: 1200, height: 800 });
		expect(sizeAfter(canvas({ width: 0, height: 800 }), SOURCE)).toEqual(SOURCE);
		expect(sizeAfter(canvas({}), null)).toBeNull();
		expect(sizeAfter(apply(beginEdit(createStack(), catalogDraft('subject', {}))).steps[0], SOURCE)).toBeNull();
	});

	it('maps catalog tools by id and keeps their values', () => {
		let stack = push(createStack(), catalogDraft('canvas', { width: 1200, height: 800, anchor: 'center' }));
		stack = push(stack, catalogDraft('colorKey', { key_mode: 'auto', tolerance: 25 }));
		stack = push(stack, catalogDraft('subject', { margin: 8 }));
		stack = push(stack, catalogDraft('pixel.sort', { amount: 3 }, [tool('pixel.sort')]));
		expect(runSteps(appliedSteps(stack), { source: SOURCE }).map((step) => [step.tool, step.params])).toEqual([
			['fit_canvas', { width: 1200, height: 800, anchor: 'center' }],
			['color_key', { key_mode: 'auto', tolerance: 25 }],
			['crop_subject', { margin: 8 }],
			['pixel.sort', { amount: 3 }]
		]);
	});

	it('reports a look that is not rendered yet and a crop after an unknown size', () => {
		let stack = push(createStack(), catalogDraft('subject', { margin: 0 }));
		stack = push(stack, crop43());
		stack = push(stack, adjustDraft({ kind: 'adjust', op: 'grain', name: 'Grain', values: { amount: 30, size: 1, seed: 7 } }));
		const steps = appliedSteps(stack);
		expect(payloadGaps(steps, { source: SOURCE })).toEqual([
			{ kind: 'size', step: steps[1].id },
			{ kind: 'bake', step: steps[2].id }
		]);
		expect(() => runSteps(steps, { source: SOURCE })).toThrow(PayloadError);
		const known = runSteps(steps, {
			source: SOURCE,
			sizes: { [steps[1].id]: { width: 640, height: 640 } },
			baked: { [steps[2].id]: 'art-grain' }
		});
		expect(known[1]).toEqual({ tool: 'crop', params: { x: 40, y: 110, width: 560, height: 420 } });
		expect(known[2]).toEqual({ tool: 'paint_layer', params: { layer: { item_id: 'art-grain' } } });
	});

	it('builds the run body for each save mode', () => {
		const steps = [{ tool: 'resize', params: { width: 10 } }];
		expect(runBody(steps, { item_id: 'lib-1' }, 'new')).toEqual({ steps, inputs: [{ item_id: 'lib-1' }], output: { mode: 'new' } });
		expect(runBody(steps, { generation_file_id: '7' }, 'update', 'copy-1').output).toEqual({ mode: 'update', target_item_id: 'copy-1' });
		expect(runBody(steps, { item_id: 'lib-1' }, 'replace').output).toEqual({ mode: 'replace' });
		expect(runBody(steps, { item_id: 'lib-1' }, 'new', null, 'col-9').output).toEqual({ mode: 'new', collection_id: 'col-9' });
	});
});

describe('effects on images', () => {
	const vignette = { kind: 'effect' as const, tool: 'vignette', name: 'Vignette', values: { amount: 60, radius: 40 } };

	it('sends an image effect as its own tool step that needs no render, hidden or not', () => {
		const state = push(push(createStack(), cropDraft({ plan: EMPTY_IMAGE_PLAN, aspect: 'free' }, 'Free', SOURCE)), toolEffectDraft(vignette));
		const steps = appliedSteps(state);
		expect(payloadGaps(steps, { source: SOURCE })).toEqual([]);
		expect(runSteps(steps, { source: SOURCE }).at(-1)).toEqual({ tool: 'vignette', params: { amount: 60, radius: 40 } });
		const hidden = appliedSteps(toggleHidden(state, steps[1].id));
		expect(runSteps(hidden, { source: SOURCE }).at(-1)).toEqual({ tool: 'vignette', params: { amount: 60, radius: 40 }, hidden: true });
		expect(sizeAfter(steps[1], SOURCE)).toEqual(SOURCE);
	});

	it('names the step after the effect and sums up its values with their labels', () => {
		const draft = toolEffectDraft({ kind: 'effect', tool: 'film_grain', name: 'Film grain', values: { amount: 40, size: 1.5 } }, EFFECT_CATALOG);
		expect([draft.tool, draft.label, draft.summary]).toEqual(['effects', 'Film grain', 'Amount 40 · Size 1.5']);
	});

	it('offers image effects with a shader and leaves looks to the Looks tab', () => {
		expect(imageEffects([...EFFECT_CATALOG, LOOK_TOOL]).map((effect) => effect.id).sort()).toEqual(['film_grain', 'retro-tape.scanlines']);
	});
});

describe('sources', () => {
	it('picks the run input and the replace rule from the source', () => {
		expect(inputRef({ path: 'p', item_id: 'lib-1' })).toEqual({ item_id: 'lib-1' });
		expect(inputRef({ path: 'p', generation_file_id: '42' })).toEqual({ generation_file_id: '42' });
		expect(inputRef({ path: 'p', artifact_id: 'art-1', item_id: 'x' })).toEqual({ item_id: 'art-1' });
		expect(inputRef({ path: 'p' })).toBeNull();
		expect(replaceOption(sourceOrigin({ path: 'p', item_id: 'lib-1' }))).toEqual({ allowed: true });
		expect(replaceOption(sourceOrigin({ path: 'p', generation_file_id: '1' }))).toEqual({
			allowed: false,
			reason: 'Files from History stay as generated'
		});
		expect(replaceOption(sourceOrigin({ path: 'p', artifact_id: 'a' })).allowed).toBe(false);
	});
});

describe('paint pinning', () => {
	function paintStack(): EditStackState<unknown> {
		let stack = push(createStack(), crop43());
		stack = push(stack, paintDraft({ layer: 'art-1', url: '/p.png', layers: 1 }, { width: 896, height: 672 }));
		stack = push(stack, lookDraft({ kind: 'look', filter: 'reel', name: 'Reel', intensity: 60 }));
		return push(stack, resizeDraft({ width: 448, height: 336, lock: true }));
	}

	it('keeps paint on the same side of every geometry step', () => {
		const stack = paintStack();
		const [crop, paint, look, resize] = stack.steps;
		expect(canMove(stack, paint.id, 0, IMAGE_PINNED_LANES)).toBe(false);
		expect(canMove(stack, crop.id, 1, IMAGE_PINNED_LANES)).toBe(false);
		expect(canMove(stack, resize.id, 1, IMAGE_PINNED_LANES)).toBe(false);
		expect(move(stack, paint.id, 0, IMAGE_PINNED_LANES)).toBe(stack);
	});

	it('lets a look move across the paint step', () => {
		const stack = paintStack();
		const [, paint, look] = stack.steps;
		const moved = move(stack, look.id, 1, IMAGE_PINNED_LANES);
		expect(moved.steps.map((step: EditStep) => step.id).slice(1, 3)).toEqual([look.id, paint.id]);
	});

	it('lets geometry steps reorder among themselves when no paint sits between them', () => {
		let stack = push(createStack(), crop43());
		stack = push(stack, resizeDraft({ width: 448, height: 336, lock: true }));
		stack = push(stack, paintDraft({ layer: 'art-1', url: '/p.png', layers: 3 }, { width: 448, height: 336 }));
		expect(canMove(stack, stack.steps[1].id, 0, IMAGE_PINNED_LANES)).toBe(true);
		expect(stack.steps[2].label).toBe('Paint · 3 layers');
	});
});

describe('rail', () => {
	it('groups the core tools and appends image tools the catalog marks for the editor', () => {
		const catalog = [
			tool('paint_layer'),
			tool('fit_canvas', { scopes: ['library', 'history', 'field'] }),
			tool('pixel.sort', { label: 'Pixel sort', icon: 'sliders' }),
			tool('audio.only', { accepts: ['audio'] }),
			tool('lib.only', { scopes: ['library'] }),
			tool('many.in', { cardinality: 'many_to_one' })
		];
		expect(editorCatalogTools(catalog).map((entry) => entry.id)).toEqual(['pixel.sort']);
		const rail = imageRail(catalog);
		expect(rail.map((group) => [group.label, group.tools.map((entry) => entry.label)])).toEqual([
			['Shape', ['Crop', 'Resize', 'Canvas', 'Slice']],
			['Paint', ['Paint', 'Mask']],
			['Look', ['Looks', 'Adjust']],
			['Tools', ['Color key', 'Subject', 'Pixel sort']]
		]);
	});
});
