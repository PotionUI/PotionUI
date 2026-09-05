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
	// -1 is the "random every run" sentinel; it is only a legal value when the
	// configured range permits negative seeds.
	$: isAuto = localValue === -1;
	$: autoAllowed = min <= -1;

	// The randomizable domain is every non-negative multiple of `step`
	// (0, step, 2*step, ...) that also falls within [min, max] - not every
	// integer starting at min. A misaligned min/max (e.g. min=101, max=149,
	// step=50) legitimately has zero eligible values.
	function computeRandomDomain(lo: number, hi: number, s: number) {
		if (!Number.isFinite(lo) || !Number.isFinite(hi) || !Number.isFinite(s)) {
			return { valid: false as const, message: 'Seed range is not configured with finite bounds.' };
		}
		if (!Number.isInteger(s) || s <= 0) {
			return { valid: false as const, message: 'Seed step must be a positive whole number.' };
		}
		if (lo > hi) {
			return { valid: false as const, message: 'Seed minimum cannot be greater than its maximum.' };
		}
		const nonNegativeLo = Math.max(lo, 0);
		const firstK = Math.ceil(nonNegativeLo / s);
		const lastK = Math.floor(hi / s);
		if (lastK < firstK) {
			return {
				valid: false as const,
				message: 'No seed value on the configured step fits within the allowed range.'
			};
		}
		return { valid: true as const, firstK, count: lastK - firstK + 1, step: s };
	}

	function computeCurrentValueError(
		val: number,
		lo: number,
		hi: number,
		s: number,
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
		if (Number.isInteger(s) && s > 0 && val % s !== 0) {
			return `Seed must be a multiple of ${s}.`;
		}
		return null;
	}

	$: domain = computeRandomDomain(min, max, step);
	$: currentValueError = computeCurrentValueError(localValue, min, max, step, isAuto, autoAllowed);
	$: validationMessage = domain.valid ? currentValueError : domain.message;

	function handleRandomize() {
		if (!domain.valid) return;
		const offset = Math.min(domain.count - 1, Math.max(0, Math.floor(random() * domain.count)));
		const randomSeed = (domain.firstK + offset) * domain.step;
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
				{min}
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
