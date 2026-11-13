<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { Alert, Button, EmptyState, LoadErrorState, SegmentedControl, Spinner } from '$lib/components/ui';
	import ActivityList from '$lib/components/organize/ActivityList.svelte';
	import RuleEditor from '$lib/components/organize/RuleEditor.svelte';
	import RuleList from '$lib/components/organize/RuleList.svelte';
	import TemplateGrid from '$lib/components/organize/TemplateGrid.svelte';
	import { setPendingRule, takePendingRule } from '$lib/organize/handoff';
	import { provideOrganizeScope } from '$lib/organize/scope';
	import { subjectNoun } from '$lib/organize/sentence';
	import { AUTO_ORGANIZE_SUBJECT, type AutoOrganizeController } from './controller.svelte';
	import { autoOrganizeHref } from './section';

	let { ctrl }: { ctrl: AutoOrganizeController } = $props();

	provideOrganizeScope('instance');

	const noun = subjectNoun(AUTO_ORGANIZE_SUBJECT);
	const view = $derived($page.url.searchParams.get('pane') === 'activity' ? 'activity' : 'rules');
	const ruleParam = $derived($page.url.searchParams.get('rule'));

	function url(extra: Record<string, string | null | undefined> = {}): string {
		return autoOrganizeHref($page.url.pathname, extra);
	}

	function selectView(next: string) {
		void goto(url({ pane: next === 'activity' ? 'activity' : null }));
	}

	function backToList() {
		ctrl.closeEditor();
		void goto(url({ pane: view === 'activity' ? 'activity' : null }));
	}

	onMount(() => {
		void ctrl.load();
	});

	$effect(() => {
		const id = ruleParam;
		const catalog = ctrl.catalog;
		untrack(() => {
			if (!id) {
				ctrl.closeEditor();
				return;
			}
			if (!catalog) return;
			if (id === 'new') {
				ctrl.openNew(takePendingRule());
				return;
			}
			void ctrl.openExisting(id).then((found) => {
				if (!found) void goto(url(), { replaceState: true });
			});
		});
	});
</script>

{#if ctrl.editor && ctrl.catalog}
	{#key ctrl.editor.key}
		<RuleEditor
			initial={ctrl.editor.draft}
			catalog={ctrl.catalog}
			onBack={backToList}
			onSaved={ctrl.upsert}
			onViewActivity={() => {
				ctrl.closeEditor();
				void goto(url({ pane: 'activity' }));
			}}
		/>
	{/key}
{:else if ctrl.resolvingRule}
	<div class="flex h-40 items-center justify-center"><Spinner size="lg" /></div>
{:else}
	<div class="space-y-4 p-4 sm:p-5" data-testid="model-auto-organize">
		{#if ctrl.attention > 0}
			<Alert variant="warning" icon>
				{ctrl.attention === 1 ? 'One rule was paused or needs a look.' : `${ctrl.attention} rules were paused or need a look.`}
				Open them below to see why.
			</Alert>
		{/if}

		<SegmentedControl
			ariaLabel="Auto-organize view"
			selected={view}
			items={[
				{ id: 'rules', label: 'Rules', count: ctrl.rules.length || undefined },
				{ id: 'activity', label: 'Activity' }
			]}
			onSelect={selectView}
		/>

		{#if view === 'activity'}
			<ActivityList subject={AUTO_ORGANIZE_SUBJECT} refreshKey={ctrl.activityRefresh} />
		{:else if ctrl.loading && ctrl.rules.length === 0}
			<div class="flex h-40 items-center justify-center"><Spinner size="lg" /></div>
		{:else if ctrl.loadError && ctrl.rules.length === 0}
			<LoadErrorState message={ctrl.loadError} onRetry={() => ctrl.load()} retrying={ctrl.loading} />
		{:else}
			<Alert variant="info" icon="wand" title="Instance rules" live="off">
				These rules belong to the instance, not to you. They look at every model, run as the system, and any admin can change them; each change and run records who made it. Your own filing rules stay on the Auto-organize page.
			</Alert>
			{#if ctrl.rules.length === 0}
				<EmptyState
					icon="wand"
					title="No rules yet"
					description="A rule watches for new {noun.plural} that match what you describe and sets them up for the whole instance."
					compact
				>
					{#snippet actions()}
						<Button size="sm" variant="secondary" icon="plus" onclick={() => goto(url({ rule: 'new' }))}>New rule</Button>
					{/snippet}
				</EmptyState>
			{:else if ctrl.visibleRules.length === 0}
				<EmptyState icon="search" title="No matching rules" description="Try a different search term." compact />
			{:else}
				<RuleList
					rules={ctrl.visibleRules}
					catalog={ctrl.catalog}
					labels={ctrl.labels}
					collectionNames={ctrl.collectionNames}
					busyId={ctrl.busyId}
					onOpen={(rule) => goto(url({ rule: rule.id }))}
					onToggle={ctrl.toggle}
					onToggleStop={ctrl.toggleStop}
					onDuplicate={ctrl.duplicate}
					onDelete={ctrl.remove}
					onReorder={ctrl.reorder}
				/>
			{/if}

			{#if ctrl.templates.length > 0}
				<TemplateGrid
					templates={ctrl.templates}
					onUse={(template) => {
						setPendingRule(template.rule);
						void goto(url({ rule: 'new' }));
					}}
				/>
			{/if}
		{/if}
	</div>
{/if}
