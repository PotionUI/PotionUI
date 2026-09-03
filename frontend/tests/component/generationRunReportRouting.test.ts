// @vitest-environment jsdom
//
// The admin generation detail page's Routing section (migration 009): chosen
// backend + reason, other candidates with their reasons, and a collapsed
// rule-trace disclosure. Hidden entirely when `generation.routing` is null -
// this is an admin-only surface, the user-facing history modal never renders it.
import { describe, it, expect, afterEach } from 'vitest';
import type { AdminGenerationListItem, RunReport } from '$lib/services/admin-api';
import type { RoutingDecision } from '$lib/types/history';

const { default: GenerationRunReport } = await import(
	'../../src/routes/admin/components/GenerationRunReport.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function mount(generation: AdminGenerationListItem, report: RunReport | null) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationRunReport as never,
		target,
		props: { generation, report, username: 'alice' }
	});
	return {
		target,
		text: () => target.textContent ?? '',
		toggleButton: () =>
			Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Rule trace')),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

function baseGeneration(overrides: Partial<AdminGenerationListItem> = {}): AdminGenerationListItem {
	return {
		id: 'gen-1',
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
		...overrides
	};
}

const routing: RoutingDecision = {
	chosen: { backend_id: 'b1', backend_name: 'Local Backend', reason: 'default backend for this engine' },
	candidates: [
		{ backend_id: 'b1', backend_name: 'Local Backend', dropped: false, reasons: ['default backend for this engine'] },
		{ backend_id: 'b2', backend_name: 'Comfy Remote', dropped: true, reasons: ['missing requirement(s): FaceDetailer node'] }
	],
	rule_trace: [{ rule: 'enabled_for_engine', before: 0, after: 2, ms: 0.04 }]
};

describe('GenerationRunReport Routing section', () => {
	it('shows the chosen backend, its reason, and the other candidate with its reason', () => {
		mounted = mount(baseGeneration({ routing }), null);

		expect(mounted.text()).toContain('Routing');
		expect(mounted.text()).toContain('Local Backend');
		expect(mounted.text()).toContain('default backend for this engine');
		expect(mounted.text()).toContain('Comfy Remote');
		expect(mounted.text()).toContain('missing requirement(s): FaceDetailer node');
	});

	it('keeps the rule trace collapsed until clicked', async () => {
		mounted = mount(baseGeneration({ routing }), null);

		expect(mounted.text()).not.toContain('enabled_for_engine');

		const toggle = mounted.toggleButton();
		expect(toggle).toBeTruthy();
		expect(toggle?.getAttribute('aria-expanded')).toBe('false');

		toggle?.click();
		await Promise.resolve();

		expect(toggle?.getAttribute('aria-expanded')).toBe('true');
		expect(mounted.text()).toContain('enabled_for_engine');
	});

	it('renders no Routing section when routing is null', () => {
		mounted = mount(baseGeneration({ routing: null }), null);

		expect(mounted.toggleButton()).toBeUndefined();
		expect(mounted.text()).not.toContain('Rule trace');
	});
});
