<script lang="ts">
	import type { EditorToolProps } from '$lib/editors/railTools';
	import { tasks } from '$lib/editors/tasks';
	import { editorsRevision } from '$lib/editors/registry';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { maskArea } from '$lib/tool-tabs/image/maskOverlays';

	let { phone = false }: EditorToolProps = $props();

	const generate = getGenerateController();
	const ui = generate?.ui;

	let entry = $derived.by(() => {
		void $editorsRevision;
		return $ui?.taskId ? tasks.get('image', $ui.taskId) : undefined;
	});
</script>

{#if entry?.region === 'optional' && !$maskArea && !phone}
	<p class="text-sm text-fg-muted" data-generate-whole>With nothing marked, the whole picture is changed.</p>
{/if}
