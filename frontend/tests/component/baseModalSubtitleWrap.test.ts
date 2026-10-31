import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { default: BaseModal } = await import('../../src/lib/components/modals/BaseModal.svelte');

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
	document.body.style.overflow = '';
});

function subtitleClass(props: Record<string, unknown>): string {
	instance = mount(BaseModal, {
		target: document.body.appendChild(document.createElement('div')),
		props: { isOpen: true, title: 'T', subtitle: 'A long subtitle', ...props }
	});
	flushSync();
	return Array.from(document.body.querySelectorAll('p')).find((p) => p.textContent === 'A long subtitle')!.className;
}

describe('BaseModal subtitle', () => {
	it('truncates by default', () => {
		expect(subtitleClass({})).toContain('truncate');
	});

	it('wraps when asked to', () => {
		expect(subtitleClass({ subtitleWrap: true })).not.toContain('truncate');
	});
});
