import { describe, expect, it } from 'vitest';
import { ensureCoreImageEditor } from '../coreEditor';
import { stepPayload } from '../payload';
import { imageRowInfo, isStepUnavailable } from '../stepKinds';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import {
	backgroundDraft,
	backgroundPayload,
	backgroundSummary,
	backgroundThumb,
	directBlockedReason,
	hexToRgb,
	initialBackground,
	isDirectMode,
	mixBackground,
	paramsFor,
	pictureTarget,
	readReplaceBackgroundParams,
	replaceBackgroundStepKind
} from './backgroundStep';

const matte = { item_id: 'mat1', url: '/api/media/mat1.png' };

describe('replace background params', () => {
	it('builds colour params from the choice and the landed mask', () => {
		const params = paramsFor({ ...initialBackground(), mode: 'color', color: '#1a2b3c' }, matte);
		expect(params).toEqual({ v: 1, kind: 'color', color: '#1A2B3C', picture: null, matte });
	});

	it('refuses a describe choice, a picture without a picture and a colour that is not a colour', () => {
		expect(paramsFor(initialBackground(), matte)).toBeNull();
		expect(paramsFor({ ...initialBackground(), mode: 'picture' }, matte)).toBeNull();
		expect(paramsFor({ ...initialBackground(), mode: 'color', color: 'nope' }, matte)).toBeNull();
	});

	it('survives being saved and reloaded', () => {
		const picture = { item_id: 'pic9', url: '/p.png', name: 'Harbour' };
		for (const choice of [
			{ mode: 'picture' as const, color: '#FFFFFF', picture },
			{ mode: 'color' as const, color: '#00FF00', picture: null },
			{ mode: 'transparent' as const, color: '#FFFFFF', picture: null }
		]) {
			const params = paramsFor(choice, matte)!;
			expect(readReplaceBackgroundParams(JSON.parse(JSON.stringify(params)))).toEqual(params);
		}
	});

	it('rejects params without a mask or with the wrong kind', () => {
		expect(readReplaceBackgroundParams(null)).toBeNull();
		expect(readReplaceBackgroundParams({ kind: 'transparent' })).toBeNull();
		expect(readReplaceBackgroundParams({ kind: 'blur', matte })).toBeNull();
	});
});

describe('replace background payload', () => {
	it('sends the colour, the picture or nothing, always with the mask', () => {
		const color = paramsFor({ ...initialBackground(), mode: 'color', color: '#112233' }, matte)!;
		expect(backgroundPayload(color)).toEqual([{ tool: 'replace_background', params: { kind: 'color', color: '#112233', matte: { item_id: 'mat1' } } }]);
		const picture = paramsFor({ ...initialBackground(), mode: 'picture', picture: { item_id: 'pic9', url: '/p.png', name: null } }, matte)!;
		expect(backgroundPayload(picture)).toEqual([{ tool: 'replace_background', params: { kind: 'picture', picture: { item_id: 'pic9' }, matte: { item_id: 'mat1' } } }]);
		const clear = paramsFor({ ...initialBackground(), mode: 'transparent' }, matte)!;
		expect(backgroundPayload(clear)).toEqual([{ tool: 'replace_background', params: { kind: 'transparent', matte: { item_id: 'mat1' } } }]);
	});

	it('leaves a step it cannot read out of the render instead of failing it', () => {
		expect(backgroundPayload({ kind: 'color' })).toEqual([{ tool: 'replace_background', params: {}, hidden: true }]);
	});

	it('is the payload the step kind returns', () => {
		const params = paramsFor({ ...initialBackground(), mode: 'transparent' }, matte)!;
		expect(replaceBackgroundStepKind.payload(params, {} as never)).toEqual(backgroundPayload(params));
	});
});

