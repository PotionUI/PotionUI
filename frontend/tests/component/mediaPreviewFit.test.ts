// @vitest-environment jsdom
//
// Covers the two props CompareFrame relies on for full-resolution,
// letterboxed compare panes: `fit="contain"` swaps the object-fit class, and
// `startFullLoaded` skips the thumbnail and renders the full-size image from
// the first render (no "click for full size" affordance needed).
import { describe, it, expect, vi } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: vi.fn(() => ({ get: vi.fn().mockResolvedValue({ data: { data: {} } }) })),
		getGenerationImageURL: vi.fn(
			(generationId: string, filename: string) => `/api/media/generations/${generationId}/${filename}`
		),
		getGenerationThumbnailURL: vi.fn(
			(generationId: string, filename: string, size: string) =>
				`/api/media/generations/${generationId}/${filename}?size=${size}`
		)
	}
}));

const { default: MediaPreview } = await import('$lib/components/MediaPreview.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: MediaPreview as any, target, props });
	return { target, component };
}

function imageFile() {
	return {
		id: 1,
		file_path: 'gen-1/image.png',
		file_type: 'image',
		is_final: true,
		created_at: '2026-01-01T00:00:00Z'
	};
}

describe('MediaPreview fit and startFullLoaded', () => {
	it('defaults to a cropped, thumbnail-only cover image', () => {
		const { target, component } = mount({ file: imageFile(), generationId: 'gen-1' });
		const img = target.querySelector('img') as HTMLImageElement;
		expect(img.className).toContain('object-cover');
		expect(img.className).not.toContain('object-contain');
		expect(img.src).toContain('size=medium');
		component.$destroy();
	});

	it('renders full-resolution, letterboxed contain when both props are set', () => {
		const { target, component } = mount({
			file: imageFile(),
			generationId: 'gen-1',
			fit: 'contain',
			startFullLoaded: true,
			loadFullOnClick: false
		});
		const img = target.querySelector('img') as HTMLImageElement;
		expect(img.className).toContain('object-contain');
		expect(img.className).not.toContain('object-cover');
		expect(img.src).toBe('http://localhost:3000/api/media/generations/gen-1/image.png');
		expect(img.src).not.toContain('size=');
		component.$destroy();
	});

	it('never shows the "click for full size" hint when startFullLoaded is set', () => {
		const { target, component } = mount({
			file: imageFile(),
			generationId: 'gen-1',
			startFullLoaded: true,
			loadFullOnClick: false
		});
		expect(target.textContent).not.toContain('Click for full size');
		component.$destroy();
	});
});
