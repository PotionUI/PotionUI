// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import type { VideoDirectorValue, DirectorCapabilities, ChainSegment } from '../../src/lib/types/videoDirector';
import type { ConsoleShot } from '../../src/lib/components/video-director/console/consoleModel';
import type { PromptResourceSpec } from '../../src/lib/utils/promptResources';
import { baseModeCap } from './directorFixtures';

vi.mock('$lib/services/api/index', async (importOriginal) => {
	const original = await importOriginal<typeof import('$lib/services/api/index')>();
	original.api.getClient = (() => ({
		get: vi.fn().mockResolvedValue({ data: { data: {} } }),
		put: vi.fn().mockResolvedValue({ data: { success: true } })
	})) as never;
	original.api.getModelById = vi.fn().mockResolvedValue({ success: true, data: { model: { name: 'Face' } } }) as never;
	original.api.getPromptResourceMembers = vi
		.fn()
		.mockResolvedValue({ success: true, data: { members: [{ kind: 'image', name: null }] } }) as never;
	return original;
});

const { default: ShotStage } = await import('../../src/lib/components/video-director/console/ShotStage.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { flushSync } = await import('svelte');

const HINT = 'This shot uses no references. Type @ to add one.';

const specs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' },
	{ field: 'reference_kits', kind: 'image', label: 'Kits', token: '<Picture @>', source: 'kit' }
];

const caps = {
	presetModes: null,
	modes: { director: baseModeCap({ keyframes: 'first_only', maxSegments: 8 }) },
	enabledModes: ['director'],
	defaultDuration: 5,
	defaultFps: 16,
	maxDuration: null,
	maxFrames: null,
	segmentRouting: true,
	references: 'per_shot',
	referenceFields: ['references', 'reference_kits']
} as unknown as DirectorCapabilities;

function doc(prompt: string): VideoDirectorValue {
	const segment: ChainSegment = {
		id: 'chain-1',
		prompt,
		prompt_segments: prompt ? [{ id: 'p0', content: prompt, chips: {}, type: 'content', enabled: true }] : [],
		duration: 3,
		loras: null,
		keyframe: null,
		keyframe_strength: 1,
		last_keyframe: null,
		last_keyframe_strength: 1,
		sub_type_override: null,
		steps: null,
		cfg: null
	};
	return {
		schema_version: 1,
		mode: 'director',
		global_prompt: '',
		global_prompt_segments: [],
		negative_prompt: '',
		negative_prompt_segments: [],
		simple: { duration: 5, fps: 24, start_image: null, first_frame: null, last_frame: null },
		timeline: { fps: 24, shots: [] },
		chain: { fps: 16, segments: [segment], continuation: { overlap_frames: 0, stitch: true }, keyframes: [], audio: [] }
	} as VideoDirectorValue;
}

const shot = {
	id: 'chain-1',
	index: 0,
	number: '01',
	title: 'Shot 1',
	durationSeconds: 3,
	startSeconds: 0,
	frames: 48,
	capFrames: null,
	newFrames: null,
	fps: 16,
	fpsLocked: false,
	timingQualified: true,
	thumb: { url: null, source: 'slate' },
	badge: 'independent',
	hasIcLora: false,
	icLoraCount: 0,
	run: null,
	tabs: [{ id: 'selection', label: 'Selection' }],
	canRemove: true,
	canDuplicate: true
} as unknown as ConsoleShot;

function mount(prompt: string, formData: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: ShotStage as never,
		target,
		props: {
			shot,
			doc: doc(prompt),
			caps,
			formData,
			presetId: 'preset-1',
			selection: { shotId: 'chain-1', kind: 'shot', id: 'chain-1' },
			onDoc: () => {},
			promptResources: specs
		}
	});
	flushSync();
	return {
		hint: () => target.querySelector('[data-testid="shot-no-references-hint"]'),
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
});

describe('shot prompt reference hint', () => {
	it('shows when a native reference exists and the prompt cites none', () => {
		mounted = mount('an empty street', { references: [{ path: '/pool/a.png', type: 'image', url: '/u/a.png' }] });
		expect(mounted.hint()?.textContent).toBe(HINT);
	});

	it('shows for a pool made only of a selected kit', () => {
		mounted = mount('an empty street', { reference_kits: [{ model: 'model:01X', strength: 1, copies: 1 }] });
		expect(mounted.hint()?.textContent).toBe(HINT);
	});

	it('hides once the prompt cites a kit', () => {
		mounted = mount('with @[reference_kits:model:01X]', { reference_kits: [{ model: 'model:01X', strength: 1, copies: 1 }] });
		expect(mounted.hint()).toBeNull();
	});

	it('hides when nothing is available, or the only kit has zero strength', () => {
		mounted = mount('an empty street', { reference_kits: [{ model: 'model:01X', strength: 0, copies: 1 }] });
		expect(mounted.hint()).toBeNull();
	});
});
