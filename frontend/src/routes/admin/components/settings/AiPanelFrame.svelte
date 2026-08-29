<script lang="ts">
	import type { Snippet } from 'svelte';

	let { children }: { children: Snippet } = $props();
</script>

<!-- The house "AI-product gradient ring" idiom (see Sidebar.svelte's
     .ai-chat-trigger) - border only, low-alpha stops so it reads as a quiet
     accent around the Search & Tagging panels, not a glow. Shared here so
     the three panels don't each carry their own copy. -->
<div class="ai-panel-frame rounded-lg p-4 space-y-5">
	{@render children()}
</div>

<style>
	.ai-panel-frame {
		border: 1px solid transparent;
		background:
			linear-gradient(rgb(var(--surface-2)), rgb(var(--surface-2))) padding-box,
			linear-gradient(
					135deg,
					rgb(var(--ai-1) / 0.45),
					rgb(var(--ai-2) / 0.45),
					rgb(var(--ai-3) / 0.45)
				)
				border-box;
	}
</style>
