import { describe, expect, it } from 'vitest';
import type { PromptResourceSpec } from './promptResources';
import {
	convertResourceTokens,
	findResourceTokens,
	resourceTokenPattern,
	resourceTokenWarnings,
	splitResourceTokenPreview
} from './promptResourceTokens';

const specs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' },
	{ field: 'reference_videos', kind: 'video', label: 'Videos', token: '<Video @>' },
	{ field: 'reference_audios', kind: 'audio', label: 'Audio', token: '<Audio @>' }
];

const formValues = {
	references: [{ relative_path: 'uploads/a.png', path: '/abs/a.png' }, 'uploads/b.png', { url: '/media/c.png' }],
	reference_videos: [{ path: 'uploads/walk.mp4' }],
	reference_audios: []
};

describe('convertResourceTokens', () => {
	it('turns each token into a marker for the item at that position', () => {
		const result = convertResourceTokens('<Picture 3> beside <Picture 1> in <Video 1>', specs, formValues);
		expect(result.text).toBe(
			'@[references:/media/c.png] beside @[references:uploads/a.png] in @[reference_videos:uploads/walk.mp4]'
		);
		expect(result.linked.map((match) => match.position)).toEqual([3, 1, 1]);
		expect(result.unresolved).toEqual([]);
	});

	it('keeps positions beyond the items and empty fields as plain text', () => {
		const result = convertResourceTokens('<Picture 4>, <Picture 0> and <Audio 1> with <Picture 2>', specs, formValues);
		expect(result.text).toBe('<Picture 4>, <Picture 0> and <Audio 1> with @[references:uploads/b.png]');
		expect(result.unresolved.map((match) => match.text)).toEqual(['<Picture 4>', '<Picture 0>', '<Audio 1>']);
	});

	it('numbers each field on its own counter', () => {
		const result = convertResourceTokens('<Video 1><Picture 1>', specs, formValues);
		expect(result.text).toBe('@[reference_videos:uploads/walk.mp4]@[references:uploads/a.png]');
	});

	it('accepts case and spacing variations the model may write', () => {
		const result = convertResourceTokens('<picture  2> and <PICTURE 1>', specs, formValues);
		expect(result.text).toBe('@[references:uploads/b.png] and @[references:uploads/a.png]');
	});

	it('lets multi-word tokens vary their inner spacing', () => {
		const spaced: PromptResourceSpec[] = [{ field: 'references', kind: 'image', token: '[Ref Image @]' }];
		expect(convertResourceTokens('[ref  image 2]', spaced, formValues).text).toBe('@[references:uploads/b.png]');
	});

	it('leaves existing markers and unrelated text untouched', () => {
		const text = 'keep @[references:uploads/a.png] and <Subject 1> and Picture 2';
		expect(convertResourceTokens(text, specs, formValues).text).toBe(text);
	});

	it('handles tokens without a space before the number', () => {
		const qwen: PromptResourceSpec[] = [{ field: 'source_image', kind: 'image', token: '<image@>' }];
		const result = convertResourceTokens('edit <image1> using <image2>', qwen, {
			source_image: ['uploads/x.png', 'uploads/y.png']
		});
		expect(result.text).toBe('edit @[source_image:uploads/x.png] using @[source_image:uploads/y.png]');
	});

	it('is a no-op without specs', () => {
		expect(convertResourceTokens('<Picture 1>', [], formValues).text).toBe('<Picture 1>');
	});
});

describe('resourceTokenPattern', () => {
	it('refuses a token that is only the placeholder', () => {
		expect(resourceTokenPattern({ field: 'x', kind: 'image', token: '@' })).toBeNull();
	});
});

describe('resourceTokenWarnings', () => {
	it('names each unresolved token once with the field it points at', () => {
		const warnings = resourceTokenWarnings('<Picture 7> and <Picture 7> and <Audio 2> and <Picture 1>', {
			specs,
			formValues,
			fieldLabels: { references: 'Reference images' }
		});
		expect(warnings).toEqual([
			'<Picture 7> stays plain text: Reference images has 3 items',
			'<Audio 2> stays plain text: Audio is empty'
		]);
	});
});

describe('splitResourceTokenPreview', () => {
	it('marks linked tokens with their handle and unresolved ones separately', () => {
		expect(splitResourceTokenPreview('a <Picture 2> b <Video 3>', specs, formValues)).toEqual([
			{ kind: 'text', text: 'a ' },
			{ kind: 'linked', text: '<Picture 2>', label: 'Picture 2' },
			{ kind: 'text', text: ' b ' },
			{ kind: 'unresolved', text: '<Video 3>' }
		]);
	});

	it('finds tokens in document order across fields', () => {
		expect(findResourceTokens('<Video 1> <Picture 1>', specs, formValues).map((match) => match.spec.field)).toEqual([
			'reference_videos',
			'references'
		]);
	});
});
