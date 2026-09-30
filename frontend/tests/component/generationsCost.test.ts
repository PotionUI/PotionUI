import { describe, it, expect, vi, afterEach } from 'vitest';

const row = (id: string, cost: unknown) => ({
	id,
	form_data: {},
	status: 'completed',
	progress: 1,
	created_at: '2026-08-14T00:00:00Z',
	completed_at: '2026-08-14T00:00:05Z',
	updated_at: '2026-08-14T00:00:05Z',
	files: [],
	rating: 0,
	is_favorite: false,
	user_id: 'user-1',
	has_run_report: false,
	preset_name: `Preset ${id}`,
	mode: 't2i',
	cost
});

vi.mock('$lib/services/admin-api', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/services/admin-api')>();
	return {
		...actual,
		getUsers: vi.fn(async () => ({ success: true, data: [{ id: 'user-1', username: 'alice' }] })),
		getAdminGenerations: vi.fn(async () => ({
			success: true,
			data: {
				generations: [
					row('provider', { amount_usd: '0.0731', source: 'provider', entries: 1, unpriced: 0 }),
					row('estimated', { amount_usd: '1.2', source: 'estimate', entries: 1, unpriced: 0 }),
					row('unpriced', { amount_usd: null, source: 'unknown', entries: 1, unpriced: 1 }),
					row('local', null)
				],
				total: 4
			}
		}))
	};
});

const { default: GenerationsTab } = await import('../../src/routes/admin/components/GenerationsTab.svelte');
const { default: GenerationCostSection } = await import('../../src/routes/admin/components/GenerationCostSection.svelte');
const { mount, unmount, flushSync } = await import('svelte');

let target: HTMLDivElement | undefined;
let component: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
	target = undefined;
});

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

function rowOf(label: string): HTMLElement {
	const found = Array.from(target!.querySelectorAll<HTMLElement>('.dt-scroll > .dt-row:not(.dt-row--head)')).find((r) =>
		r.textContent?.includes(label)
	);
	expect(found, label).toBeTruthy();
	return found!;
}

describe('admin generations cost column', () => {
	it('shows formatted amounts, an estimate marker, unpriced, and a dash for no cost', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		component = mount(GenerationsTab, { target });
		await settle();

		expect(target.textContent).toContain('Cost');
		const provider = rowOf('Preset provider');
		expect(provider.querySelector('[data-cost="amount"]')?.textContent?.trim()).toBe('$0.07');
		expect(provider.querySelector('[data-cost-estimate]')).toBeNull();

		const estimated = rowOf('Preset estimated');
		expect(estimated.querySelector('[data-cost="amount"]')?.textContent).toContain('$1.20');
		expect(estimated.querySelector('[data-cost-estimate]')?.textContent).toBe('est.');

		expect(rowOf('Preset unpriced').querySelector('[data-cost="unpriced"]')?.textContent).toContain('unpriced');
		expect(rowOf('Preset local').querySelector('[data-cost="none"]')?.textContent).toBe('—');
	});
});

describe('generation cost detail', () => {
	it('lists the total and each job with its task and source', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		component = mount(GenerationCostSection, {
			target,
			props: {
				cost: {
					amount_usd: '0.25',
					source: 'mixed',
					entries: 2,
					unpriced: 0,
					items: [
						{ id: 'c1', backend_id: 'b1', model_id: 'm1', amount_usd: '0.2', source: 'provider', detail: { task: 'txt2video' }, created_at: '2026-08-14T00:00:00Z' },
						{ id: 'c2', backend_id: 'b1', model_id: 'm1', amount_usd: '0.05', source: 'estimate', detail: {}, created_at: '2026-08-14T00:00:00Z' }
					]
				}
			}
		});
		await settle();
		const text = target.textContent ?? '';
		expect(text).toContain('Cost');
		expect(text).toContain('Mixed');
		expect(text).toContain('$0.25');
		const items = target.querySelectorAll('[data-cost-item]');
		expect(items).toHaveLength(2);
		expect(items[0].textContent).toContain('Text to video');
		expect(items[0].textContent).toContain('$0.20');
		expect(items[1].textContent).toContain('Job 2');
		expect(items[1].querySelector('[data-cost-estimate]')).toBeTruthy();
	});
});
