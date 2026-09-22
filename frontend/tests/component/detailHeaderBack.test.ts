import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { default: DetailHeader } = await import('../../src/lib/components/detail/DetailHeader.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function mountHeader(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(DetailHeader, {
		target,
		props: { title: 'Sunset study', icon: 'document', ...props }
	});
	flushSync();
}

function backButton(): HTMLButtonElement | null {
	return target.querySelector('button[data-detail-back]');
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('DetailHeader back control', () => {
	it('renders the back control before the title when backLabel and onBack are given', () => {
		mountHeader({ backLabel: 'Prompts', onBack: vi.fn() });
		const back = backButton();
		const heading = target.querySelector('h2') as HTMLElement;
		expect(back).not.toBeNull();
		expect(back!.textContent?.replace(/\s+/g, ' ').trim()).toBe('Prompts');
		expect(back!.getAttribute('aria-label')).toBe('Back to Prompts');
		expect(back!.hasAttribute('title')).toBe(false);
		expect(back!.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(back!.className.split(/\s+/)).toContain('text-xs');
	});

	it('calls onBack when the back control is clicked', () => {
		const onBack = vi.fn();
		mountHeader({ backLabel: 'Segments', onBack });
		backButton()!.click();
		expect(onBack).toHaveBeenCalledTimes(1);
	});

	it('renders no back control when backLabel or onBack is missing', () => {
		mountHeader();
		expect(backButton()).toBeNull();
		unmount(component!);
		component = null;
		target.remove();

		mountHeader({ backLabel: 'Templates' });
		expect(backButton()).toBeNull();
		unmount(component!);
		component = null;
		target.remove();

		mountHeader({ onBack: vi.fn() });
		expect(backButton()).toBeNull();
	});
});
