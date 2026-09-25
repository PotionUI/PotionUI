import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';

let StubComponent: any;

const MODEL_CARD_ACTION_HOOK = {
	id: 1,
	plugin_id: 'stub-plugin',
	hook_name: 'admin.models.card.actions',
	hook_type: 'frontend',
	component_path: 'StubModelCardAction.js',
	position: undefined,
	sort_order: 10,
	label: 'Run'
};

vi.mock('$lib/services/api/index', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api/index')>(
		'$lib/services/api/index'
	);
	return {
		...actual,
		api: {
			...actual.api,
			getClient: () => ({
				get: vi.fn(async (url: string) => {
					if (url === '/api/plugins/hooks/frontend') {
						return {
							data: { success: true, data: { 'admin.models.card.actions': [MODEL_CARD_ACTION_HOOK] } }
						};
					}
					throw new Error(`unexpected GET ${url}`);
				}),
				post: vi.fn(async () => ({ data: { success: true } }))
			})
		}
	};
});

vi.mock('$lib/plugin-api/componentResolver', () => ({
	resolvePluginComponent: vi.fn(async () => StubComponent),
	setPluginRevisions: vi.fn()
}));

const { default: ModelCard } = await import('$lib/components/ModelCard.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { control } = await import('./stubs/modelCardActionControl');
StubComponent = (await import('./stubs/StubModelCardAction.svelte')).default;

function baseModel(id = 'model-1') {
	return {
		id,
		name: 'Test Model',
		filename: 'test-model.safetensors',
		model_type: 'checkpoint',
		files: [],
		backend_ids: []
	};
}

function mountCard(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: ModelCard as never,
		target,
		props: { model: baseModel(), ...props } as never
	});
	return {
		target,
		card: () => target.querySelector<HTMLElement>('[aria-busy]'),
		ring: () => target.querySelector('.work-ring'),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mountCard> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	control.resolve = null;
});

describe('ModelCard working state', () => {
	it('is idle by default: aria-busy=false and no ring overlay', () => {
		mounted = mountCard();

		expect(mounted.card()?.getAttribute('aria-busy')).toBe('false');
		expect(mounted.ring()).toBeNull();
	});

	it('the working prop marks the card busy and renders the ring overlay', () => {
		mounted = mountCard({ working: true });

		expect(mounted.card()?.getAttribute('aria-busy')).toBe('true');
		expect(mounted.ring()).not.toBeNull();
	});

	it('context.track() from a plugin action turns the ring on until the tracked work resolves', async () => {
		mounted = mountCard({ showManagementActions: true });
		await settle();

		const button = mounted.target.querySelector<HTMLButtonElement>(
			'[data-testid="stub-model-card-action"]'
		);
		expect(button).toBeTruthy();

		flushSync(() => button!.click());
		await settle();

		expect(mounted.card()?.getAttribute('aria-busy')).toBe('true');
		expect(mounted.ring()).not.toBeNull();

		control.resolve?.();
		await settle();
		flushSync();

		expect(mounted.card()?.getAttribute('aria-busy')).toBe('false');
	});
});
