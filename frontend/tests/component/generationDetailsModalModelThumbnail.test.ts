// @vitest-environment jsdom
//
// The Models section's thumbnail precedence (admin preview_media > legacy
// thumbnail file > icon fallback) lives inline in GenerationDetailsModal.svelte,
// so mounting the real component is the only way to exercise it against a
// server-shaped models payload from getGenerationParams.
import { describe, it, expect, vi, afterEach } from 'vitest';

const getGenerationParams = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			post: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getGenerationParams,
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

function baseGeneration() {
	return {
		id: `gen-${Math.random()}`,
		form_data: {},
		status: 'completed' as const,
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		files: [{ id: 1, file_type: 'image', is_final: true, file_path: 'a.png', created_at: '2026-01-01T00:00:00Z' }],
		segments: [],
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
	// BaseModal portals its content straight onto <body> (see
	// src/lib/actions/portal.ts) so it stays viewport-relative, not under
	// `target` - queries below must look at document.body.
	return {
		component,
		// The generation's own media preview is also an <img> (class includes
		// "absolute inset-0"); the Models section thumbnail is the other one.
		modelThumbnailImg: () =>
			Array.from(document.body.querySelectorAll('img')).find(
				(img) => !img.className.includes('absolute')
			) ?? null,
		modelIconFallback: () => document.body.querySelector('.w-11.h-11 svg'),
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
	getGenerationParams.mockReset();
});

describe('GenerationDetailsModal Models thumbnail precedence', () => {
	it('renders the admin-set preview_media over a legacy thumbnail file', async () => {
		getGenerationParams.mockResolvedValue({
			success: true,
			data: {
				parameters: {},
				models: [
					{
						id: 'm1',
						model_type: 'checkpoint',
						name: 'Test Model',
						preview_media: { url: '/api/media/files/admin-preview', type: 'image' },
						files: [
							{
								id: 'f1',
								file_type: 'thumbnail',
								url: '/api/media/files/f1',
								thumbnail_medium: '/api/media/files/f1?size=medium'
							}
						]
					}
				]
			}
		});

		mounted = mountModal(baseGeneration());
		await new Promise((resolve) => setTimeout(resolve, 0));
		await new Promise((resolve) => setTimeout(resolve, 0));

		const img = mounted.modelThumbnailImg();
		expect(img).not.toBeNull();
		expect(img?.getAttribute('src')).toBe('/api/media/files/admin-preview?size=medium');
	});

	it('falls back to the legacy thumbnail file when there is no preview_media', async () => {
		getGenerationParams.mockResolvedValue({
			success: true,
			data: {
				parameters: {},
				models: [
					{
						id: 'm1',
						model_type: 'checkpoint',
						name: 'Test Model',
						files: [
							{
								id: 'f1',
								file_type: 'thumbnail',
								url: '/api/media/files/f1',
								thumbnail_medium: '/api/media/files/f1?size=medium'
							}
						]
					}
				]
			}
		});

		mounted = mountModal(baseGeneration());
		await new Promise((resolve) => setTimeout(resolve, 0));
		await new Promise((resolve) => setTimeout(resolve, 0));

		const img = mounted.modelThumbnailImg();
		expect(img?.getAttribute('src')).toBe('/api/media/files/f1?size=medium');
	});

	it('falls back to the icon when the model has neither preview_media nor files', async () => {
		getGenerationParams.mockResolvedValue({
			success: true,
			data: {
				parameters: {},
				models: [{ id: 'm1', model_type: 'checkpoint', name: 'Test Model', files: [] }]
			}
		});

		mounted = mountModal(baseGeneration());
		await new Promise((resolve) => setTimeout(resolve, 0));
		await new Promise((resolve) => setTimeout(resolve, 0));

		expect(mounted.modelThumbnailImg()).toBeNull();
		expect(mounted.modelIconFallback()).not.toBeNull();
	});
});
