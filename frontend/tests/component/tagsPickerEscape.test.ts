// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { default: TagsPicker } = await import('$lib/components/form-fields/TagsPicker.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount>;

function mountPicker() {
	const onClose = vi.fn();
	const onToggle = vi.fn();
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(TagsPicker, {
		target,
		props: {
			category: {
				key: 'instrumentation',
				label: 'Instrumentation',
				multi: true,
				allow_custom: true,
				tags: ['brushed drums', 'acoustic piano']
			},
			selected: [],
			position: { left: 0, top: 0, maxHeight: 400 },
			fieldAllowCustom: true,
			canAddMore: true,
			onToggle,
			onAddCustom: vi.fn(),
			onBackspaceEmpty: vi.fn(),
			onClose
		}
	});
	flushSync();
	return { onClose, onToggle };
}

function pressEscape(on: Element | Window) {
	on.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
}

afterEach(() => {
	unmount(component);
	target.remove();
});

describe('TagsPicker Escape', () => {
	it('closes when Escape is pressed with a tag button focused after a mouse pick', async () => {
		const { onClose, onToggle } = mountPicker();
		await Promise.resolve();
		const tagButton = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((el) =>
			el.textContent?.includes('acoustic piano')
		)!;
		tagButton.focus();
		tagButton.click();
		flushSync();
		expect(onToggle).toHaveBeenCalledWith('acoustic piano');

		pressEscape(document.activeElement ?? tagButton);
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('closes when Escape is pressed with focus outside the panel', () => {
		const { onClose } = mountPicker();
		document.body.focus();
		pressEscape(document.body);
		expect(onClose).toHaveBeenCalledTimes(1);
	});
});
