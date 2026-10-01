<script lang="ts">
	export let message: string;
</script>

{#if message}
	<p class="mb-3 text-xs text-fg-muted">{message}. Other files are dimmed.</p>
{/if}
