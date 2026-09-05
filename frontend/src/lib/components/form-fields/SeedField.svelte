<script lang="ts">
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';
	import FieldShell from './FieldShell.svelte';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;
	// Injectable so tests can drive Randomize deterministically.
	export let random: () => number = Math.random;

	$: label = config.title || name || '';
	$: description = config.description || '';
	$: min = config.minimum ?? config.min ?? -1;
	$: max = config.maximum ?? config.max ?? 2 ** 32 - 1;
	$: step = config.step ?? 1;
	$: defaultValue = config.default ?? -1;
	$: tooltip = config.tooltip;

	let localValue: number;

	$: localValue = typeof value === 'number' ? value : defaultValue;
	// -1 is the "random every run" sentinel; it is never checked against the
	// seed grid below, only against whether it falls inside [min, max].
	$: isAuto = localValue === -1;

	function computeConfigValidity(lo: number, hi: number, s: number) {
		if (!Number.isFinite(lo) || !Number.isFinite(hi) || !Number.isFinite(s)) {
			return { valid: false as const, message: 'Seed range is not configured with finite bounds.' };
		}
		if (!Number.isInteger(s) || s <= 0) {
			return { valid: false as const, message: 'Seed step must be a positive whole number.' };
		}
		if (lo > hi) {
			return { valid: false as const, message: 'Seed minimum cannot be greater than its maximum.' };
		}
		return { valid: true as const };
	}

	// The grid is anchored at `min` - the same anchor the rendered
	// `<input min max step>` uses for its own native step validation - not at
	// zero, so Randomize and validity.stepMismatch never disagree. A negative
	// min is walked forward to its first non-negative point (real seeds are
	// never negative; -1 is the sentinel, handled separately) before laying
	// out `step`-spaced candidates up to `max`.
	function computeSeedGrid(lo: number, hi: number, s: number, configValid: boolean) {
		if (!configValid) return { valid: false as const };
		const firstNonNegativeK = lo >= 0 ? 0 : Math.ceil((0 - lo) / s);
		const base = lo + firstNonNegativeK * s;
		if (base > hi) {
			return {
				valid: false as const,
				message: 'No seed value on the configured step fits within the allowed range.'
			};
		}
		return { valid: true as const, base, count: Math.floor((hi - base) / s) + 1, step: s };
	}

	type SeedGrid = ReturnType<typeof computeSeedGrid>;

	function computeCurrentValueError(
		val: number,
		lo: number,
		hi: number,
		grid: SeedGrid,
		auto: boolean,
		autoOk: boolean
	): string | null {
		if (auto) {
			return autoOk ? null : 'This field requires a fixed seed; Auto is not permitted here.';
		}
		if (!Number.isFinite(val) || !Number.isInteger(val)) {
			return 'Seed must be a whole number.';
		}
		if (val < lo || val > hi) {
			return `Seed must be between ${lo} and ${hi}.`;
		}
		if (grid.valid && (val - grid.base) % grid.step !== 0) {
			return `Seed must be a multiple of ${grid.step} starting from ${grid.base}.`;
		}
		return null;
	}

	$: configValidity = computeConfigValidity(min, max, step);
	$: grid = computeSeedGrid(min, max, step, configValidity.valid);
	// -1 is only ever legal when it sits inside the configured range on top
	// of a sane configuration - a malformed or empty range never offers it.
	$: autoAllowed = configValidity.valid && min <= -1 && max >= -1;
	$: currentValueError = computeCurrentValueError(localValue, min, max, grid, isAuto, autoAllowed);
	$: validationMessage = !configValidity.valid
		? configValidity.message
		: !grid.valid
			? grid.message
			: currentValueError;
	$: inputMin = grid.valid ? grid.base : min;

	function handleRandomize() {
		if (!grid.valid) return;
		const offset = Math.min(grid.count - 1, Math.max(0, Math.floor(random() * grid.count)));
		const randomSeed = grid.base + offset * grid.step;
		localValue = randomSeed;
		if (name) {
			onChange(name, randomSeed);
		}
	}

	function handleSetRandom() {
		if (!autoAllowed) return;
		localValue = -1;
		if (name) {
			onChange(name, -1);
		}
	}

	function handleInput(event: Event) {
		const target = event.target as HTMLInputElement;
		const numValue = parseInt(target.value, 10);
		if (Number.isNaN(numValue)) return;
		localValue = numValue;
		if (name) {
			onChange(name, numValue);
		}
	}
</script>

<FieldShell {name} {label} {description} {tooltip} labelFor={name || undefined} let:descriptionId>
	<div class="flex items-stretch h-9 bg-surface-2 border border-line-strong rounded overflow-hidden">
		{#if isAuto}
			<span class="flex-1 min-w-0 h-9 flex items-center px-3 font-mono text-sm italic text-fg-subtle truncate select-none">
				random each run
			</span>
		{:else}
			<input
				type="number"
				id={name || undefined}
				value={localValue}
				on:input={handleInput}
				min={inputMin}
				{max}
				{step}
				class="flex-1 min-w-0 h-9 px-3 bg-transparent border-0 font-mono text-sm tabular-nums text-fg outline-none focus:ring-0"
				aria-describedby={descriptionId}
			/>
		{/if}
		<Tooltip text="Roll a random seed" position="top" wrapperClass="h-9 flex items-stretch">
			<button
				type="button"
				on:click={handleRandomize}
				class="flex items-center justify-center w-9 h-9 border-l border-line-strong text-fg-muted hover:text-fg hover:bg-surface-3 transition-colors"
			>
				<svg class="w-[15px] h-[15px]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<rect x="4" y="4" width="16" height="16" rx="3" stroke-width="2" />
					<circle cx="9" cy="9" r="1.1" fill="currentColor" stroke="none" />
					<circle cx="15" cy="9" r="1.1" fill="currentColor" stroke="none" />
					<circle cx="9" cy="15" r="1.1" fill="currentColor" stroke="none" />
					<circle cx="15" cy="15" r="1.1" fill="currentColor" stroke="none" />
				</svg>
			</button>
		</Tooltip>
		{#if autoAllowed}
			<Tooltip text="Random every run (-1)" position="top" wrapperClass="h-9 flex items-stretch">
				<button
					type="button"
					on:click={handleSetRandom}
					class="flex items-center gap-1.5 px-2.5 h-9 border-l text-xs font-medium transition-colors {isAuto
						? 'border-signal/40 bg-signal/10 text-signal'
						: 'border-line-strong text-fg-muted hover:bg-surface-3'}"
				>
					<Icon name="sparkles" className="w-[13px] h-[13px]" />
					Auto
				</button>
			</Tooltip>
		{/if}
	</div>
	{#if validationMessage}
		<p class="mt-1 text-xs text-danger" role="alert">{validationMessage}</p>
	{/if}
</FieldShell>
