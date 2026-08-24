<script lang="ts">
	import FieldShell from './FieldShell.svelte';
	import { isFullWidth } from './fieldWidth';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: label = config.title || name || '';
	$: description = config.description || '';
	$: min = config.minimum ?? config.min;
	$: max = config.maximum ?? config.max;
	$: step = config.step ?? 1;
	$: disabled = config.disabled ?? false;
	$: tooltip = config.tooltip || config.configuration?.tooltip;
	$: fullWidth = isFullWidth(config);

	$: numericValue = typeof value === 'number' ? value : parseFloat(value) || 0;
	$: atMin = min !== undefined && min !== null && numericValue <= min;
	$: atMax = max !== undefined && max !== null && numericValue >= max;

	function clamp(n: number): number {
		let result = n;
		if (min !== undefined && min !== null && result < min) result = min;
		if (max !== undefined && max !== null && result > max) result = max;
		return result;
	}

	function handleInput(event: Event) {
		const target = event.target as HTMLInputElement;
		if (name) {
			const numValue = parseFloat(target.value) || 0;
			onChange(name, numValue);
		}
	}

	function stepBy(delta: number) {
		if (disabled || !name) return;
		onChange(name, clamp(numericValue + delta));
	}
</script>

<FieldShell {name} {label} {description} {tooltip} labelFor={name || undefined} let:descriptionId>
	<div
		class="{fullWidth
			? 'flex w-full'
			: 'inline-flex'} items-stretch h-9 overflow-hidden rounded border border-line-strong bg-surface-2"
	>
		<button
			type="button"
			on:click={() => stepBy(-step)}
			disabled={disabled || atMin}
			class="flex h-9 w-7 flex-shrink-0 items-center justify-center border-r border-line-strong text-fg-muted transition-colors hover:text-fg disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-fg-muted"
			aria-label="Decrease value"
		>
			&minus;
		</button>
		<input
			type="number"
			id={name || undefined}
			{value}
			on:input={handleInput}
			{min}
			{max}
			{step}
			{disabled}
			class="number-input-value h-9 border-0 bg-transparent text-center font-mono text-sm tabular-nums text-fg focus:outline-none {fullWidth
				? 'flex-1 min-w-0'
				: 'w-11'}"
			aria-describedby={descriptionId}
		/>
		<button
			type="button"
			on:click={() => stepBy(step)}
			disabled={disabled || atMax}
			class="flex h-9 w-7 flex-shrink-0 items-center justify-center border-l border-line-strong text-fg-muted transition-colors hover:text-fg disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-fg-muted"
			aria-label="Increase value"
		>
			+
		</button>
	</div>
</FieldShell>

<style>
	.number-input-value::-webkit-inner-spin-button,
	.number-input-value::-webkit-outer-spin-button {
		-webkit-appearance: none;
		margin: 0;
	}

	.number-input-value {
		-moz-appearance: textfield;
		appearance: textfield;
	}
</style>
