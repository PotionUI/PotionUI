<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';

	export let label: string;
	export let sublabel: string = '';
	export let checked: boolean;
	export let disabled: boolean = false;
	export let disabledReason: string = '';
	export let onToggle: () => void;
	export let onPreview: () => void;

	const reasonId = `pick-reason-${Math.random().toString(36).slice(2, 9)}`;
</script>

<div class="flex flex-col">
	<div
		class="relative border-2 rounded-lg overflow-hidden bg-surface-2 {checked
			? 'border-signal'
			: 'border-line-strong'} {disabled ? 'opacity-50' : ''}"
		data-selected={checked}
	>
		<button
			type="button"
			class="w-full aspect-video flex items-center justify-center cursor-pointer"
			aria-label={`Preview ${label}`}
			on:click={onPreview}
		>
			<slot />
		</button>

		<label
			class="absolute top-2 left-2 z-10 flex items-center justify-center w-7 h-7 rounded bg-surface-1/90 border border-line-strong {disabled
				? 'cursor-not-allowed'
				: 'cursor-pointer'}"
		>
			<input
				type="checkbox"
				class="w-4 h-4 accent-signal"
				{checked}
				{disabled}
				aria-label={`Select ${label}`}
				aria-describedby={disabled && disabledReason ? reasonId : undefined}
				on:change={onToggle}
			/>
		</label>

		<div class="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 to-transparent p-2 pt-4 pointer-events-none">
			<div class="pointer-events-auto">
				<Tooltip text={label} wrapperClass="block min-w-0 max-w-full">
					<p class="text-sm font-medium text-white truncate">{label}</p>
				</Tooltip>
			</div>
		</div>
	</div>
	{#if disabled && disabledReason}
		<p id={reasonId} class="mt-1 flex items-center gap-1 text-xs text-fg-subtle min-w-0">
			<Icon name="info" className="w-3 h-3 flex-shrink-0" />
			<Tooltip text={disabledReason} wrapperClass="block min-w-0 max-w-full">
				<span class="block truncate">{disabledReason}</span>
			</Tooltip>
		</p>
	{:else if sublabel}
		<Tooltip text={sublabel} wrapperClass="block min-w-0 max-w-full">
			<p class="mt-1 font-mono tabular-nums text-xs text-fg-subtle truncate">{sublabel}</p>
		</Tooltip>
	{/if}
</div>
