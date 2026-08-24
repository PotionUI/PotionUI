// @vitest-environment jsdom
//
// Proves teardown, not just green helpers: chipEditorDom/chipEditorCaret stay
// green even when a resync leaks chip component instances, because they never
// mount a real Svelte component. This spies on the actual `mount`/`unmount`
// calls ChatChipInput.svelte makes and checks every chip container that gets
// detached from the document was unmounted first.
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		suggestChatResources: vi.fn().mockResolvedValue({ success: true, data: { suggestions: [] } })
	}
}));

const mountCalls: Array<{ target: Element; component: unknown }> = [];
const unmountedComponents = new Set<unknown>();

vi.mock('svelte', async (importOriginal) => {
	const actual = await importOriginal<typeof import('svelte')>();
	return {
		...actual,
		mount: (component: unknown, options: { target: Element }) => {
			const instance = actual.mount(component as never, options as never);
			mountCalls.push({ target: options.target, component: instance });
			return instance;
		},
		unmount: (instance: unknown) => {
			unmountedComponents.add(instance);
			return actual.unmount(instance as never);
		}
	};
});

// Imported after the mocks above so ChatChipInput picks up the spied module.
const { createClassComponent } = await import('svelte/legacy');
const { default: ChatChipInput } = await import('$lib/components/chat/ChatChipInput.svelte');

function orphanedLeaks() {
	return mountCalls.filter((m) => !m.target.isConnected && !unmountedComponents.has(m.component));
}

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	mountCalls.length = 0;
	unmountedComponents.clear();
});

describe('ChatChipInput chip component lifecycle', () => {
	it('unmounts every chip instance whose host node is resynced away', async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);

		const resources = {
			r1: { uri: 'alpha', label: 'Alpha' },
			r2: { uri: 'beta', label: 'Beta' }
		};

		const component = createClassComponent({
			component: ChatChipInput as never,
			target,
			props: {
				value: 'hi @alpha and @beta done',
				resources,
				mode: '',
				formData: {},
				loraSelections: {}
			}
		});
		cleanup = () => component.$destroy();

		expect(mountCalls.length).toBe(2);
		expect(orphanedLeaks().length).toBe(0);

		// Same two chips persist across several externally-driven resyncs (the
		// controlled-input round trip: parent updates `value` after each edit).
		for (let i = 0; i < 3; i++) {
			component.$set({ value: `hi @alpha and @beta done (${i})` });
			await new Promise((resolve) => setTimeout(resolve, 0));
			await new Promise((resolve) => setTimeout(resolve, 0));
		}

		const leaks = orphanedLeaks();
		expect(leaks.length).toBe(0);
	});
});
