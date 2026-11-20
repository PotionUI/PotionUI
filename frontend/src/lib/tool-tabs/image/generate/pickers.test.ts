import { describe, expect, it } from 'vitest';
import type { TaskOffer } from '$lib/services/api/editorTasks';
import type { PresetInfo } from '$lib/types/api';
import { NO_FORMULA, formulaOptionsOf, modeOptionsOf, presetDisabledReasons, presetListOf } from './pickers';

const offer = (over: Partial<TaskOffer> = {}): TaskOffer => ({
	preset_id: 'qwen', preset_name: 'Qwen-Image 2.1', mode: 'editor:edit', from_mode: 'edit', mode_label: 'Edit', label: null, short_description: null, order: 0, region_strategy: 'mask', via: null, qualifies: true, reason: null, ...over
});
const info = (id: string, name: string): PresetInfo => ({ id, name, version: '2', tags: [], category: 'qwen', engine: 'native' });

describe('presetListOf', () => {
	it('lists each offered preset once, in offer order, taking the catalogue entry when there is one', () => {
		const flux = offer({ preset_id: 'flux', preset_name: 'Flux Fill', mode: 'editor:fill' });
		const list = presetListOf([offer(), offer({ mode: 'editor:edit2' }), flux], [info('qwen', 'Qwen-Image 2.1'), info('other', 'Not offered')]);
		expect(list.map((entry) => entry.id)).toEqual(['qwen', 'flux']);
		expect(list[0]).toMatchObject({ category: 'qwen', engine: 'native' });
		expect(list[1]).toMatchObject({ id: 'flux', name: 'Flux Fill' });
	});

	it('lists nothing when nothing is offered', () => {
		expect(presetListOf([], [info('qwen', 'Q')])).toEqual([]);
	});
});

describe('presetDisabledReasons', () => {
	it('disables a preset only when none of its modes can run, with the reason in plain words', () => {
		const offers = [
			offer({ qualifies: false, reason: 'models_missing' }),
			offer({ preset_id: 'flux', qualifies: false, reason: 'not_assigned' }),
			offer({ preset_id: 'sdxl', qualifies: false, reason: 'models_missing' }),
			offer({ preset_id: 'sdxl', mode: 'editor:ok' })
		];
		expect(presetDisabledReasons(offers)).toEqual({ qwen: 'Models not installed', flux: 'Not available to your account' });
	});
});

describe('modeOptionsOf', () => {
	it('lists the usable modes of the preset with the editor mode name and its short description', () => {
		const options = modeOptionsOf(
			[offer({ label: 'Edit', short_description: 'Change one part' }), offer({ mode: 'editor:edit2', mode_label: 'Edit 2', region_strategy: 'crop' }), offer({ mode: 'editor:bad', qualifies: false, reason: 'models_missing' }), offer({ preset_id: 'flux', mode: 'editor:fill' })],
			'qwen'
		);
		expect(options).toEqual([
			{ value: 'editor:edit', label: 'Edit', description: 'Change one part' },
			{ value: 'editor:edit2', label: 'Edit 2', description: 'Works on the marked area' }
		]);
	});

	it('says under a mode that reaches the task through the fallback that it does', () => {
		const options = modeOptionsOf([offer({ via: 'edit_region', short_description: 'Change one part.' }), offer({ mode: 'editor:edit2', mode_label: 'Edit 2' })], 'qwen');
		expect(options[0].description).toBe('Change one part. Uses Edit a region with a built-in instruction.');
		expect(options[1].description).toBe('Repaints inside the mask');
	});

	it('is empty without a preset', () => {
		expect(modeOptionsOf([offer()], null)).toEqual([]);
	});
});

describe('formulaOptionsOf', () => {
	it('puts no formula first so the formula can be cleared', () => {
		expect(formulaOptionsOf([{ id: 'f1', name: 'Film look', values: {} }])).toEqual([{ value: NO_FORMULA, label: 'No formula' }, { value: 'f1', label: 'Film look' }]);
	});
});
