<script lang="ts">
	/** Bare "thinking" placeholder shown before an assistant turn has produced
	 * any content, tool call, or trace step yet. Shares ChatMessage's
	 * avatar/body anatomy so it doesn't jump when real content mounts in its place. */
	import Logo from '../brand/Logo.svelte';
</script>

<article class="message assistant-message">
	<div class="ai-avatar"><Logo size={18} /></div>
	<div class="assistant-body">
		<div class="assistant-meta"><strong>PotionAI</strong></div>
		<div class="thinking-dots">
			<span class="dot" style="animation-delay: 0ms"></span>
			<span class="dot" style="animation-delay: 0.2s"></span>
			<span class="dot" style="animation-delay: 0.4s"></span>
			<span>Thinking...</span>
		</div>
	</div>
</article>
