<!--
	Reusable chip/tags editor: type or paste a comma/newline-separated list,
	remove individual chips, or (read-only) click a chip to copy it. Extracted
	from the old ModelTriggerWordsCard so the trigger-words UX (now the
	`triggers` tags attribute - see ModelAttributesCard.svelte) survives as a
	general-purpose control for any `tags` attribute.

	Controlled: `value` is owned by the caller, every add/remove calls
	`onChange` with the next full array rather than persisting anything itself
	- the caller decides when/how that becomes a save (batched with other
	fields, or immediately).
-->
<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { mergeTriggerWords } from '$lib/utils/triggerWords';

	let {
		value = [],
		editable = true,
		onChange,
		onChipClick,
		placeholder = 'Type a word or paste a comma/newline list...',
		emptyText = 'None set'
	}: {
		value?: string[];
		editable?: boolean;
		onChange?: (values: string[]) => void;
		/** Only consulted when `editable` is false - e.g. click-to-copy. */
		onChipClick?: (chip: string) => void;
		placeholder?: string;
		emptyText?: string;
	} = $props();

	let newInput = $state('');

	function addFrom(input: string) {
		const next = mergeTriggerWords(value, input);
		if (next.length !== value.length) onChange?.(next);
		newInput = '';
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter' || event.key === ',') {
			event.preventDefault();
			addFrom(newInput);
		}
	}

	function handleBlur() {
		if (newInput.trim()) addFrom(newInput);
	}

	function handlePaste(event: ClipboardEvent) {
		const pasted = event.clipboardData?.getData('text') || '';
		if (!/[,\r\n]/.test(pasted)) return;
		// Read the raw clipboard payload before a one-line input can normalize
		// its newlines into spaces, then import it exactly like typed input.
		event.preventDefault();
		addFrom([newInput, pasted].filter(Boolean).join('\n'));
	}

	function removeChip(index: number) {
		onChange?.(value.filter((_, i) => i !== index));
	}
</script>

{#if editable}
	<div class="space-y-1.5">
		{#if value.length > 0}
			<div class="flex flex-wrap gap-1.5">
				{#each value as chip, index}
					<span class="inline-flex items-center gap-1 px-2 py-0.5 bg-surface-3 text-fg text-xs rounded border border-line-strong">
						{chip}
						<button
							type="button"
							class="text-fg-subtle hover:text-fg"
							onclick={() => removeChip(index)}
							aria-label={`Remove ${chip}`}
						>
							<Icon name="close" className="w-3 h-3" />
						</button>
					</span>
				{/each}
			</div>
		{/if}
		<input
			type="text"
			bind:value={newInput}
			onkeydown={handleKeydown}
			onpaste={handlePaste}
			onblur={handleBlur}
			{placeholder}
			class="input text-sm"
		/>
	</div>
{:else if value.length > 0}
	<div class="flex flex-wrap gap-1.5">
		{#each value as chip}
			{#if onChipClick}
				<button
					type="button"
					class="px-2 py-0.5 bg-surface-3 text-fg text-xs rounded border border-line-strong hover:border-line-hover transition-colors"
					title="Click to copy"
					onclick={() => onChipClick?.(chip)}
				>
					{chip}
				</button>
			{:else}
				<span class="px-2 py-0.5 bg-surface-3 text-fg text-xs rounded border border-line-strong">{chip}</span>
			{/if}
		{/each}
	</div>
{:else}
	<p class="text-sm text-fg-subtle italic">{emptyText}</p>
{/if}
