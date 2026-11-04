import { describe, it, expect } from 'vitest';
import type { GenerationTab } from '$lib/types/tabs';
import {
	SETTLE_MS,
	canEditStep,
	captureLoaded,
	changedKeys,
	editCount,
	isDirty,
	nodeFingerprint,
	observeLoaded,
	sameValue,
	stepIndex,
	stepOptions,
	stepOverrides,
	stepTabPatch,
	stepTitle
} from './stepEdit';
import { indexPresets } from './model';
import type { ElixirDefinition, ElixirNode } from './types';

const tab = (over: Record<string, unknown> = {}) =>
	({ formPublished: true, formData: { steps: 20, cfg: 4 }, prompt: 'a bottle', negativePrompt: '', ...over }) as unknown as GenerationTab;

const node = (over: Partial<ElixirNode> = {}): ElixirNode => ({
	id: 'n1',
	kind: 'preset',
	preset_id: 'wan',
	mode: 'i2v',
	overrides: {},
	session_mode: 'copy',
	session_snapshot: { prompt: 'a bottle', formData: { steps: 20, cfg: 4 } },
	...over
});

describe('sameValue', () => {
	it('treats every empty shape as equal', () => {
		for (const value of [undefined, null, '', [], {}]) expect(sameValue(value, undefined)).toBe(true);
	});

	it('compares non empty values structurally', () => {
		expect(sameValue({ a: [1] }, { a: [1] })).toBe(true);
		expect(sameValue('a', 'b')).toBe(false);
		expect(sameValue(0, undefined)).toBe(false);
	});
});

describe('dirty tracking', () => {
	it('is clean until the form is published', () => {
		expect(captureLoaded(tab({ formPublished: false }), 0)).toBeNull();
		expect(isDirty(tab(), null)).toBe(false);
	});

	it('is clean right after the baseline is captured', () => {
		const loaded = captureLoaded(tab(), 0);
		expect(editCount(tab(), loaded)).toBe(0);
	});

	it('counts each changed prompt field and form field', () => {
		const loaded = captureLoaded(tab(), 0);
		const edited = tab({ prompt: 'a vial', formData: { steps: 30, cfg: 4, seed: 7 } });
		expect(changedKeys(edited, loaded).sort()).toEqual(['formData.seed', 'formData.steps', 'prompt']);
		expect(editCount(edited, loaded)).toBe(3);
	});

	it('counts a prompt and its mirrored segments as one edit', () => {
		const loaded = captureLoaded(tab(), 0);
		const edited = tab({ prompt: 'a vial', promptSegments: [{ id: 's', content: 'a vial' }] });
		expect(changedKeys(edited, loaded)).toEqual(['prompt']);
	});

	it('goes clean again when an edit is reverted', () => {
		const loaded = captureLoaded(tab(), 0);
		expect(isDirty(tab({ prompt: 'x' }), loaded)).toBe(true);
		expect(isDirty(tab({ prompt: 'a bottle' }), loaded)).toBe(false);
	});

	it('re-captures inside the settle window so form normalisation is not an edit', () => {
		const first = captureLoaded(tab(), 100)!;
		const normalised = tab({ formData: { steps: 20, cfg: 4, width: 1280 } });
		const second = observeLoaded(first, normalised, 100 + SETTLE_MS - 1);
		expect(second?.capturedAt).toBe(100);
		expect(isDirty(normalised, second)).toBe(false);
	});

	it('keeps the baseline once the settle window has passed', () => {
		const first = captureLoaded(tab(), 0)!;
		const edited = tab({ formData: { steps: 99, cfg: 4 } });
		const second = observeLoaded(first, edited, SETTLE_MS + 1);
		expect(second).toBe(first);
		expect(isDirty(edited, second)).toBe(true);
	});
});

