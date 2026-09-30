// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';

const { default: ModelResultRow } = await import('$lib/components/form-fields/ModelResultRow.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mountRow(model: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: ModelResultRow as never,
		target,
		props: { model, onSelect: () => {}, onToggleFavorite: () => {} }
	});
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountRow> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('Model picker row for a cloud model', () => {
	it('does not show the slug as a file name', () => {
		mounted = mountRow({ id: 'm1', name: 'Veo 3.1', filename: 'google-veo-3-1', model_type: 'cloud' });
		expect(mounted.target.textContent).toContain('Veo 3.1');
		expect(mounted.target.textContent).not.toContain('google-veo-3-1');
	});

	it('still shows the file name of a file model', () => {
		mounted = mountRow({ id: 'm2', name: 'Flux', filename: 'flux-dev.safetensors', model_type: 'checkpoint' });
		expect(mounted.target.textContent).toContain('flux-dev');
	});
});
