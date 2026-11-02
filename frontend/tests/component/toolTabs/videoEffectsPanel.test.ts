import { afterEach, describe, expect, it } from 'vitest';
import { mountComponent } from '../helpers/mount';
import { effectGroups, effectTools } from '$lib/effects/catalog';
import { EFFECT_CATALOG } from '../fixtures/effectCatalog';

const { default: VideoToolOptions } = await import('$lib/components/tool-tabs/video/VideoToolOptions.svelte');

const groups = effectGroups(effectTools(EFFECT_CATALOG));

function panel(props: Record<string, unknown> = {}) {
	const calls: Array<[string, unknown]> = [];
	const view = mountComponent(VideoToolOptions, {
		tool: 'effects',
		op: 'film_grain',
		params: { amount: 30, size: 1.5, color: false, seed: 0 },
		effect: 'film_grain',
		effects: groups,
		clip: { width: 1920, height: 1080, duration: 8 },
		playhead: 1,
		outputLength: 8,
		onparams: (op: string, params: unknown) => calls.push([op, params]),
		onaspect: () => undefined,
		oneffect: (id: string) => calls.push(['effect', id]),
		onsetin: () => undefined,
		onsetout: () => undefined,
		onpreview: () => calls.push(['preview', null]),
		oncancelpreview: () => undefined,
		onframe: () => undefined,
		onpickaudio: () => undefined,
		...props
	});
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	const all = (sel: string) => [...view.target.querySelectorAll<HTMLElement>(sel)];
	return { view, calls, q, all };
}

afterEach(() => {
	document.body.innerHTML = '';
});

describe('the Effects panel', () => {
	it('lists every catalog effect under its group, sorted by label', () => {
		const { all } = panel();
		expect(all('[data-effect-group]').map((el) => el.dataset.effectGroup)).toEqual(['colour', 'texture', 'more']);
		expect(all('[data-effect-card]').map((el) => el.dataset.effectCard)).toEqual(['fix_colors', 'film_grain', 'retro-tape.scanlines', 'retro-tape.wobble']);
		expect(all('[data-effect-group] > span').map((el) => el.textContent)).toEqual(['Colour', 'Texture', 'More']);
	});

	it('never says where an effect came from', () => {
		const { view } = panel({ op: 'retro-tape.scanlines', effect: 'retro-tape.scanlines', params: {} });
		const text = view.target.textContent!.toLowerCase();
		expect(text).toContain('scanlines');
		expect(text).not.toContain('plugin');
		expect(text).not.toContain('retro-tape');
		expect(text).not.toContain('core');
		expect(view.target.innerHTML).not.toMatch(/title=/);
	});

	it('opens the active effect with its catalog params and sends edits for that effect', () => {
		const { q, calls } = panel({ op: 'retro-tape.scanlines', effect: 'retro-tape.scanlines', params: { strength: 35, gap: 3, roll: 8 } });
		const card = q('[data-effect-card="retro-tape.scanlines"]')!;
		expect(card.dataset.open).toBe('true');
		expect(card.querySelector('[data-effect-description]')!.textContent).toContain('CRT lines');
		expect(card.querySelectorAll('[data-param]').length).toBe(3);
		expect(q('[data-effect-card="film_grain"]')!.dataset.open).toBeUndefined();
		q<HTMLButtonElement>('[data-effect-card="fix_colors"] button')!.click();
		expect(calls).toContainEqual(['effect', 'fix_colors']);
	});

	it('shows Preview 3 s only when the live preview falls back', () => {
		expect(panel().q('[data-preview-clip]')).toBeNull();
		const fallback = panel({ showClip: true });
		expect(fallback.q('[data-preview-clip]')!.textContent).toContain('Preview 3 s');
		fallback.q<HTMLButtonElement>('[data-preview-clip]')!.click();
		expect(fallback.calls).toContainEqual(['preview', null]);
	});

	it('picks effects from a strip on a phone', () => {
		const { all, q, calls } = panel({ phone: true });
		expect(all('[data-effect-chip]').map((el) => el.dataset.effectChip)).toEqual(['fix_colors', 'film_grain', 'retro-tape.scanlines', 'retro-tape.wobble']);
		expect(q('[data-effect-chip="film_grain"]')!.getAttribute('aria-pressed')).toBe('true');
		q<HTMLButtonElement>('[data-effect-chip="retro-tape.wobble"]')!.click();
		expect(calls).toContainEqual(['effect', 'retro-tape.wobble']);
		expect(q('[data-preview-clip]')).toBeNull();
	});

	it('says so when there are no effects', () => {
		expect(panel({ effects: [], effect: null, op: null }).q('[data-effects-empty]')).not.toBeNull();
	});
});
