// Covers the state machine MeshPreview.svelte itself owns (loading -> ready,
// loading -> error via the model-viewer `error` event, the empty state, and
// re-entering "loading" when the file prop swaps to a new mesh) - none of
// which meshUrl.test.ts can see, since that file only exercises the pure URL/
// metadata resolver functions, never the component. `@google/model-viewer`
// is mocked here because it calls `customElements.define` / touches WebGL at
// import time, none of which jsdom can host; mounting the *real* package is
// exactly what the component's dynamic import + moduleFailed branch guards
// against; that failure path gets its own file/mock.
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('@google/model-viewer', () => ({}));

const { createClassComponent } = await import('svelte/legacy');
const { default: MeshPreview } = await import(
	'$lib/components/workbench/renderers/MeshPreview.svelte'
);

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function mount(file: Record<string, unknown> | null) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: MeshPreview as never,
		target,
		props: { file }
	});
	return {
		target,
		component: component as { $set: (props: Record<string, unknown>) => void; $destroy: () => void },
		viewerEl: () => target.querySelector('model-viewer'),
		alertEl: () => target.querySelector('[role="alert"]'),
		spinnerEl: () => target.querySelector('[role="status"].animate-spin'),
		resetButton: () => target.querySelector('button[aria-label="Reset view"]'),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.useRealTimers();
});

describe('MeshPreview loading lifecycle', () => {
	it('shows the empty state and no viewer element when the file has no resolvable url', async () => {
		mounted = mount({});
		await settle();

		expect(mounted.target.textContent).toContain('No model');
		expect(mounted.viewerEl()).toBeNull();
	});

	it('shows a loading spinner and a hidden model-viewer once the module resolves, before load fires', async () => {
		mounted = mount({ url: '/api/mesh/a.glb' });
		await settle();

		expect(mounted.spinnerEl()).not.toBeNull();
		expect(mounted.target.textContent).toContain('Loading model');
		const viewer = mounted.viewerEl();
		expect(viewer).not.toBeNull();
		expect(viewer?.getAttribute('src')).toBe('/api/mesh/a.glb');
		expect(viewer?.getAttribute('style')).toContain('opacity: 0');
	});

	it('becomes ready and reveals the viewer + reset button when model-viewer fires load', async () => {
		mounted = mount({ url: '/api/mesh/a.glb' });
		await settle();

		mounted.viewerEl()!.dispatchEvent(new Event('load'));
		await settle();

		expect(mounted.spinnerEl()).toBeNull();
		expect(mounted.viewerEl()?.getAttribute('style')).toContain('opacity: 1');
		expect(mounted.resetButton()).not.toBeNull();
	});

	it('shows an error alert and removes the viewer element when model-viewer fires error', async () => {
		mounted = mount({ url: '/api/mesh/a.glb' });
		await settle();

		mounted.viewerEl()!.dispatchEvent(new CustomEvent('error', { detail: { type: 'loadfailure' } }));
		await settle();

		expect(mounted.alertEl()?.textContent).toContain('could not be displayed');
		expect(mounted.viewerEl()).toBeNull();
	});

	it('re-enters the loading state when the file prop switches to a different mesh', async () => {
		mounted = mount({ url: '/api/mesh/a.glb' });
		await settle();
		mounted.viewerEl()!.dispatchEvent(new Event('load'));
		await settle();
		expect(mounted.spinnerEl()).toBeNull();

		mounted.component.$set({ file: { url: '/api/mesh/b.glb' } });
		await settle();

		expect(mounted.spinnerEl()).not.toBeNull();
		expect(mounted.viewerEl()?.getAttribute('src')).toBe('/api/mesh/b.glb');
		expect(mounted.viewerEl()?.getAttribute('style')).toContain('opacity: 0');
	});

	it('times out to an error state if load/error never fire within the load budget', async () => {
		vi.useFakeTimers();
		mounted = mount({ url: '/api/mesh/a.glb' });
		// Flush the real microtask the dynamic import + onMount need to resolve,
		// interleaved with the fake clock so the 45s timer gets armed.
		for (let i = 0; i < 8; i++) {
			await vi.advanceTimersByTimeAsync(0);
		}
		expect(mounted.spinnerEl()).not.toBeNull();

		await vi.advanceTimersByTimeAsync(45000);

		expect(mounted.alertEl()?.textContent).toContain('took too long to load');
		expect(mounted.viewerEl()).toBeNull();
	});
});
