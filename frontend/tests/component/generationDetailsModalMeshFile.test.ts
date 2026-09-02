// @vitest-environment jsdom
//
// `isMeshFile` and the mesh branch (routing to MeshPreview instead of the
// img/video fallback, and the format badge derived from `resolveMeshFormat`)
// live inline in GenerationDetailsModal.svelte, so mounting the real component
// is the only way to exercise the case-insensitive match against a real
// server-shaped `file_type` value.
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

function baseGeneration(files: Record<string, unknown>[]) {
	return {
		id: `gen-${Math.random()}`,
		form_data: {},
		status: 'completed' as const,
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		files,
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
		// The top-left overlay's file-format badge: uppercase MP4/PNG/mesh-format/audio-format.
		formatBadge: () =>
			Array.from(document.body.querySelectorAll('span.uppercase')).find((el) =>
				/^[A-Z0-9]+$/.test(el.textContent?.trim() ?? '')
			)?.textContent?.trim(),
		hasImg: () => !!document.body.querySelector('img'),
		hasVideo: () => !!document.body.querySelector('video'),
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

describe('GenerationDetailsModal mesh support', () => {
	it('routes an uppercase MESH file_type to the mesh branch, not the image fallback', () => {
		mounted = mountModal(
			baseGeneration([
				{
					id: 1,
					file_type: 'MESH',
					is_final: true,
					mesh_format: 'ply',
					file_path: 'a.ply',
					created_at: '2026-01-01T00:00:00Z'
				}
			])
		);

		expect(mounted.formatBadge()).toBe('PLY');
		expect(mounted.hasImg()).toBe(false);
		expect(mounted.hasVideo()).toBe(false);
	});

	it('routes a mixed-case Mesh file_type to the mesh branch as well', () => {
		mounted = mountModal(
			baseGeneration([
				{
					id: 1,
					file_type: 'Mesh',
					is_final: true,
					mesh_format: 'obj',
					file_path: 'a.obj',
					created_at: '2026-01-01T00:00:00Z'
				}
			])
		);

		expect(mounted.formatBadge()).toBe('OBJ');
		expect(mounted.hasImg()).toBe(false);
	});

	it('still renders the img fallback for a plain image file (control case)', () => {
		mounted = mountModal(
			baseGeneration([
				{
					id: 1,
					file_type: 'image',
					is_final: true,
					file_path: 'a.png',
					created_at: '2026-01-01T00:00:00Z'
				}
			])
		);

		expect(mounted.formatBadge()).toBe('PNG');
		expect(mounted.hasImg()).toBe(true);
	});
});
