import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';

const modules = import.meta.glob('../../../content/plugins/sdk/HostUi.svelte', { eager: true }) as Record<string, { default: any }>;
const HostUi = Object.values(modules)[0].default;

function fakeEntry() {
	const mounted: any[] = [];
	return {
		mounted,
		entry: {
			mount: vi.fn((target: HTMLElement, props: any) => {
				const handle = { target, props };
				mounted.push(handle);
				return handle;
			}),
			update: vi.fn(),
			unmount: vi.fn()
		}
	};
}

describe('HostUi plugin wrapper', () => {
	let host: HTMLElement;

	afterEach(() => {
		host?.remove();
		delete (window as any).__potionui;
	});

	function setup(components: Record<string, unknown>) {
		(window as any).__potionui = { components };
		host = document.createElement('div');
		document.body.appendChild(host);
	}

	it('mounts the host component with props and children, updates and unmounts', () => {
		const { entry, mounted } = fakeEntry();
		setup({ Button: entry });
		const children = createRawSnippet(() => ({ render: () => '<b>kid</b>' }));
		const component = mount(HostUi, { target: host, props: { name: 'Button', props: { variant: 'primary' }, children } });
		flushSync();

		expect(entry.mount).toHaveBeenCalledTimes(1);
		const [target, props] = entry.mount.mock.calls[0];
		expect(target).toBeInstanceOf(HTMLElement);
		expect(props.variant).toBe('primary');
		const slot = document.createElement('div');
		props.children(slot);
		expect(slot.querySelector('b')?.textContent).toBe('kid');

		unmount(component);
		expect(entry.unmount).toHaveBeenCalledWith(mounted[0]);
	});

	it('pushes prop changes to update', () => {
		const { entry } = fakeEntry();
		setup({ Badge: entry });
		let props = $state({ variant: 'neutral' });
		const component = mount(HostUi, {
			target: host,
			props: {
				name: 'Badge',
				get props() {
					return props;
				}
			}
		});
		flushSync();
		entry.update.mockClear();
		props = { variant: 'danger' };
		flushSync();
		expect(entry.update).toHaveBeenCalledWith(expect.anything(), { variant: 'danger' });
		unmount(component);
	});

	it('logs the missing name for an unknown component', () => {
		setup({});
		const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
		const component = mount(HostUi, { target: host, props: { name: 'Buton' } });
		flushSync();
		expect(errors).toHaveBeenCalledWith("Unknown host component 'Buton'");
		unmount(component);
		errors.mockRestore();
	});
});
