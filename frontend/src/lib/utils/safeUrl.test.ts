import { describe, expect, it } from 'vitest';
import { sanitizeImageUrl, sanitizeLinkUrl } from './safeUrl';
import { processMarkdown } from './markdown';

const HOSTILE = [
	'javascript&#58;alert(1)',
	'javascript&#x3a;alert(1)',
	'javascript&#x3A;alert(1)',
	'javascript&#58alert(1)',
	'java\tscript:alert(1)',
	'java\nscript:alert(1)',
	'JaVaScRiPt:alert(1)',
	'&#106;avascript:alert(1)',
	'&#x6A;avascript:alert(1)',
	'javascript&colon;alert(1)',
	'javascript&amp;colon;alert(1)',
	'javascript&amp;#58;alert(1)',
	'java\u200bscript:alert(1)',
	'java\u202escript:alert(1)',
	'\u0001javascript:alert(1)',
	'\u0000javascript:alert(1)',
	' \tjavascript:alert(1)',
	'data:text/html,<script>alert(1)</script>',
	'data:image/png;base64,AAAA',
	'vbscript:msgbox(1)',
	'file:///etc/passwd',
	'blob:https://x.example/abc',
	'//evil.example',
	'/\\evil.example',
	'\\\\evil.example',
	'foo/bar',
	'not a url'
];

describe('sanitizeLinkUrl', () => {
	it.each(HOSTILE)('rejects %j', (url) => {
		expect(sanitizeLinkUrl(url)).toBeNull();
	});

	it('allows ordinary targets', () => {
		expect(sanitizeLinkUrl('https://example.com/a?b=1')).toBe('https://example.com/a?b=1');
		expect(sanitizeLinkUrl('http://example.com')).toBe('http://example.com');
		expect(sanitizeLinkUrl('mailto:a@b.c')).toBe('mailto:a@b.c');
		expect(sanitizeLinkUrl('/docs/x')).toBe('/docs/x');
		expect(sanitizeLinkUrl('#anchor')).toBe('#anchor');
		expect(sanitizeLinkUrl('./rel')).toBe('./rel');
		expect(sanitizeLinkUrl('../rel')).toBe('../rel');
		expect(sanitizeLinkUrl('?q=1')).toBe('?q=1');
	});

	it('escapes quotes and angle brackets so the value cannot leave the attribute', () => {
		const out = sanitizeLinkUrl('https://x.example/" onmouseover="alert(1)<b>') ?? '';
		expect(out).not.toMatch(/["<>]/);
		expect(out).toContain('&quot;');
	});

	it('decodes entities that hide a quote', () => {
		const out = sanitizeLinkUrl('https://x.example/&quot; onmouseover=&quot;x') ?? '';
		expect(out).not.toContain('"');
	});
});

describe('sanitizeImageUrl', () => {
	it.each([
		...HOSTILE,
		'mailto:a@b.c'
	])('rejects %j', (url) => {
		expect(sanitizeImageUrl(url)).toBeNull();
	});

	it('allows http, https and same-origin paths', () => {
		expect(sanitizeImageUrl('https://x.example/a.png')).toBe('https://x.example/a.png');
		expect(sanitizeImageUrl('/media/a.png')).toBe('/media/a.png');
	});

	it('cannot break out of the attribute', () => {
		const out = sanitizeImageUrl('/a.png" onerror="alert(1)') ?? '';
		expect(out).not.toContain('"');
	});
});

describe('processMarkdown link rendering', () => {
	it.each(HOSTILE.filter((u) => !/[)\n]/.test(u.replace(/\(1\)$/, ''))))(
		'renders no anchor for %j',
		(url) => {
			const html = processMarkdown(`[x](${url})`);
			expect(html).not.toContain('<a ');
			expect(html).not.toMatch(/href=/i);
		}
	);

	it('keeps an attribute break-out inside the href value', () => {
		const html = processMarkdown('[x](https://a.example/" onmouseover="alert(1))');
		const tag = html.match(/<a\b[^>]*>/)?.[0] ?? '';
		expect([...tag.matchAll(/\s([a-zA-Z-]+)=/g)].map((m) => m[1])).toEqual(['href', 'class', 'target', 'rel']);
	});

	it('renders ordinary links with rel and target', () => {
		const html = processMarkdown('[a](https://example.com) [b](mailto:a@b.c) [c](/docs/x) [d](#anchor) [e](./rel)');
		for (const href of ['https://example.com', 'mailto:a@b.c', '/docs/x', '#anchor', './rel']) {
			expect(html).toContain(`href="${href}"`);
		}
		expect(html).toContain('rel="noopener noreferrer"');
		expect(html).toContain('target="_blank"');
	});
});
