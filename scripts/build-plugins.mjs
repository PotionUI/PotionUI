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
 *      types, renderers, contributions, hooks.frontend, sidebar_widgets,
 *      admin_tabs) is compiled standalone to `frontend/dist/<Name>.js` with
 *      a default export, for direct dynamic `import()` (componentResolver.ts).
 *
 * Usage: node scripts/build-plugins.mjs [--debug] [pluginId ...]
 *   With no plugin ids, builds every discovered plugin. Exits non-zero if any
 *   plugin fails to build.
 *
 * Build modes (`BUILD_OPTIONS` below):
 *   - release (default) - minified, no source maps. This is what gets
 *     committed to `frontend/dist/` for a marketplace plugin.
 *   - debug - pass `--debug` or set `PLUGIN_BUILD=debug` to get unminified
 *     output with source maps, for stepping through a plugin dist in the
 *     browser devtools. Never commit a debug build.
 * Both modes set `legalComments: 'eof'` explicitly (upstream MIT/GPL notices
 * from bundled deps land at the end of the output file, not stripped) so the
 * policy doesn't depend on esbuild's bundling-dependent default. Output is
 * otherwise deterministic: no timestamps, dates or random ids are emitted,
 * and entry/outfile names are derived from the component name, not a hash.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync, statSync, lstatSync, unlinkSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join, basename } from 'path';
import { createRequire } from 'module';
import { compilePluginStylesheet } from './plugin-stylesheet.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const REPO_ROOT = join(__dirname, '..');
const FRONTEND_DIR = join(REPO_ROOT, 'frontend');
process.chdir(existsSync(FRONTEND_DIR) ? FRONTEND_DIR : REPO_ROOT);

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

const DEBUG_BUILD = process.argv.includes('--debug') || process.env.PLUGIN_BUILD === 'debug';

const BUILD_OPTIONS = {
	legalComments: 'eof',
	...(DEBUG_BUILD ? { minify: false, sourcemap: true } : { minify: true, sourcemap: false })
};

function buildOptions(plugin) {
	return { ...BUILD_OPTIONS, preserveSymlinks: lstatSync(plugin.dir).isSymbolicLink() };
}

/** A release build doesn't emit a `.map`, but esbuild never cleans an outdir - a
 * `.map` left over from a prior debug build (or from before this policy existed)
 * would otherwise sit there stale, unreferenced and undetected. */
function removeStaleMap(outfile) {
	if (DEBUG_BUILD) return;
	const mapFile = `${outfile}.map`;
	if (existsSync(mapFile)) unlinkSync(mapFile);
}

function discoverPlugins(pluginIdFilter) {
	const plugins = [];
	for (const root of PLUGIN_ROOTS) {
		if (!existsSync(root)) continue;
		for (const name of readdirSync(root)) {
			const pluginDir = join(root, name);
			let isDirectory;
			try {
				isDirectory = statSync(pluginDir).isDirectory();
			} catch (error) {
				console.warn(`skipping ${pluginDir}: ${error.code ?? error.message}`);
				continue;
			}
			if (!isDirectory) continue;
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

function referencedComponents(manifest) {
	const names = new Set();
	const walk = (node) => {
		if (Array.isArray(node)) {
			node.forEach(walk);
		} else if (node && typeof node === 'object') {
			for (const [key, value] of Object.entries(node)) {
				if (key === 'component' && typeof value === 'string' && value) {
					names.add(basename(value, '.js').replace(/\.svelte$/, ''));
				} else {
					walk(value);
				}
			}
		}
	};
	walk(manifest);
	return names;
}

function removeStaleChunks(distDir) {
	if (!existsSync(distDir)) return;
	for (const file of readdirSync(distDir)) {
		if (/^chunk-[\w-]+\.js(\.map)?$/.test(file)) unlinkSync(join(distDir, file));
	}
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

	await esbuild.build({
		entryPoints: [indexJs],
		bundle: true,
		outfile,
		format: 'esm',
		plugins: [
			sveltePlugin({
				compilerOptions: { css: 'injected', generate: 'client', runes: true }
			})
		],
		external: [],
		...buildOptions(plugin)
	});
	removeStaleMap(outfile);

	return [componentName, ...(await buildPageStylesheet(plugin, srcDir, distDir, componentName))];
}

async function buildPageStylesheet(plugin, srcDir, distDir, componentName) {
	const source = join(srcDir, `${componentName}.css`);
	if (!existsSync(source)) return [];
	const css = await compilePluginStylesheet({ pluginId: plugin.id, pluginDir: plugin.dir, source, frontendDir: FRONTEND_DIR });
	const { code } = await esbuild.transform(css, { loader: 'css', minify: !DEBUG_BUILD, legalComments: 'eof' });
	mkdirSync(distDir, { recursive: true });
	writeFileSync(join(distDir, `${componentName}.css`), code);
	return [`${componentName}.css`];
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
		splitting: true,
		chunkNames: 'chunk-[hash]',
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
		...buildOptions(plugin),
		target: 'es2020',
		platform: 'browser'
	});
	for (const name of wanted) {
		removeStaleMap(join(distDir, `${name}.js`));
	}

	return [...wanted];
}

async function buildPlugin(plugin) {
	const srcDir = join(plugin.dir, 'frontend', 'src');
	const distDir = join(plugin.dir, 'frontend', 'dist');
	const hasIndexJs = existsSync(join(srcDir, 'index.js'));
	removeStaleChunks(distDir);

	const built = hasIndexJs ? await buildPagePlugin(plugin, srcDir, distDir) : [];
	built.push(...(await buildComponentPlugin(plugin, srcDir, distDir, new Set(built))));

	return built;
}

function describeFailure(err) {
	const first = Array.isArray(err?.errors) ? err.errors[0] : undefined;
	if (first) {
		const where = first.location ? `${first.location.file}:${first.location.line}: ` : '';
		return `${where}${first.text}`;
	}
	return err instanceof Error ? err.message : String(err);
}

async function main() {
	const pluginIdFilter = process.argv.slice(2).filter((arg) => arg !== '--debug');
	const plugins = discoverPlugins(pluginIdFilter);

	console.log(`Build mode: ${DEBUG_BUILD ? 'debug (unminified, source maps)' : 'release (minified, no source maps)'}`);

	if (plugins.length === 0) {
		console.log('No plugin frontends found to build.');
		return;
	}

	const failures = [];
	for (const plugin of plugins) {
		process.stdout.write(`Building ${plugin.id}... `);
		try {
			const built = await buildPlugin(plugin);
			console.log(built.length ? `OK (${built.join(', ')})` : 'OK (nothing to build)');
		} catch (err) {
			const message = describeFailure(err);
			failures.push({ id: plugin.id, message });
			console.log('FAILED');
			console.error(message);
		}
	}

	console.log(`\n${plugins.length - failures.length}/${plugins.length} plugin frontends built successfully.`);
	if (failures.length > 0) {
		console.error(`\nFailed plugins (${failures.length}):`);
		for (const { id, message } of failures) {
			console.error(`  ${id}: ${message.split('\n').find((line) => line.trim()) ?? 'unknown error'}`);
		}
		process.exit(1);
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
