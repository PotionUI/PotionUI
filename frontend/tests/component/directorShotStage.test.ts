// @vitest-environment jsdom
//
// Video Director console -- ShotStage.svelte (Wave 1, lane d). Covers:
// tab row hidden with only Selection / shown with the right labels, the
// global-prompt fallback copy, the keyframe variant's fields, the beat
// variant's Range row appearing only on a timeline profile, and the
// Overrides disclosure rendering its Steps/CFG inputs disabled.
import { describe, it, expect, afterEach } from 'vitest';
import type {
	VideoDirectorValue,
	DirectorCapabilities,
	DirectorModeCapability,
	ChainSegment,
	DirectorPromptSegment
} from '../../src/lib/types/videoDirector';
import type { ConsoleShot } from '../../src/lib/components/video-director/console/consoleModel';
import type { ConsoleSelection } from '../../src/lib/components/video-director/console/consoleSelection';

const { default: ShotStage } = await import('../../src/lib/components/video-director/console/ShotStage.svelte');
const { default: OverridesDisclosure } = await import('../../src/lib/components/video-director/console/OverridesDisclosure.svelte');
const { createClassComponent } = await import('svelte/legacy');

function baseModeCap(overrides: Partial<DirectorModeCapability> = {}): DirectorModeCapability {
	return {
		tips: [],
		maxDuration: null,
		audio: false,
		icLora: false,
		maxKeyframes: null,
		perSegmentLoras: false,
		keyframes: 'none',
		maxSegments: null,
		maxFramesPerSegment: null,
		defaultSegmentDuration: 5,
		continuation: null,
		maxOverlapFrames: null,
		continuationDisabled: false,
		...overrides
	};
}

function baseDoc(): VideoDirectorValue {
	return {
		schema_version: 1,
		mode: 'director',
		global_prompt: 'Cinematic workshop, warm tungsten light',
		global_prompt_segments: [],
		negative_prompt: '',
		negative_prompt_segments: [],
		simple: { duration: 5, fps: 24, start_image: null, first_frame: null, last_frame: null },
		timeline: { duration: 5, fps: 24, segments: [], keyframes: [], audio: [], ic_lora: [] },
		chain: { fps: 16, segments: [], continuation: { overlap_frames: 0, stitch: true }, keyframes: [], audio: [] }
	};
}

function tlSegment(id: string, text: string, start: number, end: number): DirectorPromptSegment {
	return { id, start, end, text, prompt_segments: text ? [{ id: `${id}-p0`, content: text, chips: {}, type: 'content', enabled: true }] : [] };
}

function chainSegment(id: string, prompt: string, duration: number, overrides: Partial<ChainSegment> = {}): ChainSegment {
	return {
		id,
		prompt,
		prompt_segments: prompt ? [{ id: `${id}-p0`, content: prompt, chips: {}, type: 'content', enabled: true }] : [],
		duration,
		loras: null,
		keyframe: null,
		keyframe_strength: 1,
		last_keyframe: null,
		last_keyframe_strength: 1,
		sub_type_override: null,
		...overrides
	};
}

// One LTX-style timeline shot: two prompt beats and one free keyframe.
function timelineDoc(): VideoDirectorValue {
	const doc = baseDoc();
	doc.timeline = {
		duration: 5,
		fps: 24,
		segments: [tlSegment('seg-1', 'Establishing move', 0, 3), tlSegment('seg-2', 'Detail beat', 3, 5)],
		keyframes: [{ id: 'kf-1', start: 1.8, role: 'free', strength: 0.85, media: { path: 'lighting_ref_1.jpg' } }],
		audio: [],
		ic_lora: []
	};
	return doc;
}

function timelineCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: { director: baseModeCap({ keyframes: 'anywhere', audio: true, icLora: true, maxKeyframes: 8 }) },
		enabledModes: ['director'],
		defaultDuration: 5,
		defaultFps: 24,
		maxDuration: null,
		maxFrames: null,
		segmentRouting: false,
		references: null,
		referenceFields: []
	};
}

// One Wan-style chain shot -- a chain "shot" IS its own full-span beat
// (PLAN.md D3), so there is no separate start/end to range-edit.
function chainDoc(): VideoDirectorValue {
	const doc = baseDoc();
	doc.chain = {
		fps: 16,
		segments: [chainSegment('chain-1', 'Artisan at work', 3, { loras: { high: [], low: [] } })],
		continuation: { overlap_frames: 0, stitch: true },
		keyframes: [],
		audio: []
	};
	return doc;
}

function chainCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: { director: baseModeCap({ keyframes: 'first_only', maxSegments: 8, perSegmentLoras: true }) },
		enabledModes: ['director'],
		defaultDuration: 5,
		defaultFps: 16,
		maxDuration: null,
		maxFrames: null,
		segmentRouting: true,
		references: null,
		referenceFields: []
	};
}

// Whole-film reference pool (MiniMax-H3 refs-style): every shot always
// conditions on the entire pool, so the tab has no per-shot selection at all.
function chainCapsWholeRefs(): DirectorCapabilities {
	return { ...chainCaps(), references: 'whole', referenceFields: ['references', 'reference_audios'] };
}

function baseShot(overrides: Partial<ConsoleShot> = {}): ConsoleShot {
	return {
		id: 'shot-1',
		index: 0,
		number: '01',
		title: 'Shot 1',
		durationSeconds: 5,
		startSeconds: 0,
		frames: 120,
		capFrames: null,
		newFrames: null,
		fps: 24,
		fpsLocked: false,
		thumb: { url: null, source: 'slate' },
		badge: 'independent',
		hasIcLora: false,
		icLoraCount: 0,
		run: null,
		tabs: [{ id: 'selection', label: 'Selection' }],
		canRemove: true,
		canDuplicate: true,
		...overrides
	};
}

