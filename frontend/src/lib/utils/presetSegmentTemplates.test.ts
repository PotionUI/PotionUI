import { describe, it, expect } from 'vitest';
import { resolvePresetSegmentTemplates } from './presetSegmentTemplates';

const flatEntry = {
	name: 'Full structure',
	description: 'Every slot',
	tags: ['structure'],
	segments: [{ name: 'Subject', content: 'a cat', enabled: true }]
};

const modeEntry = {
	name: 'Reference pass',
	segments: [{ name: 'Reference', content: 'match the reference', enabled: true }]
};

describe('resolvePresetSegmentTemplates', () => {
	it('reads the flat list when the mode declares no override', () => {
		const templates = resolvePresetSegmentTemplates(
			'sdxl/base',
			{ segment_templates: [flatEntry], modes: { refs: {} } },
			'refs'
		);

		expect(templates.map((t) => t.name)).toEqual(['Full structure']);
		expect(templates[0].id).toBe('preset:sdxl/base:*:0');
		expect(templates[0].origin).toBe('preset');
		expect(templates[0].description).toBe('Every slot');
		expect(templates[0].tags).toEqual(['structure']);
		expect(templates[0].segments).toEqual(flatEntry.segments);
	});

	it('replaces the flat list with the mode list when the mode declares one', () => {
		const templates = resolvePresetSegmentTemplates(
			'sdxl/base',
			{
				segment_templates: [flatEntry],
				modes: { refs: { segment_templates: [modeEntry] } }
			},
			'refs'
		);

		expect(templates.map((t) => t.name)).toEqual(['Reference pass']);
		expect(templates[0].id).toBe('preset:sdxl/base:refs:0');
	});

	it('treats an empty mode list as "this mode offers none"', () => {
		expect(
			resolvePresetSegmentTemplates(
				'sdxl/base',
				{ segment_templates: [flatEntry], modes: { refs: { segment_templates: [] } } },
				'refs'
			)
		).toEqual([]);
	});

	it('falls back to the flat list for a mode with no entry of its own', () => {
		const templates = resolvePresetSegmentTemplates(
			'sdxl/base',
			{ segment_templates: [flatEntry], modes: { refs: { segment_templates: [modeEntry] } } },
			'txt2img'
		);

		expect(templates.map((t) => t.name)).toEqual(['Full structure']);
		expect(templates[0].id).toBe('preset:sdxl/base:*:0');
	});

	it('drops malformed entries without shifting the ids of the survivors', () => {
		const templates = resolvePresetSegmentTemplates(
			'sdxl/base',
			{
				segment_templates: [
					{ segments: [] },
					{ name: '   ', segments: [] },
					{ name: 'No segments key' },
					{ name: 'Segments not a list', segments: 'nope' },
					'not an object',
					flatEntry
				]
			},
			null
		);

		expect(templates.map((t) => t.name)).toEqual(['Full structure']);
		expect(templates[0].id).toBe('preset:sdxl/base:*:5');
	});

	it('keeps segment entries verbatim and drops non-object ones', () => {
		const segments = [{ content: 'kept', prefix: 'a ', suffix: ' b' }, 'dropped', null];
		const templates = resolvePresetSegmentTemplates('p', { segment_templates: [{ name: 'T', segments }] }, null);

		expect(templates[0].segments).toEqual([{ content: 'kept', prefix: 'a ', suffix: ' b' }]);
	});

	it('returns nothing when the preset declares no prompt vars or no list', () => {
		expect(resolvePresetSegmentTemplates('p', undefined, 'txt2img')).toEqual([]);
		expect(resolvePresetSegmentTemplates('p', {}, 'txt2img')).toEqual([]);
		expect(resolvePresetSegmentTemplates('p', { segment_templates: 'nope' }, null)).toEqual([]);
	});
});
