// @vitest-environment jsdom
//
// Lane (c) of the Video Director "shot console" W1 rework
// (frontend/src/lib/components/video-director/console/). Exercises
// ShotRail.svelte (+ its RailPromptLane/RailKeyframesLane/RailAddColumn
// leaves) directly against object literals shaped like the ShotRailModel /
// ConsoleSelection contract from W1-BRIEF.md -- that module
// (console/shotRailModel.ts) is lane (a)'s and isn't landed yet, so this
// suite duck-types the model rather than importing it. Swap to importing the
// real types once console/shotRailModel.ts exists; the shapes below are
// written to match it exactly.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { default: ShotRail } = await import('$lib/components/video-director/console/ShotRail.svelte');

type ConsoleSelection = { shotId: string; kind: 'keyframe' | 'beat' | 'audio'; id: string } | null;

interface RailModelFixtureOptions {
	keyframesCap?: number | null;
	keyframesCanAdd?: boolean;
	promptCanAdd?: boolean;
}

function buildRail(opts: RailModelFixtureOptions = {}) {
	return {
		durationSeconds: 5.5,
		ticks: [
			{ atPercent: 0, major: true, label: '0s' },
			{ atPercent: 9.1, major: false, label: null },
			{ atPercent: 18.2, major: true, label: '1s' },
			{ atPercent: 27.3, major: false, label: null },
			{ atPercent: 36.4, major: true, label: '2s' },
			{ atPercent: 100, major: true, label: '5.5s' }
		],
		lanes: {
			prompt: {
				beats: [
					{ id: 'beat-1', startPercent: 0, widthPercent: 54.5, text: 'Establishing move', global: false },
					{ id: 'gap-1', startPercent: 54.5, widthPercent: 25.5, text: '', global: true },
					{ id: 'beat-2', startPercent: 80, widthPercent: 20, text: 'Detail beat', global: false }
				],
				canAdd: opts.promptCanAdd ?? true,
				addDisabledReason: opts.promptCanAdd === false ? 'Beats are LTX-only' : null
			},
			keyframes: {
				marks: [
					{ id: 'kf-start', kind: 'start' as const, atPercent: 0, thumbUrl: null, label: 'START', empty: true },
					{ id: 'kf-free-1', kind: 'free' as const, atPercent: 32.7, thumbUrl: null, label: '1.8 s', empty: true },
					{ id: 'kf-end', kind: 'end' as const, atPercent: 100, thumbUrl: null, label: 'END', empty: true }
				],
				count: 1,
				cap: opts.keyframesCap === undefined ? 8 : opts.keyframesCap,
				canAdd: opts.keyframesCanAdd ?? true
			},
			audio: {
				clips: [{ id: 'audio-1', startPercent: 0, widthPercent: 100, role: 'Condition', filename: 'ambience.wav' }],
				canAdd: true
			}
		}
	};
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let rectSpy: ReturnType<typeof vi.spyOn>;

function mountRail(props: {
	rail: ReturnType<typeof buildRail>;
	selection?: ConsoleSelection;
	onSelect?: (sel: ConsoleSelection) => void;
	onAddBeat?: (atSeconds: number) => void;
	onAddKeyframe?: (atSeconds: number) => void;
	onAddAudio?: () => void;
	onMoveKeyframe?: (id: string, atSeconds: number) => void;
	onRemoveKeyframe?: (id: string) => void;
	onResizeBeat?: (id: string, edge: 'start' | 'end', atSeconds: number) => void;
}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(ShotRail, {
		target,
		props: {
			shotId: 'shot-1',
			selection: null,
			onSelect: vi.fn(),
			onAddBeat: vi.fn(),
			onAddKeyframe: vi.fn(),
			onAddAudio: vi.fn(),
			onMoveKeyframe: vi.fn(),
			onRemoveKeyframe: vi.fn(),
			onResizeBeat: vi.fn(),
			...props
		}
	});
	flushSync();
}

beforeEach(() => {
	// jsdom's getBoundingClientRect is always a zero rect; the rail's hover
	// and drag math needs a real width to turn a pointer clientX into a
	// fraction of the lane.
	rectSpy = vi
		.spyOn(HTMLElement.prototype, 'getBoundingClientRect')
		.mockReturnValue({ left: 0, top: 0, width: 400, height: 46, right: 400, bottom: 46, x: 0, y: 0, toJSON() {} } as DOMRect);
});

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
	rectSpy.mockRestore();
});

