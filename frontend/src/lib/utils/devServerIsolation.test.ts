import { createRequire } from 'node:module';
import { describe, it, expect } from 'vitest';
import tailwindConfig from '../../../tailwind.config.js';
import { devWatchIgnored } from '../../../devWatchIgnore';

const picomatch = createRequire(import.meta.url)('picomatch') as (
	glob: string | string[],
	options?: { dot?: boolean }
) => (input: string) => boolean;

const stripDot = (glob: string) => glob.replace(/^\.\//, '');

function scannedByTailwind(path: string): boolean {
	const globs: string[] = tailwindConfig.content as string[];
	const included = globs.filter((g) => !g.startsWith('!')).map(stripDot);
	const excluded = globs.filter((g) => g.startsWith('!')).map((g) => stripDot(g.slice(1)));
	const hit = (list: string[]) => list.length > 0 && picomatch(list, { dot: true })(path);
	return hit(included) && !hit(excluded);
}

const ignoredByDevWatcher = picomatch(devWatchIgnored, { dot: true });

describe('tailwind content', () => {
	it.each([
		'src/lib/foo.test.ts',
		'src/lib/foo.svelte.test.ts',
		'src/routes/admin/a.spec.ts',
		'src/lib/components/apikeys/fixtures.ts',
		'src/lib/tool-tabs/image/fixtures/oldEdits.ts',
		'../content/plugins/marketplace/model-browser/frontend/src/lib/format.test.js'
	])('does not scan %s', (path) => {
		expect(scannedByTailwind(path)).toBe(false);
	});

	it.each([
		'src/lib/foo.ts',
		'src/lib/components/Button.svelte',
		'src/routes/+page.svelte',
		'src/app.html',
		'../content/plugins/marketplace/model-browser/frontend/src/lib/format.js'
	])('still scans %s', (path) => {
		expect(scannedByTailwind(path)).toBe(true);
	});
});

describe('dev watcher ignore list', () => {
	it.each([
		'/repo/frontend/src/lib/foo.test.ts',
		'/repo/frontend/src/lib/foo.svelte.test.ts',
		'/repo/frontend/tests/e2e/a.spec.ts',
		'/repo/frontend/tests/component/x/y.svelte',
		'/repo/frontend/.e2e-build/build/index.html',
		'/repo/frontend/tests/e2e/.playwright-artifacts/run/trace.zip',
		'C:/repo/frontend/.e2e-build/.svelte-kit/generated/root.svelte'
	])('ignores %s', (path) => {
		expect(ignoredByDevWatcher(path)).toBe(true);
	});

	it.each([
		'/repo/frontend/src/lib/foo.ts',
		'/repo/frontend/src/routes/+page.svelte',
		'/repo/frontend/src/app.html',
		'/repo/frontend/src/lib/stores/testing.ts',
		'/repo/frontend/static/favicon.svg'
	])('does not ignore %s', (path) => {
		expect(ignoredByDevWatcher(path)).toBe(false);
	});
});
