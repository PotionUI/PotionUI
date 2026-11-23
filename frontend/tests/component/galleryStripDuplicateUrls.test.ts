// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';
import GalleryStrip from '$lib/components/workbench/GalleryStrip.svelte';

describe('GalleryStrip', () => {
	it('renders a batch that contains the same image url twice without a duplicate key error', () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const url = '/api/media/generations/g1/a.png';
		const app = mount(GalleryStrip, {
			target,
			props: {
				batchImages: [{ url }, { url }, { url: '/api/media/generations/g1/b.png' }] as any,
				batchVideos: [],
				batchAudios: [],
				batchMeshes: [],
				selectedIndex: 0
			}
		});
		expect(target.querySelectorAll('img[alt^="Generated image"]').length).toBe(3);
		unmount(app);
		target.remove();
	});
});