describe('ShotRail ruler', () => {
	it('right-aligns only the last major tick label', () => {
		mountRail({ rail: buildRail() });
		const majors = Array.from(target.querySelectorAll('.tmaj'));
		expect(majors).toHaveLength(4);
		// bite-check: every non-last major tick must NOT carry the flip
		majors.slice(0, -1).forEach((el) => {
			expect((el as HTMLElement).getAttribute('style')).not.toContain('translateX(-100%)');
		});
		const last = majors[majors.length - 1] as HTMLElement;
		expect(last.textContent).toBe('5.5s');
		expect(last.getAttribute('style')).toContain('translateX(-100%)');
	});

	it('places minor ticks by percent without a label', () => {
		mountRail({ rail: buildRail() });
		const minors = Array.from(target.querySelectorAll('.tmin'));
		expect(minors).toHaveLength(2);
		expect((minors[0] as HTMLElement).getAttribute('style')).toContain('9.1%');
		expect(minors[0].textContent).toBe('');
	});
});

describe('ShotRail prompt lane', () => {
	it('places real beats and the global-fill gap by percent', () => {
		mountRail({ rail: buildRail() });
		// locate by label text rather than position, to stay robust to markup order
		const beats = Array.from(target.querySelectorAll('.beat-b'));
		expect(beats.map((b) => b.querySelector('.lbl')?.textContent)).toEqual(['Establishing move', 'Detail beat']);
		expect((beats[1] as HTMLElement).getAttribute('style')).toContain('80%');
		expect((beats[1] as HTMLElement).getAttribute('style')).toContain('20%');

		const gap = target.querySelector('.global-fill') as HTMLElement;
		expect(gap).toBeTruthy();
		expect(gap.getAttribute('style')).toContain('54.5%');
		expect(gap.getAttribute('style')).toContain('25.5%');
		expect(gap.querySelector('.lbl')?.textContent).toBe('Global');
	});

	it('shows an insert cue snapped to the nearest 0.25s while hovering, and none once the pointer leaves', () => {
		mountRail({ rail: buildRail() });
		const lane = target.querySelector('.prompt-lane') as HTMLElement;
		// fraction = 152/400 = 0.38 -> 0.38 * 5.5s = 2.09s -> snaps to 2.00s
		lane.dispatchEvent(new PointerEvent('pointermove', { clientX: 152, bubbles: true }));
		flushSync();
		const cue = target.querySelector('.prompt-lane .insert-cue .pill');
		expect(cue?.textContent).toBe('+ prompt at 2.00 s');

		lane.dispatchEvent(new PointerEvent('pointerleave', { bubbles: true }));
		flushSync();
		expect(target.querySelector('.prompt-lane .insert-cue')).toBeNull();
	});

	it('does not offer an insert cue when the lane cannot grow (chain profile, one full-span beat)', () => {
		mountRail({ rail: buildRail({ promptCanAdd: false }) });
		const lane = target.querySelector('.prompt-lane') as HTMLElement;
		lane.dispatchEvent(new PointerEvent('pointermove', { clientX: 152, bubbles: true }));
		flushSync();
		expect(target.querySelector('.prompt-lane .insert-cue')).toBeNull();
	});

	it('reports the selected beat through onSelect, scoped to this shot', () => {
		const onSelect = vi.fn();
		mountRail({ rail: buildRail(), onSelect });
		const beat = Array.from(target.querySelectorAll('.beat-b')).find((b) => b.textContent?.includes('Detail beat'))!;
		(beat as HTMLElement).click();
		expect(onSelect).toHaveBeenCalledWith({ shotId: 'shot-1', kind: 'beat', id: 'beat-2' });
	});
});

