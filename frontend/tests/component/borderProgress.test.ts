import { afterEach, describe, expect, it } from 'vitest';
import { mountComponent } from './helpers/mount';
import { borderProgressOf } from '$lib/components/generation-panel/borderProgress';

const { default: BorderProgress } = await import('$lib/components/generation-panel/BorderProgress.svelte');

describe('borderProgressOf', () => {
	it('maps each editor run state onto the Generate panel bar states', () => {
		expect(borderProgressOf(null)).toEqual({ state: 'idle', percent: null });
		expect(borderProgressOf({ status: 'queued' })).toEqual({ state: 'loading', percent: null });
		expect(borderProgressOf({ status: 'running', progress: null })).toEqual({ state: 'loading', percent: null });
		expect(borderProgressOf({ status: 'running', progress: 0.4 })).toEqual({ state: 'running', percent: 40 });
		expect(borderProgressOf({ status: 'running', progress: 0 })).toEqual({ state: 'running', percent: 0 });
		expect(borderProgressOf({ status: 'running', progress: 1.7 })).toEqual({ state: 'running', percent: 100 });
		expect(borderProgressOf({ status: 'done', progress: 1 })).toEqual({ state: 'idle', percent: null });
		expect(borderProgressOf({ status: 'failed' })).toEqual({ state: 'idle', percent: null });
		expect(borderProgressOf({ status: 'cancelled', progress: 0.2 })).toEqual({ state: 'idle', percent: null });
	});
});

describe('BorderProgress', () => {
	let view: ReturnType<typeof mountComponent> | undefined;
	afterEach(() => view?.destroy());

	it('is hidden at rest, indeterminate while waiting and a percent bar while running', () => {
		view = mountComponent(BorderProgress, { progress: { state: 'idle', percent: null } });
		const bar = () => view!.target.querySelector<HTMLElement>('[role="progressbar"]')!;
		expect(bar().hidden).toBe(true);
		view.destroy();
		view = mountComponent(BorderProgress, { progress: { state: 'loading', percent: null } });
		expect(bar().hidden).toBe(false);
		expect(bar().hasAttribute('aria-valuenow')).toBe(false);
		expect(bar().getAttribute('data-border-progress')).toBe('loading');
		view.destroy();
		view = mountComponent(BorderProgress, { progress: { state: 'running', percent: 40 } });
		expect(bar().getAttribute('aria-valuenow')).toBe('40');
		expect(bar().querySelector<HTMLElement>('.border-progress-fill')!.getAttribute('style')).toContain('width: 40%');
	});
});