describe('replace background row', () => {
	it('labels the step and says what it was replaced with', () => {
		const color = paramsFor({ ...initialBackground(), mode: 'color', color: '#ABCDEF' }, matte)!;
		expect(backgroundDraft(color)).toMatchObject({ tool: 'replace_background', label: 'Replace background', summary: '#ABCDEF' });
		expect(backgroundSummary(paramsFor({ ...initialBackground(), mode: 'transparent' }, matte)!)).toBe('Transparent');
		expect(backgroundSummary(paramsFor({ ...initialBackground(), mode: 'picture', picture: { item_id: 'p', url: null, name: 'Harbour' } }, matte)!)).toBe('Harbour');
	});

	it('shows a swatch for a colour, the picture for a picture and nothing for transparent', () => {
		expect(backgroundThumb(paramsFor({ ...initialBackground(), mode: 'color', color: '#FF0000' }, matte)!)).toContain('%23FF0000');
		expect(backgroundThumb(paramsFor({ ...initialBackground(), mode: 'picture', picture: { item_id: 'p', url: '/p.png', name: null } }, matte)!)).toBe('/p.png');
		expect(backgroundThumb(paramsFor({ ...initialBackground(), mode: 'transparent' }, matte)!)).toBeNull();
	});
});

describe('choosing the background', () => {
	it('runs a model only for Describe', () => {
		expect(isDirectMode('replace_background', 'describe')).toBe(false);
		expect(isDirectMode('replace_background', 'color')).toBe(true);
		expect(isDirectMode('edit_region', 'color')).toBe(false);
	});

	it('says why it cannot apply yet', () => {
		expect(directBlockedReason({ ...initialBackground(), mode: 'color' }, false)).toBe('Find the subject or mark the background first');
		expect(directBlockedReason({ ...initialBackground(), mode: 'picture' }, true)).toBe('Choose a picture first');
		expect(directBlockedReason({ ...initialBackground(), mode: 'color' }, true)).toBeNull();
	});

	it('takes a library item or an upload as a picture and refuses a bare history file', () => {
		expect(pictureTarget({ item_id: 'i1', url: '/u', name: 'n' })).toEqual({ item_id: 'i1', url: '/u', name: 'n' });
		expect(pictureTarget({ artifact_id: 'a1', url: '/a' })).toEqual({ item_id: 'a1', url: '/a', name: null });
		expect(pictureTarget({ url: '/h' })).toBeNull();
	});
});

describe('mixBackground', () => {
	const base = () => new Uint8ClampedArray([200, 100, 50, 255, 200, 100, 50, 255]);
	const mask = (first: number, second: number) => new Uint8ClampedArray([first, first, first, 255, second, second, second, 255]);

	it('paints a colour where the background is marked and keeps the subject', () => {
		const pixels = base();
		mixBackground(pixels, mask(0, 255), { kind: 'color', rgb: hexToRgb('#0000FF') });
		expect([...pixels]).toEqual([200, 100, 50, 255, 0, 0, 255, 255]);
	});

	it('blends the soft edge', () => {
		const pixels = base();
		mixBackground(pixels, mask(128, 0), { kind: 'color', rgb: [0, 0, 0] });
		expect(pixels[0]).toBe(100);
		expect(pixels[4]).toBe(200);
	});

	it('makes the marked background transparent', () => {
		const pixels = base();
		mixBackground(pixels, mask(255, 0), { kind: 'transparent' });
		expect([pixels[3], pixels[7]]).toEqual([0, 255]);
	});

	it('uses the picture pixels behind the marked background', () => {
		const pixels = base();
		const picture = new Uint8ClampedArray([9, 8, 7, 255, 9, 8, 7, 255]);
		mixBackground(pixels, mask(255, 255), { kind: 'picture', pixels: picture });
		expect([...pixels.slice(0, 4)]).toEqual([9, 8, 7, 255]);
	});
});

describe('a saved edit with a replaced background', () => {
	const saved = (hidden = false): EditStep => ({ id: 's4', tool: 'replace_background', icon: 'x', label: 'Replace background', summary: '#112233', params: JSON.parse(JSON.stringify(paramsFor({ ...initialBackground(), mode: 'color', color: '#112233' }, matte))), lane: 'paint', hidden } as EditStep);

	it('reloads as a known step and renders through the media tool payload', () => {
		ensureCoreImageEditor();
		expect(isStepUnavailable(saved())).toBe(false);
		expect(stepPayload(saved(), null)).toEqual([{ tool: 'replace_background', params: { kind: 'color', color: '#112233', matte: { item_id: 'mat1' } } }]);
	});

	it('gets a thumbnail in its row', () => {
		ensureCoreImageEditor();
		expect(imageRowInfo(saved())?.thumb).toContain('%23112233');
	});
});