describe('ShotRail keyframes lane', () => {
	it('places anchors at the edges and a free keyframe by percent', () => {
		mountRail({ rail: buildRail() });
		const anchors = Array.from(target.querySelectorAll('.kf-anchor'));
		expect(anchors).toHaveLength(2);
		expect((anchors[0] as HTMLElement).getAttribute('style')).toMatch(/left:\s*0/);
		expect((anchors[1] as HTMLElement).getAttribute('style')).toMatch(/right:\s*0/);

		const free = target.querySelector('.kf-free') as HTMLElement;
		expect(free.getAttribute('style')).toContain('32.7%');
	});

	it('reports the selected keyframe through onSelect', () => {
		const onSelect = vi.fn();
		mountRail({ rail: buildRail(), onSelect });
		(target.querySelector('.kf-free') as HTMLElement).click();
		expect(onSelect).toHaveBeenCalledWith({ shotId: 'shot-1', kind: 'keyframe', id: 'kf-free-1' });
	});

	it('nudges a selected free keyframe by 0.25s on arrow keys', () => {
		const onMoveKeyframe = vi.fn();
		mountRail({ rail: buildRail(), onMoveKeyframe });
		const free = target.querySelector('.kf-free') as HTMLElement;
		free.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		expect(onMoveKeyframe).toHaveBeenCalledWith('kf-free-1', 32.7 / 100 * 5.5 + 0.25);
		free.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
		expect(onMoveKeyframe).toHaveBeenCalledWith('kf-free-1', 32.7 / 100 * 5.5 - 0.25);
	});

	// Maintainer bug (09-04): the hover insert-cue tracked the pointer over
	// the START anchor too, sitting on top of it (and stealing the click)
	// even though the cue carries `pointer-events: none`. Hovering an
	// existing mark must show no cue, and clicking it must only select --
	// never insert a new keyframe underneath it.
	it('hovering the START anchor shows no insert cue; clicking it selects without inserting', () => {
		const onSelect = vi.fn();
		const onAddKeyframe = vi.fn();
		mountRail({ rail: buildRail(), onSelect, onAddKeyframe });
		const anchor = target.querySelector('.kf-anchor') as HTMLElement;
		expect(anchor.getAttribute('style')).toMatch(/left:\s*0/);

		anchor.dispatchEvent(new PointerEvent('pointermove', { clientX: 0, bubbles: true }));
		flushSync();
		expect(target.querySelector('.kf-lane .insert-cue')).toBeNull();

		anchor.click();
		expect(onSelect).toHaveBeenCalledWith({ shotId: 'shot-1', kind: 'keyframe', id: 'kf-start' });
		expect(onAddKeyframe).not.toHaveBeenCalled();
	});

	// Maintainer bug (09-04): "when I place a frame on the keyframes timeline
	// the drag & drop works really poorly -- I drag and see the change only
	// after I drop". A per-move document write (full console-model
	// recompute) made the mark visibly lag behind the pointer; it must now
	// follow the pointer live off local state and commit exactly once, on
	// pointerup.
	function leftPercentOf(el: Element): number {
		const style = el.getAttribute('style') ?? '';
		const match = /left:\s*([\d.]+)%/.exec(style);
		expect(match).not.toBeNull();
		return parseFloat(match![1]);
	}

	it('a free keyframe drag follows the pointer live and commits once, on pointerup', () => {
		const onMoveKeyframe = vi.fn();
		mountRail({ rail: buildRail(), onMoveKeyframe });
		const free = target.querySelector('.kf-free') as HTMLElement;
		const thumb = target.querySelector('.kf-thumb40') as HTMLElement;

		free.dispatchEvent(new PointerEvent('pointerdown', { clientX: 130.8, pointerId: 1, bubbles: true }));
		flushSync();

		// fraction = 240/400 = 0.6 -> 0.6 * 5.5s = 3.3s -> snaps to 3.25s (13 * 0.25s)
		window.dispatchEvent(new PointerEvent('pointermove', { clientX: 240, pointerId: 1 }));
		flushSync();
		const expectedPercent = (3.25 / 5.5) * 100;
		expect(leftPercentOf(free)).toBeCloseTo(expectedPercent, 5);
		expect(leftPercentOf(thumb)).toBeCloseTo(expectedPercent, 5);
		expect(target.querySelector('.drag-label')?.textContent).toBe('3.25s');
		expect(onMoveKeyframe).not.toHaveBeenCalled();

		window.dispatchEvent(new PointerEvent('pointerup', { clientX: 240, pointerId: 1 }));
		flushSync();
		expect(onMoveKeyframe).toHaveBeenCalledTimes(1);
		expect(onMoveKeyframe).toHaveBeenCalledWith('kf-free-1', 3.25);
		expect(target.querySelector('.drag-label')).toBeNull();
	});

	// Maintainer bug (09-04): "I can't remove the dynamic keyframes".
	it('Delete on a selected free mark removes it; the same key on an anchor is a no-op', () => {
		const onRemoveKeyframe = vi.fn();
		mountRail({ rail: buildRail(), onRemoveKeyframe });
		const free = target.querySelector('.kf-free') as HTMLElement;
		free.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }));
		expect(onRemoveKeyframe).toHaveBeenCalledWith('kf-free-1');

		onRemoveKeyframe.mockClear();
		const anchor = target.querySelector('.kf-anchor') as HTMLElement;
		anchor.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }));
		expect(onRemoveKeyframe).not.toHaveBeenCalled();
	});

	it('Escape cancels a free keyframe drag without committing', () => {
		const onMoveKeyframe = vi.fn();
		mountRail({ rail: buildRail(), onMoveKeyframe });
		const free = target.querySelector('.kf-free') as HTMLElement;

		free.dispatchEvent(new PointerEvent('pointerdown', { clientX: 130.8, pointerId: 1, bubbles: true }));
		flushSync();
		window.dispatchEvent(new PointerEvent('pointermove', { clientX: 240, pointerId: 1 }));
		flushSync();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();
		expect(onMoveKeyframe).not.toHaveBeenCalled();
		expect(target.querySelector('.drag-label')).toBeNull();
		expect(leftPercentOf(target.querySelector('.kf-free') as HTMLElement)).toBeCloseTo(32.7, 5);
	});
});

