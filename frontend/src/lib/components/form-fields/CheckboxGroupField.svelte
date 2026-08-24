<script lang="ts">
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: label = config.title || name || '';
	$: description = config.description || '';
	$: options = config.options || [];
	$: tooltip = config.tooltip;
	$: layout = config.layout || 'vertical'; // Layout mode
	$: disabled = config.disabled || false;

	let selectedValues: string[];
	$: selectedValues = Array.isArray(value) ? value : [];

	function handleCheckboxChange(optValue: string, checked: boolean) {
		let newValues: string[];
		if (checked) {
			newValues = [...selectedValues, optValue];
		} else {
			newValues = selectedValues.filter((v) => v !== optValue);
		}
		if (name) {
			onChange(name, newValues);
		}
	}
</script>

<div class="field-card">
	<fieldset class="border-0 p-0 m-0 min-w-0">
		<legend class="label">
			{label}
			{#if tooltip}
				<Tooltip text={tooltip} position="top">
					<span class="ml-1 text-fg-muted cursor-help inline-flex items-center">
						<Icon name="info" className="w-3.5 h-3.5" />
					</span>
				</Tooltip>
			{/if}
		</legend>
		{#if description}
			<p id={name ? `${name}-desc` : undefined} class="text-xs text-fg-muted mt-1 mb-2">{description}</p>
		{/if}

	<div class="{layout === 'horizontal' ? 'flex flex-wrap' : 'flex flex-col'} gap-2 mt-2">
		{#each options as opt}
			{@const optValue = typeof opt === 'string' ? opt : opt.value}
			{@const optLabel = typeof opt === 'string' ? opt : opt.label}
			{@const optIcon = typeof opt === 'string' ? undefined : opt.icon}
			{@const isSelected = selectedValues.includes(optValue)}
			<button
				type="button"
				on:click={() => handleCheckboxChange(optValue, !isSelected)}
				disabled={disabled}
				class="relative flex items-center gap-2 px-3 py-2 rounded border transition-all {isSelected
					? 'border-signal/40 bg-signal/10'
					: 'border-line-strong bg-surface-2 hover:border-line-hover'} {disabled
					? 'opacity-50 cursor-not-allowed'
					: 'cursor-pointer'}"
			>
				<!-- Icon (if provided) -->
				{#if optIcon}
					<div class="{isSelected ? 'text-signal' : 'text-fg-muted'}">
						<Icon name={optIcon} className="w-4 h-4" />
					</div>
				{/if}

				<!-- Label -->
				<div class="flex-1 text-left">
					<span class="text-sm font-medium {isSelected ? 'text-signal' : 'text-fg-muted'}">
						{optLabel}
					</span>
				</div>

				<!-- Checkbox indicator -->
				<div
					class="w-4 h-4 rounded border flex items-center justify-center shrink-0 {isSelected
						? 'border-signal-solid bg-signal-solid'
						: 'border-line-strong bg-surface-2'}"
				>
					{#if isSelected}
						<Icon name="check" className="w-3 h-3 text-white" strokeWidth={3} />
					{/if}
				</div>
			</button>
		{/each}
	</div>
	</fieldset>
</div>
