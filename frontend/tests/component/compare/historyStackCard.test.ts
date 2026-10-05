import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getGenerationThumbnailURL: vi.fn(() => '/thumb.png')
	}
}));

const { default: GenerationCard } = await import('$lib/components/GenerationCard.svelte');
const { createClassComponent } = await import('svelte/legacy');

function item(over: Record<string, unknown> = {}) {
	return {
		id: 'gen-1',
		form_data: {},
		status: 'completed' as const,
		progress: 1,
		created_at: '2026-10-05T10:00:00Z',
		updated_at: '2026-10-05T10:00:00Z',
		files: [],
		rating: 0,
		is_favorite: false,
		...over
	};
}

const stack = {
	grid: { id: 'g1', cols: 4, rows: 3, cell_count: 12 },
	grid_id: 'g1',
	grid_x: 0,
	grid_y: 0,
	axis_values: { sampler: 'euler', scheduler: 'simple' }
};

function mountCard(generation: ReturnType<typeof item>, extra: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationCard as never,
		target,
		props: { generation: generation as never, tile: { width: 320, height: 240 }, showCheckbox: true, ...extra }
	});
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountCard> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

const q = (selector: string) => mounted?.target.querySelector(selector) ?? null;

describe('history stack card', () => {
	it('badges a grid entry with columns × rows', () => {
		mounted = mountCard(item(stack));
		expect(q('[data-testid="grid-stack-chip"]')?.textContent?.trim()).toBe('4 × 3');
	});

	it('names the axes and counts the cells in the info bar', () => {
		mounted = mountCard(item(stack));
		expect(q('[data-testid="grid-stack-title"]')?.textContent?.trim()).toBe('Sampler × Scheduler');
		expect(q('[data-testid="grid-stack-count"]')?.textContent?.trim()).toBe('12 cells');
	});

	it('draws the stack layers behind the card', () => {
		mounted = mountCard(item(stack));
		expect(q('[data-testid="grid-stack-layers"]')).not.toBeNull();
	});

	it('chips a cell listed on its own and draws no stack', () => {
		mounted = mountCard(item({ grid_id: 'g1', grid_x: 2, grid_y: 1, grid_cols: 4, axis_values: { sampler: 'dpmpp_2m' } }));
		expect(q('[data-testid="grid-cell-chip"]')?.textContent?.trim()).toBe('cell 7');
		expect(q('[data-testid="grid-stack-chip"]')).toBeNull();
		expect(q('[data-testid="grid-stack-layers"]')).toBeNull();
		expect(q('[data-testid="grid-stack-count"]')).toBeNull();
	});

	it('leaves an ordinary generation untouched', () => {
		mounted = mountCard(item());
		expect(q('[data-testid="grid-stack-chip"]')).toBeNull();
		expect(q('[data-testid="grid-cell-chip"]')).toBeNull();
		expect(q('[data-testid="grid-stack-layers"]')).toBeNull();
	});

	it('moves the chip clear of the selection checkbox', () => {
		mounted = mountCard(item(stack), { showCheckbox: true });
		expect(q('[data-testid="grid-stack-chip"]')?.className).toContain('left-9');
		mounted.destroy();
		mounted = mountCard(item(stack), { showCheckbox: false });
		expect(q('[data-testid="grid-stack-chip"]')?.className).toContain('left-2');
	});
});