describe('ShotRail add column', () => {
	it('enables the keyframe add button under the cap', () => {
		mountRail({ rail: buildRail({ keyframesCap: 8, keyframesCanAdd: true }) });
		const btn = target.querySelector('button[aria-label="Add keyframe"]') as HTMLButtonElement;
		expect(btn).toBeTruthy();
		expect(btn.disabled).toBe(false);
		expect(btn.classList.contains('disabled')).toBe(false);
	});

	it('disables the keyframe add button at the cap, with the reason as the title', () => {
		mountRail({ rail: buildRail({ keyframesCap: 8, keyframesCanAdd: false }) });
		const btn = target.querySelector('button[aria-label="Add keyframe"]') as HTMLButtonElement;
		expect(btn.disabled).toBe(true);
		expect(btn.classList.contains('disabled')).toBe(true);
		expect(btn.title).toContain('8');
	});

	it('renders a spacer, not a button, when the profile has zero free-keyframe capability (Wan anchors-only)', () => {
		mountRail({ rail: buildRail({ keyframesCap: 0, keyframesCanAdd: false }) });
		expect(target.querySelector('button[aria-label="Add keyframe"]')).toBeNull();
		// the prompt lane's own add button is unaffected
		expect(target.querySelector('button[aria-label="Add prompt beat"]')).toBeTruthy();
	});

	it('adds a beat at the next open slot (the gap between the two beats)', () => {
		const onAddBeat = vi.fn();
		mountRail({ rail: buildRail(), onAddBeat });
		(target.querySelector('button[aria-label="Add prompt beat"]') as HTMLButtonElement).click();
		expect(onAddBeat).toHaveBeenCalledTimes(1);
		const atSeconds = onAddBeat.mock.calls[0][0];
		// the only real gap is [3.0, 4.4]s (beat-1 ends at 3.0s, beat-2 starts at 4.4s)
		expect(atSeconds).toBeGreaterThan(3.0);
		expect(atSeconds).toBeLessThan(4.4);
	});
});
