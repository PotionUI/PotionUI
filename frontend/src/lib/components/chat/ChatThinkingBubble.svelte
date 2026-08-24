<script lang="ts">
	/** Bare "thinking" placeholder shown before an assistant turn has produced
	 * any content, tool call, or trace step yet. */
</script>

<div class="flex flex-col gap-1.5">
	<span class="font-mono text-2xs font-medium uppercase tracking-[0.07em] text-fg-subtle">ASSISTANT</span>
	<div class="rounded-lg bg-surface-1 border border-line px-4 py-3">
		<div class="flex items-center gap-2 text-xs text-fg-subtle">
			<div class="flex gap-1">
				<span class="w-1.5 h-1.5 rounded-full bg-fg-subtle" style="animation: thinking-pulse 1.2s ease-in-out infinite; animation-delay: 0ms"></span>
				<span class="w-1.5 h-1.5 rounded-full bg-fg-subtle" style="animation: thinking-pulse 1.2s ease-in-out infinite; animation-delay: 0.2s"></span>
				<span class="w-1.5 h-1.5 rounded-full bg-fg-subtle" style="animation: thinking-pulse 1.2s ease-in-out infinite; animation-delay: 0.4s"></span>
			</div>
			<span>Thinking...</span>
		</div>
	</div>
</div>

<style>
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
