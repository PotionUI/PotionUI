// @vitest-environment jsdom
//
// `meshFiles` and the mesh grid-tile branch live inline in GenerationCard.svelte
// (no extracted helper), so the only way to exercise the real filtering -
// case-insensitive `file_type`, `is_final` exclusion, and the marker icon's
// mesh-first priority over video/audio - is to mount the component itself.
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		// The video case in the marker-priority test still lands `currentMediaFile`
		// on the video (mediaFiles orders video ahead of mesh), so MediaPreview
		// mounts and asks for a thumbnail URL - irrelevant to what's under test.
		getGenerationThumbnailURL: vi.fn(() => '/thumb.png')
	}
}));

const { default: GenerationCard } = await import('$lib/components/GenerationCard.svelte');
const { createClassComponent } = await import('svelte/legacy');

const CUBE_PATH = 'M21 7.5l-9-5.25L3 7.5m18 0l-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9';
const VIDEO_PATH =
	'M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z';

function baseGeneration(files: Record<string, unknown>[]) {
	return {
		id: `gen-${Math.random()}`,
		form_data: {},
		status: 'completed' as const,
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		files,
		rating: 0,
		is_favorite: false
	};
}

function mountCard(generation: ReturnType<typeof baseGeneration>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationCard as never,
		target,
		props: { generation: generation as never }
	});
	return {
		target,
		component,
		formatLabel: () => target.querySelector('.font-mono.text-2xs.uppercase')?.textContent?.trim(),
		nextButton: () => target.querySelector<HTMLButtonElement>('[aria-label="Next image"]'),
		markerIconPath: () =>
			target
				.querySelector('.absolute.top-2.z-20 svg path')
				?.getAttribute('d'),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountCard> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('GenerationCard mesh support', () => {
	it('matches file_type case-insensitively and drops non-final mesh files', () => {
		mounted = mountCard(
			baseGeneration([
				{
					id: 1,
					file_type: 'MESH',
					is_final: true,
					mesh_format: 'ply',
					file_path: 'a.ply',
					created_at: '2026-01-01T00:00:00Z'
				},
				{
					id: 2,
					file_type: 'mesh',
					is_final: false,
					mesh_format: 'obj',
					file_path: 'b.obj',
					created_at: '2026-01-01T00:00:00Z'
				}
			])
		);

		// Only the final, uppercase-typed mesh file should have survived the filter,
		// so there is exactly one media file (no carousel) and its format is 'PLY'.
		expect(mounted.nextButton()).toBeNull();
		expect(mounted.formatLabel()).toBe('PLY');
	});

	it('gives the mesh marker icon priority over video', () => {
		mounted = mountCard(
			baseGeneration([
				{
					id: 1,
					file_type: 'video',
					is_final: true,
					file_path: 'v.mp4',
					created_at: '2026-01-01T00:00:00Z'
				},
				{
					id: 2,
					file_type: 'mesh',
					is_final: true,
					mesh_format: 'glb',
					file_path: 'm.glb',
					created_at: '2026-01-01T00:00:00Z'
				}
			])
		);

		const path = mounted.markerIconPath();
		expect(path).toBe(CUBE_PATH);
		expect(path).not.toBe(VIDEO_PATH);
	});
});
