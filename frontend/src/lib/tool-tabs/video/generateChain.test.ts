import { afterEach, describe, expect, it } from 'vitest';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import { GENERATE_TOOL, type GenerateParams } from '../image/generate/generateStep';
import { runViews } from '../image/generate/runEngine';
import { basisBeneath, chainSignature, outOfDateNotice, rebase, refKey } from './generateChain';

const ORIGINAL = { item_id: 'orig' };

function edit(id: string, tool: string, params: unknown, hidden = false): EditStep<unknown> {
	return { id, tool, icon: '', label: tool, summary: '', params, hidden };
}

function generated(id: string, clip: string, basis: string | null, over: Partial<GenerateParams> = {}): EditStep<unknown> {
	const params: GenerateParams = {
		v: 2,
		task: 'restore',
		prompt: '',
		setup: { preset_id: 'ltx', mode: 'editor:restore', form_data: {}, source: { kind: 'defaults', id: null, name: null }, label: '' },
		area: { region: null, size: null },
		candidates: 1,
		results: [{ patch: { item_id: clip }, url: `/uploads/${clip}.mp4`, seed: 1, clip: true, range: { start_s: 2, end_s: 4 }, duration_s: 6.4, added: null }],
		chosen: 0,
		seed: 1,
		rect: null,
		soft: false,
		run: { id: `run_${id}`, status: 'done', error: null },
		range: { start_s: 2, end_s: 4 },
		...(basis === null ? {} : { basis: { geometry: basis, size: null } }),
		...over
	};
	return edit(id, GENERATE_TOOL, params);
}

const trim = (id: string, end: number) => edit(id, 'trim', { start: 0, end });

afterEach(() => runViews.set({}));

describe('a settled generate step in a video edit', () => {
	it('leaves the original and every step in place when nothing was generated', () => {
		const found = rebase([trim('s1', 3)], ORIGINAL);
		expect(found.base).toBeNull();
		expect(found.input).toEqual(ORIGINAL);
		expect(found.url).toBeNull();
		expect(found.tail.map((step) => step.id)).toEqual(['s1']);
	});

	it('becomes the new base: its clip is the input and only the steps after it apply', () => {
		const steps = [trim('s1', 3), generated('s2', 'clip2', chainSignature(refKey(ORIGINAL), [trim('s1', 3)])), edit('s3', 'mute', {})];
		const found = rebase(steps, ORIGINAL);
		expect(found.base?.step.id).toBe('s2');
		expect(found.input).toEqual({ item_id: 'clip2' });
		expect(found.url).toBe('/uploads/clip2.mp4');
		expect(found.tail.map((step) => step.id)).toEqual(['s3']);
	});

	it('is out of date when a step beneath it changed, and is then left out of the chain', () => {
		const old = chainSignature(refKey(ORIGINAL), [trim('s1', 3)]);
		const found = rebase([trim('s1', 2), generated('s2', 'clip2', old)], ORIGINAL);
		expect([...found.stale]).toEqual(['s2']);
		expect(found.base).toBeNull();
		expect(found.input).toEqual(ORIGINAL);
		expect(found.tail.map((step) => step.id)).toEqual(['s1']);
	});

	it('is out of date when a step beneath it is hidden', () => {
		const signature = chainSignature(refKey(ORIGINAL), [trim('s1', 3)]);
		const found = rebase([{ ...trim('s1', 3), hidden: true }, generated('s2', 'clip2', signature)], ORIGINAL);
		expect(found.stale.has('s2')).toBe(true);
	});

	it('is left out while it still runs, and from a failed or cancelled run', () => {
		const running = generated('s1', 'clip2', null, { results: [], run: { id: 'run_s1', status: 'running', error: null } });
		expect(rebase([running], ORIGINAL).left.has('s1')).toBe(true);
		expect(rebase([running], ORIGINAL).base).toBeNull();
		runViews.set({ run_s1: { status: 'failed' } as never });
		expect(rebase([generated('s1', 'clip2', null)], ORIGINAL).left.has('s1')).toBe(true);
	});

	it('does not count a hidden generate step', () => {
		const found = rebase([generated('s1', 'clip2', null, {}), { ...generated('s2', 'clip3', null), hidden: true }], ORIGINAL);
		expect(found.base?.step.id).toBe('s1');
	});

	it('chains: a second generate step runs on the first result and the last settled one is the base', () => {
		const first = generated('s1', 'clip2', chainSignature(refKey(ORIGINAL), []));
		const middle = edit('s2', 'speed', { speed: 2, keep_pitch: true });
		const second = generated('s3', 'clip3', chainSignature('i:clip2', [middle]));
		const found = rebase([first, middle, second], ORIGINAL);
		expect(found.base?.step.id).toBe('s3');
		expect(found.input).toEqual({ item_id: 'clip3' });
		expect(found.tail).toEqual([]);
		expect(found.stale.size).toBe(0);
	});

	it('hands the next run the clip and steps beneath it, with a basis that matches once it settles', () => {
		const first = generated('s1', 'clip2', chainSignature(refKey(ORIGINAL), []));
		const middle = edit('s2', 'speed', { speed: 2, keep_pitch: true });
		const beneath = basisBeneath([first, middle], ORIGINAL, 's3');
		expect(beneath.input).toEqual({ item_id: 'clip2' });
		expect(beneath.tail.map((step) => step.id)).toEqual(['s2']);
		const second = generated('s3', 'clip3', beneath.basis.geometry);
		expect(rebase([first, middle, second], ORIGINAL).base?.step.id).toBe('s3');
		expect(basisBeneath([first, middle, second], ORIGINAL, 's1').input).toEqual(ORIGINAL);
	});

	it('trusts a step recorded without a basis, and every step while the original is still resolving', () => {
		expect(rebase([trim('s1', 2), generated('s2', 'clip2', null)], ORIGINAL).base?.step.id).toBe('s2');
		expect(rebase([trim('s1', 2), generated('s2', 'clip2', 'other')], null).base?.step.id).toBe('s2');
	});

	it('words the notice for steps that are out of date', () => {
		expect(outOfDateNotice(0)).toBeNull();
		expect(outOfDateNotice(1)).toBe('1 edit is not included: the clip beneath it changed.');
		expect(outOfDateNotice(2)).toBe('2 edits are not included: the clip beneath them changed.');
	});
});
