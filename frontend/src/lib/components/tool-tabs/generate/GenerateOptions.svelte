<script lang="ts">
	import type { EditorToolProps } from '$lib/editors/railTools';
	import { tasks } from '$lib/editors/tasks';
	import { editorsRevision } from '$lib/editors/registry';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { hintsOf } from '$lib/tool-tabs/image/generate/taskOptions';
	import { maskArea } from '$lib/tool-tabs/image/maskOverlays';

	let { phone = false }: EditorToolProps = $props();

	const generate = getGenerateController();
	const ui = generate?.ui;

	let entry = $derived.by(() => {
		void $editorsRevision;
		return $ui?.taskId ? tasks.get(generate?.editor ?? 'image', $ui.taskId) : undefined;
	});
	let hints = $derived(hintsOf($ui?.taskId));
</script>

{#if entry?.region === 'optional' && !$maskArea && !phone}
	<p class="text-sm text-fg-muted" data-generate-whole>With nothing marked, the whole picture is changed.</p>
{/if}

{#if $ui?.taskId && hints.length}
	<div class="flex flex-col gap-2" data-task-hints>
		{#each hints as hint (hint.name)}
			<label class="flex min-h-8 cursor-pointer items-center gap-2.5 text-base text-fg">
				<input
					type="checkbox"
					class="h-4 w-4 flex-none rounded-sm border-line-strong bg-surface-2 accent-[rgb(var(--signal))] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-signal"
					checked={$ui.options[$ui.taskId]?.[hint.name] === true}
					onchange={(event) => generate?.setOption($ui.taskId!, hint.name, (event.currentTarget as HTMLInputElement).checked)}
					data-task-hint={hint.name}
				/>
				{hint.label}
			</label>
		{/each}
	</div>
{/if}
