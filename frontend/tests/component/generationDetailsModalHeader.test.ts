// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getOrganizeProvenance: vi.fn(() => Promise.resolve({ success: true, data: [] })),
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			post: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getGenerationParams: vi.fn().mockResolvedValue({ success: true, data: { parameters: {}, models: [] } }),
		getGenerationById: vi.fn().mockResolvedValue({ success: false, error: 'not mocked' }),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getBaseURL: vi.fn(() => ''),
		getToken: vi.fn(() => null),
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn()
	}
}));

const { default: GenerationDetailsModal } = await import('$lib/components/modals/GenerationDetailsModal.svelte');
const { createClassComponent } = await import('svelte/legacy');

const generation = {
	id: 'gen-header',
	form_data: {},
	status: 'completed' as const,
	progress: 1,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-01T00:00:00Z',
	segments: [],
	rating: 0,
	is_favorite: false,
	files: [{ file_path: '/outputs/gen-header/0.png', file_type: 'image', width: 512, height: 512 }]
};

let destroy: (() => void) | undefined;

afterEach(() => {
	destroy?.();
	destroy = undefined;
});

function mountWith(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationDetailsModal as never,
		target,
		props: { generation: generation as never, isOpen: true, ...props }
	});
	destroy = () => {
		component.$destroy();
		target.remove();
	};
	return component;
}

describe('GenerationDetailsModal header', () => {
	it('keeps the id as a chip hidden on phones next to a copy button, with no status badge', () => {
		mountWith({});

		const chip = document.body.querySelector('[data-details-meta] code');
		expect(chip?.textContent).toBe('gen-header');
		expect(chip?.className).toContain('hidden');
		expect(chip?.className).toContain('sm:inline');
		expect(document.body.querySelector('[data-details-meta] button[aria-label="Copy ID"]')).not.toBeNull();
		expect(document.body.textContent).not.toMatch(/completed/i);
	});

	it('reserves a minimum width for the title', () => {
		mountWith({});

		const title = document.body.querySelector('h2');
		expect(title?.parentElement?.className).toContain('min-w-[8rem]');
	});

	it('keeps Generation Details as the dialog name while the title shows the preset', () => {
		mountWith({});

		expect(document.body.querySelector('[role="dialog"]')?.getAttribute('aria-label')).toBe('Generation Details');
	});

	it.each([
		['completed', 'text-success'],
		['failed', 'text-danger'],
		['running', 'text-signal'],
		['pending', 'text-warning'],
		['cancelled', 'text-fg-muted']
	])('colours the media icon for a %s generation', (status, cls) => {
		mountWith({ generation: { ...generation, status } as never });

		const icon = document.body.querySelector('[data-details-status]') as HTMLButtonElement;
		expect(icon.getAttribute('data-details-status')).toBe(status);
		expect(icon.querySelector('svg')?.getAttribute('class')).toContain(cls);
		expect(icon.getAttribute('aria-label')).toContain(status);
	});

	it('shows the status word in the tooltip and reaches it by keyboard', () => {
		mountWith({ generation: { ...generation, status: 'failed' } as never });

		const icon = document.body.querySelector('[data-details-status]') as HTMLButtonElement;
		expect(icon.tagName).toBe('BUTTON');
		expect(icon.tabIndex).toBe(0);
		expect(icon.hasAttribute('data-has-tooltip')).toBe(true);
		icon.focus();
		expect(document.activeElement).toBe(icon);
	});

	it('moves the preset name into the meta line when the host passes a title', () => {
		mountWith({ title: 'Compare grid' });

		expect(document.body.querySelector('h2')?.textContent).toBe('Compare grid');
		expect(document.body.querySelector('[data-details-meta]')?.textContent).toContain('Unknown preset');
	});

	it('gives the date, duration and id a decorative leading icon and no dot separators', () => {
		mountWith({ generation: { ...generation, run_seconds: 102, completed_at: '2026-01-01T00:02:00Z' } as never });

		const meta = document.body.querySelector('[data-details-meta]') as HTMLElement;
		const icons = [...meta.querySelectorAll('[aria-hidden="true"] svg')];
		expect(icons).toHaveLength(3);
		expect(meta.textContent).not.toContain('·');
		expect(meta.querySelector('[data-testid="detail-run-line"] svg')).not.toBeNull();
		expect(meta.querySelector('[data-testid="detail-generation-time"]')?.parentElement?.querySelector('svg')).not.toBeNull();
	});

	it('words the duration without "took" and keeps "failed after" for a failure', () => {
		mountWith({ generation: { ...generation, run_seconds: 102, completed_at: '2026-01-01T00:02:00Z' } as never });
		expect(document.body.querySelector('[data-testid="detail-generation-time"]')?.textContent).toBe('1 min 42 s');
		document.body.innerHTML = '';
		mountWith({ generation: { ...generation, status: 'failed', run_seconds: 38, completed_at: undefined } as never });
		expect(document.body.querySelector('[data-testid="detail-generation-time"]')?.textContent).toBe('failed after 38 s');
	});
});
