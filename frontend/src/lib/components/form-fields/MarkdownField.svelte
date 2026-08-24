<script lang="ts">
	import { processMarkdown } from '$lib/utils/markdown';

	export let config: any;

	$: description = config.description || '';
	$: content = config.content || description || '';
	const enableHtml = config.enableHtml || false;
	const className = config.className || '';
	const finalClassName = `prose prose-sm max-w-none text-fg-muted ${className}`.trim();
</script>

<div class="border-l border-line-strong pl-2.5 py-1">
	<div class="my-1 text-sm leading-[18px] {finalClassName}">
		{#if enableHtml}
			{@html content}
		{:else}
			{@html processMarkdown(content)}
		{/if}
	</div>
</div>