function mount(props: {
	shot: ConsoleShot;
	doc: VideoDirectorValue;
	caps: DirectorCapabilities;
	selection: ConsoleSelection;
	presetId?: string;
	formData?: Record<string, unknown> | null;
}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: ShotStage as never,
		target,
		props: {
			shot: props.shot,
			doc: props.doc,
			caps: props.caps,
			formData: props.formData ?? null,
			presetId: props.presetId ?? 'preset-1',
			selection: props.selection,
			onDoc: () => {}
		}
	});
	return {
		target,
		text: () => target.textContent ?? '',
		tabButtons: () => Array.from(target.querySelectorAll<HTMLButtonElement>('.stage-tab')),
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

describe('ShotStage tab row', () => {
	it('is hidden when the shot has no tabs beyond Selection', () => {
		mounted = mount({
			shot: baseShot(),
			doc: chainDoc(),
			caps: chainCaps(),
			selection: null
		});

		expect(mounted.target.querySelector('.stage-tabs')).toBeNull();
	});

	it('shows one button per tab with its base label and count chip split out', () => {
		mounted = mount({
			shot: baseShot({ tabs: [{ id: 'selection', label: 'Selection' }, { id: 'loras', label: 'LoRAs · 2' }] }),
			doc: chainDoc(),
			caps: chainCaps(),
			selection: null
		});

		const buttons = mounted.tabButtons();
		expect(buttons).toHaveLength(2);
		expect(buttons[0].textContent?.trim()).toBe('Selection');
		expect(buttons[1].textContent?.replace(/\s+/g, ' ').trim()).toBe('LoRAs 2');
		expect(buttons[1].querySelector('.cnt2')?.textContent).toBe('2');
	});
});

describe('ShotStage global fallback', () => {
	it('renders the film Global prompt and the override hint when nothing is selected', () => {
		mounted = mount({
			shot: baseShot(),
			doc: chainDoc(),
			caps: chainCaps(),
			selection: null
		});

		expect(mounted.text()).toContain('Global prompt');
		expect(mounted.text()).toContain('Cinematic workshop, warm tungsten light');
		expect(mounted.text()).toContain('This shot uses the global prompt here — add a prompt beat on the timeline to override it.');
	});
});

describe('ShotStage keyframe variant', () => {
	it('renders Role/Time/Strength/Source fields for the selected free keyframe', () => {
		mounted = mount({
			shot: baseShot(),
			doc: timelineDoc(),
			caps: timelineCaps(),
			selection: { shotId: 'shot-1', kind: 'keyframe', id: 'kf-1' }
		});

		const text = mounted.text();
		expect(text).toContain('Keyframe — free, 1.8 s');
		expect(mounted.target.querySelector('.stage-kf')).not.toBeNull();
		expect(text).toContain('Role');
		expect(text).toContain('Free');
		expect(text).toContain('Time');
		expect(text).toContain('Strength');
		expect(text).toContain('0.85');
		expect(text).toContain('Source');
		expect(text).toContain('lighting_ref_1.jpg');
	});

	it('offers named-landmark snap chips for a free (unlocked) keyframe', () => {
		mounted = mount({
			shot: baseShot(),
			doc: timelineDoc(),
			caps: timelineCaps(),
			selection: { shotId: 'shot-1', kind: 'keyframe', id: 'kf-1' }
		});

		const chips = Array.from(mounted.target.querySelectorAll<HTMLButtonElement>('.snap-chip'));
		const labels = chips.map((c) => c.textContent?.replace(/\s+/g, ' ').trim());
		expect(labels).toEqual(['Start 0.00s', 'Block 1 end 3.00s', 'End 5.00s']);
	});
});

describe('ShotStage beat variant Range row', () => {
	it('shows the editable Range row for a timeline (LTX-style) beat', () => {
		mounted = mount({
			shot: baseShot(),
			doc: timelineDoc(),
			caps: timelineCaps(),
			selection: { shotId: 'shot-1', kind: 'beat', id: 'seg-1' }
		});

		expect(mounted.text()).toContain('Prompt beat — 0.0 – 3.0 s');
		const head = mounted.target.querySelector('.stage-beat-head');
		expect(head).not.toBeNull();
		expect(head?.textContent).toContain('Range');
		expect(head?.textContent).toContain('Remove beat');
		expect(mounted.target.querySelectorAll('.range-input')).toHaveLength(2);
	});

	it('shows no Range row for a chain shot\'s own full-span beat', () => {
		mounted = mount({
			shot: baseShot({ id: 'chain-1' }),
			doc: chainDoc(),
			caps: chainCaps(),
			selection: { shotId: 'chain-1', kind: 'beat', id: 'chain-1' }
		});

		expect(mounted.text()).toContain('Prompt beat');
		expect(mounted.text()).not.toContain('Prompt beat —');
		expect(mounted.target.querySelector('.stage-beat-head')).toBeNull();
		expect(mounted.target.querySelectorAll('.range-input')).toHaveLength(0);
	});
});

describe('OverridesDisclosure', () => {
	function mountOverrides() {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({ component: OverridesDisclosure as never, target, props: {} });
		return {
			target,
			destroy: () => {
				component.$destroy();
				target.remove();
			}
		};
	}

	it('renders Steps and CFG inputs disabled with an explanatory title', () => {
		const disclosure = mountOverrides();
		try {
			const inputs = Array.from(disclosure.target.querySelectorAll<HTMLInputElement>('.overrides-input'));
			expect(inputs).toHaveLength(2);
			for (const input of inputs) {
				expect(input.disabled).toBe(true);
				expect(input.title).toBe('Per-shot overrides land in the next wave');
			}
		} finally {
			disclosure.destroy();
		}
	});
});

describe('ShotStage References tab -- whole-film pool (read-only)', () => {
	// Ported from the deleted tests/component/railReferencePoolPreview.test.ts
	// (git show HEAD~1 -- that file tested the old Rail "Refs" lane, since
	// removed with no replacement until this tab). The assertion that
	// mattered: a pool item's thumbnail gates on `opt.item.type` -- an audio
	// reference has a `url` but must never reach a bare `<img src>` (a broken-
	// image icon in a real browser), only an image reference may.
	it('lists the pool read-only, rendering an audio reference through the Icon fallback, never an <img>', async () => {
		mounted = mount({
			shot: baseShot({ tabs: [{ id: 'selection', label: 'Selection' }, { id: 'references', label: 'References · 2' }] }),
			doc: chainDoc(),
			caps: chainCapsWholeRefs(),
			selection: null,
			formData: {
				references: [{ path: '/pool/a.png', type: 'image', url: '/pool/a.png' }],
				reference_audios: [{ path: '/pool/voice.mp3', type: 'audio', url: '/pool/voice.mp3' }]
			}
		});

		const referencesTab = mounted.tabButtons().find((b) => b.textContent?.includes('References'));
		expect(referencesTab).toBeTruthy();
		referencesTab!.click();
		await Promise.resolve();

		expect(mounted.text()).toContain('This shot inherits every reference in the pool');
		expect(mounted.target.querySelector('input[type="checkbox"]')).toBeNull();

		const imgs = Array.from(mounted.target.querySelectorAll('img'));
		expect(imgs.some((img) => img.getAttribute('src') === '/pool/a.png')).toBe(true);
		expect(imgs.some((img) => img.getAttribute('src') === '/pool/voice.mp3')).toBe(false);
	});
});
