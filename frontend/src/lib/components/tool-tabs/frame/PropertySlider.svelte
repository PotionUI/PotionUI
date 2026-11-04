<script lang="ts">
	import { trackFraction, trackOffset } from '$lib/components/form-fields/sliderGeometry';

	let {
		label,
		value,
		min,
		max,
		step = 1,
		defaultValue = 0,
		unit = '',
		inline = false,
		disabled = false,
		onchange,
		oncommit
	}: {
		label: string;
		value: number;
		min: number;
		max: number;
		step?: number;
		defaultValue?: number;
		unit?: string;
		inline?: boolean;
		disabled?: boolean;
		onchange: (value: number) => void;
		oncommit?: () => void;
	} = $props();

	let changed = $derived(value !== defaultValue);
	let from = $derived(trackOffset(trackFraction(Math.min(value, defaultValue), min, max)));
	let to = $derived(trackOffset(trackFraction(Math.max(value, defaultValue), min, max)));
	let tick = $derived(trackOffset(trackFraction(defaultValue, min, max)));
	let readout = $derived(`${value > 0 && min < 0 ? '+' : ''}${Number.isInteger(value) ? value : Math.round(value * 100) / 100}${unit}`);

	function input(event: Event) {
		onchange(parseFloat((event.target as HTMLInputElement).value));
	}

	function reset() {
		if (disabled || !changed) return;
		onchange(defaultValue);
		oncommit?.();
	}

	function keydown(event: KeyboardEvent) {
		if (event.key !== 'Backspace' && event.key !== 'Delete') return;
		event.preventDefault();
		reset();
	}
</script>

<div class="flex {inline ? 'min-w-0 flex-1 items-center gap-2.5' : 'flex-col gap-1'}" data-property-slider={label} data-changed={changed || undefined} ondblclick={reset} role="presentation">
	<div class="flex items-center justify-between gap-2 {inline ? 'flex-none' : ''}">
		<span class="font-mono text-sm uppercase tracking-[0.08em] {changed ? 'text-fg-muted' : 'text-fg-subtle'}">{label}</span>
		{#if !inline}
			<span class="font-mono text-sm tabular-nums {changed ? 'text-fg' : 'text-fg-subtle'}" data-property-value>{readout}</span>
		{/if}
	</div>
	<div class="relative flex min-w-0 items-center {inline ? 'flex-1' : ''} py-1">
		<input
			type="range"
			{min}
			{max}
			step={step}
			value={value}
			{disabled}
			aria-label={label}
			style="background: linear-gradient(to right, rgb(var(--surface-3)) {from}, rgb(var(--signal)) {from}, rgb(var(--signal)) {to}, rgb(var(--surface-3)) {to})"
			class="property-range h-1.5 w-full cursor-pointer appearance-none rounded-full disabled:cursor-not-allowed disabled:opacity-50"
			oninput={input}
			onchange={() => oncommit?.()}
			onkeydown={keydown}
		/>
		<span class="pointer-events-none absolute top-1/2 h-2.5 w-px -translate-x-1/2 -translate-y-1/2 bg-fg-subtle/70" style="left: {tick}" aria-hidden="true"></span>
	</div>
	{#if inline}
		<span class="w-9 flex-none text-right font-mono text-sm tabular-nums text-fg" data-property-value>{readout}</span>
	{/if}
</div>

<style>
	.property-range::-webkit-slider-thumb {
		appearance: none;
		width: 14px;
		height: 14px;
		margin-top: -4px;
		border-radius: 50%;
		background: rgb(var(--fg));
		cursor: pointer;
	}

	.property-range::-webkit-slider-runnable-track {
		height: 6px;
		border-radius: 9999px;
	}

	.property-range::-moz-range-thumb {
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: rgb(var(--fg));
		cursor: pointer;
		border: none;
	}

	.property-range::-moz-range-track {
		height: 6px;
		border-radius: 9999px;
		background: transparent;
	}

	.property-range:focus-visible {
		outline: 1px solid rgb(var(--signal));
		outline-offset: 3px;
	}
</style>
