import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { readable } from 'svelte/store';

const copyText = vi.fn(async (_text: string) => true);
vi.mock('$lib/utils/clipboard', () => ({ copyText: (text: string) => copyText(text) }));
vi.mock('$lib/stores/kits', () => ({
	kitDirectory: readable(new Map()),
	ensureKits: vi.fn(async () => undefined)
}));

const { default: ParametersView } = await import('$lib/components/generation/ParametersView.svelte');
const { mountComponent } = await import('./helpers/mount');
const { forgetFoldPreference } = await import('$lib/components/generation/parametersFold');

const UP = 'uploads/3f2b8c1e-9d4a-4e7b-a1c2-5d6e7f8a9b0c.png';
const KIT = 'kit:kt_0a1b2c3d/it_4e5f6a7b';
const RAW = `Keep the face from @[references:${UP}] and the outfit of @[references:${KIT}].\n\nsecond paragraph @kits.mara.face stays text, @[nofield] too.`;
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const block = () => document.body.querySelector('[data-testid="parameter-block"]') as HTMLElement;

beforeEach(() => {
	localStorage.clear();
	forgetFoldPreference();
	copyText.mockClear();
});

afterEach(() => vi.restoreAllMocks());

describe('reference tokens in long text', () => {
	it('shows chips instead of the raw token and keeps everything else as plain text', () => {
		mountComponent(ParametersView, {
			params: { positive_prompt: RAW },
			formData: { references: [{ relative_path: UP, name: 'mara.png', url: '/api/media/uploads/m.png', type: 'image' }] }
		});
		const text = block().textContent ?? '';
		expect(text).not.toContain('@[references:');
		expect(text).toContain('Picture 1');
		expect(text).toContain('@kits.mara.face stays text');
		expect(text).toContain('@[nofield] too');
		expect(block().querySelector('[data-ref-chip="media"]')).not.toBeNull();
		expect(block().querySelector('[data-ref-chip="kit"]')).not.toBeNull();
	});

	it('numbers references without form data and shows a thumbnail from the upload url', () => {
		mountComponent(ParametersView, { params: { notes: `a @[references:${UP}] b @[references:uploads/other-file.png] c ${'x'.repeat(60)}` } });
		const chips = [...block().querySelectorAll('[data-ref-chip="media"]')];
		expect(chips.map((c) => c.textContent?.trim())).toEqual(['Picture 1', 'Picture 2']);
		expect(block().querySelector('img')?.getAttribute('src')).toBe(`/api/media/uploads/${UP.split('/')[1]}`);
	});

	it('shows a missing reference chip for a token it cannot read', () => {
		mountComponent(ParametersView, { params: { notes: `see @[references:weird-key] ${'y'.repeat(80)}` } });
		expect(block().querySelector('[data-ref-chip="missing"]')?.textContent).toContain('missing reference');
	});

	it('copies the raw stored text, tokens included, from the icon and from the box', async () => {
		mountComponent(ParametersView, { params: { positive_prompt: RAW } });
		(block().querySelector('button[aria-label="Copy Positive prompt"]') as HTMLButtonElement).click();
		await flush();
		expect(copyText).toHaveBeenLastCalledWith(RAW);
		copyText.mockClear();
		(block().querySelector('div.cursor-pointer') as HTMLElement).click();
		await flush();
		expect(copyText).toHaveBeenCalledWith(RAW);
	});

	it('still clamps with chips inside and offers Show more only on overflow', () => {
		vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(400);
		vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(120);
		mountComponent(ParametersView, { params: { positive_prompt: RAW } });
		const clamp = block().querySelector('.clamp') as HTMLElement;
		expect(clamp.querySelector('[data-ref-chip]')).not.toBeNull();
		const more = [...block().querySelectorAll('button')].find((b) => b.textContent?.includes('Show more')) as HTMLButtonElement;
		expect(more).toBeDefined();
		more.click();
		flushSync();
		expect(block().textContent).toContain('Show less');
		expect(copyText).not.toHaveBeenCalled();
	});

	it('leaves a value without tokens as plain text, with no chip machinery', () => {
		mountComponent(ParametersView, { params: { positive_prompt: 'a red bicycle '.repeat(10) } });
		expect(block().querySelector('[data-ref-chip]')).toBeNull();
		expect(block().textContent).toContain('a red bicycle');
	});

	it('detects tokens by syntax in any long value, whatever the key', () => {
		mountComponent(ParametersView, { params: { zzz: `${'p'.repeat(90)} @[whatever:${UP}]` } });
		expect(block().querySelector('[data-ref-chip="media"]')).not.toBeNull();
	});
});
