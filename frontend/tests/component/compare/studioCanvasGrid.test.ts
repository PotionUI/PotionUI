import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		setOnAuthExpired: vi.fn(),
		getGenerationById: vi.fn().mockResolvedValue({ success: false }),
		getGenerationImageURL: vi.fn(() => '/media.mp4'),
		getClient: vi.fn(() => ({ get: vi.fn(), post: vi.fn() }))
	}
}));

const { default: StudioCanvas } = await import('../../../src/routes/generate/components/studio/StudioCanvas.svelte');
const { createClassComponent, flushSync } = await import('svelte/legacy').then(async (m) => ({
	...m,
	flushSync: (await import('svelte')).flushSync
}));
const { setAxis, setCompare, turnOffCompare } = await import('$lib/generation/compare/compareStore.svelte');

const axis = {
	field: 'sampler',
	type: 'select',
	label: 'Sampler',
	values: ['euler', 'er_sde', 'dpmpp_2m'].map((v) => ({ value: v, label: v }))
};

const tab = {
	id: 'studio-tab',
	generation: {
		currentGeneration: null,
		isGenerating: false,
		currentProgress: null,
		batchImages: [],
		batchVideos: [],
		batchAudios: [],
		batchMeshes: []
	}
} as never;

const mounted: Array<() => void> = [];

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({ component: StudioCanvas as never, target, props: { tab } });
	mounted.push(() => {
		instance.$destroy();
		target.remove();
	});
	return target;
}

afterEach(() => {
	turnOffCompare('studio-tab');
	while (mounted.length) mounted.pop()?.();
});

describe('Studio stage compare grid', () => {
	it('shows the idle hint when compare is off', () => {
		const target = mount();
		expect(target.textContent).toContain('Describe something below');
		expect(target.querySelector('[data-testid="compare-grid"]')).toBeNull();
	});

	it('swaps the stage for the compare grid once compare is armed with an axis', async () => {
		const target = mount();
		setCompare('studio-tab', { armed: true });
		setAxis('studio-tab', 'x', axis);
		await new Promise((resolve) => setTimeout(resolve, 0));
		flushSync();
		expect(target.querySelector('[data-testid="studio-compare-stage"]')).not.toBeNull();
		expect(target.querySelector('[data-testid="compare-grid"]')).not.toBeNull();
		expect(target.textContent).not.toContain('Describe something below');
		expect(target.querySelectorAll('[data-testid="compare-x-label"]').length).toBe(3);
	});
});
