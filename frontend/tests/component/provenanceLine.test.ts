// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const getOrganizeProvenance = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: { getOrganizeProvenance: (...args: unknown[]) => getOrganizeProvenance(...args) }
}));

const { default: ProvenanceLine } = await import('../../src/lib/components/organize/ProvenanceLine.svelte');

async function settle() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let instance: ReturnType<typeof mount> | null = null;
let target: HTMLElement;

function render(itemType: 'generation' | 'upload' = 'generation', itemId = 'g1') {
	instance = mount(ProvenanceLine, { target, props: { itemType, itemId } });
}

function provenanceRow(over: Record<string, unknown> = {}) {
	return {
		rule_id: 'r1',
		rule_name: 'Krea landscapes',
		rule_deleted: false,
		run_id: 'run1',
		action: 'add_to_collection',
		target_type: 'collection',
		target_id: 'c1',
		target_name: 'Landscapes',
		created_at: '2026-10-05T09:12:00+00:00',
		...over
	};
}

beforeEach(() => {
	target = document.createElement('div');
	document.body.appendChild(target);
	getOrganizeProvenance.mockReset();
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target.remove();
});

describe('ProvenanceLine', () => {
	it('shows the rule that filed the item, linked to the rule', async () => {
		getOrganizeProvenance.mockResolvedValue({
			success: true,
			data: [provenanceRow(), provenanceRow({ action: 'add_tags', target_type: 'tag', target_name: 'landscape' })]
		});
		render();
		await settle();
		expect(getOrganizeProvenance).toHaveBeenCalledWith('generation', 'g1');
		expect(target.querySelectorAll('li')).toHaveLength(1);
		expect(target.textContent).toContain('Added by rule');
		expect(target.textContent).toContain('Landscapes, landscape');
		const link = target.querySelector('a') as HTMLAnchorElement;
		expect(link.textContent?.trim()).toBe('Krea landscapes');
		expect(link.getAttribute('href')).toBe('/auto-organize?subject=generations&rule=r1');
	});

	it('says a deleted rule without a link', async () => {
		getOrganizeProvenance.mockResolvedValue({ success: true, data: [provenanceRow({ rule_deleted: true })] });
		render('upload', 'u1');
		await settle();
		expect(target.textContent).toContain('Added by a deleted rule');
		expect(target.querySelector('a')).toBeNull();
	});

	it('renders nothing when no rule filed the item', async () => {
		getOrganizeProvenance.mockResolvedValue({ success: true, data: [] });
		render();
		await settle();
		expect(target.querySelector('[data-testid="provenance-line"]')).toBeNull();
	});

	it('stays silent when the request fails', async () => {
		getOrganizeProvenance.mockRejectedValue(new Error('boom'));
		render();
		await settle();
		expect(target.textContent?.trim()).toBe('');
	});
});
