// @vitest-environment jsdom
// Mounts the REAL compiled plugin dists the way PluginSlot does, so a
// toolchain/runtime interop break (Svelte 4 class API vs Svelte 5 function
// components, dual-runtime mounts) fails here instead of in the live app.
import { describe, expect, it } from 'vitest';
import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const REPO_ROOT = resolve(__dirname, '../../../../..');
// Vite refuses to serve files outside the frontend root, so stage each dist
// into node_modules (inside the root, gitignored) and import it from there.
const STAGE_DIR = resolve(__dirname, '../../../../node_modules/.plugin-dist-under-test');

const IMAGE_ACTION_PROPS = {
	context: {
		generationId: 'gen-1',
		fileIndex: 0,
		filename: 'x.png',
		fileUrl: '/api/media/generations/gen-1/x.png',
		fileType: 'image'
	},
	hookName: 'image.actions',
	pluginId: 'test'
};

const DISTS = [
	{
		path: 'content/plugins/marketplace/civitai-provider/frontend/dist/ExportCivitaiButton.js',
		props: IMAGE_ACTION_PROPS
	},
	{
		path: 'content/plugins/marketplace/image-modal/frontend/dist/ImageModalAction.js',
		props: IMAGE_ACTION_PROPS
	},
	// prompt-text-import is a `content/plugins/local/` dev repo (gitignored here,
	// not always checked out) - skip it where it isn't present.
	{
		path: 'content/plugins/local/prompt-text-import/frontend/dist/TextImportModal.js',
		props: { onClose: () => {}, onImported: () => {} }
	}
].filter((dist) => existsSync(resolve(REPO_ROOT, dist.path)));

describe.each(DISTS)('plugin dist $path', ({ path, props }) => {
	it('mounts and destroys via the class API with props', async () => {
		mkdirSync(STAGE_DIR, { recursive: true });
		const staged = resolve(STAGE_DIR, basename(path));
		copyFileSync(resolve(REPO_ROOT, path), staged);
		const mod = await import(/* @vite-ignore */ pathToFileURL(staged).href);
		const Component = mod.default;
		expect(Component).toBeTypeOf('function');

		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = new Component({ target, props });
		expect(instance).toBeTruthy();
		if (typeof instance.$set === 'function') {
			instance.$set(props);
		}
		instance.$destroy();
		target.remove();
	});
});
