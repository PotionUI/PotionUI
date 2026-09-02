// @vitest-environment jsdom
//
// Mounts real compiled plugin dists through the HOST runtime's `mount()` -
// the same call `<svelte:component>` compiles down to - to reproduce the
// maintainer's crash (a plugin dist bundles its OWN copy of the Svelte
// runtime, so the host runtime cannot invoke it directly) and to prove
// `resolvePluginComponent`'s wrapper (`_wrapPluginDistComponent` /
// `PluginDistHost.svelte`) fixes it. Needs `resolve.conditions: ['browser']`
// (this config, not the default `vite.config.ts`) so `mount()` from 'svelte'
// resolves to the client runtime instead of the SSR entry - see
// `src/lib/components/plugins/pluginDistMount.test.ts` for the class-API-only
// counterpart that runs under the default config.
import { describe, expect, it } from 'vitest';
import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mount, unmount, flushSync } from 'svelte';
import { _wrapPluginDistComponent } from '$lib/plugin-api/componentResolver';
import { reactiveProps } from './stubs/reactiveProps.svelte';

const REPO_ROOT = resolve(__dirname, '../../..');
const STAGE_DIR = resolve(__dirname, '../../node_modules/.plugin-dist-under-test');

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
		name: 'ExportCivitaiButton',
		path: 'content/plugins/marketplace/civitai-provider/frontend/dist/ExportCivitaiButton.js',
		props: IMAGE_ACTION_PROPS
	},
	{
		name: 'ImageModalAction',
		path: 'content/plugins/marketplace/image-modal/frontend/dist/ImageModalAction.js',
		props: IMAGE_ACTION_PROPS
	},
	{
		name: 'TextImportModal',
		// `content/plugins/local/` is a gitignored private dev repo, not always checked out.
		path: 'content/plugins/local/prompt-text-import/frontend/dist/TextImportModal.js',
		props: { onClose: () => {}, onImported: () => {} }
	}
].filter((dist) => existsSync(resolve(REPO_ROOT, dist.path)));

async function loadDist(path: string): Promise<any> {
	mkdirSync(STAGE_DIR, { recursive: true });
	const staged = resolve(STAGE_DIR, basename(path));
	copyFileSync(resolve(REPO_ROOT, path), staged);
	const mod = await import(/* @vite-ignore */ pathToFileURL(staged).href);
	return mod.default;
}

function target(): HTMLDivElement {
	const el = document.createElement('div');
	document.body.appendChild(el);
	return el;
}

describe.each(DISTS)('$name mounted raw through the host runtime', ({ path, props }) => {
	it('throws (dual-runtime crash)', async () => {
		const RawComponent = await loadDist(path);
		expect(() => mount(RawComponent, { target: target(), props })).toThrow();
	});
});

describe.each(DISTS)('$name mounted via _wrapPluginDistComponent', ({ path, props }) => {
	it('mounts and unmounts cleanly through the host runtime', async () => {
		const RawComponent = await loadDist(path);
		const Wrapped = _wrapPluginDistComponent(RawComponent);

		let instance: any;
		expect(() => {
			instance = mount(Wrapped, { target: target(), props: { ...props } });
		}).not.toThrow();

		expect(() => unmount(instance)).not.toThrow();
	});
});

describe('ExportCivitaiButton via _wrapPluginDistComponent', () => {
	it('forwards a live prop update to the mounted dist', async () => {
		const RawComponent = await loadDist(
			'content/plugins/marketplace/civitai-provider/frontend/dist/ExportCivitaiButton.js'
		);
		const Wrapped = _wrapPluginDistComponent(RawComponent);
		const el = target();

		// `mount()` props are read once unless the object handed to it is itself
		// reactive - reactiveProps() gives us that, and set() mutates it after
		// the initial mount the way a live host-tree prop change would.
		const { props, set } = reactiveProps({ ...IMAGE_ACTION_PROPS, context: { ...IMAGE_ACTION_PROPS.context, fileType: 'video' } });
		const instance = mount(Wrapped, { target: el, props });
		flushSync();

		// fileType 'video' -> ExportCivitaiButton's `{#if isImage}` renders nothing.
		expect(el.querySelector('button')).toBeNull();

		set({ context: { ...IMAGE_ACTION_PROPS.context, fileType: 'image' } });
		flushSync();
		// The dist's own `$set` (its bundled runtime's legacy class-API shim)
		// flushes on a macrotask, not synchronously with the host's flushSync -
		// jsdom has no native `requestAnimationFrame`, which is what its
		// scheduler falls back to timing against.
		await new Promise((r) => setTimeout(r, 0));

		expect(el.querySelector('button')).toBeTruthy();

		unmount(instance);
	});
});
