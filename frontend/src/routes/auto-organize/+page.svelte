<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { Alert, Button, EmptyState, LoadErrorState, SegmentedControl, Spinner } from '$lib/components/ui';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import type { LibrarySectionMeta } from '$lib/components/library/librarySection';
	import ActivityList from '$lib/components/organize/ActivityList.svelte';
	import RuleEditor from '$lib/components/organize/RuleEditor.svelte';
	import RuleList from '$lib/components/organize/RuleList.svelte';
	import TemplateGrid from '$lib/components/organize/TemplateGrid.svelte';
	import { api } from '$lib/services/api';
	import { confirmDialog } from '$lib/stores/confirm';
	import { toasts } from '$lib/stores/toast';
	import { draftFromParts, draftFromRule, emptyDraft, effectiveKind, type RuleDraft, type ValueLabels } from '$lib/organize/draft';
	import { parseOrganizeError } from '$lib/organize/errors';
	import { startRuleFrom, takePendingRule } from '$lib/organize/handoff';
	import { resolveModelNames } from '$lib/organize/labels';
	import { attentionCount } from '$lib/organize/status';
	import { ORGANIZE_SUBJECTS, organizeHref, slugFromSubject, subjectFromSlug, type OrganizeSlug } from '$lib/organize/subjects';
	import { templatesFor } from '$lib/organize/templates';
	import { subjectNoun } from '$lib/organize/sentence';
	import type { OrganizeCatalog, OrganizeRule, OrganizeSubject, OrganizeSummary } from '$lib/types/organize';

	const sections: readonly LibrarySectionMeta<OrganizeSlug>[] = ORGANIZE_SUBJECTS.map((s) => ({
		id: s.slug,
		label: s.label,
		icon: s.icon
	}));

	let catalog = $state<OrganizeCatalog | null>(null);
	let summary = $state<OrganizeSummary | null>(null);
	let rules = $state<OrganizeRule[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let busyId = $state<string | null>(null);
	let query = $state('');
	let modelNames = $state<Record<string, string>>({});
	let editor = $state<{ key: number; draft: RuleDraft } | null>(null);
	let activityRefresh = $state(0);
	let editorCounter = 0;
	let resolvingRule = $state(false);

	const subjectParam = $derived($page.url.searchParams.get('subject'));
	const subject = $derived<OrganizeSubject>(subjectFromSlug(subjectParam));
	const slug = $derived(slugFromSubject(subject));
	const view = $derived($page.url.searchParams.get('view') === 'activity' ? 'activity' : 'rules');
	const ruleParam = $derived($page.url.searchParams.get('rule'));
	const noun = $derived(subjectNoun(subject));

	const visibleRules = $derived.by(() => {
		const q = query.trim().toLowerCase();
		return q ? rules.filter((r) => r.name.toLowerCase().includes(q)) : rules;
	});
	const labels = $derived<ValueLabels>({ models: modelNames, options: {} });
	const collectionNames = $derived.by(() => {
		const out: Record<string, string> = {};
		for (const rule of rules) for (const c of rule.targets.collections) out[c.id] = c.name;
		return out;
	});
	const sectionCounts = $derived.by(() => {
		const out: Partial<Record<OrganizeSlug, number>> = {};
		if (summary) for (const s of ORGANIZE_SUBJECTS) out[s.slug] = summary.subjects[s.subject]?.total ?? 0;
		return out;
	});
	const templates = $derived(templatesFor(subject, catalog));
	const attention = $derived(attentionCount(rules));

	function url(extra: Record<string, string | null | undefined> = {}, subj: OrganizeSubject = subject): string {
		return organizeHref(subj, extra);
	}

	function selectSection(next: OrganizeSlug) {
		query = '';
		void goto(url({}, subjectFromSlug(next)));
	}

	function selectView(next: string) {
		void goto(url({ view: next === 'activity' ? 'activity' : null }));
	}

	function backToList() {
		editor = null;
		void goto(url({ view: view === 'activity' ? 'activity' : null }));
	}

	async function loadRules() {
		const [rulesResult, summaryResult] = await Promise.allSettled([
			api.listOrganizeRules(subject),
			api.getOrganizeSummary()
		]);
		if (rulesResult.status === 'fulfilled' && rulesResult.value.success) {
			rules = rulesResult.value.data ?? [];
			loadError = null;
		} else {
			loadError =
				rulesResult.status === 'fulfilled'
					? rulesResult.value.message || 'The rules could not be loaded.'
					: parseOrganizeError(rulesResult.reason, 'The rules could not be loaded.').message;
		}
		if (summaryResult.status === 'fulfilled' && summaryResult.value.success) summary = summaryResult.value.data ?? null;
		await resolveLabels();
	}

	async function resolveLabels() {
		if (!catalog) return;
		const ids: string[] = [];
		for (const rule of rules) {
			for (const cond of rule.conditions) {
				const spec = catalog.facts.find((f) => f.key === cond.fact);
				if (effectiveKind(spec) !== 'model_ref') continue;
				if (Array.isArray(cond.value)) ids.push(...(cond.value as string[]));
				else if (typeof cond.value === 'string') ids.push(cond.value);
			}
		}
		if (ids.length > 0) modelNames = { ...modelNames, ...(await resolveModelNames(ids)) };
	}

	async function loadAll() {
		loading = true;
		if (!catalog) {
			try {
				const response = await api.getOrganizeCatalog();
				if (response.success && response.data) catalog = response.data;
			} catch (err) {
				loadError = parseOrganizeError(err, 'Auto-organize could not be loaded.').message;
			}
		}
		await loadRules();
		loading = false;
	}

	let loadedSubject: OrganizeSubject | null = null;
	$effect(() => {
		const current = subject;
		if (loadedSubject === current) return;
		loadedSubject = current;
		void loadAll();
	});

	$effect(() => {
		const id = ruleParam;
		const cat = catalog;
		if (!id) {
			editor = null;
			return;
		}
		if (!cat) return;
		if (id === 'new') {
			if (editor && editor.draft.id === null) return;
			const pending = takePendingRule();
			const useSubject = pending?.subject ?? subject;
			const draft = pending ? draftFromParts(useSubject, pending) : emptyDraft(useSubject);
			editorCounter += 1;
			editor = { key: editorCounter, draft };
			if (useSubject !== subject) void goto(url({ rule: 'new' }, useSubject), { replaceState: true });
			return;
		}
		if (editor?.draft.id === id) return;
		void openExisting(id);
	});

	async function openExisting(id: string) {
		resolvingRule = true;
		try {
			const known = rules.find((r) => r.id === id);
			const rule = known ?? (await api.getOrganizeRule(id)).data;
			if (!rule) {
				toasts.error('That rule could not be found.');
				void goto(url(), { replaceState: true });
				return;
			}
			if (rule.subject !== subject) {
				void goto(url({ rule: id }, rule.subject), { replaceState: true });
				return;
			}
			editorCounter += 1;
			editor = { key: editorCounter, draft: draftFromRule(rule) };
		} catch {
			toasts.error('That rule could not be found.');
			void goto(url(), { replaceState: true });
		} finally {
			resolvingRule = false;
		}
	}

	function upsert(rule: OrganizeRule) {
		const exists = rules.some((r) => r.id === rule.id);
		rules = exists ? rules.map((r) => (r.id === rule.id ? rule : r)) : [...rules, rule];
		void resolveLabels();
		void api.getOrganizeSummary().then((r) => {
			if (r.success && r.data) summary = r.data;
		});
		activityRefresh += 1;
	}

	async function handleToggle(rule: OrganizeRule, next: boolean) {
		busyId = rule.id;
		try {
			const response = await api.patchOrganizeRule(rule.id, { enabled: next });
			if (response.success && response.data) upsert(response.data);
			else toasts.error(response.message || 'The rule could not be changed.');
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The rule could not be changed.').message);
		} finally {
			busyId = null;
		}
	}

	async function handleToggleStop(rule: OrganizeRule) {
		try {
			const response = await api.patchOrganizeRule(rule.id, { stop_after: !rule.stop_after });
			if (response.success && response.data) upsert(response.data);
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The rule could not be changed.').message);
		}
	}

	async function handleDuplicate(rule: OrganizeRule) {
		try {
			const response = await api.duplicateOrganizeRule(rule.id);
			if (response.success && response.data) {
				upsert(response.data);
				toasts.success(`Copied "${rule.name}". The copy is switched off.`);
			}
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The rule could not be copied.').message);
		}
	}

	async function handleDelete(rule: OrganizeRule) {
		const ok = await confirmDialog({
			title: `Delete "${rule.name}"?`,
			message: 'Items it already filed stay where they are, and its activity stays in the list.',
			variant: 'danger'
		});
		if (!ok) return;
		try {
			const response = await api.deleteOrganizeRule(rule.id);
			if (response.success) {
				rules = rules.filter((r) => r.id !== rule.id);
				void api.getOrganizeSummary().then((r) => {
					if (r.success && r.data) summary = r.data;
				});
			}
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The rule could not be deleted.').message);
		}
	}

	async function handleReorder(ids: string[]) {
		if (query.trim()) return;
		const previous = rules;
		rules = ids.map((id) => previous.find((r) => r.id === id)!).filter(Boolean);
		try {
			const response = await api.reorderOrganizeRules(subject, ids);
			if (response.success && response.data) rules = response.data;
			else rules = previous;
		} catch (err) {
			rules = previous;
			toasts.error(parseOrganizeError(err, 'The order could not be saved.').message);
		}
	}

	function newRule() {
		void goto(url({ rule: 'new' }));
	}
</script>

<svelte:head>
	<title>Auto-organize · PotionUI</title>
</svelte:head>

<LibraryShell
	title="Auto-organize"
	persistKey="auto-organize"
	{sections}
	section={slug}
	onSelectSection={selectSection}
	{sectionCounts}
	count={rules.length}
	detailOpen={!!editor || resolvingRule}
	titleLabel="Auto-organize"
>
	{#snippet toolbar()}
		{#if view === 'rules'}
			<LibraryFilterBar
				q={query}
				onQueryChange={(value) => (query = value)}
				searchPlaceholder="Search rules by name…"
			/>
		{/if}
	{/snippet}

	{#snippet primary()}
		<Button variant="primary" size="sm" icon="plus" onclick={newRule}>New rule</Button>
	{/snippet}

	{#if editor && catalog}
		{#key editor.key}
			<RuleEditor
				initial={editor.draft}
				{catalog}
				onBack={backToList}
				onSaved={upsert}
				onViewActivity={() => {
					editor = null;
					void goto(url({ view: 'activity' }));
				}}
			/>
		{/key}
	{:else if resolvingRule}
		<div class="flex h-40 items-center justify-center"><Spinner size="lg" /></div>
	{:else}
		<div class="space-y-4 p-4 sm:p-5">
			{#if summary?.paused_by_admin}
				<Alert variant="warning" icon>
					Auto-organize is paused by an administrator. Your rules are kept and start again when it is switched back on.
				</Alert>
			{/if}
			{#if attention > 0}
				<Alert variant="warning" icon>
					{attention === 1 ? 'One rule was paused or needs a look.' : `${attention} rules were paused or need a look.`}
					Open them below to see why.
				</Alert>
			{/if}

			<SegmentedControl
				ariaLabel="Auto-organize view"
				selected={view}
				items={[
					{ id: 'rules', label: 'Rules', count: rules.length || undefined },
					{ id: 'activity', label: 'Activity' }
				]}
				onSelect={selectView}
			/>

			{#if view === 'activity'}
				<ActivityList {subject} refreshKey={activityRefresh} />
			{:else if loading && rules.length === 0}
				<div class="flex h-40 items-center justify-center"><Spinner size="lg" /></div>
			{:else if loadError && rules.length === 0}
				<LoadErrorState message={loadError} onRetry={loadAll} retrying={loading} />
			{:else}
				<p class="text-sm text-fg-muted">
					Rules file your new {noun.plural} into collections{subject === 'model' ? '' : ' and tags'} for you. They only add, they never remove anything, and every run can be undone from Activity.
				</p>
				{#if rules.length === 0}
					<EmptyState
						icon="wand"
						title="No rules yet"
						description="A rule watches for new {noun.plural} that match what you describe and files them for you."
						compact
					>
						{#snippet actions()}
							<Button variant="primary" size="sm" icon="plus" onclick={newRule}>New rule</Button>
						{/snippet}
					</EmptyState>
				{:else if visibleRules.length === 0}
					<EmptyState icon="search" title="No matching rules" description="Try a different search term." compact />
				{:else}
					<RuleList
						rules={visibleRules}
						{catalog}
						{labels}
						{collectionNames}
						{busyId}
						onOpen={(rule) => goto(url({ rule: rule.id }))}
						onToggle={handleToggle}
						onToggleStop={handleToggleStop}
						onDuplicate={handleDuplicate}
						onDelete={handleDelete}
						onReorder={handleReorder}
					/>
				{/if}

				{#if templates.length > 0}
					<TemplateGrid {templates} onUse={(template) => startRuleFrom(template.rule)} />
				{/if}
			{/if}
		</div>
	{/if}
</LibraryShell>
