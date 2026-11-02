import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { modesRequest } from '../../src/lib/components/recipes/modeInstall/modeInstallFixtures';
import type { SetupConsentSlot, SetupConsentVariant, SetupConsentGpuProfile, SetupRun } from '$lib/services/api/setup';
import type { RecipeArtifact } from '$lib/services/api/recipes';
import type { Download } from '$lib/stores/downloads';
import { initialModelDownloadState } from '$lib/utils/modelDownloadState';

const { default: GatedModelLink } = await import('../../src/lib/components/models/GatedModelLink.svelte');
const { default: RecipeArtifactCards } = await import('../../src/routes/admin/components/recipes/RecipeArtifactCards.svelte');
const { default: RecipeRunProgress } = await import('../../src/lib/components/recipes/RecipeRunProgress.svelte');
const { default: ConsentVariantPicker } = await import('../../src/lib/components/recipes/ConsentVariantPicker.svelte');
const { default: SlotVariantRow } = await import('../../src/lib/components/recipes/SlotVariantRow.svelte');
const { default: ModeInstallSheet } = await import('../../src/lib/components/recipes/modeInstall/ModeInstallSheet.svelte');
const { default: DownloadDetail } = await import('../../src/routes/admin/components/DownloadDetail.svelte');

const HF = 'https://huggingface.co/Lightricks/LTX-2.5';
let mounted: ReturnType<typeof mount> | null = null;

function render(component: any, props: Record<string, unknown>) {
	const target = document.body.appendChild(document.createElement('div'));
	mounted = mount(component, { target, props });
	flushSync();
	return target;
}

const links = (root: ParentNode) => Array.from(root.querySelectorAll<HTMLAnchorElement>('a[data-gated-link]'));

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	document.body.innerHTML = '';
	document.body.style.overflow = '';
});

describe('GatedModelLink', () => {
	it('opens the page in a new tab without leaking the opener', () => {
		const target = render(GatedModelLink, { source: { license_url: HF } });
		const [link] = links(target);
		expect(link.getAttribute('href')).toBe(HF);
		expect(link.getAttribute('target')).toBe('_blank');
		expect(link.getAttribute('rel')).toBe('noopener noreferrer');
		expect(link.textContent).toContain('Open on Hugging Face');
		expect(link.querySelector('svg')).not.toBeNull();
	});

	it('renders nothing when no page can be resolved', () => {
		const target = render(GatedModelLink, { source: { provider: 'civitai', ref: '1' } });
		expect(links(target)).toHaveLength(0);
	});
});

describe('recipe artifact cards', () => {
	function artifact(overrides: Partial<RecipeArtifact> = {}): RecipeArtifact {
		return {
			id: 'a',
			kind: 'model',
			model_type: 'diffusion_model',
			filename: 'a.safetensors',
			display_name: 'Transformer',
			size_bytes: 1000,
			required: true,
			gated: false,
			license_url: null,
			...overrides
		};
	}

	it('links a gated artifact and leaves a free one alone', () => {
		const target = render(RecipeArtifactCards, {
			artifacts: [
				artifact({ id: 'g', gated: true, source: 'huggingface', repo_id: 'Lightricks/LTX-2.5' }),
				artifact({ id: 'f' })
			]
		});
		const cards = target.querySelectorAll('[data-recipe-artifact-card]');
		expect(links(cards[0])[0].getAttribute('href')).toBe(HF);
		expect(links(cards[1])).toHaveLength(0);
	});
});

describe('recipe run consent', () => {
	function run(artifacts: Record<string, unknown>[]): SetupRun {
		const attempt = {
			step_key: 'fetch',
			attempt: 1,
			status: 'awaiting_consent',
			safe_output: { consent_request: { artifacts, total_bytes: 1000, providers: [] } }
		};
		return {
			id: 'run1',
			recipe_id: 'r',
			recipe_version: 1,
			scope: 'admin',
			mode: 'apply',
			status: 'awaiting_consent',
			current_step: 'fetch',
			steps: [{ step_key: 'fetch', title: 'Fetch models', kind: 'fetch', ordinal: 0, status: 'awaiting_consent', attempts: [attempt] }],
			attempts: []
		} as unknown as SetupRun;
	}
	const base = { size_bytes: 1000, kind: 'checkpoint', license_url: null };

	it('links gated files in the consent list only', () => {
		const target = render(RecipeRunProgress, {
			run: run([
				{ ...base, id: 'g', display_name: 'Gated one', gated: true, license_url: HF },
				{ ...base, id: 'f', display_name: 'Free one', gated: false }
			]),
			actions: { applyAction: async () => ({}), grantConsent: async () => ({}) },
			onRunUpdated: () => {}
		});
		expect(links(target)).toHaveLength(1);
		expect(links(target)[0].getAttribute('href')).toBe(HF);
	});
});

