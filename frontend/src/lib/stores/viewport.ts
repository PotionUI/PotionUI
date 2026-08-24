import { writable, derived } from 'svelte/store';
import { browser } from '$app/environment';

export const viewportWidth = writable(browser ? window.innerWidth : 1024);
export const viewportHeight = writable(browser ? window.innerHeight : 768);

export const isMobile = derived(viewportWidth, ($width) => $width < 768);

if (browser) {
	function updateViewport() {
		viewportWidth.set(window.innerWidth);
		viewportHeight.set(window.innerHeight);
	}

	window.addEventListener('resize', updateViewport);
	window.addEventListener('orientationchange', () => {
		// Small delay to let the browser settle after orientation change
		setTimeout(updateViewport, 100);
	});
}
