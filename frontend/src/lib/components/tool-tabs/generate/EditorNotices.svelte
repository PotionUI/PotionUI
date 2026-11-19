<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { authStore } from '$lib/stores/auth';
	import { tasks } from '$lib/editors/tasks';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { offerOf } from '$lib/tool-tabs/image/generate/setup';
	import { viaNote } from '$lib/tool-tabs/image/generate/taskOptions';

	let { taskId, hint = true }: { taskId: string; hint?: boolean } = $props();

	const generate = getGenerateController()!;
	const ui = generate.ui;

	let task = $derived($ui.tasks.find((entry) => entry.id === taskId) ?? null);
	let choice = $derived($ui.choice[taskId] ?? null);
	let notice = $derived($ui.picker.notice);
	let nothingOffered = $derived((task?.offers.length ?? 0) === 0);
	let needsPick = $derived(!choice && !notice && !nothingOffered && !$ui.picker.open);
	let via = $derived.by(() => {
		const offer = task && choice ? offerOf(task.offers, choice) : null;
		return viaNote(offer, (id) => tasks.get(generate.editor, id)?.label);
	});
	let isAdmin = $derived($authStore.user?.account_type === 'ADMIN');
</script>

{#if via}
	<p class="m-0 flex min-h-8 min-w-0 items-center gap-2 text-sm text-fg-muted" data-via-note><Icon name="info" className="h-3.5 w-3.5 flex-none" /><span class="min-w-0">{via}</span></p>
{/if}
{#if notice}
	<div class="flex min-h-8 min-w-0 flex-1 items-center gap-2 rounded bg-warning/10 px-2.5 text-sm text-fg" role="alert" data-picker-notice>
		<Icon name="warning" className="h-3.5 w-3.5 flex-none text-warning" /><span class="min-w-0 truncate">{notice}</span>
	</div>
{/if}
{#if nothingOffered}
	<p class="m-0 flex min-h-8 min-w-0 flex-1 items-center gap-2 rounded bg-surface-2 px-2.5 text-sm text-fg-muted" data-picker-empty>
		<Icon name="model" className="h-3.5 w-3.5 flex-none" />
		<span><b class="font-semibold text-fg">No model can do this yet.</b> {isAdmin ? `Install a model that edits ${generate.editor === 'video' ? 'clips' : 'pictures'}, for example from Admin → Recipes.` : `Ask your admin to add a model that edits ${generate.editor === 'video' ? 'clips' : 'pictures'}.`}</span>
	</p>
{:else if needsPick && hint}
	<p class="m-0 flex min-h-8 items-center gap-2 text-sm text-fg-muted" data-picker-hint><Icon name="info" className="h-3.5 w-3.5 flex-none" />Choose a preset first</p>
{/if}