function variant(overrides: Partial<SetupConsentVariant> = {}): SetupConsentVariant {
	return {
		id: 'v',
		label: 'Standard',
		precision: 'int8',
		filename: 'v.safetensors',
		size_bytes: 1000,
		installed: false,
		gated: false,
		license_url: null,
		uploader: null,
		source: 'huggingface',
		repo_id: 'Lightricks/LTX-2.5',
		source_url: null,
		is_recipe_default: false,
		fast: null,
		recommended: true,
		note: null,
		...overrides
	};
}

describe('consent variant picker', () => {
	const gpu: SetupConsentGpuProfile = {
		generation: 'ada',
		generation_label: 'Ada',
		name: 'GPU',
		vram_gb: 24,
		compute_capability: '8.9',
		fast_precisions: []
	};
	function slot(variants: SetupConsentVariant[]): SetupConsentSlot {
		return {
			id: 's',
			label: 'Transformer',
			kind: 'checkpoint',
			model_type: 'diffusion_model',
			required: true,
			recommended_variant_id: variants[0].id,
			reason: '',
			variants
		};
	}

	it('links a gated variant through its repo and not a free one', () => {
		const target = render(ConsentVariantPicker, {
			gpu,
			slots: [slot([variant({ id: 'g', gated: true }), variant({ id: 'f' })])],
			selections: {},
			onPick: vi.fn()
		});
		expect(links(target).length).toBeGreaterThan(0);
		expect(links(target).every((a) => a.getAttribute('href') === HF)).toBe(true);
		const free = render(ConsentVariantPicker, { gpu, slots: [slot([variant()])], selections: {}, onPick: vi.fn() });
		expect(links(free)).toHaveLength(0);
	});

	it('links a gated file that has no alternatives', () => {
		const target = render(ConsentVariantPicker, {
			gpu,
			slots: [slot([variant({ id: 'only', gated: true })])],
			selections: {},
			onPick: vi.fn()
		});
		expect(links(target)[0].getAttribute('href')).toBe(HF);
	});
});

describe('slot variant row', () => {
	it('links a gated variant and not a free one', () => {
		const gated = render(SlotVariantRow, {
			variant: variant({ gated: true }),
			download: initialModelDownloadState,
			onDownload: vi.fn()
		});
		expect(links(gated)[0].getAttribute('href')).toBe(HF);
		const free = render(SlotVariantRow, { variant: variant(), download: initialModelDownloadState, onDownload: vi.fn() });
		expect(links(free)).toHaveLength(0);
	});

	it('links a failed gated download whose variant was not flagged', () => {
		const target = render(SlotVariantRow, {
			variant: variant({ source: null, repo_id: null }),
			download: { ...initialModelDownloadState, phase: 'failed', error: 'denied', gated: true, gatedRepo: 'org/name' },
			onDownload: vi.fn()
		});
		expect(links(target)[0].getAttribute('href')).toBe('https://huggingface.co/org/name');
	});
});

describe('mode install sheet', () => {
	it('links the gated notice and drops it when the gated mode is unticked', () => {
		render(ModeInstallSheet, { request: modesRequest(), onConfirm: vi.fn(), onCancel: vi.fn() });
		const notice = document.body.querySelector('[data-mode-gated]')!;
		expect(links(notice)[0].getAttribute('href')).toBe('https://example.test/licence');
		(document.body.querySelector('[data-mode-row="motion"] input') as HTMLInputElement).click();
		flushSync();
		expect(document.body.querySelector('[data-mode-gated]')).toBeNull();
		expect(links(document.body)).toHaveLength(0);
	});
});

describe('download detail', () => {
	function download(overrides: Partial<Download> = {}): Download {
		return {
			id: 'd1',
			type: 'model',
			url: 'https://huggingface.co/org/name/resolve/main/a.safetensors',
			destination_path: '/m/a.safetensors',
			filename: 'a.safetensors',
			status: 'failed',
			progress: 0,
			total_bytes: null,
			downloaded_bytes: 0,
			speed_bytes_per_sec: null,
			error_message: 'Access denied (HTTP 403)',
			provider_id: null,
			tags: [],
			checksum_sha256: null,
			retry_count: 0,
			created_at: null,
			started_at: null,
			completed_at: null,
			created_by: null,
			...overrides
		} as Download;
	}

	it('links a failed gated download and not an ordinary failure', () => {
		const gated = render(DownloadDetail, {
			download: download({ gated: true, gated_repo: 'org/name' }),
			onBack: vi.fn()
		});
		expect(links(gated)[0].getAttribute('href')).toBe('https://huggingface.co/org/name');
		const plain = render(DownloadDetail, { download: download(), onBack: vi.fn() });
		expect(links(plain)).toHaveLength(0);
	});
});
