<script lang="ts">
	// Inline @resource chip mounted inside ChatChipInput's contenteditable.
	// Keeps the 'inline-chip' class so the editor's mount bookkeeping and CSS apply.
	export let uri: string;
	export let label: string;
	export let disabled: boolean = false;
	export let onremove: (() => void) | undefined = undefined;
</script>

<span
	class="inline-chip inline-flex items-center gap-1 px-1.5 rounded bg-signal/10 text-signal border border-signal/25 text-xs leading-5 align-baseline whitespace-nowrap"
	title={uri}
>
	<span class="font-mono">@</span><span class="truncate max-w-[180px]">{label}</span>
	{#if !disabled && onremove}
		<button
			type="button"
			class="text-signal/70 hover:text-signal transition-colors duration-100 leading-none"
			title="Remove resource"
			on:click|stopPropagation={onremove}
		>
			×
		</button>
	{/if}
</span>
