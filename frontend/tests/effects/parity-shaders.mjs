import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const lib = path.resolve(here, '../../src/lib');

const libAlias = {
	name: 'lib-alias',
	setup(context) {
		context.onResolve({ filter: /^\$lib(\/|$)/ }, (args) =>
			context.resolve(`./${args.path.slice(5).replace(/^\//, '')}`, { resolveDir: lib, kind: args.kind })
		);
	}
};

async function bundle() {
	const result = await build({
		entryPoints: [path.join(here, 'parityHarness.ts')],
		bundle: true,
		format: 'iife',
		platform: 'browser',
		target: 'es2022',
		write: false,
		logLevel: 'silent',
		plugins: [libAlias]
	});
	return result.outputFiles[0].text;
}

async function main() {
	const [jobPath, outPath] = process.argv.slice(2);
	if (!jobPath || !outPath) {
		console.error('usage: node parity-shaders.mjs <job.json> <out.json>');
		process.exit(2);
	}
	const jobs = JSON.parse(readFileSync(jobPath, 'utf8'));
	const code = await bundle();
	const { DISPLAY: _display, WAYLAND_DISPLAY: _wayland, ...env } = process.env;
	const browser = await chromium.launch({
		args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
		env
	});
	try {
		const page = await browser.newPage();
		await page.setContent('<!doctype html><html><body></body></html>');
		await page.addScriptTag({ content: code });
		const renderer = await page.evaluate(() => globalThis.rendererName());
		const results = {};
		for (const job of jobs.effects) {
			results[job.key] = await page.evaluate((input) => globalThis.renderParityJob(input), job.job);
		}
		writeFileSync(outPath, JSON.stringify({ renderer, results }));
	} finally {
		await browser.close();
	}
}

main().catch((error) => {
	console.error(error instanceof Error ? error.message : String(error));
	process.exit(1);
});
