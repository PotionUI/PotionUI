import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { join } from 'node:path';

const e2eBuildDir = process.env.E2E_BUILD_DIR;

/** @type {import('@sveltejs/kit').Config} */
const config = {
	// Use vitePreprocess which handles PostCSS automatically
	preprocess: vitePreprocess(),

	kit: {
		...(e2eBuildDir ? { outDir: join(e2eBuildDir, '.svelte-kit') } : {}),
		// Static SPA build: the backend serves frontend/build directly
		// (src/bootstrap/static_frontend.py) with index.html as the fallback
		// for client-side routes. `strict: false` because nothing here is
		// prerendered - the whole app is dynamic/client-rendered (ssr = false
		// in the root +layout.ts), so adapter-static's "did every page
		// prerender?" check doesn't apply.
		adapter: adapter({
			...(e2eBuildDir ? { pages: join(e2eBuildDir, 'build'), assets: join(e2eBuildDir, 'build') } : {}),
			fallback: 'index.html',
			strict: false
		})
	}
};

export default config;
