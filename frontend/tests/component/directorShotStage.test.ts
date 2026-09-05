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
const { chainEdgeKeyframeId } = await import('../../src/lib/utils/videoDirector');

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
		fpsLocked: false,
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
		timeline: {
			fps: 24,
			shots: [{ id: 'shot-1', duration: 5, continue_from_previous: false, segments: [], keyframes: [], audio: [], ic_lora: [] }]
		},
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
		steps: null,
		cfg: null,
		...overrides
	};
}

// One LTX-style timeline shot: two prompt beats and one free keyframe.
function timelineDoc(): VideoDirectorValue {
	const doc = baseDoc();
	doc.timeline = {
		fps: 24,
		shots: [
			{
				id: 'shot-1',
				duration: 5,
				continue_from_previous: false,
				segments: [tlSegment('seg-1', 'Establishing move', 0, 3), tlSegment('seg-2', 'Detail beat', 3, 5)],
				keyframes: [{ id: 'kf-1', start: 1.8, role: 'free', strength: 0.85, media: { path: 'lighting_ref_1.jpg' } }],
				audio: [],
				ic_lora: []
			}
		]
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
		timingQualified: true,
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
	onDoc?: (next: VideoDirectorValue) => void;
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
			onDoc: props.onDoc ?? (() => {})
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

// Maintainer bug (09-04): "I can't remove the dynamic keyframes". A free
// keyframe minted empty (the normal "Add keyframe" flow -- no media yet)
// had its Remove button hidden by the earlier empty-anchor fix, which
// gated Remove on `model.media` for every role instead of only locked
// (first/last) edges.
describe('ShotStage free keyframe remove (09-04 bug regression)', () => {
	it('an EMPTY free keyframe still shows Remove; clicking it drops the keyframe from the doc', () => {
		const doc = timelineDoc();
		doc.timeline.shots[0].keyframes = [{ id: 'kf-1', start: 1.8, role: 'free', strength: 0.85, media: null }];
		let latest: VideoDirectorValue | undefined;
		mounted = mount({
			shot: baseShot(),
			doc,
			caps: timelineCaps(),
			selection: { shotId: 'shot-1', kind: 'keyframe', id: 'kf-1' },
			onDoc: (next) => (latest = next)
		});

		const removeBtn = Array.from(mounted.target.querySelectorAll<HTMLButtonElement>('.btn')).find((b) =>
			b.textContent?.includes('Remove')
		);
		expect(removeBtn).toBeTruthy();
		removeBtn!.click();

		expect(latest?.timeline.shots[0].keyframes.some((k) => k.id === 'kf-1')).toBe(false);
	});

	it('a FILLED free keyframe still shows Remove and drops the keyframe on click', () => {
		let latest: VideoDirectorValue | undefined;
		mounted = mount({
			shot: baseShot(),
			doc: timelineDoc(),
			caps: timelineCaps(),
			selection: { shotId: 'shot-1', kind: 'keyframe', id: 'kf-1' },
			onDoc: (next) => (latest = next)
		});

		const removeBtn = Array.from(mounted.target.querySelectorAll<HTMLButtonElement>('.btn')).find((b) =>
			b.textContent?.includes('Remove')
		);
		expect(removeBtn).toBeTruthy();
		removeBtn!.click();

		expect(latest?.timeline.shots[0].keyframes.some((k) => k.id === 'kf-1')).toBe(false);
	});
});

describe('ShotStage chain edge keyframe (09-04 bug regression)', () => {
	// The bug: shotRailModel.ts's RailKeyframesLane anchor minted an ad-hoc
	// `${segment.id}-leading` id that neither `parseChainEdgeKeyframeId` nor
	// the placed-keyframes list recognised -- clicking START/END selected
	// something `deriveStageModel` could never resolve, so the stage showed
	// nothing. `chainEdgeKeyframeId` is the one true id both the rail mark
	// (shotRailModel.test.ts) and this selection must agree on.
	it('selecting the START anchor id shows the keyframe panel with its media', () => {
		const doc = chainDoc();
		doc.chain = {
			...doc.chain,
			segments: doc.chain.segments.map((s) =>
				s.id === 'chain-1' ? { ...s, keyframe: { path: 'start_frame.png' }, keyframe_strength: 1 } : s
			)
		};
		mounted = mount({
			shot: baseShot({ id: 'chain-1' }),
			doc,
			caps: chainCaps(),
			selection: { shotId: 'chain-1', kind: 'keyframe', id: chainEdgeKeyframeId('first', 'chain-1') }
		});

		expect(mounted.target.querySelector('.stage-kf')).not.toBeNull();
		const text = mounted.text();
		expect(text).toContain('Role');
		expect(text).toContain('Start');
		expect(text).toContain('start_frame.png');
	});

	it('selecting the END anchor id shows the keyframe panel', () => {
		const doc = chainDoc();
		doc.chain = {
			...doc.chain,
			segments: doc.chain.segments.map((s) =>
				s.id === 'chain-1' ? { ...s, last_keyframe: { path: 'end_frame.png' }, last_keyframe_strength: 1 } : s
			)
		};
		mounted = mount({
			shot: baseShot({ id: 'chain-1' }),
			doc,
			caps: chainCaps(),
			selection: { shotId: 'chain-1', kind: 'keyframe', id: chainEdgeKeyframeId('last', 'chain-1') }
		});

		expect(mounted.target.querySelector('.stage-kf')).not.toBeNull();
		const text = mounted.text();
		expect(text).toContain('End');
		expect(text).toContain('end_frame.png');
	});

	// Maintainer bug (09-04): "click on the 'start' frame -- I can't add
	// anything there". Root cause: `rail.keyframes` (railModel.ts's
	// `deriveChainRail`) only mirrors an edge once it ALREADY has media, so
	// `buildKeyframeModel`'s guard on that list returned null for an empty
	// edge and the stage fell through to the Global-prompt fallback. Fixed
	// by resolving a chain-edge selection straight from the segment/block,
	// independent of that mirror.
	it('an EMPTY START anchor selects and shows a media well; picking one sets segment.keyframe', async () => {
		const doc = chainDoc(); // no keyframe on chain-1 by default
		let emitted: VideoDirectorValue | null = null;
		mounted = mount({
			shot: baseShot({ id: 'chain-1' }),
			doc,
			caps: chainCaps(),
			selection: { shotId: 'chain-1', kind: 'keyframe', id: chainEdgeKeyframeId('first', 'chain-1') },
			formData: { start_image: { path: '/pool/a.png', type: 'image', url: '/pool/a.png' } },
			onDoc: (next) => {
				emitted = next;
			}
		});

		// The well renders (empty state) instead of falling back to the
		// Global-prompt panel.
		expect(mounted.target.querySelector('.stage-kf')).not.toBeNull();
		expect(mounted.target.querySelector('.stage-kf-img.empty')).not.toBeNull();
		expect(mounted.text()).toContain('Start');

		// Strength disabled until an image exists; no Remove for an empty well.
		const strengthInput = mounted.target.querySelector<HTMLInputElement>('.strength-slider');
		expect(strengthInput?.disabled).toBe(true);
		expect(Array.from(mounted.target.querySelectorAll('button')).some((b) => b.textContent?.trim() === 'Remove')).toBe(false);

		// Simulate a pick via the form's reference pool (DirectorMediaSlot's
		// "From form" list) -- avoids driving a real file-upload control.
		const buttonsBefore = mounted.target.querySelectorAll('button').length;
		const fromFormBtn = Array.from(mounted.target.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
			b.textContent?.includes('From form')
		);
		expect(fromFormBtn).toBeTruthy();
		fromFormBtn!.click();
		await Promise.resolve();

		const buttonsAfter = Array.from(mounted.target.querySelectorAll<HTMLButtonElement>('button'));
		expect(buttonsAfter.length).toBeGreaterThan(buttonsBefore);
		const optionBtn = buttonsAfter[buttonsAfter.length - 1];
		optionBtn.click();
		await Promise.resolve();

		expect(emitted).not.toBeNull();
		const seg = emitted!.chain.segments.find((s) => s.id === 'chain-1')!;
		expect(seg.keyframe).toEqual({ form_ref: { field: 'start_image', path: '/pool/a.png' } });
	});

	// Maintainer bug (09-04): "I'm on the first shot and I can 'snap' a
	// keyframe to the end which will be... the last shot". The keyframe
	// stage's "Snap to" landmarks (and its Time field) must be shot-local:
	// this shot's own Start(0)/End(its own duration) only, never a landmark
	// from another shot in the same film.
	it('a 2-shot film, shot 1 selected: snap landmarks are exactly Start/End of shot 1, nothing from shot 2', () => {
		const doc = chainDoc();
		doc.chain = {
			fps: 16,
			segments: [
				chainSegment('chain-1', 'first shot', 3), // 48 frames @16fps
				chainSegment('chain-2', 'second shot', 4) // 64 frames -- must never leak in
			],
			continuation: { overlap_frames: 0, stitch: true },
			keyframes: [{ id: 'kf-1', at: 1.5, strength: 1, media: null }], // lands in shot 1 (0..3s)
			audio: []
		};
		const caps: DirectorCapabilities = {
			...chainCaps(),
			modes: { director: { ...chainCaps().modes.director!, keyframes: 'anywhere', maxKeyframes: 8 } }
		};
		mounted = mount({
			shot: baseShot({ id: 'chain-1' }),
			doc,
			caps,
			selection: { shotId: 'chain-1', kind: 'keyframe', id: 'kf-1' }
		});

		const chips = Array.from(mounted.target.querySelectorAll<HTMLButtonElement>('.snap-chip'));
		const labels = chips.map((c) => c.textContent?.replace(/\s+/g, ' ').trim());
		expect(labels).toEqual(['Start 0.00s', 'End 3.00s']);
	});
});

describe('ShotStage default selection (09-04 bug regression)', () => {
	it("a chain shot's own beat is selected by default -- never the global-prompt fallback", () => {
		// ShotStage itself is a pure function of the `selection` prop it's
		// given -- the DEFAULT (nothing selected yet) is ShotConsole.svelte's
		// job (see its own $effect), so this pins the two states this bug
		// actually confused: passing the shot's own beat selection must show
		// the Prompt editor, not the read-only global fallback.
		mounted = mount({
			shot: baseShot({ id: 'chain-1' }),
			doc: chainDoc(),
			caps: chainCaps(),
			selection: { shotId: 'chain-1', kind: 'beat', id: 'chain-1' }
		});
		expect(mounted.target.querySelector('.stage-beat')).not.toBeNull();
		expect(mounted.text()).not.toContain('This shot uses the global prompt here');
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
	function mountOverrides(props: { doc: VideoDirectorValue; caps: DirectorCapabilities; shotId: string; onDoc?: (v: VideoDirectorValue) => void }) {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({
			component: OverridesDisclosure as never,
			target,
			props: { doc: props.doc, caps: props.caps, shotId: props.shotId, onDoc: props.onDoc ?? (() => {}) }
		});
		return {
			target,
			destroy: () => {
				component.$destroy();
				target.remove();
			}
		};
	}

	// Overrides went LIVE (Steps/CFG bound to ChainSegment.steps/cfg) --
	// editable, not permanently disabled; empty = auto (null).
	it('renders Steps and CFG editable, seeded from the segment, empty when unset', () => {
		const doc = chainDoc();
		doc.chain = { ...doc.chain, segments: doc.chain.segments.map((s) => (s.id === 'chain-1' ? { ...s, steps: 30, cfg: null } : s)) };
		const disclosure = mountOverrides({ doc, caps: chainCaps(), shotId: 'chain-1' });
		try {
			const inputs = Array.from(disclosure.target.querySelectorAll<HTMLInputElement>('.overrides-input'));
			expect(inputs).toHaveLength(2);
			for (const input of inputs) expect(input.disabled).toBe(false);
			expect(inputs[0].value).toBe('30');
			expect(inputs[1].value).toBe('');
			expect(inputs[1].placeholder).toBe('auto');
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
