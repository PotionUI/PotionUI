import { readFileSync } from 'fs';
import { join, sep } from 'path';
import { createRequire } from 'module';
import { pathToFileURL } from 'url';

export const PLUGIN_SCOPE_ATTRIBUTE = 'data-potionui-plugin';

const DOCUMENT_ROOT = /^(?::root|html|body)(?=$|\s|>)/;

export function pluginScopeSelector(pluginId) {
	return `[${PLUGIN_SCOPE_ATTRIBUTE}="${pluginId.replace(/["\\]/g, '\\$&')}"]`;
}

export function scopeSelector(scope, selector) {
	const root = selector.match(DOCUMENT_ROOT);
	return root ? scope + selector.slice(root[0].length) : `${scope} ${selector}`;
}

export function scopePluginRules(scope) {
	return {
		postcssPlugin: 'potionui-plugin-scope',
		Once(root) {
			root.walkRules((rule) => {
				if (rule.parent?.type === 'atrule' && /keyframes$/i.test(rule.parent.name)) return;
				rule.selectors = rule.selectors.map((selector) => scopeSelector(scope, selector));
			});
		}
	};
}

function glob(...parts) {
	return join(...parts).split(sep).join('/');
}

export async function compilePluginStylesheet({ pluginId, pluginDir, source, frontendDir }) {
	const frontendRequire = createRequire(join(frontendDir, 'package.json'));
	const postcss = frontendRequire('postcss');
	const tailwindcss = frontendRequire('tailwindcss');
	const hostConfig = (await import(pathToFileURL(join(frontendDir, 'tailwind.config.js')).href)).default;
	const config = {
		presets: [hostConfig],
		content: [glob(pluginDir, 'frontend', 'src', '**', '*.{svelte,js,ts}'), `!${glob(pluginDir, '**', 'node_modules', '**')}`],
		corePlugins: { preflight: false }
	};
	const result = await postcss([tailwindcss(config), scopePluginRules(pluginScopeSelector(pluginId))]).process(
		readFileSync(source, 'utf-8').replace(/\r\n?/g, '\n'),
		{ from: source }
	);
	return result.css;
}
