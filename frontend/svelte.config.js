import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	// Use vitePreprocess which handles PostCSS automatically
	preprocess: vitePreprocess(),

	kit: {
		// Static SPA build: the backend serves frontend/build directly
		// (src/bootstrap/static_frontend.py) with index.html as the fallback
		// for client-side routes. `strict: false` because nothing here is
		// prerendered - the whole app is dynamic/client-rendered (ssr = false
		// in the root +layout.ts), so adapter-static's "did every page
		// prerender?" check doesn't apply.
		adapter: adapter({
			fallback: 'index.html',
			strict: false
		})
	}
};

export default config;
