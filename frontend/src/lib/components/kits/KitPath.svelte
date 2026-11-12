<script lang="ts">
	let {
		handle,
		item = null,
		class: className = ''
	}: {
		handle: string;
		item?: string | null;
		class?: string;
	} = $props();
</script>

<span class="font-mono tabular-nums {className}"><span class="text-fg-subtle">@kits.</span>{handle}{#if item}<span class="text-fg-subtle">.</span>{item}{/if}</span>
