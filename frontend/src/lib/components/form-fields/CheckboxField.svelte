<script lang="ts">
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: label = config.title || name || '';
	$: description = config.description || '';
	$: tooltip = config.tooltip;
	$: disabled = config.disabled ?? false;

	function handleChange(event: Event) {
		const target = event.target as HTMLInputElement;
		if (name) {
			onChange(name, target.checked);
		}
	}

	$: checked = Boolean(value);
</script>

<div class="field-card">
	<label
		for={name || 'checkbox'}
		class="flex items-center justify-between gap-3 select-none {disabled
			? 'cursor-not-allowed opacity-50'
			: 'cursor-pointer'}"
	>
		<span class="min-w-0">
			<span class="inline-flex items-center gap-1 text-sm font-medium text-fg">
				{label}
				{#if tooltip}
					<Tooltip text={tooltip} position="top">
						<span class="text-fg-muted cursor-help inline-flex items-center">
							<Icon name="info" className="w-3.5 h-3.5" />
						</span>
					</Tooltip>
				{/if}
			</span>
			{#if description}
				<span class="block text-xs text-fg-muted mt-0.5">{description}</span>
			{/if}
		</span>
		<span class="relative inline-flex h-5 w-9 shrink-0 items-center">
			<input
				type="checkbox"
				id={name || 'checkbox'}
				{checked}
				{disabled}
				on:change={handleChange}
				class="peer sr-only"
			/>
			<span
				class="pointer-events-none absolute inset-0 rounded-full border border-line-strong bg-surface-3 transition-colors duration-150 ease-out peer-checked:border-signal-solid peer-checked:bg-signal-solid peer-focus-visible:ring-2 peer-focus-visible:ring-signal peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-canvas"
			></span>
			<span
				class="pointer-events-none absolute left-0.5 h-4 w-4 rounded-full bg-white transition-transform duration-150 ease-out peer-checked:translate-x-4"
			></span>
		</span>
	</label>
</div>
