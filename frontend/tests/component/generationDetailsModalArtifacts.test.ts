// @vitest-environment jsdom
//
// The history detail modal is the only place an ordinary user inspects a
// finished generation, and its Artifacts card is the only reachable consumer of
// `artifactRendererRegistry` outside the admin pages. These tests drive the
// real modal: a registered artifact type renders through its registered
// component, an unregistered one falls back to the raw payload, an artifact the
// recorder dropped shows why, and neither a report load nor a renderer resolve
// left in flight by a generation switch may paint the generation the modal has
// left.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { flushSync } from 'svelte';

const hoisted = vi.hoisted(() => ({
	resolve: null as null | ((artifactType: string) => Promise<unknown>),
	runReports: new Map<string, unknown>(),
	reportLoad: null as null | ((generationId: string) => Promise<unknown>)
}));

vi.mock('$lib/registries/artifactRendererRegistry', async () => {
	const actual = await vi.importActual<
		typeof import('$lib/registries/artifactRendererRegistry')
	>('$lib/registries/artifactRendererRegistry');
	return {
		artifactRendererRegistry: {
			...actual.artifactRendererRegistry,
			resolve: (artifactType: string) =>
				(hoisted.resolve ?? actual.artifactRendererRegistry.resolve)(artifactType)
		}
	};
});

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			post: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getGenerationParams: vi
			.fn()
			.mockResolvedValue({ success: true, data: { parameters: {}, models: [] } }),
		getGenerationById: vi.fn().mockResolvedValue({ success: false, error: 'not mocked' }),
		getGenerationRunReport: vi.fn((generationId: string) =>
			hoisted.reportLoad
				? hoisted.reportLoad(generationId)
				: Promise.resolve({
						success: true,
						data: { run_report: hoisted.runReports.get(generationId) ?? null }
					})
		),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getBaseURL: vi.fn(() => ''),
		getToken: vi.fn(() => null),
		setOnAuthExpired: vi.fn()
	}
}));

const { default: GenerationDetailsModal } = await import(
	'$lib/components/modals/GenerationDetailsModal.svelte'
);
const { default: SeedArtifact } = await import(
	'$lib/components/generation/artifacts/SeedArtifact.svelte'
);
const { default: FallbackArtifact } = await import(
	'$lib/components/generation/artifacts/FallbackArtifact.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

interface ReportArtifact {
	at: string;
	pipe_id: string | null;
	artifact_type: string;
	artifact_data: Record<string, unknown> | null;
	omitted?: { reason: string; bytes: number };
}

function report(artifacts: ReportArtifact[]) {
	return {
		schema_version: 2,
		status_history: [],
		pipe_timers: {},
		artifacts,
		plugin_outputs: {},
		prompt_template: null,
		artifacts_omitted: 0,
		plugin_outputs_omitted: 0,
		artifacts_bytes: 0,
		stored_bytes: 0
	};
}

function generation(id: string) {
	return {
		id,
		form_data: {},
		status: 'completed' as const,
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		files: [],
		segments: [],
		rating: 0,
		is_favorite: false
	};
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((r) => (resolve = r));
	return { promise, resolve };
}

async function settle() {
	await new Promise((r) => setTimeout(r, 0));
	flushSync();
}

let component: ReturnType<typeof createClassComponent> | null = null;
let target: HTMLDivElement;

// The modal's root carries `use:portal`, which relocates it to `document.body`,
// so assertions read the whole document rather than `target`.
function mountModal(gen: ReturnType<typeof generation>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: GenerationDetailsModal as never,
		target,
		props: { generation: gen as never, isOpen: true }
	});
	flushSync();
	return component;
}

function artifactsToggle(): HTMLButtonElement | undefined {
	return Array.from(document.body.querySelectorAll('button')).find((b) =>
		b.textContent?.includes('Artifacts')
	) as HTMLButtonElement | undefined;
}

async function expandArtifacts() {
	artifactsToggle()?.click();
	await settle();
}

function bodyText(): string {
	return document.body.textContent ?? '';
}

beforeEach(() => {
	hoisted.resolve = null;
	hoisted.reportLoad = null;
	hoisted.runReports.clear();
});

afterEach(() => {
	component?.$destroy();
	component = null;
	target?.remove();
});

