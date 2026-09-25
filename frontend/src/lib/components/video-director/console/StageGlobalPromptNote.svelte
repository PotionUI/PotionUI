<script lang="ts">
	let {
		globalPrompt,
		negativePrompt,
		onEdit
	}: {
		globalPrompt: string;
		negativePrompt: string;
		onEdit?: () => void;
	} = $props();

	let hasGlobal = $derived(globalPrompt.trim() !== '');
	let hasNegative = $derived(negativePrompt.trim() !== '');
</script>

{#if hasGlobal || hasNegative}
	<div class="global-note">
		<div class="global-note-head">
			<span class="label">Global prompt</span>
			<span class="hint">Attached to every shot</span>
			{#if onEdit}
				<button type="button" class="edit-link" onclick={onEdit}>Edit</button>
			{/if}
		</div>
		{#if hasGlobal}
			<p class="txt">{globalPrompt}</p>
		{/if}
		{#if hasNegative}
			<p class="txt"><span class="neg-label">Negative:</span> {negativePrompt}</p>
		{/if}
	</div>
{/if}

<style>
	.global-note {
		margin-bottom: 12px;
		padding: 8px 0 8px 12px;
		border-left: 2px solid rgb(var(--line-strong));
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.global-note-head {
		display: flex;
		align-items: baseline;
		gap: 8px;
	}
	.label {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: rgb(var(--fg-subtle));
	}
	.hint {
		font-size: 12px;
		color: rgb(var(--fg-disabled));
	}
	.edit-link {
		margin-left: auto;
		border: none;
		background: none;
		padding: 0;
		font-size: 12px;
		color: rgb(var(--fg-subtle));
		cursor: pointer;
	}
	.edit-link:hover {
		color: rgb(var(--fg));
		text-decoration: underline;
	}
	.txt {
		font-size: 12.5px;
		line-height: 1.5;
		color: rgb(var(--fg-subtle));
	}
	.neg-label {
		font-size: 12.5px;
		font-weight: 600;
		color: rgb(var(--fg-disabled));
	}
</style>
