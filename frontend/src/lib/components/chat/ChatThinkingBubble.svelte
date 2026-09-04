<script lang="ts">
	/** Bare "thinking" placeholder shown before an assistant turn has produced
	 * any content, tool call, or trace step yet. Shares ChatMessage's
	 * avatar/body grid so it doesn't jump when real content mounts in its place. */
	import Logo from '../brand/Logo.svelte';
</script>

<div class="grid grid-cols-[28px_minmax(0,1fr)] items-start gap-3">
	<div class="ai-avatar flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-surface-1 text-fg">
		<Logo size={16} />
	</div>
	<div class="min-w-0 pt-0.5">
		<div class="flex h-[21px] items-center gap-1.5">
			<span class="text-xs font-semibold text-fg-muted">Potion AI</span>
		</div>
		<div class="flex items-center gap-2 text-xs text-fg-subtle">
			<div class="flex gap-1">
				<span class="h-1.5 w-1.5 rounded-full bg-fg-subtle" style="animation: thinking-pulse 1.2s ease-in-out infinite; animation-delay: 0ms"></span>
				<span class="h-1.5 w-1.5 rounded-full bg-fg-subtle" style="animation: thinking-pulse 1.2s ease-in-out infinite; animation-delay: 0.2s"></span>
				<span class="h-1.5 w-1.5 rounded-full bg-fg-subtle" style="animation: thinking-pulse 1.2s ease-in-out infinite; animation-delay: 0.4s"></span>
			</div>
			<span>Thinking...</span>
		</div>
	</div>
</div>

<style>
	/* Same border-box/padding-box gradient-ring idiom as the sidebar's AI Chat
	   trigger (Sidebar.svelte) and ChatMessage's assistant avatar — the one
	   place the AI-product gradient renders as a border, never a fill. */
	.ai-avatar {
		border: 1px solid transparent;
		background:
			linear-gradient(rgb(var(--surface-1)), rgb(var(--surface-1))) padding-box,
			linear-gradient(135deg, rgb(var(--ai-1)), rgb(var(--ai-2)), rgb(var(--ai-3))) border-box;
	}

	@keyframes thinking-pulse {
		0%,
		100% {
			opacity: 0.25;
		}
		50% {
			opacity: 1;
		}
	}
</style>