describe('GenerationDetailsModal artifacts card', () => {
	it('renders a registered artifact type through its registered component', async () => {
		hoisted.runReports.set(
			'gen-seed',
			report([
				{ at: '2026-01-01T00:00:00Z', pipe_id: 'generator', artifact_type: 'seed', artifact_data: { seed: 424242 } }
			])
		);

		mountModal(generation('gen-seed'));
		await settle();

		expect(artifactsToggle()?.getAttribute('aria-expanded')).toBe('false');
		expect(bodyText()).not.toContain('424242');

		await expandArtifacts();

		expect(bodyText()).toContain('424242');
		expect(document.body.querySelector('pre')).toBeNull();
	});

	it('falls back to the raw payload for an artifact type with no renderer', async () => {
		hoisted.runReports.set(
			'gen-unknown',
			report([
				{
					at: '2026-01-01T00:00:00Z',
					pipe_id: 'generator',
					artifact_type: 'totally_unregistered_kind',
					artifact_data: { marker: 'raw-payload-marker' }
				}
			])
		);

		mountModal(generation('gen-unknown'));
		await settle();
		await expandArtifacts();

		const pre = document.body.querySelector('pre');
		expect(pre?.textContent).toContain('raw-payload-marker');
	});

	it('shows why a dropped payload is missing instead of rendering it', async () => {
		hoisted.runReports.set(
			'gen-omitted',
			report([
				{
					at: '2026-01-01T00:00:00Z',
					pipe_id: 'generator',
					artifact_type: 'compare_images',
					artifact_data: null,
					omitted: { reason: 'item_bytes', bytes: 5242880 }
				}
			])
		);

		mountModal(generation('gen-omitted'));
		await settle();
		await expandArtifacts();

		expect(bodyText()).toContain('Payload not recorded');
		expect(bodyText()).toContain('5242880');
		expect(bodyText()).toContain('item bytes');
	});

	it('renders nothing when the generation has no persisted report', async () => {
		mountModal(generation('gen-no-report'));
		await settle();

		expect(artifactsToggle()).toBeUndefined();
	});

	it('drops a report load left in flight by a generation switch', async () => {
		const first = deferred<unknown>();
		hoisted.reportLoad = (generationId: string) =>
			generationId === 'gen-old'
				? (first.promise as Promise<unknown>)
				: Promise.resolve({
						success: true,
						data: {
							run_report: report([
								{
									at: '2026-01-01T00:00:00Z',
									pipe_id: 'generator',
									artifact_type: 'seed',
									artifact_data: { seed: 222222 }
								}
							])
						}
					});

		mountModal(generation('gen-old'));
		await settle();

		component!.$set({ generation: generation('gen-new') as never });
		await settle();
		await expandArtifacts();
		expect(bodyText()).toContain('222222');

		first.resolve({
			success: true,
			data: {
				run_report: report([
					{
						at: '2026-01-01T00:00:00Z',
						pipe_id: 'generator',
						artifact_type: 'seed',
						artifact_data: { seed: 111111 }
					}
				])
			}
		});
		await settle();

		expect(bodyText()).not.toContain('111111');
		expect(bodyText()).toContain('222222');
	});

	it('drops a renderer resolve left in flight by a generation switch', async () => {
		hoisted.runReports.set(
			'gen-old',
			report([
				{ at: '2026-01-01T00:00:00Z', pipe_id: 'generator', artifact_type: 'seed', artifact_data: { seed: 111111 } }
			])
		);
		hoisted.runReports.set(
			'gen-new',
			report([
				{ at: '2026-01-01T00:00:00Z', pipe_id: 'generator', artifact_type: 'seed', artifact_data: { seed: 222222 } }
			])
		);

		// Each resolve call gets its own deferred, so the load started for the
		// generation the modal has left can be completed last on purpose.
		const pending: ReturnType<typeof deferred<unknown>>[] = [];
		hoisted.resolve = () => {
			const entry = deferred<unknown>();
			pending.push(entry);
			return entry.promise as Promise<unknown>;
		};

		mountModal(generation('gen-old'));
		await settle();
		await expandArtifacts();
		expect(pending).toHaveLength(1);

		component!.$set({ generation: generation('gen-new') as never });
		await settle();
		expect(pending).toHaveLength(2);

		pending[1].resolve(FallbackArtifact);
		await settle();
		expect(document.body.querySelector('pre')?.textContent).toContain('222222');

		pending[0].resolve(SeedArtifact);
		await settle();

		expect(document.body.querySelector('pre')?.textContent).toContain('222222');
		expect(
			Array.from(document.body.querySelectorAll('button')).some((b) =>
				b.textContent?.includes('222222')
			)
		).toBe(false);
	});
});
