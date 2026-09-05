import { describe, it, expect } from 'vitest';
import { isPrecacheEligibleStaticPath, selectPrecacheFiles, extractShellAssets } from './shell';

describe('isPrecacheEligibleStaticPath', () => {
	it('keeps fonts, icons, brand, favicon and manifest', () => {
		expect(isPrecacheEligibleStaticPath('/fonts/Inter.woff2')).toBe(true);
		expect(isPrecacheEligibleStaticPath('/icons/potion-orb-192.png')).toBe(true);
		expect(isPrecacheEligibleStaticPath('/brand/logo.png')).toBe(true);
		expect(isPrecacheEligibleStaticPath('/favicon.png')).toBe(true);
		expect(isPrecacheEligibleStaticPath('/favicon.svg')).toBe(true);
		expect(isPrecacheEligibleStaticPath('/manifest.json')).toBe(true);
	});

	it('excludes frontend-kit assets', () => {
		expect(isPrecacheEligibleStaticPath('/frontend-kit/hero.png')).toBe(false);
	});

	it('excludes gitignored backup files ending in ~', () => {
		expect(isPrecacheEligibleStaticPath('/brand/logo.png~')).toBe(false);
		expect(isPrecacheEligibleStaticPath('/brand/potion-orb-128.png~')).toBe(false);
	});

	it('excludes paths outside the allowed prefixes', () => {
		expect(isPrecacheEligibleStaticPath('/some-other-file.txt')).toBe(false);
		expect(isPrecacheEligibleStaticPath('/favicon/nested.png')).toBe(false);
	});
});

describe('selectPrecacheFiles', () => {
	it('filters a files list down to the eligible subset', () => {
		const files = [
			'/favicon.png',
			'/favicon.svg',
			'/manifest.json',
			'/fonts/Inter.woff2',
			'/icons/potion-orb-192.png',
			'/brand/logo.png',
			'/brand/logo.png~',
			'/frontend-kit/hero.png',
			'/frontend-kit/screenshot.png'
		];

		expect(selectPrecacheFiles(files)).toEqual([
			'/favicon.png',
			'/favicon.svg',
			'/manifest.json',
			'/fonts/Inter.woff2',
			'/icons/potion-orb-192.png',
			'/brand/logo.png'
		]);
	});
});

describe('extractShellAssets', () => {
	it('finds modulepreload and stylesheet /_app/ links', () => {
		const html = `
			<link href="/_app/immutable/entry/start.abc.js" rel="modulepreload">
			<link href="/_app/immutable/entry/app.def.js" rel="modulepreload">
			<link href="/_app/immutable/assets/0.ghi.css" rel="stylesheet">
			<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
			<link rel="manifest" href="/manifest.json" />
		`;

		expect(extractShellAssets(html).sort()).toEqual(
			[
				'/_app/immutable/entry/start.abc.js',
				'/_app/immutable/entry/app.def.js',
				'/_app/immutable/assets/0.ghi.css'
			].sort()
		);
	});

	it('finds script src /_app/ references', () => {
		const html = `<script src="/_app/immutable/entry/start.abc.js"></script>`;
		expect(extractShellAssets(html)).toEqual(['/_app/immutable/entry/start.abc.js']);
	});

	it('finds inline import() calls to /_app/ modules', () => {
		const html = `
			<script>
				Promise.all([
					import("/_app/immutable/entry/start.abc.js"),
					import("/_app/immutable/entry/app.def.js")
				]).then(([kit, app]) => kit.start(app, element));
			</script>
		`;

		expect(extractShellAssets(html).sort()).toEqual(
			['/_app/immutable/entry/start.abc.js', '/_app/immutable/entry/app.def.js'].sort()
		);
	});

	it('ignores external and non-/_app/ URLs', () => {
		const html = `
			<link href="https://fonts.googleapis.com/css2" rel="stylesheet">
			<script src="https://cdn.example.com/lib.js"></script>
			<link href="/favicon.svg" rel="icon">
		`;
		expect(extractShellAssets(html)).toEqual([]);
	});

	it('de-duplicates assets referenced by both a modulepreload link and an import()', () => {
		const html = `
			<link href="/_app/immutable/entry/start.abc.js" rel="modulepreload">
			<script>import("/_app/immutable/entry/start.abc.js");</script>
		`;
		expect(extractShellAssets(html)).toEqual(['/_app/immutable/entry/start.abc.js']);
	});
});
