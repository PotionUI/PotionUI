import { describe, it, expect, afterEach } from 'vitest';
import { flushSync } from 'svelte';

const { default: TextInput } = await import('$lib/components/form-fields/TextInput.svelte');
const { createClassComponent } = await import('svelte/legacy');

const CONFIG = {
	title: 'ABC transcription',
	configuration: {
		input_type: 'textarea',
		rows: 12,
		mono: true,
		pattern: '^(?=[\\s\\S]*^[ \\t]*X:)(?=[\\s\\S]*^[ \\t]*K:)',
		pattern_message: 'ABC needs an X: (reference number) and a K: (key) header line'
	}
};

let destroy: (() => void) | null = null;

function mount(value: string) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({
		component: TextInput as never,
		target,
		props: { name: 'abc', config: CONFIG, value, onChange: () => {} }
	});
	flushSync();
	destroy = () => {
		instance.$destroy();
		target.remove();
	};
	return target;
}

afterEach(() => {
	destroy?.();
	destroy = null;
});

describe('TextInput mono + pattern', () => {
	it('renders a tall mono textarea', () => {
		const target = mount('');
		const textarea = target.querySelector('textarea')!;
		expect(textarea.getAttribute('rows')).toBe('12');
		expect(textarea.className).toContain('font-mono');
		expect(textarea.getAttribute('spellcheck')).toBe('false');
	});

	it('shows the pattern message for a header-less score', () => {
		const target = mount('C D E F | G A B c |');
		expect(target.querySelector('[data-pattern-error]')?.textContent).toBe(
			'ABC needs an X: (reference number) and a K: (key) header line'
		);
		expect(target.querySelector('textarea')?.getAttribute('aria-invalid')).toBe('true');
	});

	it('stays quiet for a valid or empty score', () => {
		expect(mount('X:1\nK:C\nCDEF|').querySelector('[data-pattern-error]')).toBeNull();
		destroy?.();
		expect(mount('').querySelector('[data-pattern-error]')).toBeNull();
	});
});
