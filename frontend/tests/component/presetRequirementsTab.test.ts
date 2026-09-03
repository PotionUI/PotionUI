// @vitest-environment jsdom
//
// Covers the Requirements tab's three checked states (ready / missing /
// unknown) - the hero fraction+verdict must track `summary`, a section with
// any non-ok item stays expanded by default while an all-ok section
// collapses to its one-line header, and an action button only ever renders
// next to a non-ok row. Also covers the "never declares requirements" quiet
// empty state and the re-check button forcing `?refresh=1`.
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { RequirementResultInfo } from '$lib/types/api';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetRequirements: vi.fn() }
}));

const gotoMock = vi.fn();
vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => gotoMock(...args) }));

const api = await import('$lib/services/api/index');
const { default: PresetRequirementsTab } = await import(
	'../../src/routes/admin/components/presets/PresetRequirementsTab.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function result(overrides: Partial<RequirementResultInfo> = {}): RequirementResultInfo {
	return {
		status: 'ok',
		detail: 'ok',
		type: 'binary',
		name: 'ffmpeg',
		...overrides
	};
}

function mockResponse(results: RequirementResultInfo[], checkedAt = 1_735_000_000) {
	const summary = { ok: 0, missing: 0, unknown: 0 };
	for (const r of results) summary[r.status] += 1;
	return { success: true, data: { results, summary, checked_at: checkedAt } };
}

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: PresetRequirementsTab as never,
		target,
		props: { presetId: 'preset-1' }
	});
	return {
		target,
		button: (text: string) =>
			Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
				b.textContent?.includes(text)
			),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('PresetRequirementsTab', () => {
	it('renders the ready verdict and a collapsed all-ok section', async () => {
		vi.mocked(api.api.getPresetRequirements).mockResolvedValue(
			mockResponse([
				result({ type: 'binary', name: 'ffmpeg', detail: "'ffmpeg' found on PATH" }),
				result({ type: 'python_package', name: 'xformers>=0.0.28', detail: '0.0.29 installed' })
			])
		);

		mounted = mount();
		await settle();

		expect(mounted.target.textContent).toContain('2');
		expect(mounted.target.textContent).toContain('/ 2 satisfied');
		expect(mounted.target.textContent).toContain('Ready');
		expect(mounted.target.textContent).toContain('This preset is ready to run.');
		// All-ok sections collapse - their item rows aren't rendered until expanded.
		expect(mounted.target.textContent).not.toContain('found on PATH');
	});

	it('expands a section with a missing requirement and shows its hint + action', async () => {
		vi.mocked(api.api.getPresetRequirements).mockResolvedValue(
			mockResponse([
				result({ type: 'binary', name: 'ffmpeg', detail: "'ffmpeg' found on PATH" }),
				result({
					type: 'comfyui_node',
					name: 'comfyui_node: KleinDiffusionSampler',
					status: 'missing',
					detail: 'not installed on "Local ComfyUI"',
					hint: 'Install the node pack from ComfyUI Manager, then re-check',
					action: { kind: 'open_backends', payload: {} }
				})
			])
		);

		mounted = mount();
		await settle();

		expect(mounted.target.textContent).toContain("Can't run here");
		expect(mounted.target.textContent).toContain("This preset can't run here: 1 missing.");
		// The Backend section (comfyui_node) has a problem and is expanded by default.
		expect(mounted.target.textContent).toContain('comfyui_node: KleinDiffusionSampler');
		expect(mounted.target.textContent).toContain('Install the node pack from ComfyUI Manager');
		const actionButton = mounted.button('Open Backends');
		expect(actionButton).toBeTruthy();

		actionButton?.click();
		expect(gotoMock).toHaveBeenCalledWith('/admin?tab=backends');
	});

	it('never renders an action button next to an ok row', async () => {
		vi.mocked(api.api.getPresetRequirements).mockResolvedValue(
			mockResponse([
				result({
					type: 'model',
					name: "model tagged 'klein-9b'",
					status: 'ok',
					detail: "a model tagged 'klein-9b' is present",
					action: undefined
				}),
				result({
					type: 'comfyui_model',
					name: 'checkpoints/klein-9b-fp8.safetensors',
					status: 'missing',
					detail: 'no available model with that hash found in the depot',
					action: { kind: 'open_downloader', payload: {} }
				})
			])
		);

		mounted = mount();
		await settle();

		// Expand the Models section (default-expanded since it has a problem) and
		// confirm exactly one action button exists - next to the missing row only.
		const buttons = Array.from(mounted.target.querySelectorAll('button')).filter((b) =>
			b.textContent?.includes('Open Downloader')
		);
		expect(buttons).toHaveLength(1);
	});

	it('shows the unknown verdict when nothing is missing but something could not be checked', async () => {
		vi.mocked(api.api.getPresetRequirements).mockResolvedValue(
			mockResponse([
				result({ type: 'binary', name: 'ffmpeg', detail: "'ffmpeg' found on PATH" }),
				result({
					type: 'vram_min_gb',
					name: '16 GB VRAM',
					status: 'unknown',
					detail: 'no local VRAM reading available',
					hint: undefined
				})
			])
		);

		mounted = mount();
		await settle();

		expect(mounted.target.textContent).toContain('1 unknown');
		expect(mounted.target.textContent).toContain("Can't confirm this preset can run here");
	});

	it('shows a quiet empty state when the preset declares no requirements', async () => {
		vi.mocked(api.api.getPresetRequirements).mockResolvedValue(mockResponse([]));

		mounted = mount();
		await settle();

		expect(mounted.target.textContent).toContain('This preset declares no requirements');
	});

	it('re-checks with refresh=true when the re-check action is used', async () => {
		vi.mocked(api.api.getPresetRequirements).mockResolvedValue(
			mockResponse([result({ type: 'binary', name: 'ffmpeg', detail: 'found' })])
		);

		mounted = mount();
		await settle();

		expect(api.api.getPresetRequirements).toHaveBeenCalledWith('preset-1', false);

		const recheck = mounted.target.querySelector<HTMLButtonElement>('button[aria-label="Re-check requirements"]');
		expect(recheck).toBeTruthy();
		recheck?.click();
		await settle();

		expect(api.api.getPresetRequirements).toHaveBeenLastCalledWith('preset-1', true);
	});
});
