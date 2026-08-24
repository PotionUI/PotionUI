#!/usr/bin/env node
/**
 * Unified plugin frontend build.
 *
 * Replaces the 8 near-identical per-plugin `frontend/build.js` +
 * `package.json` + `node_modules` copies with one esbuild + esbuild-svelte
 * toolchain (declared once in `content/plugins/package.json`, pinned to the
 * same svelte major as `frontend`) that discovers and builds every plugin
 * under `content/plugins/marketplace` and `content/plugins/local`.
 *
 * Two build shapes, auto-detected per plugin (mirrors what the old
 * per-plugin build.js scripts already did). A plugin may need both: a page
 * entry plus standalone components (renderers, field types), in which case
 * both passes run and the page's own component is skipped by the second.
 *
 *   1. "Page" plugins (`frontend/src/index.js` present): the index module
 *      imports one `./<Component>.svelte` and re-exports
 *      `mountPlugin`/`unmountPlugin`. Bundled as a single ESM entry to
 *      `frontend/dist/<Component>.js` (the manifest's `pages[].component`).
 *
 *   2. "Component" plugins (no `index.js`): every top-level `.svelte` file
 *      under `frontend/src` referenced by the plugin's manifest (field
 *      types, renderers, contributions, hooks.frontend, sidebar_widgets) is
 *      compiled standalone to `frontend/dist/<Name>.js` with a default
 *      export, for direct dynamic `import()` (componentResolver.ts).
 *
 * Usage: node scripts/build-plugins.mjs [pluginId ...]
 *   With no args, builds every discovered plugin. Exits non-zero if any
 *   plugin fails to build.
 */
import { readFileSync, existsSync, readdirSync, statSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join, basename } from 'path';
import { createRequire } from 'module';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const REPO_ROOT = join(__dirname, '..');

// The toolchain (esbuild, esbuild-svelte, svelte, js-yaml) lives in
// `content/plugins/node_modules` (installed from the single
// `content/plugins/package.json`), not next to this script - resolve
// explicitly from there instead of a static import, which would only walk
// up from `scripts/`.
const pluginsRequire = createRequire(join(REPO_ROOT, 'content', 'plugins', 'package.json'));
const esbuild = pluginsRequire('esbuild');
const sveltePlugin = pluginsRequire('esbuild-svelte').default ?? pluginsRequire('esbuild-svelte');
const yaml = pluginsRequire('js-yaml');

const PLUGIN_ROOTS = [
	join(REPO_ROOT, 'content', 'plugins', 'marketplace'),
	join(REPO_ROOT, 'content', 'plugins', 'local')
];

// Per-plugin compiler-option overrides for the "page" build shape, keyed by
// plugin id, for a plugin whose frontend needs to opt out of runes mode
// (none currently do - form-builder was the one outlier, and it left the
// tree; see its own repo if it ever comes back).
const PAGE_RUNES_OVERRIDES = {};

function discoverPlugins(pluginIdFilter) {
	const plugins = [];
	for (const root of PLUGIN_ROOTS) {
		if (!existsSync(root)) continue;
		for (const name of readdirSync(root)) {
			const pluginDir = join(root, name);
			if (!statSync(pluginDir).isDirectory()) continue;
			const manifestPath = join(pluginDir, 'manifest.yml');
			const frontendSrc = join(pluginDir, 'frontend', 'src');
			if (!existsSync(manifestPath) || !existsSync(frontendSrc)) continue;

			const manifest = yaml.load(readFileSync(manifestPath, 'utf-8'));
			const pluginId = manifest.id || name;
			if (pluginIdFilter.length && !pluginIdFilter.includes(pluginId)) continue;

			plugins.push({ id: pluginId, dir: pluginDir, manifest });
		}
	}
	return plugins;
}

