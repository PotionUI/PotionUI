<script lang="ts">
	let { isOpen, initialPath, onCreated }: { isOpen: boolean; initialPath?: string; onCreated: (root: { id: string; label: string }) => void } = $props();
</script>

{#if isOpen}
	<button type="button" data-add-stub data-initial-path={initialPath} onclick={() => onCreated({ id: 'fresh', label: 'Fresh' })}>create</button>
{/if}
