// Covers the branch meshPreviewLoadLifecycle.test.ts's success-mocked
// `@google/model-viewer` cannot reach: the dynamic `import('@google/model-viewer')`
// in onMount rejecting (e.g. the CDN/bundle chunk failing to fetch). Needs its
// own file because the mock is set up once per module and must reject instead
// of resolve.
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('@google/model-viewer', () => {
	throw new Error('module unavailable');
});

const { createClassComponent } = await import('svelte/legacy');
const { default: MeshPreview } = await import(
	'$lib/components/workbench/renderers/MeshPreview.svelte'
);

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let target: HTMLDivElement | undefined;
let component: { $destroy: () => void } | undefined;

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target?.remove();
	target = undefined;
});

describe('MeshPreview module load failure', () => {
	it('shows an error alert instead of a permanent spinner when the viewer module fails to import', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		component = createClassComponent({
			component: MeshPreview as never,
			target,
			props: { file: { url: '/api/mesh/a.glb' } }
		}) as never;
		await settle();

		expect(target.querySelector('[role="alert"]')?.textContent).toContain('failed to load');
		expect(target.querySelector('[role="status"].animate-spin')).toBeNull();
		expect(target.querySelector('model-viewer')).toBeNull();
	});
});