/** Every `component:` reference in a manifest, across all sections that carry one. */
function referencedComponents(manifest) {
	const names = new Set();
	const sections = [
		manifest.pages,
		manifest.sidebar_widgets,
		manifest.field_types,
		manifest.renderers,
		manifest.contributions,
		manifest.prompt_importers,
		manifest.hooks?.frontend
	];
	for (const section of sections) {
		if (!Array.isArray(section)) continue;
		for (const entry of section) {
			if (entry?.component) {
				names.add(basename(entry.component, '.js').replace(/\.svelte$/, ''));
			}
		}
	}
	return names;
}

async function buildPagePlugin(plugin, srcDir, distDir) {
	const indexJs = join(srcDir, 'index.js');
	const source = readFileSync(indexJs, 'utf-8');
	const match = source.match(/from\s+['"]\.\/(\w+)\.svelte['"]/);
	if (!match) {
		throw new Error(
			`${plugin.id}: frontend/src/index.js doesn't import a single './<Component>.svelte' - ` +
			`can't infer the output filename`
		);
	}
	const componentName = match[1];
	const outfile = join(distDir, `${componentName}.js`);
	const runes = PAGE_RUNES_OVERRIDES[plugin.id] ?? true;

	await esbuild.build({
		entryPoints: [indexJs],
		bundle: true,
		outfile,
		format: 'esm',
		plugins: [
			sveltePlugin({
				compilerOptions: { css: 'injected', generate: 'client', runes }
			})
		],
		external: [],
		minify: false,
		sourcemap: true
	});

	return [componentName];
}

async function buildComponentPlugin(plugin, srcDir, distDir, skip = new Set()) {
	const wanted = new Set([...referencedComponents(plugin.manifest)].filter((n) => !skip.has(n)));
	if (wanted.size === 0) {
		return [];
	}

	const entryPoints = [];
	for (const name of wanted) {
		const svelteFile = join(srcDir, `${name}.svelte`);
		if (!existsSync(svelteFile)) {
			throw new Error(
				`${plugin.id}: manifest references component "${name}" but ` +
				`frontend/src/${name}.svelte doesn't exist`
			);
		}
		entryPoints.push(svelteFile);
	}

	await esbuild.build({
		entryPoints,
		bundle: true,
		outdir: distDir,
		format: 'esm',
		plugins: [
			sveltePlugin({
				// componentApi 4 makes the compiled component self-mounting via
				// `new Component({target, props})` against its OWN bundled Svelte
				// runtime. Without it the host's runtime would have to mount a
				// component compiled against the bundled copy - two Svelte 5
				// runtimes cannot share a mount, it crashes in template init.
				compilerOptions: { generate: 'dom', hydratable: false, css: 'injected', compatibility: { componentApi: 4 } }
			})
		],
		external: [],
		minify: false,
		sourcemap: true,
		target: 'es2020',
		platform: 'browser'
	});

	return [...wanted];
}

async function buildPlugin(plugin) {
	const srcDir = join(plugin.dir, 'frontend', 'src');
	const distDir = join(plugin.dir, 'frontend', 'dist');
	const hasIndexJs = existsSync(join(srcDir, 'index.js'));

	const built = hasIndexJs ? await buildPagePlugin(plugin, srcDir, distDir) : [];
	built.push(...(await buildComponentPlugin(plugin, srcDir, distDir, new Set(built))));

	return built;
}

async function main() {
	const pluginIdFilter = process.argv.slice(2);
	const plugins = discoverPlugins(pluginIdFilter);

	if (plugins.length === 0) {
		console.log('No plugin frontends found to build.');
		return;
	}

	let failures = 0;
	for (const plugin of plugins) {
		process.stdout.write(`Building ${plugin.id}... `);
		try {
			const built = await buildPlugin(plugin);
			console.log(built.length ? `OK (${built.join(', ')})` : 'OK (nothing to build)');
		} catch (err) {
			failures += 1;
			console.log('FAILED');
			console.error(err instanceof Error ? err.message : err);
		}
	}

	console.log(`\n${plugins.length - failures}/${plugins.length} plugin frontends built successfully.`);
	if (failures > 0) {
		process.exit(1);
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
