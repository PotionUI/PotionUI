<script lang="ts">
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';
	import { trackFraction, trackOffset } from './sliderGeometry';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: label = config.title || name || '';
	$: description = config.description || '';
	// ?? not ||: an explicit 0 bound is legitimate and || would discard it.
	$: min = config.minimum ?? config.min ?? 0;
	$: max = config.maximum ?? config.max ?? 100;
	$: step = config.step ?? 1;
	$: domStep = config.step ?? 'any';
	$: tooltip = config.tooltip || config.configuration?.tooltip;
	$: disabled = config.disabled || false;
	$: defaultValue = config.default;

	let localValue: number;
	let previousValue: any = undefined;
	let isEditing = false;
	let editValue = '';
	let inputEl: HTMLInputElement;
	let rangeEl: HTMLInputElement;

	// Initialize local value only when external value actually changes
	$: {
		if (value !== previousValue) {
			localValue = typeof value === 'number' ? value : (min || 0);
			previousValue = value;
		}
	}

	$: range = max - min;
	$: fillStop = trackOffset(trackFraction(localValue, min, max));
	// Order-independent backstop for the same sanitization trap: assigning the
	// property re-runs it against the bounds actually in the DOM. No-op while
	// dragging, since handleInput already holds the input's own value.
	$: if (rangeEl && typeof localValue === 'number' && parseFloat(rangeEl.value) !== localValue) {
		rangeEl.value = String(localValue);
	}

	$: hasDefaultTick = typeof defaultValue === 'number' && range > 0 && defaultValue >= min && defaultValue <= max;
	$: defaultOffset = trackOffset(hasDefaultTick ? trackFraction(defaultValue, min, max) : 0);

	function handleInput(event: Event) {
		const target = event.target as HTMLInputElement;
		const newValue = parseFloat(target.value);
		localValue = newValue;
		previousValue = newValue;
		if (name) {
			onChange(name, newValue);
		}
	}

	function startEditing() {
		if (disabled) return;
		isEditing = true;
		editValue = String(localValue);
		// Focus the input after Svelte updates the DOM
		requestAnimationFrame(() => inputEl?.select());
	}

	function commitEdit() {
		isEditing = false;
		let parsed = parseFloat(editValue);
		if (isNaN(parsed)) return;
		// Clamp to min/max
		parsed = Math.max(min, Math.min(max, parsed));
		// Snap to step
		parsed = Math.round(parsed / step) * step;
		// Fix floating point
		const decimals = (String(step).split('.')[1] || '').length;
		parsed = parseFloat(parsed.toFixed(decimals));
		localValue = parsed;
		previousValue = parsed;
		if (name) {
			onChange(name, parsed);
		}
	}

	function handleEditKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			commitEdit();
		} else if (event.key === 'Escape') {
			isEditing = false;
		}
	}
</script>

<div class="field-card">
	<div class="flex items-center justify-between mb-2">
		<div class="flex items-center gap-2">
			<label class="label !mb-0" for={name || undefined}>{label}</label>
			{#if tooltip}
				<Tooltip text={tooltip} position="top">
					<span class="text-fg-subtle cursor-help inline-flex items-center">
						<Icon name="info" className="w-3.5 h-3.5" />
					</span>
				</Tooltip>
			{/if}
		</div>
		{#if isEditing}
			<input
				bind:this={inputEl}
				type="text"
				bind:value={editValue}
				on:blur={commitEdit}
				on:keydown={handleEditKeydown}
				class="w-16 text-sm font-medium font-mono tabular-nums text-fg-muted bg-surface-3 px-2 py-0.5 rounded border border-line-hover text-center outline-none focus:border-accent transition-colors duration-150"
			/>
		{:else}
			<button
				type="button"
				on:click={startEditing}
				class="text-sm font-medium font-mono tabular-nums text-fg bg-surface-3 px-2 py-0.5 rounded hover:bg-surface-3/70 active:scale-95 transition-all duration-150 ease-out cursor-text min-w-[3rem] text-center"
				title="Click to type a value"
			>
				{localValue}
			</button>
		{/if}
	</div>
	<div class="relative flex items-center py-1">
		<!-- min/max/step MUST precede value: Svelte sets attributes in template
		     order, and a range input sanitizes an incoming value against whatever
		     step is current. Set value first and the default step of 1 snaps e.g.
		     0.5 to 1 (a tie rounds up), leaving the native thumb at 1 while the
		     gradient fill still draws localValue. -->
		<input
			bind:this={rangeEl}
			type="range"
			id={name || undefined}
			{min}
			{max}
			step={domStep}
			value={localValue}
			on:input={handleInput}
			{disabled}
			style="background: linear-gradient(to right, rgb(var(--signal)) {fillStop}, rgb(var(--surface-3)) {fillStop})"
			class="slider-range w-full h-1.5 rounded-full appearance-none cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
			aria-describedby={description && name ? `${name}-desc` : undefined}
		/>
		{#if hasDefaultTick}
			<span
				class="pointer-events-none absolute top-1/2 -translate-x-1/2 -translate-y-1/2 w-px h-2.5 bg-fg-subtle/70"
				style="left: {defaultOffset}"
				title="default {defaultValue}"
			></span>
		{/if}
	</div>
	<div class="flex items-center justify-between mt-1">
		<span class="font-mono text-2xs tabular-nums text-fg-subtle">{min}</span>
		{#if hasDefaultTick}
			<span class="font-mono text-2xs text-fg-subtle">tick = default</span>
		{/if}
		<span class="font-mono text-2xs tabular-nums text-fg-subtle">{max}</span>
	</div>
	{#if description}
		<p id={name ? `${name}-desc` : undefined} class="text-xs text-fg-muted mt-1">{description}</p>
	{/if}
</div>

<style>
	/* Custom range slider thumb — scoped styles use rgb(var(--token)) per design tokens */
	.slider-range::-webkit-slider-thumb {
		appearance: none;
		width: 14px;
		height: 14px;
		margin-top: -4px;
		border-radius: 50%;
		background: rgb(var(--fg));
		cursor: pointer;
	}

	.slider-range::-webkit-slider-runnable-track {
		height: 6px;
		border-radius: 9999px;
	}

	.slider-range::-moz-range-thumb {
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: rgb(var(--fg));
		cursor: pointer;
		border: none;
	}

	.slider-range::-moz-range-track {
		height: 6px;
		border-radius: 9999px;
		background: transparent;
	}
</style>
