<script lang="ts">
	import { Button, CopyButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import {
		textArtifactAction,
		textArtifactValues,
		type TextArtifactData
	} from '$lib/generation/artifacts/textArtifact';

	let {
		artifact,
		onApply
	}: {
		artifact: { artifact_data: TextArtifactData };
		onApply?: (values: Record<string, unknown>) => void;
	} = $props();

	let data = $derived(artifact.artifact_data);
	let action = $derived(textArtifactAction(data));
	let lineCount = $derived(data.text ? data.text.split('\n').filter((line) => line.trim()).length : 0);

	function apply() {
		const values = textArtifactValues(data);
		if (values) onApply?.(values);
	}
</script>

<div class="space-y-2" data-text-artifact>
	<div class="flex items-center justify-between gap-2">
		<div class="flex min-w-0 items-center gap-2">
			<span class="truncate text-xs font-semibold text-fg">{data.title}</span>
			<span class="shrink-0 font-mono text-xs tabular-nums text-fg-subtle">{lineCount} lines</span>
		</div>
		<div class="flex shrink-0 items-center gap-1">
			{#if action && onApply}
				<Tooltip text="Write this into the {action.field} field and match its settings" position="top">
					<Button size="xs" variant="secondary" icon="pencil" onclick={apply}>{action.label}</Button>
				</Tooltip>
			{/if}
			<Tooltip text="Copy" position="top">
				<CopyButton text={data.text} size="xs" ariaLabel="Copy {data.title}" />
			</Tooltip>
		</div>
	</div>
	<pre
		class="max-h-64 overflow-auto rounded bg-surface-1 border border-line px-3 py-2 text-xs leading-relaxed text-fg-muted {data.mono
			? 'font-mono tabular-nums whitespace-pre'
			: 'font-sans whitespace-pre-wrap'}">{data.text}</pre>
</div>
