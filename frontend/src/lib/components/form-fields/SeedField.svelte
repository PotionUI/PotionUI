<script lang="ts">
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';
	import FieldShell from './FieldShell.svelte';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: label = config.title || name || '';
	$: description = config.description || '';
	$: min = (config.minimum || config.min) ?? -1;
	$: max = (config.maximum || config.max) ?? 2 ** 32 - 1;
	$: step = config.step ?? 1;
	$: defaultValue = config.default ?? -1;
	$: tooltip = config.tooltip;

	let localValue: number;

	$: localValue = typeof value === 'number' ? value : defaultValue;
	$: isAuto = localValue === -1;

	function handleRandomize() {
		const randomSeed = Math.floor(Math.random() * (max - (min < 0 ? 0 : min) + 1)) + (min < 0 ? 0 : min);
		localValue = randomSeed;
		if (name) {
			onChange(name, randomSeed);
		}
	}

	function handleSetRandom() {
		localValue = -1;
		if (name) {
			onChange(name, -1);
		}
	}

	function handleInput(event: Event) {
		const target = event.target as HTMLInputElement;
		const numValue = parseInt(target.value) || 0;
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
	</div>
</FieldShell>