describe('stepOverrides', () => {
	it('writes only the changed form field on top of a copied session', () => {
		const loaded = captureLoaded(tab(), 0);
		const result = stepOverrides(node(), tab({ formData: { steps: 30, cfg: 4 } }), loaded);
		expect(result).toEqual({ formData: { steps: 30 } });
	});

	it('drops an override that goes back to the session value', () => {
		const withOverride = node({ overrides: { formData: { steps: 30 } } });
		const loaded = captureLoaded(tab({ formData: { steps: 30, cfg: 4 } }), 0);
		const result = stepOverrides(withOverride, tab({ formData: { steps: 20, cfg: 4 } }), loaded);
		expect(result).toEqual({});
	});

	it('keeps overrides the user did not touch', () => {
		const withOverride = node({ overrides: { prompt: 'kept', formData: { cfg: 5 } } });
		const loaded = captureLoaded(tab({ prompt: 'kept', formData: { steps: 20, cfg: 5 } }), 0);
		const result = stepOverrides(withOverride, tab({ prompt: 'kept', formData: { steps: 25, cfg: 5 } }), loaded);
		expect(result).toEqual({ prompt: 'kept', formData: { cfg: 5, steps: 25 } });
	});

	it('stores a changed prompt as an override', () => {
		const loaded = captureLoaded(tab(), 0);
		expect(stepOverrides(node(), tab({ prompt: 'a vial' }), loaded)).toEqual({ prompt: 'a vial' });
	});

	it('removes a prompt override that matches the session again', () => {
		const withOverride = node({ overrides: { prompt: 'a vial' } });
		const loaded = captureLoaded(tab({ prompt: 'a vial' }), 0);
		expect(stepOverrides(withOverride, tab({ prompt: 'a bottle' }), loaded)).toEqual({});
	});

	it('works for a node without a session', () => {
		const own = node({ session_mode: 'new', session_snapshot: null, overrides: {} });
		const loaded = captureLoaded(tab({ prompt: '', formData: {} }), 0);
		const result = stepOverrides(own, tab({ prompt: 'fresh', formData: { steps: 8 } }), loaded);
		expect(result).toEqual({ prompt: 'fresh', formData: { steps: 8 } });
	});

	it('returns the current overrides when nothing was captured', () => {
		expect(stepOverrides(node({ overrides: { prompt: 'p' } }), tab(), null)).toEqual({ prompt: 'p' });
	});
});

describe('stepTabPatch', () => {
	it('fills the tab from the settings and pins preset, mode and variant', () => {
		const patch = stepTabPatch(node({ form_name: 'Fast' }), { prompt: 'p', formData: { a: 1 } });
		expect(patch).toMatchObject({
			selectedPreset: 'wan',
			selectedMode: 'i2v',
			selectedVariant: 'Fast',
			selectedSessionId: null,
			prompt: 'p',
			formData: { a: 1 },
			formPublished: false
		});
	});

	it('takes the variant from the settings when the node has none', () => {
		expect(stepTabPatch(node(), { selectedVariant: 'Slow' }).selectedVariant).toBe('Slow');
	});

	it('copes with a step that has no settings at all', () => {
		expect(stepTabPatch(node(), null)).toMatchObject({ prompt: '', formData: {}, selectedVariant: null });
	});
});

describe('step list', () => {
	const definition: ElixirDefinition = {
		nodes: [
			{ id: 'files', kind: 'media' },
			node({ id: 'a', session_mode: 'follow' }),
			node({ id: 'b', preset_id: 'krea', mode: 't2i' }),
			node({ id: 'c', preset_id: null })
		] as ElixirNode[],
		edges: []
	};
	const index = indexPresets([
		{ preset_id: 'wan', mode: 'i2v', preset_name: 'Wan 2.2', mode_label: 'Image to video' },
		{ preset_id: 'krea', mode: 't2i', preset_name: 'Krea 2', mode_label: 'Text to image' }
	]);

	it('lists the preset steps in lane order and skips files and unfinished steps', () => {
		const steps = stepOptions(definition, ['files', 'a', 'b', 'c'], index);
		expect(steps.map((step) => step.id)).toEqual(['a', 'b']);
		expect(steps[0]).toMatchObject({ detail: 'Wan 2.2 · Image to video', follow: true });
		expect(steps[1].follow).toBe(false);
	});

	it('finds a step position', () => {
		const steps = stepOptions(definition, ['a', 'b'], index);
		expect(stepIndex(steps, 'b')).toBe(1);
		expect(stepIndex(steps, 'zzz')).toBe(-1);
	});

	it('knows which nodes can be opened', () => {
		expect(canEditStep(node())).toBe(true);
		expect(canEditStep(node({ preset_id: null }))).toBe(false);
		expect(canEditStep({ id: 'm', kind: 'media' } as ElixirNode)).toBe(false);
		expect(canEditStep(null)).toBe(false);
	});
});

describe('titles and fingerprints', () => {
	it('joins elixir and step in the tab title', () => {
		expect(stepTitle('Potion study', 'Orbit left')).toBe('Potion study › Orbit left');
		expect(stepTitle('Potion study', null)).toBe('Potion study');
	});

	it('ignores position and title when fingerprinting a node', () => {
		const base = node();
		const moved = { ...base, position: { x: 4, y: 9 }, title: 'Renamed' };
		expect(nodeFingerprint(moved)).toBe(nodeFingerprint(base));
		expect(nodeFingerprint({ ...base, overrides: { prompt: 'x' } })).not.toBe(nodeFingerprint(base));
	});
});
