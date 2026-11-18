<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { IconButton, Spinner } from '$lib/components/ui';
	import GenerationFormPane from '$lib/components/generation/GenerationFormPane.svelte';
	import dismiss from '$lib/actions/dismiss';
	import { editorsRevision } from '$lib/editors/registry';
	import { tasks } from '$lib/editors/tasks';
	import { detachedFormTab, localSectionCollapsed } from '$lib/form/detachedFormTab';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import type { SettingsContext } from '$lib/tool-tabs/image/generate/controller';
	import { choiceKey, chipLabel } from '$lib/tool-tabs/image/generate/setup';
	import { isChanged } from '$lib/tool-tabs/image/generate/tweaks';

	let { phone = false, onback }: { host?: unknown; phone?: boolean; onback?: () => void } = $props();

	const generate = getGenerateController()!;
	const ui = generate.ui;

	let taskId = $derived($ui.taskId);
	let key = $derived(taskId && $ui.choice[taskId] ? choiceKey($ui.choice[taskId]!) : null);
	let entry = $derived.by(() => {
		void $editorsRevision;
		return taskId ? tasks.get(generate.editor, taskId) : undefined;
	});
	let label = $derived(entry?.label ?? $ui.tasks.find((item) => item.id === taskId)?.label ?? 'Edit');
	let context = $state.raw<SettingsContext | null>(null);
	let status = $state<'loading' | 'ready' | 'failed'>('loading');
	let reloads = $derived($ui.reloads);

	$effect(() => {
		const id = taskId;
		void key;
		void reloads;
		if (!id || !key) {
			context = null;
			status = 'failed';
			return;
		}
		let live = true;
		status = 'loading';
		void generate.settings(id).then((found) => {
			if (!live) return;
			context = found;
			status = found ? 'ready' : 'failed';
		});
		return () => {
			live = false;
		};
	});

	let tweak = $derived(taskId ? $ui.tweaks[taskId] : undefined);
	let changed = $derived(isChanged(tweak));
	let appliedFormulas = $derived(taskId ? ($ui.formulas[taskId] ?? []) : []);
	let formTab = $derived(
		context
			? detachedFormTab({ id: `editor-settings:${context.key}`, presetId: context.offer.preset_id, mode: context.offer.mode, formData: context.values })
			: null
	);
	let folds = $derived(context ? localSectionCollapsed(context.offer.preset_id, context.offer.mode) : undefined);
	let setupText = $derived(context ? chipLabel(context.offer, context.source) : '');

	function edited(data: Record<string, unknown>) {
		if (taskId) generate.tweak(taskId, data);
	}
</script>

{#snippet header()}
	<div class="flex-none border-b border-line px-3 pb-2.5 pt-2" data-settings-header>
		<div class="flex min-h-7 items-center gap-2.5">
			<Tooltip text="Back to {label}">
				<button
					type="button"
					class="inline-flex items-center gap-1 rounded py-0.5 pr-1 text-sm font-medium text-fg-muted transition-colors hover:text-fg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-signal"
					aria-label="Back to {label}"
					onclick={onback}
					data-detail-back
				>
					<Icon name="chevron-left" className="h-3.5 w-3.5" />{label}
				</button>
			</Tooltip>
			<span class="h-5 w-px flex-none bg-line-strong" aria-hidden="true"></span>
			<h2 class="min-w-0 truncate text-base font-semibold text-fg">Settings</h2>
		</div>
		<div class="mt-0.5 flex items-center gap-2 font-mono text-sm text-fg-subtle">
			<span class="min-w-0 flex-1 truncate" data-settings-setup>{setupText}</span>
			{#if changed}
				<span class="inline-flex flex-none items-center gap-1.5 text-signal" data-settings-changed><span class="h-1.5 w-1.5 rounded-full bg-signal"></span>Changed</span>
			{/if}
		</div>
	</div>
{/snippet}

<section
	class="flex min-h-0 flex-col {phone ? 'flex-none border-t border-line-strong bg-surface-1 shadow-[var(--shadow-dock)] [flex:0_0_55%]' : 'flex-1'}"
	aria-label="Settings"
	use:dismiss={{ onDismiss: () => onback?.(), outside: false }}
	data-settings-view={phone ? 'phone' : 'desktop'}
>
	{@render header()}
	<div class="min-h-0 flex-1 overflow-y-auto px-4 pb-4 pt-2 {phone ? 'px-3' : ''}" data-settings-body>
		{#if status === 'loading'}
			<div class="flex justify-center py-8"><Spinner size="sm" /></div>
		{:else if status === 'failed' || !formTab}
			<p class="rounded bg-surface-2 px-2.5 py-2 text-sm text-fg-muted" role="alert" data-settings-unavailable>Pick a setup to see its settings.</p>
		{:else}
			{#if appliedFormulas.length > 0}
				<ul class="mb-3 flex flex-col gap-1.5" aria-label="Applied formulas" data-applied-formulas>
					{#each appliedFormulas as applied (applied.id)}
						<li class="flex items-center gap-3 rounded-lg border border-signal/40 bg-signal/10 px-3 py-2" data-applied-formula={applied.id}>
							<Icon name="check" className="h-4 w-4 flex-shrink-0 text-signal" />
							<div class="min-w-0 flex-1">
								<div class="truncate text-sm text-fg"><span class="font-semibold">{applied.name}</span> applied</div>
								<div class="font-mono text-sm tabular-nums text-fg-muted" data-applied-counts>
									{applied.changed} changed{#if applied.skipped > 0}{' · '}{applied.skipped} skipped{/if}
								</div>
							</div>
							<Tooltip text="Remove {applied.name}" position="bottom" delay={150}>
								<IconButton icon="close" label="Remove {applied.name}" size="sm" onclick={() => taskId && generate.removeAppliedFormula(taskId, applied.id)} />
							</Tooltip>
						</li>
					{/each}
				</ul>
			{/if}
			{#key `${formTab.id}:${reloads}`}
				<GenerationFormPane tab={formTab} sectionCollapsedContext={folds} onFormDataChange={edited} />
			{/key}
		{/if}
	</div>
</section>
