import { defineConfig } from 'vitest/config';
import { svelte, vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import path from 'node:path';

// Component-level tests MOUNT real Svelte components, which the default config
// cannot do: without `resolve.conditions: ['browser']`, `svelte` resolves to its
// SSR entry and `mount()` dies on `index-server.js`. Kept as a separate config
// rather than folded into vite.config.ts so `npm run test:unit` keeps its
// node environment and its `src/**` scope untouched.
//
//   npm run test:component
//
// Tests live in tests/component/, deliberately outside `src/**` so the default
// suite's include pattern cannot pick them up and fail them.
const root = path.dirname(new URL(import.meta.url).pathname);

export default defineConfig({
	root,
	plugins: [svelte({ preprocess: vitePreprocess() })],
	resolve: {
		conditions: ['browser'],
		alias: {
			$lib: path.join(root, 'src/lib'),
			'$app/environment': path.join(root, 'tests/component/stubs/appEnvironment.ts'),
			'$app/stores': path.join(root, 'tests/component/stubs/appStores.ts'),
			'$app/navigation': path.join(root, 'tests/component/stubs/appNavigation.ts')
		}
	},
	test: {
		environment: 'jsdom',
		include: ['tests/component/**/*.{test,spec}.ts'],
		setupFiles: ['tests/component/setup/webAnimations.ts']
	}
});
