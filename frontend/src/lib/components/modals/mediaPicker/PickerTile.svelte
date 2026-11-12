<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import tooltip from '$lib/actions/tooltip';
	import PickerFace from './PickerFace.svelte';
	import type { PickerEntry } from './pickerEntries';

	let {
		entry,
		multiple,
		selected = false,
		disabled = false,
		reason = '',
		onActivate,
		onToggle
	}: {
		entry: PickerEntry;
		multiple: boolean;
		selected?: boolean;
		disabled?: boolean;
		reason?: string;
		onActivate: (entry: PickerEntry) => void;
		onToggle: (entry: PickerEntry) => void;
	} = $props();

	let ratio = $derived(entry.ratio);
	let sizing = $derived(
		`--r:${ratio};flex:${ratio} 1 calc(${ratio} * var(--pk-rh));min-width:calc(${ratio} * var(--pk-min));max-width:calc(${ratio} * var(--pk-rh) * 1.3)`
	);
</script>

<div class="flex min-w-0 flex-col" style={sizing} data-pick-tile={entry.key} data-selected={selected ? 'true' : undefined} data-disabled={disabled ? 'true' : undefined}>
	<div class="group relative">
		<button
			type="button"
			{disabled}
			tabindex={multiple ? -1 : 0}
			aria-hidden={multiple ? 'true' : undefined}
			aria-label={multiple ? undefined : `Select ${entry.accessibleName}`}
			data-pick-main
			data-pick-focus={multiple ? undefined : ''}
			class="block w-full rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal {disabled
				? 'cursor-not-allowed'
				: 'cursor-pointer'}"
			onclick={() => (multiple ? onToggle(entry) : onActivate(entry))}
		>
			<span
				class="relative block w-full overflow-hidden rounded-lg border bg-surface-2 transition-colors {selected
					? 'border-line-hover ring-2 ring-signal'
					: 'border-line group-hover:border-line-hover'} {disabled ? 'opacity-40' : ''}"
				style="aspect-ratio:{ratio}"
			>
				<PickerFace {entry} />
				{#if selected}
					<span class="pointer-events-none absolute inset-0 bg-signal/15"></span>
				{/if}
			</span>
		</button>
		{#if multiple}
			<label
				class="absolute left-2 top-2 z-10 flex h-7 w-7 items-center justify-center rounded border border-line-strong bg-surface-1/90 focus-within:ring-2 focus-within:ring-signal {disabled
					? 'cursor-not-allowed'
					: 'cursor-pointer'}"
			>
				<input
					type="checkbox"
					class="h-4 w-4 accent-signal"
					checked={selected}
					{disabled}
					data-pick-focus
					aria-label={`Select ${entry.accessibleName}`}
					onchange={() => onToggle(entry)}
					onkeydown={(event) => {
						if (event.key === 'Enter') {
							event.preventDefault();
							onToggle(entry);
						}
					}}
				/>
			</label>
		{/if}
	</div>
	<p class="mt-1 truncate text-sm text-fg" use:tooltip={{ text: entry.label, truncateOnly: true }}>{entry.label}</p>
	{#if reason}
		<p class="flex min-w-0 items-center gap-1 text-sm text-fg-subtle" data-pick-reason>
			<Icon name="info" className="h-3.5 w-3.5 flex-shrink-0" />
			<span class="block truncate">{reason}</span>
		</p>
	{:else}
		<p class="truncate font-mono text-sm tabular-nums text-fg-subtle">{entry.sublabel}</p>
	{/if}
</div>
