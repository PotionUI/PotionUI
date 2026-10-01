<script lang="ts">
	let { label, count }: { label: string; count?: number } = $props();
</script>

<div class="group-head">{label}{#if count !== undefined}<span>{count}</span>{/if}</div>

<style>
	.group-head {
		position: sticky;
		top: 0;
		z-index: 2;
		display: flex;
		align-items: center;
		justify-content: space-between;
		height: 28px;
		padding: 0 16px;
		background: rgb(var(--surface-1));
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 12px;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: rgb(var(--fg-subtle));
	}

	.group-head span {
		color: rgb(var(--fg-disabled));
		font-variant-numeric: tabular-nums;
	}
</style>
