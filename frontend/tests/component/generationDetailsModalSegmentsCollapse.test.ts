// @vitest-environment jsdom
//
// Multi-segment generations can carry dozens of prompt segments, and the
// Segments card used to render them all open, making the modal very long.
// It now starts collapsed and expands on click.
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
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
		setOnAuthExpired: vi.fn()
	}
}));

const { default: GenerationDetailsModal } = await import(
	'$lib/components/modals/GenerationDetailsModal.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function baseGeneration(segments: Array<Record<string, unknown>>) {
	return {
		id: `gen-${Math.random()}`,
		form_data: {},
		status: 'completed' as const,
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		files: [],
		segments,
		rating: 0,
		is_favorite: false
	};
}

// The modal's root node carries `use:portal`, which relocates it to
// `document.body` (see src/lib/actions/portal.ts) so it isn't nested under
// `target` once mounted -- assertions have to look at `document.body`.
function mountModal(generation: ReturnType<typeof baseGeneration>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationDetailsModal as never,
		target,
		props: { generation: generation as never, isOpen: true }
	});
	return {
		target,
		component,
		text: () => document.body.textContent ?? '',
		toggleButton: () =>
			Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.includes('Segments')),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountModal> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('GenerationDetailsModal Segments section', () => {
	const segments = [
		{
			prompt_index: 0,
			segment_index: 0,
			channel: 'positive',
			text: 'a very distinctive rooftop cat prompt segment'
		},
		{
			prompt_index: 0,
			segment_index: 1,
			channel: 'negative',
			text: 'blurry, low quality'
		}
	];

	it('is collapsed by default: shows the count but not segment text', () => {
		mounted = mountModal(baseGeneration(segments));

		const toggle = mounted.toggleButton();
		expect(toggle).toBeTruthy();
		expect(toggle?.getAttribute('aria-expanded')).toBe('false');
		expect(mounted.text()).toContain('Segments');
		expect(mounted.text()).toContain('2');
		expect(mounted.text()).not.toContain('a very distinctive rooftop cat prompt segment');
	});

	it('expands to reveal segment text on click, and collapses again on a second click', async () => {
		mounted = mountModal(baseGeneration(segments));

		const toggle = mounted.toggleButton();
		toggle?.click();
		await Promise.resolve();

		expect(toggle?.getAttribute('aria-expanded')).toBe('true');
		expect(mounted.text()).toContain('a very distinctive rooftop cat prompt segment');

		toggle?.click();
		await Promise.resolve();

		expect(toggle?.getAttribute('aria-expanded')).toBe('false');
		expect(mounted.text()).not.toContain('a very distinctive rooftop cat prompt segment');
	});
});
