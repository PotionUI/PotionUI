// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';

const { default: ChatMessage } = await import('../../src/lib/components/ChatMessage.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({ component: ChatMessage as never, target, props });
	return target;
}

afterEach(() => {
	document.body.innerHTML = '';
});

describe('ChatMessage encoded script links', () => {
	it.each([
		'javascript&#58;alert&#40;1&#41;',
		'javascript&#x3a;alert(1)',
		'java\tscript:alert(1)',
		'&#106;avascript:alert(1)'
	])('renders no javascript href for %j', (url) => {
		const target = mount({ role: 'assistant', content: `see [click me](${url}) now` });
		const hrefs = [...target.querySelectorAll('[href]')].map((el) => el.getAttribute('href') ?? '');
		expect(hrefs.filter((h) => h.replace(/[\s\u0000-\u001f]/g, '').toLowerCase().startsWith('javascript'))).toEqual([]);
		expect(target.querySelector('a[href*="alert"]')).toBeNull();
		expect(target.textContent).toContain('click me');
	});

	it('still renders a normal https link', () => {
		const target = mount({ role: 'assistant', content: '[ok](https://example.com/x)' });
		expect(target.querySelector('a')?.getAttribute('href')).toBe('https://example.com/x');
	});
});
