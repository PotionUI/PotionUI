// @vitest-environment jsdom
//
// A Video Director generation's shots live in `form_data.video_director`, not
// in the generic prompt `segments` array (that's the segmented-prompt path;
// a Director generation submits a single representative prompt instead). The
// modal previously showed nothing about the Director document at all, which
// looks exactly like "the shots were never saved" even though they round-trip
// fine on the backend.
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

function baseGeneration(form_data: Record<string, unknown>) {
	return {
		id: `gen-${Math.random()}`,
		form_data,
		status: 'completed' as const,
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		files: [],
		segments: [], // present so the modal doesn't attempt a detail re-fetch
		rating: 0,
		is_favorite: false
	};
}

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
		// GenerationDetailsModal wraps itself in `<div use:portal>` on top of
		// BaseModal's own portal (src/lib/actions/portal.ts), so its content
		// lands on document.body, never inside `target`.
		text: () => document.body.textContent ?? '',
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

describe('GenerationDetailsModal Video Director section', () => {
	it('renders each shot prompt from form_data.video_director.segments', () => {
		mounted = mountModal(
			baseGeneration({
				video_director: {
					schema_version: 1,
					mode: 'director',
					segments: [
						{ id: 'seg-1', prompt: 'a cat on a rooftop', sub_type: 't2v' },
						{ id: 'seg-2', prompt: 'the cat jumps down', sub_type: 'chain' }
					],
					media: [{ id: 'm1', role: 'keyframe', at: 0.5 }],
					audio: [{ id: 'a1', role: 'condition' }]
				}
			})
		);

		const text = mounted.text();
		expect(text).toContain('Director shots');
		expect(text).toContain('a cat on a rooftop');
		expect(text).toContain('the cat jumps down');
		expect(text).toContain('1 keyframe');
		expect(text).toContain('1 audio track');
	});

	it('renders nothing when the generation has no video_director document', () => {
		mounted = mountModal(baseGeneration({}));

		expect(mounted.text()).not.toContain('Director shots');
	});
});
