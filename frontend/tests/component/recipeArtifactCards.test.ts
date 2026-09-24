import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { RecipeArtifact } from '$lib/services/api/recipes';

const { default: RecipeArtifactCards } = await import(
	'../../src/routes/admin/components/recipes/RecipeArtifactCards.svelte'
);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function artifact(overrides: Partial<RecipeArtifact> = {}): RecipeArtifact {
	return {
		id: 'video-vae',
		kind: 'model',
		model_type: 'vae',
		filename: 'video-vae.safetensors',
		display_name: 'MiniMax-H3 Video VAE',
		size_bytes: 21_500_000_000,
		required: true,
		gated: false,
		license_url: null,
		...overrides
	};
}

function mountCards(artifacts: RecipeArtifact[]) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(RecipeArtifactCards, { target, props: { artifacts } });
	flushSync();
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('RecipeArtifactCards', () => {
	it('renders one card per artifact, with no table', () => {
		mountCards([artifact({ id: 'a' }), artifact({ id: 'b', display_name: 'Text encoder' })]);
		expect(target.querySelectorAll('[data-recipe-artifact-card]').length).toBe(2);
		expect(target.querySelector('table')).toBeNull();
	});

	it('truncates a long name with a tooltip trigger, never a title attribute', () => {
		mountCards([artifact({ display_name: 'A'.repeat(80) })]);
		const card = target.querySelector('[data-recipe-artifact-card]') as HTMLElement;
		expect(card.querySelector('span.truncate')).not.toBeNull();
		expect(card.querySelector('[title]')).toBeNull();
	});

	it('shows type, size and required as nowrap mono, and the optional label otherwise', () => {
		mountCards([artifact({ required: false })]);
		const card = target.querySelector('[data-recipe-artifact-card]') as HTMLElement;
		expect(card.textContent).toContain('vae');
		expect(card.textContent).toContain('20.02');
		expect(card.textContent).toContain('optional');
	});

	it('renders gated and licence chips on the name line', () => {
		mountCards([artifact({ gated: true, license_url: 'https://example.com/licence' })]);
		const card = target.querySelector('[data-recipe-artifact-card]') as HTMLElement;
		expect(card.textContent).toContain('gated');
		const link = card.querySelector('a[href="https://example.com/licence"]');
		expect(link?.textContent?.trim()).toBe('licence');
	});
});
