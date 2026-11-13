import { describe, expect, it } from 'vitest';
import { describeRefs, groupRefs, hasRefTokens, kindFromKey, tokenizeRefs, uploadUrl } from './promptRefs';

const UP = 'uploads/3f2b8c1e-9d4a-4e7b-a1c2-5d6e7f8a9b0c.png';
const UP2 = 'uploads/aaaaaaaa-9d4a-4e7b-a1c2-5d6e7f8a9b0c.jpg';
const KIT = 'kit:kt_0a1b2c3d/it_4e5f6a7b';

describe('tokenizeRefs', () => {
	it('leaves plain text alone', () => {
		expect(tokenizeRefs('a red bicycle')).toEqual([{ type: 'text', text: 'a red bicycle' }]);
		expect(hasRefTokens('a red bicycle')).toBe(false);
	});

	it('splits a plain upload reference out of the text', () => {
		expect(tokenizeRefs(`Use @[references:${UP}] as the face`)).toEqual([
			{ type: 'text', text: 'Use ' },
			{ type: 'ref', raw: `@[references:${UP}]`, field: 'references', key: UP },
			{ type: 'text', text: ' as the face' }
		]);
	});

	it('finds several tokens in one prompt, kit and upload, and keeps line breaks', () => {
		const parts = tokenizeRefs(`@[references:${UP}]\n\nthen @[references:${KIT}] and @[references:${UP2}]`);
		expect(parts.filter((p) => p.type === 'ref').map((p) => (p as { key: string }).key)).toEqual([UP, KIT, UP2]);
		expect(parts[1]).toEqual({ type: 'text', text: '\n\nthen ' });
	});

	it('leaves an unknown or malformed token as text', () => {
		const text = 'see @[nofield] and @kits.mara.face and [references:x]';
		expect(tokenizeRefs(text).every((p) => p.type === 'text')).toBe(true);
		expect(hasRefTokens('@[nofield]')).toBe(false);
	});
});

describe('describeRefs', () => {
	const parts = (text: string) => tokenizeRefs(text);

	it('resolves a reference through the form data: name, url and position among the field items', () => {
		const formData = {
			references: [
				{ relative_path: UP2, name: 'harbour.jpg', url: '/api/media/uploads/h.jpg', type: 'image' },
				{ relative_path: UP, name: 'mara.png', url: '/api/media/uploads/m.png', type: 'image' }
			]
		};
		const [view] = describeRefs(parts(`@[references:${UP}]`), formData);
		expect(view).toMatchObject({ type: 'media', position: 2, item: { url: '/api/media/uploads/m.png', name: 'mara.png' } });
		expect(view.type === 'media' && view.spec.kind).toBe('image');
	});

	it('numbers references by first appearance when the form data is not available', () => {
		const views = describeRefs(parts(`@[references:${UP}] @[references:${UP2}] @[references:${UP}]`), null);
		expect(views.map((v) => (v.type === 'media' ? v.position : null))).toEqual([1, 2, 1]);
		expect(views[0]).toMatchObject({ item: { url: `/api/media/uploads/${UP.split('/')[1]}` } });
	});

	it('numbers each kind separately and falls back to a missing reference when the kind is unknown', () => {
		const views = describeRefs(parts('@[references:uploads/a.mp4] @[references:uploads/b.png] @[references:weird-key]'), null);
		expect(views.map((v) => v.type)).toEqual(['media', 'media', 'missing']);
		expect(views.map((v) => (v.type === 'media' ? [v.spec.kind, v.position] : null))).toEqual([['video', 1], ['image', 1], null]);
	});

	it('keeps a reference whose file is gone from the form data as a numbered media chip', () => {
		const views = describeRefs(parts(`@[references:${UP}]`), { references: [] });
		expect(views[0]).toMatchObject({ type: 'media', position: 1 });
	});

	it('hands kit keys to the kit chip and ignores unparsable kit keys', () => {
		const views = describeRefs(parts(`@[references:${KIT}] @[references:kit:nope]`), null);
		expect(views[0]).toMatchObject({ type: 'kit', key: KIT });
		expect(views[1].type).toBe('missing');
	});

	it('detects by token syntax, never by the field name', () => {
		const views = describeRefs(parts(`@[anything_at_all:${UP}]`), null);
		expect(views[0]).toMatchObject({ type: 'media', field: 'anything_at_all' });
	});
});

describe('helpers', () => {
	it('derives the kind from the extension and the upload url from the key', () => {
		expect(kindFromKey('a/b.PNG')).toBe('image');
		expect(kindFromKey('a/b.webm')).toBe('video');
		expect(kindFromKey('a/b.wav')).toBe('audio');
		expect(kindFromKey('a/b')).toBeNull();
		expect(uploadUrl(UP)).toBe(`/api/media/uploads/${UP.split('/')[1]}`);
		expect(uploadUrl('generations/x.png')).toBeNull();
	});
});

describe('groupRefs', () => {
	const nodes = (text: string) => groupRefs(tokenizeRefs(text));
	const ref = `@[references:${UP}]`;

	it.each([['.'], [','], [')'], ['.)'], ['!?'], [';']])('keeps a chip together with the punctuation %s right after it', (punct) => {
		expect(nodes(`with ${ref}${punct} then`)).toEqual([
			{ type: 'text', text: 'with ' },
			{ type: 'ref', raw: ref, field: 'references', key: UP, trailing: punct },
			{ type: 'text', text: ' then' }
		]);
	});

	it('leaves nothing trailing when a space follows the chip', () => {
		const result = nodes(`with ${ref} then`);
		expect(result[1]).toMatchObject({ type: 'ref', trailing: '' });
		expect(result[2]).toEqual({ type: 'text', text: ' then' });
	});

	it('takes the whole non-space run after the chip and handles the end of the text', () => {
		expect(nodes(`${ref}word-after`)[0]).toMatchObject({ trailing: 'word-after' });
		const end = nodes(`last ${ref}.`);
		expect(end).toHaveLength(2);
		expect(end[1]).toMatchObject({ trailing: '.' });
	});

	it('groups two chips in a row without losing text', () => {
		const result = nodes(`${ref}, ${ref}.`);
		expect(result.map((n) => (n.type === 'ref' ? `ref:${n.trailing}` : `text:${n.text}`))).toEqual(['ref:,', 'text: ', 'ref:.']);
	});

	it('does not change plain text and never drops characters', () => {
		const text = `a ${ref}. b ${ref}, c\n\n(${ref}) d`;
		const rebuilt = nodes(text).map((n) => (n.type === 'text' ? n.text : n.raw + n.trailing)).join('');
		expect(rebuilt).toBe(text);
		expect(nodes('plain text')).toEqual([{ type: 'text', text: 'plain text' }]);
	});
});
