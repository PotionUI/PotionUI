import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { CloudSpend } from '$lib/utils/cloudCost';
import CloudSpendSection from '../../src/routes/admin/components/CloudSpendSection.svelte';

let target: HTMLDivElement | undefined;
let component: ReturnType<typeof mount> | null = null;

function render(spend: CloudSpend | null) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(CloudSpendSection, { target, props: { spend } });
	flushSync();
	return target;
}

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
});

const spend: CloudSpend = {
	from: null,
	to: null,
	total_usd: '4.5',
	entries: 7,
	unpriced: 2,
	by_backend: [
		{ backend_id: 'b2', backend_name: 'Small', amount_usd: '0.5', source: 'estimate', entries: 2, unpriced: 0 },
		{ backend_id: 'b1', backend_name: 'OpenRouter', amount_usd: '4', source: 'provider', entries: 5, unpriced: 2 }
	],
	by_model: [
		{ model_id: 'm2', model: null, amount_usd: null, source: 'unknown', entries: 1, unpriced: 1 },
		{ model_id: 'm1', model: 'Veo 3.1', amount_usd: '3', source: 'provider', entries: 3, unpriced: 0 }
	]
};

describe('CloudSpendSection', () => {
	it('shows the total, both tables sorted by amount, and the estimate and unpriced hint', () => {
		const root = render(spend);
		expect(root.querySelector('[data-spend-total]')?.textContent).toBe('$4.50');
		const backendRows = Array.from(root.querySelectorAll('[data-spend-table="By backend"] [data-spend-row]')).map((r) => r.textContent);
		expect(backendRows[0]).toContain('OpenRouter');
		expect(backendRows[1]).toContain('Small');
		expect(backendRows[1]).toContain('est.');
		const modelRows = Array.from(root.querySelectorAll('[data-spend-table="By model"] [data-spend-row]')).map((r) => r.textContent);
		expect(modelRows[0]).toContain('Veo 3.1');
		expect(modelRows[1]).toContain('Unknown model');
		expect(modelRows[1]).toContain('unpriced');
		const note = root.querySelector('[data-spend-note]')?.textContent ?? '';
		expect(note).toContain('2 jobs with no known price');
		expect(note).toContain('estimated');
	});

	it('renders nothing when there is no cloud spend', () => {
		expect(render(null).querySelector('[data-cloud-spend]')).toBeNull();
		component && unmount(component);
		target?.remove();
		const empty = render({ ...spend, entries: 0, unpriced: 0, total_usd: '0', by_backend: [], by_model: [] });
		expect(empty.querySelector('[data-cloud-spend]')).toBeNull();
		expect(empty.textContent?.trim()).toBe('');
	});
});
