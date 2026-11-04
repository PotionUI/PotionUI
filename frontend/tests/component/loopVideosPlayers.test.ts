// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync } from 'svelte';

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
const { default: LibraryItemModal } = await import('$lib/components/library/LibraryItemModal.svelte');
const { loopVideos } = await import('$lib/stores/loopVideos');
const { createClassComponent } = await import('svelte/legacy');
const { get } = await import('svelte/store');

const generation = {
	id: 'gen-loop',
	form_data: {},
	status: 'completed' as const,
	progress: 1,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-01T00:00:00Z',
	segments: [],
	rating: 0,
	is_favorite: false,
	files: [{ file_path: '/outputs/gen-loop/0.mp4', file_type: 'video', width: 512, height: 512 }]
};

const libraryItem = {
	id: 'lib-1',
	url: '/media/clip.mp4',
	media_type: 'video',
	tags: [],
	provenance: null
};

let destroy: (() => void) | undefined;

beforeEach(() => {
	localStorage.clear();
	if (get(loopVideos) === false) loopVideos.toggle();
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
});

function mountComponent(component: unknown, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({ component: component as never, target, props });
	destroy = () => {
		instance.$destroy();
		target.remove();
	};
	flushSync();
}

const video = () => document.body.querySelector<HTMLVideoElement>('video')!;
const toggle = () => document.body.querySelector<HTMLButtonElement>('[data-loop-toggle] button')!;

function exerciseToggle() {
	expect(video().loop).toBe(true);
	expect(toggle().getAttribute('aria-pressed')).toBe('true');
	toggle().click();
	flushSync();
	expect(video().loop).toBe(false);
	expect(toggle().getAttribute('aria-pressed')).toBe('false');
}

describe('video loop preference', () => {
	it('generation details loops by default and the toggle turns it off', () => {
		mountComponent(GenerationDetailsModal, { generation: generation as never, isOpen: true });
		exerciseToggle();
	});

	it('library viewer loops by default and the toggle turns it off', () => {
		mountComponent(LibraryItemModal, {
			item: libraryItem as never,
			onClose: () => {},
			onDeleteRequest: () => {}
		});
		exerciseToggle();
	});

	it('one viewer toggle updates the shared preference seen by the other', () => {
		mountComponent(LibraryItemModal, {
			item: libraryItem as never,
			onClose: () => {},
			onDeleteRequest: () => {}
		});
		toggle().click();
		flushSync();
		destroy?.();
		mountComponent(GenerationDetailsModal, { generation: generation as never, isOpen: true });
		expect(video().loop).toBe(false);
	});
});
