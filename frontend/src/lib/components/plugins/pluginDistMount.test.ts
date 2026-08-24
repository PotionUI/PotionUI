// @vitest-environment jsdom
// Mounts the REAL compiled plugin dists the way PluginSlot does, so a
// toolchain/runtime interop break (Svelte 4 class API vs Svelte 5 function
// components, dual-runtime mounts) fails here instead of in the live app.
import { describe, expect, it } from 'vitest';
import { mkdirSync, copyFileSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const REPO_ROOT = resolve(__dirname, '../../../../..');
// Vite refuses to serve files outside the frontend root, so stage each dist
// into node_modules (inside the root, gitignored) and import it from there.
const STAGE_DIR = resolve(__dirname, '../../../../node_modules/.plugin-dist-under-test');
const DISTS = [
	'content/plugins/marketplace/civitai-provider/frontend/dist/ExportCivitaiButton.js',
	'content/plugins/marketplace/image-modal/frontend/dist/ImageModalAction.js'
];

describe.each(DISTS)('plugin dist %s', (path) => {
	it('mounts and destroys via the class API with props', async () => {
		mkdirSync(STAGE_DIR, { recursive: true });
		const staged = resolve(STAGE_DIR, basename(path));
		copyFileSync(resolve(REPO_ROOT, path), staged);
		const mod = await import(/* @vite-ignore */ pathToFileURL(staged).href);
		const Component = mod.default;
		expect(Component).toBeTypeOf('function');

		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = new Component({
			target,
			props: {
				context: {
					generationId: 'gen-1',
					fileIndex: 0,
					filename: 'x.png',
					fileUrl: '/api/media/generations/gen-1/x.png',
					fileType: 'image'
				},
				hookName: 'image.actions',
				pluginId: 'test'
			}
		});
		expect(instance).toBeTruthy();
		instance.$set({ context: { fileType: 'video' } });
		instance.$destroy();
		target.remove();
	});
});
