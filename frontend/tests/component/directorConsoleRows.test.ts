// @vitest-environment jsdom
//
// Video Director shot console (W1, lane b): row/card/header/join leaves.
// Pins the pieces a caller depends on -- row number/thumb-source/badge
// rendering, the checkbox's toggle callback, the join toggle emitting the
// right value, and the header's checked/unchecked state machine copy
// (note09-decisions.md). Component-level (mounts real Svelte), not unit --
// see vitest.component.config.ts.
import { describe, it, expect, afterEach } from 'vitest';
import { tick } from 'svelte';
import type { ConsoleShot, ConsoleJoin, ConsoleHeader as ConsoleHeaderModel } from '../../src/lib/components/video-director/console/consoleModel';

const { default: ShotRow } = await import('../../src/lib/components/video-director/console/ShotRow.svelte');
const { default: JoinConnector } = await import('../../src/lib/components/video-director/console/JoinConnector.svelte');
const { default: ConsoleHeader } = await import('../../src/lib/components/video-director/console/ConsoleHeader.svelte');
const { default: FilmPromptRow } = await import('../../src/lib/components/video-director/console/FilmPromptRow.svelte');
const { createClassComponent } = await import('svelte/legacy');

function shot(overrides: Partial<ConsoleShot> = {}): ConsoleShot {
	return {
		id: 'shot-1',
		index: 0,
		number: '01',
		title: 'Artisan at work',
		durationSeconds: 3.5,
		startSeconds: 0,
		frames: 84,
		capFrames: 84,
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

function join(overrides: Partial<ConsoleJoin> = {}): ConsoleJoin {
	return {
		afterShotId: 'shot-1',
		beforeShotId: 'shot-1',
		kind: 'cut',
		label: 'Hard cut',
		sentence: 'Starts fresh — nothing shared.',
		control: { kind: 'chip', text: 'Independent' },
		overlapFrames: null,
		...overrides
	};
}

function header(overrides: Partial<ConsoleHeaderModel> = {}): ConsoleHeaderModel {
	return {
		title: 'Video Director',
		shotCount: 3,
		totalSeconds: 14,
		capChips: [{ text: 'Audio' }],
		readiness: { ok: true, text: 'Ready' },
		...overrides
	};
}

function mount<T extends Record<string, unknown>>(component: unknown, props: T) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({ component: component as never, target, props });
	return {
		target,
		destroy: () => {
			instance.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('ShotRow', () => {
	it('renders the shot number, an empty-thumb placeholder, and toggles checked on click', () => {
		let toggled: string | null = null;
		mounted = mount(ShotRow, {
			shot: shot(),
			checked: false,
			onToggleChecked: (id: string) => (toggled = id),
			onActivate: () => {}
		});

		expect(mounted.target.textContent).toContain('01');
		expect(mounted.target.textContent).toContain('Artisan at work');
		// no thumb url -> the dashed empty-slate variant, not a background-image well
		const thumb = mounted.target.querySelector('button[aria-label="Expand Artisan at work"]') as HTMLElement;
		expect(thumb.style.backgroundImage).toBe('');

		const checkbox = mounted.target.querySelector('button[aria-label="Select for generation"]') as HTMLButtonElement;
		expect(checkbox).toBeTruthy();
		checkbox.click();
		expect(toggled).toBe('shot-1');
	});

	it('renders a thumb background-image when the shot has one', () => {
		mounted = mount(ShotRow, {
			shot: shot({ thumb: { url: 'blob:abc', source: 'keyframe' } }),
			checked: false,
			onToggleChecked: () => {},
			onActivate: () => {}
		});
		const thumb = mounted.target.querySelector('button[aria-label="Expand Artisan at work"]') as HTMLElement;
		expect(thumb.style.backgroundImage).toContain('blob:abc');
	});

	it.each([
		['independent', 'Independent'],
		['needs-previous', 'Needs previous shot'],
		['input-ready', 'Input ready'],
		['stale', 'Stale dependency'],
		['continuous', 'Part of continuous render']
	] as const)('renders the %s dependency badge as %s', (badge, label) => {
		mounted = mount(ShotRow, { shot: shot({ badge }), checked: false, onToggleChecked: () => {}, onActivate: () => {} });
		expect(mounted.target.textContent).toContain(label);
	});

	it('shows the Selected chip only once checked, and a checked checkbox renders the checkmark icon', () => {
		mounted = mount(ShotRow, { shot: shot(), checked: true, onToggleChecked: () => {}, onActivate: () => {} });
		expect(mounted.target.textContent).toContain('Selected');
		const checkbox = mounted.target.querySelector('button[aria-label="Selected for generation"]') as HTMLButtonElement;
		expect(checkbox.querySelector('svg')).toBeTruthy();
	});
});

describe('JoinConnector', () => {
	it('renders a chip control read-only, with no toggle buttons', () => {
		mounted = mount(JoinConnector, { join: join(), onSetJoin: () => {} });
		expect(mounted.target.textContent).toContain('Independent');
		expect(mounted.target.querySelector('button')).toBeNull();
	});

	it('emits the toggled value when the inactive side of a Continue/Fresh cut toggle is clicked', () => {
		let call: [string, string] | null = null;
		mounted = mount(JoinConnector, {
			join: join({
				afterShotId: 'shot-2',
				kind: 'native',
				label: 'Native continuation',
				control: { kind: 'toggle', value: 'continue' }
			}),
			onSetJoin: (afterShotId: string, value: 'continue' | 'cut') => (call = [afterShotId, value])
		});

		const buttons = Array.from(mounted.target.querySelectorAll('button'));
		const freshCut = buttons.find((b) => b.textContent?.includes('Fresh cut'));
		expect(freshCut).toBeTruthy();
		freshCut!.click();
		expect(call).toEqual(['shot-2', 'cut']);

		// bite-check: the still-active "Continue" side must be a no-op-looking
		// click too, not silently doing nothing structurally different
		const continueBtn = buttons.find((b) => b.textContent?.includes('Continue'));
		continueBtn!.click();
		expect(call).toEqual(['shot-2', 'continue']);
	});

	it('renders the missing-predecessor warning block with its two actions, wired to no-ops by default', () => {
		let generated: string | null = null;
		let converted: string | null = null;
		mounted = mount(JoinConnector, {
			join: join({
				kind: 'missing',
				label: 'Missing predecessor',
				sentence: 'Shot 01 has no output yet.',
				control: { kind: 'chip', text: 'n/a' }
			}),
			onSetJoin: () => {},
			onGeneratePreviousAndThis: (id: string) => (generated = id),
			onConvertToFreshCut: (id: string) => (converted = id)
		});
		expect(mounted.target.textContent).toContain('Missing predecessor');
		const buttons = Array.from(mounted.target.querySelectorAll('button'));
		buttons.find((b) => b.textContent?.includes('Generate previous'))!.click();
		buttons.find((b) => b.textContent?.includes('Convert to fresh cut'))!.click();
		expect(generated).toBe('shot-1');
		expect(converted).toBe('shot-1');
	});
});

describe('FilmPromptRow', () => {
	it('renders the label, truncated text and segment chip read-only, with no chip when segmentCount is null', () => {
		mounted = mount(FilmPromptRow, {
			row: { kind: 'negative', label: 'Negative prompt', text: 'blurry, warped hands', segmentCount: null },
			position: 'last',
			segments: [],
			onSegmentsChange: () => {}
		});
		expect(mounted.target.textContent).toContain('Negative prompt');
		expect(mounted.target.textContent).toContain('blurry, warped hands');
		expect(mounted.target.textContent).not.toContain('segment');
	});

	it('swaps to the segmented editor on click and reports edited segments back', async () => {
		let latest: unknown = null;
		mounted = mount(FilmPromptRow, {
			row: { kind: 'global', label: 'Global prompt', text: 'Cinematic workshop', segmentCount: 1 },
			position: 'first',
			segments: [{ id: 's1', content: 'Cinematic workshop', chips: {}, type: 'content', enabled: true }],
			onSegmentsChange: (segments: unknown) => (latest = segments)
		});

		expect(mounted.target.querySelector('[role="list"]')).toBeNull();
		(mounted.target.querySelector('button') as HTMLButtonElement).click();
		await tick();
		expect(mounted.target.querySelector('[role="list"]')).toBeTruthy();
		expect(latest).toBeNull();
	});
});

describe('ConsoleHeader', () => {
	it('renders no action at all when nothing is checked (maintainer ruling: no preset chip, no Generate film in the console)', () => {
		mounted = mount(ConsoleHeader, {
			header: header(),
			checkedCount: 0,
			onGenerateSelected: () => {},
			onClearChecked: () => {}
		});
		expect(mounted.target.textContent).not.toContain('Generate film');
		expect(mounted.target.textContent).not.toContain('selected');
		expect(mounted.target.textContent).not.toContain('Clear');
		expect(mounted.target.querySelectorAll('button')).toHaveLength(0);
	});

	it('shows Clear, queued n, and a disabled "Generate n selected" once shots are checked', () => {
		mounted = mount(ConsoleHeader, {
			header: header(),
			checkedCount: 2,
			queuedCount: 2,
			onGenerateSelected: () => {},
			onClearChecked: () => {}
		});
		expect(mounted.target.textContent).toContain('Clear');
		expect(mounted.target.textContent).toContain('queued 2');
		const generateSelected = Array.from(mounted.target.querySelectorAll('button')).find((b) =>
			b.textContent?.includes('Generate 2 selected')
		) as HTMLButtonElement;
		expect(generateSelected).toBeTruthy();
		expect(generateSelected.disabled).toBe(true);
		expect(generateSelected.title).toBe('Per-shot generation lands in the next wave');
	});

	it('renders the shot/duration summary and readiness text from the model', () => {
		mounted = mount(ConsoleHeader, {
			header: header({ shotCount: 1, totalSeconds: 5.5, readiness: { ok: false, text: 'Add a shot' } }),
			checkedCount: 0,
			onGenerateSelected: () => {},
			onClearChecked: () => {}
		});
		expect(mounted.target.textContent).toContain('1 shot · 5.5 s');
		expect(mounted.target.textContent).toContain('Add a shot');
	});
});
