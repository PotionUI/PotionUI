import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { buildResolveRequest, createKitResolver, hasKitCites, skipLines, summarizeSkips } from './kitResolver';
import type { KitNotice, KitResolution } from './types';

const notice = (extra: Partial<KitNotice>): KitNotice => ({ code: 'unsupported_kind', message: 'x', kit: 'mara', item: 'voice', kind: 'audio', field: 'refs', ...extra });
const resolution = (text: string): KitResolution => ({ field: 'refs', items: [], skipped: [], expanded: text, text, takes: { image: true, video: true, audio: true, text: true }, limits: {} });

describe('hasKitCites', () => {
	it('sees stored markers and plain paths only', () => {
		expect(hasKitCites(['a @[refs:kit:kt_00000001/it_00000002]'])).toBe(true);
		expect(hasKitCites(['say @kits.mara.face'])).toBe(true);
		expect(hasKitCites(['@[refs:a.png]', null, ''])).toBe(false);
	});
});

describe('buildResolveRequest', () => {
	it('is null without a kit citation and otherwise bounded', () => {
		expect(buildResolveRequest({ texts: ['hi'], loaded: [], field: null, presetId: 'p', mode: 'm' })).toBeNull();
		const request = buildResolveRequest({ texts: ['@kits.mara ' + 'x'.repeat(30000)], loaded: ['kt_1'], field: 'refs', presetId: 'p', mode: null })!;
		expect(request.text.length).toBe(20000);
		expect(request).toMatchObject({ loaded: ['kt_1'], field: 'refs', preset_id: 'p', mode: null });
	});
});

describe('skip wording', () => {
	it('strips the code marks from the plain line and de-duplicates', () => {
		expect(skipLines([notice({ message: 'This model does not take audio: `voice` is skipped.' }), notice({ message: 'This model does not take audio: `voice` is skipped.' })])).toEqual([
			'This model does not take audio: voice is skipped.'
		]);
	});

	it('summarizes for the deck', () => {
		expect(summarizeSkips([notice({}), notice({ kit: 'car_interior', item: 'engine' })])).toEqual({ count: 2, title: '2 items skipped', detail: 'voice and engine: audio' });
		expect(summarizeSkips([notice({ item: null, code: 'kit_deleted', message: 'The kit `x` no longer exists' })])?.count).toBe(1);
		expect(summarizeSkips([])).toBeNull();
	});
});

describe('createKitResolver', () => {
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => vi.useRealTimers());
	const req = (text: string) => ({ text });

	it('debounces to one call with the latest request', async () => {
		const call = vi.fn().mockResolvedValue({ success: true, data: resolution('ok') });
		const resolver = createKitResolver(call, 400);
		resolver.request(req('a'));
		resolver.request(req('ab'));
		await vi.advanceTimersByTimeAsync(399);
		expect(call).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(2);
		expect(call).toHaveBeenCalledTimes(1);
		expect(call).toHaveBeenCalledWith(req('ab'));
		expect(get(resolver).result?.text).toBe('ok');
	});

	it('does not call again for an unchanged request, and clears on null', async () => {
		const call = vi.fn().mockResolvedValue({ success: true, data: resolution('ok') });
		const resolver = createKitResolver(call, 10);
		resolver.request(req('a'));
		await vi.advanceTimersByTimeAsync(20);
		resolver.request(req('a'));
		await vi.advanceTimersByTimeAsync(20);
		expect(call).toHaveBeenCalledTimes(1);
		resolver.request(null);
		expect(get(resolver).result).toBeNull();
	});

	it('ignores a late answer to a superseded request and keeps the old result on failure', async () => {
		let release: (value: unknown) => void = () => {};
		const call = vi.fn().mockImplementationOnce(() => new Promise((resolve) => (release = resolve))).mockResolvedValueOnce({ success: true, data: resolution('new') });
		const resolver = createKitResolver(call, 10);
		resolver.request(req('a'));
		await vi.advanceTimersByTimeAsync(20);
		resolver.request(req('b'));
		await vi.advanceTimersByTimeAsync(20);
		release({ success: true, data: resolution('old') });
		await vi.advanceTimersByTimeAsync(1);
		expect(get(resolver).result?.text).toBe('new');
		const failing = createKitResolver(vi.fn().mockRejectedValue(new Error('x')), 10);
		failing.request(req('z'));
		await vi.advanceTimersByTimeAsync(20);
		expect(get(failing)).toMatchObject({ failed: true, loading: false });
	});
});
