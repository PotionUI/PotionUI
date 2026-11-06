import { describe, expect, it } from 'vitest';
import type { GenerationTab } from '$lib/types/tabs';
import { buildSendTargets, videoFieldsOf, withFieldValue } from './sendTargets';

const schema = {
	properties: {
		main: {
			children: [
				{ name: 'prompt', type: 'text', title: 'Prompt' },
				{ name: 'control_video', type: 'video', title: 'Control video' },
				{ name: 'refs', type: 'media', title: 'References', configuration: { accepted_types: ['image', 'video'], multiple: true } },
				{ name: 'start_image', type: 'image', title: 'Start image' },
				{ name: 'stills', type: 'media', title: 'Stills', configuration: { accepted_types: ['image'] } }
			]
		}
	}
};

describe('send targets', () => {
	it('lists only the fields that take a video', () => {
		expect(videoFieldsOf(schema)).toEqual([
			{ name: 'control_video', title: 'Control video', multiple: false },
			{ name: 'refs', title: 'References', multiple: true }
		]);
		expect(videoFieldsOf(null)).toEqual([]);
	});

	it('makes one row per tab and field', () => {
		const tabs = [{ id: 't1', name: 'Portrait study', color: 'rgb(1 2 3)' }] as GenerationTab[];
		const targets = buildSendTargets(tabs, { t1: videoFieldsOf(schema) });
		expect(targets.map((target) => [target.id, target.name, target.detail])).toEqual([
			['t1::control_video', 'Portrait study', 'Control video'],
			['t1::refs', 'Portrait study', 'References']
		]);
	});

	it('replaces a single field and appends to a multi field', () => {
		const clip = { path: 'uploads/a.mp4' };
		expect(withFieldValue({ x: 1 }, { field: 'control_video', multiple: false }, clip)).toEqual({ x: 1, control_video: clip });
		expect(withFieldValue({ refs: [{ path: 'b' }] }, { field: 'refs', multiple: true }, clip)).toEqual({ refs: [{ path: 'b' }, clip] });
	});
});
