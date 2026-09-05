// @vitest-environment jsdom
//
// SEC-02: JsonTreeNode used to build its search-highlight markup by
// string-concatenating the raw JSON text into an HTML string and rendering
// it via {@html} (both for object keys and string values) - so a workflow
// JSON containing e.g. `<img src=x onerror=...>` became live markup the
// moment a search query matched it. The fix renders highlight segments as
// plain text interpolation inside an explicit <mark>, never {@html}.
// Component-level (mounts the real component) - see vitest.component.config.ts.
import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { createClassComponent } from 'svelte/legacy';

const { default: JsonTreeNode } = await import('../../src/lib/components/JsonTreeNode.svelte');

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({ component: JsonTreeNode as never, target, props });
	return {
		target,
		destroy: () => {
			instance.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mount> | undefined;

beforeEach(() => {
	(window as unknown as { __pwned?: boolean }).__pwned = undefined;
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

const PAYLOAD_KEY = '<img src=x onerror="window.__pwned=1">';
const PAYLOAD_VALUE = '<svg onload="window.__pwned=1">pwn</svg>';
const ENCODED_PAYLOAD = '&lt;img src=x onerror=&quot;window.__pwned=1&quot;&gt;';

function assertNoInjection(target: HTMLElement) {
	expect(target.querySelector('img, svg, script')).toBeNull();
	target.querySelectorAll('*').forEach((el) => {
		expect(el.hasAttribute('onerror')).toBe(false);
		expect(el.hasAttribute('onload')).toBe(false);
	});
	expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
}

function baseProps(overrides: Record<string, unknown> = {}) {
	return {
		data: PAYLOAD_VALUE,
		path: 'root.field',
		keyName: PAYLOAD_KEY,
		expandedPaths: new Set<string>(),
		matchingPaths: new Set<string>(),
		searchQuery: '',
		togglePath: () => {},
		isRoot: false,
		...overrides
	};
}

describe('JsonTreeNode search highlighting - XSS safety', () => {
	it('renders a malicious key/value as inert text with a matching query, no injected elements', () => {
		mounted = mount(baseProps({ searchQuery: 'onerror' }));
		assertNoInjection(mounted.target);
		expect(mounted.target.textContent).toContain(PAYLOAD_KEY);
		expect(mounted.target.textContent).toContain(PAYLOAD_VALUE);
	});

	it('renders as inert text with a non-matching query', () => {
		mounted = mount(baseProps({ searchQuery: 'nomatch' }));
		assertNoInjection(mounted.target);
		expect(mounted.target.textContent).toContain(PAYLOAD_KEY);
	});

	it('renders as inert text with a cleared (empty) query', () => {
		mounted = mount(baseProps({ searchQuery: '' }));
		assertNoInjection(mounted.target);
		expect(mounted.target.textContent).toContain(PAYLOAD_KEY);
	});

	it('renders already-encoded markup verbatim as text, not double-decoded into live elements', () => {
		mounted = mount(baseProps({ keyName: ENCODED_PAYLOAD, searchQuery: 'img' }));
		assertNoInjection(mounted.target);
		expect(mounted.target.textContent).toContain(ENCODED_PAYLOAD);
	});

	it('does not throw on a regex-metacharacter query and still matches literally', () => {
		mounted = mount(baseProps({ data: 'a(b)c', searchQuery: '(' }));
		expect(() => mounted!.target.textContent).not.toThrow();
		assertNoInjection(mounted.target);
		const marks = Array.from(mounted.target.querySelectorAll('mark'));
		expect(marks.map((m) => m.textContent).join('')).toBe('(');
	});

	it('wraps a matching substring in <mark> whose concatenated text equals the match, repeated matches included', () => {
		mounted = mount(
			baseProps({
				data: 'foo bar FOO baz foo',
				keyName: '',
				searchQuery: 'foo'
			})
		);
		const marks = Array.from(mounted.target.querySelectorAll('mark'));
		expect(marks.length).toBe(3);
		expect(marks.every((m) => m.textContent?.toLowerCase() === 'foo')).toBe(true);
		// case preserved verbatim in the rendered text, not normalized
		expect(marks[1].textContent).toBe('FOO');
	});

	it('produces no <mark> elements for a non-matching or empty query', () => {
		mounted = mount(baseProps({ data: 'plain string', keyName: '', searchQuery: 'zzz' }));
		expect(mounted.target.querySelectorAll('mark').length).toBe(0);
		mounted.destroy();

		mounted = mount(baseProps({ data: 'plain string', keyName: '', searchQuery: '' }));
		expect(mounted.target.querySelectorAll('mark').length).toBe(0);
	});
});
