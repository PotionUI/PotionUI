// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { default: Icon } = await import('../../src/lib/components/Icon.svelte');
const { registerIcon, getAllIconNames, getFilledIconPaths } = await import('../../src/lib/utils/IconLibrary');

let target: HTMLDivElement | undefined;
let component: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
});

function render(name: string) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(Icon, { target, props: { name } });
	flushSync();
}

describe('Icon with a plugin-registered icon', () => {
	it('draws an icon registered after it mounted, without a remount', () => {
		render('my-plugin-late-icon');
		expect(target!.querySelector('svg')).toBeNull();
		expect(target!.textContent?.trim()).toBe('M');

		expect(registerIcon('my-plugin-late-icon', ['M4 4h16', 'M4 20h16'])).toBe(true);
		flushSync();

		expect(Array.from(target!.querySelectorAll('path')).map((path) => path.getAttribute('d'))).toEqual([
			'M4 4h16',
			'M4 20h16'
		]);
	});

	it('draws an icon registered before it mounted', () => {
		registerIcon('my-plugin-early-icon', 'M12 4v16');
		render('my-plugin-early-icon');

		expect(target!.querySelector('path')?.getAttribute('d')).toBe('M12 4v16');
	});

	it('still draws the filled glyphs of a core icon', () => {
		const name = getAllIconNames().find((icon) => getFilledIconPaths(icon).length > 0)!;
		render(name);

		expect(target!.querySelectorAll('path[fill="currentColor"]')).toHaveLength(getFilledIconPaths(name).length);
	});
});
